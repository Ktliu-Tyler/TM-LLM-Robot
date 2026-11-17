"""Jarvis wake -> converse -> interruptible final playback -> standby."""
import base64
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time

ASR_PYTHON = "/home/ktliu/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/.venv/bin/python"
SPEAK_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voices = @($voice.GetInstalledVoices() | Where-Object Enabled)
    $chinese = @($voices | Where-Object { $_.VoiceInfo.Culture.Name -eq 'zh-TW' })
    if (-not $chinese.Count) { $chinese = @($voices | Where-Object { $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'zh' }) }
    if (-not $chinese.Count) { throw 'Windows Chinese TTS voice not installed' }
    $voice.SelectVoice($chinese[0].VoiceInfo.Name)
    $voice.SetOutputToWaveFile($request.path)
    $voice.Speak($request.text)
} finally { $voice.Dispose() }
'''


def normalize_utterance(text):
    return re.sub(r"^(?:(?:hey|嘿)[,，\s]*)?(?:jarvis|賈維斯|賈維思)[,，。!！\s]*", "", text.strip(), flags=re.I)


def task_finished(text):
    return bool(re.fullmatch(r"(?:好[的]?[,，\s]*)?(?:任務結束|結束任務|停止語音對話|結束語音對話)(?:[,，\s]*謝謝)?[。！!\s]*", text))


class VoiceLoop:
    def __init__(self, node):
        self.node = node
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = self.worker = None
        self.sid = None
        self.failure = None
        self.idle_seconds = float(os.getenv("PROJECT_A_VOICE_IDLE_SECONDS", "60"))
        self.status = dict(active=False, phase="off", text="", error="")

    def snapshot(self, sid):
        with self.lock:
            if sid != self.sid:
                return dict(active=False, phase="other_session" if self.status["active"] else "off", text="", error="")
            return dict(self.status)

    def update(self, phase, **fields):
        with self.lock:
            self.status.update(phase=phase, **fields)
        self.node.voice_event(self.sid, phase, **fields)

    def on_wake(self):
        with self.lock:
            accepted = self.status["phase"] in ("waiting_wake", "speaking_final")
        if accepted:
            self.wake_event.set()
            self.node.voice_event(self.sid, "wake", keyword="Jarvis")
        return accepted

    def start(self, sid, think):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("已有語音對話運行，請先停止")
            python = os.getenv("PROJECT_A_ASR_PYTHON", ASR_PYTHON)
            if not Path(python).is_file():
                raise ValueError("請設定 PROJECT_A_ASR_PYTHON 為含 qwen_asr 的 Python 路徑")
            self.sid, self.failure = sid, None
            self.stop_event, self.wake_event = threading.Event(), threading.Event()
            self.status = dict(active=True, phase="loading_asr", text="", error="")
            self.thread = threading.Thread(target=self.run, args=(python, think), daemon=True)
            self.thread.start()

    def stop(self, sid):
        if sid != self.sid:
            raise ValueError("請在啟動語音的會話停止")
        self.stop_event.set()

    def command(self, **data):
        if self.worker and self.worker.poll() is None:
            self.worker.stdin.write(json.dumps(data)+"\n")
            self.worker.stdin.flush()

    def run_process(self, command, data=None, interruptible=False, timeout=90):
        proc = subprocess.Popen(command, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        start = time.monotonic()
        try:
            while not self.stop_event.is_set() and not (interruptible and self.wake_event.is_set()):
                try:
                    _, error = proc.communicate(input=data, timeout=0.1)
                    if proc.returncode:
                        raise RuntimeError("語音播放失敗："+error.decode(errors="replace")[-300:])
                    return True
                except subprocess.TimeoutExpired:
                    data = None
                    if time.monotonic()-start > timeout:
                        raise TimeoutError("語音播放逾時")
            return False
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def speak(self, text, phase="notice", interruptible=False):
        if self.stop_event.is_set():
            return False
        self.wake_event.clear()
        self.update(phase, spoken_text=text)
        self.command(op="wake", enabled=interruptible)
        # Avoid the synthesized voice saying its own wake word into the mic.
        spoken = re.sub(r"jarvis|賈維斯|賈維思", "助理", text, flags=re.I)
        spoken = re.sub(r"[*#`]", "", spoken)
        try:
            with tempfile.TemporaryDirectory(prefix="jarvis-tts-") as directory:
                wav = Path(directory)/"speech.wav"
                windows_path = subprocess.check_output(["wslpath", "-w", str(wav)], text=True).strip()
                command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                    base64.b64encode(SPEAK_SCRIPT.encode("utf-16-le")).decode("ascii")]
                data = json.dumps({"text": spoken, "path": windows_path}, ensure_ascii=False).encode()
                if not self.run_process(command, data, interruptible):
                    return False
                # Linux playback is immediately cancellable; Windows only creates WAV.
                playback = ["paplay"]
                if os.getenv("PROJECT_A_TTS_SINK"):
                    playback += ["--device", os.environ["PROJECT_A_TTS_SINK"]]
                return self.run_process(playback+[str(wav)], interruptible=interruptible, timeout=180)
        finally:
            self.command(op="wake", enabled=False)

    def wait_for_wake(self):
        self.wake_event.clear()
        self.update("waiting_wake")
        self.command(op="wake", enabled=True)
        while not self.stop_event.wait(0.1):
            if self.failure:
                raise RuntimeError(self.failure)
            if self.wake_event.is_set():
                self.command(op="wake", enabled=False)
                self.update("awakened")
                return True
        return False

    def wait_for_answer(self, tid):
        started, announced = time.monotonic(), False
        while not self.stop_event.wait(0.1):
            with self.node.lock:
                session = self.node.sessions[self.sid]
                busy, state = session["busy"], session["phase"]
                turn = dict(session["turns"][-1])
            if not busy:
                if turn["turn_id"] != tid:
                    raise RuntimeError("語音輪次已變更")
                return turn
            if not announced and time.monotonic()-started >= 4:
                self.speak("我正在查詢資料，請稍等。" if "Inventory" in state else "我還在處理，請稍等一下。", "progress_speech")
                announced = True
                self.update("processing")
        return None

    def run(self, python, think):
        try:
            events = queue.Queue()
            self.worker = subprocess.Popen([python, "-u", str(Path(__file__).with_name("chat_audio_worker.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, start_new_session=True)
            def reader():
                for line in self.worker.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("phase") == "wake":
                        self.on_wake()
                    elif event.get("phase") == "error":
                        self.failure = event.get("error", "ASR error")
                        events.put(event)
                    else:
                        events.put(event)
                self.failure = "語音辨識程序已結束，請檢查終端日誌"
            threading.Thread(target=reader, daemon=True).start()
            def receive(timeout=180):
                deadline = time.monotonic()+timeout
                while not self.stop_event.is_set():
                    if self.failure:
                        raise RuntimeError(self.failure)
                    if time.monotonic()>deadline:
                        raise TimeoutError("語音辨識等待逾時")
                    try:
                        event = events.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    phase = event.pop("phase")
                    self.update(phase, **event)
                    if phase in ("ready", "transcript", "empty"):
                        return phase, event
                return "stopped", {}
            if receive()[0] != "ready":
                return
            self.speak("語音喚醒已開啟。", "notice")
            while self.wait_for_wake():
                last_input = time.monotonic()
                while not self.stop_event.is_set():
                    remaining = self.idle_seconds-(time.monotonic()-last_input)
                    if remaining <= 0:
                        self.update("idle_timeout")
                        break
                    self.command(op="listen", timeout=min(15, remaining))
                    phase, event = receive()
                    if phase in ("empty", "stopped"):
                        continue
                    text = normalize_utterance(event.get("text", ""))
                    if not text:
                        continue
                    last_input = time.monotonic()
                    if task_finished(text):
                        self.update("task_finished", text=text)
                        self.speak("好的，任務結束，已回到待機。", "notice")
                        break
                    tid = self.node.submit(self.sid, text, think, from_voice=True)
                    self.update("processing", text=text)
                    turn = self.wait_for_answer(tid)
                    if turn is None:
                        break
                    answer = "這次處理失敗，請看畫面的錯誤訊息。" if turn["error"] else turn.get("final", turn["content"])
                    completed = self.speak(answer, "speaking_final", interruptible=True)
                    if self.stop_event.is_set():
                        break
                    if not completed:
                        self.update("interrupted")
                    else:
                        self.stop_event.wait(0.35)
                    # User idle time excludes model inference and playback.
                    last_input = time.monotonic()
        except Exception as error:
            if not self.stop_event.is_set():
                self.update("error", error=str(error))
        finally:
            proc = self.worker
            if proc and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            with self.lock:
                failed = self.status["phase"] == "error"
                self.status["active"] = False
            if not failed:
                self.update("off")
