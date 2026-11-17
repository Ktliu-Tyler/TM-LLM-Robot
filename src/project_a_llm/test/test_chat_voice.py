"""Wake admission, task end, progress and playback cancellation checks."""
import subprocess
import threading
from types import SimpleNamespace
import pytest
from project_a_llm.chat_voice import VoiceLoop, normalize_utterance, task_finished


def voice():
    node = SimpleNamespace(lock=threading.RLock(), voice_event=lambda *a, **k: None,
        sessions={"s": {"busy": False, "phase": "idle", "turns": [
            {"turn_id": "t", "final": "筆在 A1", "content": "", "error": ""}]}})
    result = VoiceLoop(node)
    result.sid = "s"
    return result


@pytest.mark.parametrize("phase,accepted", [("waiting_wake", True), ("speaking_final", True),
    ("progress_speech", False), ("notice", False), ("processing", False), ("recognizing", False)])
def test_wake_only_standby_or_final_playback(phase, accepted):
    v = voice()
    v.status["phase"] = phase
    assert v.on_wake() is accepted
    assert v.wake_event.is_set() is accepted


def test_name_prefix_and_task_end():
    assert normalize_utterance("Hey Jarvis，我的筆在哪？") == "我的筆在哪？"
    assert normalize_utterance("Jarvis") == ""
    assert task_finished("任務結束。")
    assert task_finished("好的，任務結束，謝謝。")
    assert not task_finished("任務結束是什麼意思？")


def test_slow_model_announces_once():
    v = voice()
    v.node.sessions["s"]["busy"] = True
    spoken = []
    v.speak = lambda text, *a, **k: spoken.append(text)
    threading.Timer(4.4, lambda: v.node.sessions["s"].update(busy=False)).start()
    assert v.wait_for_answer("t")["final"] == "筆在 A1"
    assert spoken == ["我還在處理，請稍等一下。"]


def test_stop_drops_pending_speech():
    v = voice()
    v.stop_event.set()
    assert v.wait_for_answer("t") is None
    assert v.speak("不應播出") is False


def test_interrupt_kills_playback_process(monkeypatch):
    v = voice()
    killed = []
    def communicate(**kwargs):
        v.wake_event.set()
        raise subprocess.TimeoutExpired("paplay", 0.1)
    proc = SimpleNamespace(communicate=communicate, poll=lambda: None,
                           kill=lambda: killed.append(True), wait=lambda: 0)
    monkeypatch.setattr("project_a_llm.chat_voice.subprocess.Popen", lambda *a, **k: proc)
    assert v.run_process(["paplay", "test.wav"], interruptible=True) is False
    assert killed == [True]
