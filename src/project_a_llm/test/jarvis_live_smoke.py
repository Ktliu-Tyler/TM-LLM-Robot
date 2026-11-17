"""Opt-in real audio/KWS/ASR/LLM/ROS test via disposable PulseAudio devices.

Requires Windows Chinese + English TTS, configured ASR venv and wake model.
No microphone recording; synthetic fixtures go to a private virtual source.
"""
import base64
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen


def main():
    root = Path(__file__).resolve().parents[3]
    modules, processes, logs = [], [], []
    with tempfile.TemporaryDirectory(prefix="jarvis-smoke-") as folder:
        directory = Path(folder)
        try:
            suffix = str(os.getpid())
            source, sink = "jarvis_input_"+suffix, "jarvis_output_"+suffix
            for name in (source, sink):
                module = subprocess.check_output(["pactl", "load-module", "module-null-sink", "sink_name="+name], text=True).strip()
                modules.append(module)
            # Windows synthesizes fixture WAV files; never uses a live microphone.
            script = r'''
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$items = [Console]::In.ReadToEnd() | ConvertFrom-Json
foreach ($item in $items) {
 $v = New-Object System.Speech.Synthesis.SpeechSynthesizer
 try {
  $name = @($v.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq $item.culture })[0].VoiceInfo.Name
  $v.SelectVoice($name); $v.SetOutputToWaveFile($item.path); $v.Speak($item.text)
 } finally { $v.Dispose() }
}
'''
            fixtures = [("wake", "Jarvis", "en-US"), ("query", "我的筆在哪裡？", "zh-TW"),
                        ("followup", "那它的預設歸位位置呢？", "zh-TW"), ("end", "任務結束", "zh-TW")]
            payload = [{"text": text, "culture": culture, "path": subprocess.check_output(
                ["wslpath", "-w", str(directory/(name+".wav"))], text=True).strip()} for name, text, culture in fixtures]
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                base64.b64encode(script.encode("utf-16-le")).decode()],
                input=json.dumps(payload, ensure_ascii=False).encode(), check=True, timeout=30)
            dbpath = directory/"workbench.sqlite3"
            with sqlite3.connect(f"file:{root/'data/workbench.sqlite3'}?mode=ro", uri=True) as db:
                with sqlite3.connect(dbpath) as dest:
                    db.backup(dest)
            def dump():
                with sqlite3.connect(dbpath) as db:
                    return list(db.iterdump())
            before = dump()
            env = dict(os.environ, ROS_DOMAIN_ID="87", PROJECT_A_DB=str(dbpath),
                PROJECT_A_VOICE_SOURCE=source+".monitor", PROJECT_A_TTS_SINK=sink,
                PROJECT_A_VOICE_IDLE_SECONDS="8", HF_HUB_OFFLINE="1")
            for name, extra in [("state", []), ("chat", ["--ros-args", "-p", "port:=18767"])]:
                log = open(directory/(name+".log"), "w+")
                logs.append(log)
                processes.append(subprocess.Popen(["ros2", "run", "project_a_llm", name, *extra],
                    env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
            def api(path, data=None):
                req = Request("http://127.0.0.1:18767"+path,
                    data=None if data is None else json.dumps(data).encode(), headers={"Content-Type":"application/json"})
                with urlopen(req, timeout=5) as response:
                    return json.load(response)
            for _ in range(120):
                try:
                    sid = api("/api/session", {"user_id":"U_A"})["session_id"]
                    break
                except OSError:
                    time.sleep(0.25)
            else:
                raise RuntimeError("chat startup failed")
            def state():
                return api("/api/state?session_id="+sid)
            def until(predicate, label, timeout=180):
                deadline = time.monotonic()+timeout
                while time.monotonic()<deadline:
                    current = state()
                    if current["voice"]["error"]:
                        raise RuntimeError(current["voice"]["error"])
                    if predicate(current):
                        print("PASS", label, flush=True)
                        return current
                    time.sleep(0.1)
                raise TimeoutError(label+": "+json.dumps(state(), ensure_ascii=False))
            def play(name):
                subprocess.run(["paplay", "--device", source, str(directory/(name+".wav"))], check=True)
            until(lambda s:s["service_ready"], "Inventory discovery", 30)
            api("/api/voice", {"session_id":sid,"action":"start"})
            until(lambda s:s["voice"]["phase"]=="waiting_wake", "standby ready")
            play("query")
            time.sleep(1)
            assert not state()["turns"], "Non-wake audio reached model"
            print("PASS non-wake speech ignored", flush=True)
            play("wake")
            until(lambda s:s["voice"]["phase"]=="listening", "wake opens microphone", 15)
            play("query")
            first = until(lambda s:len(s["turns"])==1 and s["voice"]["phase"]=="speaking_final", "wake -> ASR -> LLM -> final playback")
            print("FIRST_TURN", json.dumps(first["turns"][-1], ensure_ascii=False), flush=True)
            assert any(e["kind"]=="tool_end" for e in first["events"])
            print("ANSWER",first["turns"][-1].get("final"),flush=True)
            until(lambda s:"paplay --device "+sink in subprocess.check_output(
                ["ps", "-eo", "args"], text=True), "final WAV playback started", 15)
            play("wake")
            until(lambda s:any(e["kind"]=="voice_interrupted" for e in s["events"]), "Jarvis interrupts final playback", 15)
            play("followup")
            second = until(lambda s:len(s["turns"])==2 and not s["busy"], "follow-up context")
            assert not second["turns"][-1]["error"]
            print("ANSWER",second["turns"][-1].get("final"),flush=True)
            until(lambda s:s["voice"]["phase"]=="listening", "automatic listening after reply")
            play("end")
            until(lambda s:s["voice"]["phase"]=="waiting_wake" and any(e["kind"]=="voice_task_finished" for e in s["events"]), "task end -> standby")
            assert len(state()["turns"])==2, "Task end incorrectly sent to LLM"
            play("wake")
            until(lambda s:s["voice"]["phase"]=="waiting_wake" and any(e["kind"]=="voice_idle_timeout" for e in s["events"]), "idle timeout -> standby", 45)
            api("/api/voice", {"session_id":sid,"action":"stop"})
            final = until(lambda s:not s["voice"]["active"], "stop releases microphone", 15)
            assert dump()==before
            print("PASS database unchanged",flush=True)
            print(json.dumps({"voice_events":[e for e in final["events"] if e["kind"].startswith("voice_")]},ensure_ascii=False),flush=True)
        finally:
            for proc in processes:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGINT)
            for proc in processes:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            for module in reversed(modules):
                subprocess.run(["pactl","unload-module",module],check=False)
            for log in logs:
                log.seek(0);print(log.read());log.close()


if __name__=="__main__":
    main()
