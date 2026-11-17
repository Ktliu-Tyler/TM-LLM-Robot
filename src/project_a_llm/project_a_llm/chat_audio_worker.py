"""Persistent ASR + streaming keyword worker; stdin/stdout JSON protocol."""
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque

OUTPUT = threading.Lock()


def emit(phase, **fields):
    with OUTPUT:
        print(json.dumps(dict(phase=phase, **fields), ensure_ascii=False), flush=True)


class AudioInput:
    def __init__(self, np, spotter):
        self.np, self.spotter = np, spotter
        self.condition = threading.Condition()
        self.kws_lock = threading.Lock()
        self.frames = deque(maxlen=400)
        self.seq = self.wake_seq = self.wake_at = 0
        self.enabled = False
        self.error = None
        self.proc = subprocess.Popen(["parec", "--raw", "--latency-msec=100", "--format=s16le", "--channels=1",
            "--rate=16000", "--device", os.getenv("PROJECT_A_VOICE_SOURCE", "RDPSource")], stdout=subprocess.PIPE)
        threading.Thread(target=self.read, daemon=True).start()

    def enable_wake(self, enabled):
        with self.kws_lock:
            self.enabled = enabled
            self.spotter.reset()

    def read(self):
        try:
            while True:
                raw = self.proc.stdout.read(3200)
                if len(raw) != 3200:
                    raise RuntimeError("麥克風串流中斷")
                with self.condition:
                    self.seq += 1
                    self.frames.append((self.seq, raw))
                    self.condition.notify_all()
                samples = self.np.frombuffer(raw, dtype="<i2").astype(self.np.float32)/32768
                with self.kws_lock:
                    if self.enabled and self.spotter.accept(samples):
                        self.enabled = False
                        self.wake_at = time.monotonic()
                        # Do not feed the tail of the wake word back to Chinese ASR.
                        # Frames after detection are retained while playback stops.
                        self.wake_seq = self.seq
                        emit("wake", keyword="Jarvis")
        except Exception as error:
            self.error = str(error)
            emit("error", error=self.error)
            with self.condition:
                self.condition.notify_all()

    def capture(self, timeout=30):
        np = self.np
        threshold = float(os.getenv("PROJECT_A_VOICE_RMS", "0.012"))
        with self.condition:
            cursor = self.wake_seq if time.monotonic()-self.wake_at < 2 else self.seq
            self.wake_at = 0
        chunks, pre = [], deque(maxlen=3)
        voiced = silence = 0
        started = time.monotonic()
        emit("listening")
        while chunks or time.monotonic()-started < timeout:
            with self.condition:
                if self.error:
                    raise RuntimeError(self.error)
                frames = [(seq, raw) for seq, raw in self.frames if seq > cursor]
                if not frames:
                    self.condition.wait(0.2)
                    continue
            for seq, raw in frames:
                cursor = seq
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)/32768
                loud = float(np.sqrt(np.mean(samples*samples))) >= threshold
                if not chunks:
                    pre.append(raw)
                    if loud:
                        chunks.extend(pre)
                        voiced = 1
                        emit("recording")
                else:
                    chunks.append(raw)
                    voiced += int(loud)
                    silence = 0 if loud else silence+1
                    if silence >= 9 or len(chunks) >= 150:
                        return np.frombuffer(b"".join(chunks), dtype="<i2").astype(np.float32)/32768 if voiced >= 3 else None
        return None

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def main():
    from contextlib import redirect_stdout
    emit("loading_asr")
    with redirect_stdout(sys.stderr):
        import numpy as np
        import torch
        from qwen_asr import Qwen3ASRModel
        device = os.getenv("PROJECT_A_ASR_DEVICE", "cuda:0" if torch.cuda.is_available() else "cpu")
        torch.set_num_threads(int(os.getenv("PROJECT_A_ASR_THREADS", "6")))
        model = Qwen3ASRModel.from_pretrained(
            os.getenv("PROJECT_A_ASR_MODEL", "Qwen/Qwen3-ASR-0.6B"), device_map=device,
            dtype=torch.bfloat16 if device.startswith("cuda") else torch.float32, max_new_tokens=256)
        # Pay lazy CUDA initialization before announcing readiness.
        model.transcribe(audio=(np.zeros(16000, dtype=np.float32), 16000), language="Chinese")
    audio_input = None
    emit("ready", device=device, model=os.getenv("PROJECT_A_ASR_MODEL", "Qwen/Qwen3-ASR-0.6B"))
    try:
        for line in sys.stdin:
            try:
                command = json.loads(line)
                if command.get("op") in ("wake", "listen") and not command.get("wav") and audio_input is None:
                    from chat_kws import JarvisSpotter
                    audio_input = AudioInput(np, JarvisSpotter())
                    emit("wake_ready")
                if command.get("op") == "wake":
                    audio_input.enable_wake(bool(command.get("enabled")))
                    continue
                if command.get("op") != "listen":
                    raise ValueError("未知音訊指令")
                audio = command.get("wav")
                if not audio:
                    audio_input.enable_wake(False)
                    samples = audio_input.capture(float(command.get("timeout", 30)))
                    if samples is None:
                        emit("empty")
                        continue
                    audio = (samples, 16000)
                emit("recognizing")
                before = time.monotonic()
                with redirect_stdout(sys.stderr):
                    text = model.transcribe(audio=audio, language="Chinese")[0].text.strip()
                text = subprocess.run(["uconv", "-f", "UTF-8", "-t", "UTF-8", "-x",
                    "Simplified-Traditional"], input=text, text=True, capture_output=True, check=True).stdout
                emit("transcript" if text else "empty", text=text,
                     duration_ms=round((time.monotonic()-before)*1000))
            except Exception as error:
                emit("error", error=str(error))
    finally:
        if audio_input:
            audio_input.close()


if __name__ == "__main__":
    main()
