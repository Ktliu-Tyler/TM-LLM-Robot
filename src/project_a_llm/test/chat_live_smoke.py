"""Opt-in live model/ROS integration check; uses a temporary DB backup.

Run after sourcing install/setup.bash: python3 .../test/chat_live_smoke.py
This is deliberately not collected by pytest (requires running Ollama).
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import tempfile
import time
import uuid
from urllib.request import Request, urlopen


def main():
    root = Path(__file__).resolve().parents[3]
    with tempfile.TemporaryDirectory(prefix="project-a-chat-") as folder:
        dbpath = Path(folder) / "workbench.sqlite3"
        with sqlite3.connect(f"file:{root / 'data/workbench.sqlite3'}?mode=ro", uri=True) as source:
            with sqlite3.connect(dbpath) as target:
                source.backup(target)
        def rows():
            with sqlite3.connect(dbpath) as db:
                return list(db.iterdump())
        before = rows()
        env = dict(os.environ, ROS_DOMAIN_ID="87", PROJECT_A_DB=str(dbpath))
        processes = []
        logs = []
        try:
            for name, extra in [("state", []), ("chat", ["--ros-args", "-p", "port:=18766"])]:
                log = open(Path(folder) / (name + ".log"), "w+")
                logs.append(log)
                processes.append(subprocess.Popen(["ros2", "run", "project_a_llm", name, *extra],
                    env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
            def api(path, data=None):
                request = Request("http://127.0.0.1:18766" + path,
                    data=None if data is None else json.dumps(data).encode(),
                    headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=5) as response:
                    return json.load(response)
            for _ in range(60):
                try:
                    sid = api("/api/session", {"user_id": "U_A"})["session_id"]
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("Chat startup failed")
            for _ in range(40):
                if api("/api/state?session_id=" + sid)["service_ready"]:
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Inventory discovery failed")
            report = []
            for question in ["你好，請叫我測試員阿七。", "我剛剛請你叫我什麼？", "我的筆在哪裡？", "那它的預設歸位位置呢？"]:
                start = time.monotonic()
                tid = api("/api/chat", {"session_id": sid, "text": question})["turn_id"]
                for _ in range(320):
                    state = api("/api/state?session_id=" + sid)
                    if not state["busy"]:
                        break
                    time.sleep(0.5)
                else:
                    raise TimeoutError("Turn stalled")
                turn = state["turns"][-1]
                events = [e for e in state["events"] if e["turn_id"] == tid]
                item = dict(question=question, answer=turn["content"], error=turn["error"],
                    seconds=round(time.monotonic()-start, 2), events=events)
                report.append(item)
                print(json.dumps(item, ensure_ascii=False), flush=True)
                assert not turn["error"], turn["error"]
                if "筆" in question or "歸位" in question:
                    assert any(e["kind"] == "tool_end" for e in events), "No successful database call"
            assert "阿七" in report[1]["answer"], "Conversation memory missing"
            assert rows() == before, "Read-only chat modified database"
            other = api("/api/session", {"user_id": "U_B"})["session_id"]
            assert not api("/api/state?session_id="+other)["turns"]
            print("PASS: live four-turn conversation, tool round trips, DB unchanged, separate session")
            # Exercise the original architecture's input/result contract too.
            import rclpy
            from std_msgs.msg import String
            rclpy.init(domain_id=87)
            probe = rclpy.create_node("chat_contract_probe")
            received = []
            probe.create_subscription(String, "/project_a/agent/result",
                lambda msg: received.append(json.loads(msg.data)), 10)
            publisher = probe.create_publisher(String, "/project_a/agent/input", 10)
            def spin_until(predicate, timeout=160):
                deadline = time.monotonic()+timeout
                while time.monotonic()<deadline:
                    rclpy.spin_once(probe, timeout_sec=0.1)
                    if predicate():
                        return
                raise TimeoutError("ROS input/result contract")
            try:
                spin_until(lambda:publisher.get_subscription_count()>0, 20)
                event = dict(utterance_id="contract-"+str(uuid.uuid4()), user_id="U_A", text="我的筆在哪裡？")
                publisher.publish(String(data=json.dumps(event, ensure_ascii=False)))
                spin_until(lambda:len(received)>=1)
                assert received[0]["status"]=="ok", received
                publisher.publish(String(data=json.dumps(event, ensure_ascii=False)))
                spin_until(lambda:len(received)>=2, 10)
                assert received[0]["turn_id"]==received[1]["turn_id"]
                event["text"]="不同內容"
                publisher.publish(String(data=json.dumps(event, ensure_ascii=False)))
                spin_until(lambda:len(received)>=3, 10)
                assert received[2]["error"]=="ID_CONFLICT"
                assert rows()==before
                print("PASS: ROS input/result, duplicate replay, ID conflict, DB unchanged", flush=True)
            finally:
                probe.destroy_node()
                rclpy.shutdown()
        finally:
            for proc in processes:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGINT)
            for proc in processes:
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            for log in logs:
                log.seek(0)
                print(log.read())
                log.close()


if __name__ == "__main__":
    main()
