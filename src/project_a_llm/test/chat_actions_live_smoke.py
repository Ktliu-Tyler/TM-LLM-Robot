"""Real model/ROS movement tests on disposable DBs; isolated domain, no robot."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

import rclpy
from std_msgs.msg import String


def main():
    root = Path(__file__).resolve().parents[3]
    for simulation in (True, False):
        with tempfile.TemporaryDirectory(prefix="jarvis-actions-") as directory:
            dbpath = Path(directory)/"workbench.sqlite3"
            with sqlite3.connect(f"file:{root/'data/workbench.sqlite3'}?mode=ro", uri=True) as source:
                with sqlite3.connect(dbpath) as db:
                    source.backup(db)
            def position(item):
                with sqlite3.connect(dbpath) as db:
                    return db.execute("SELECT location,home_location FROM objects WHERE object_id=?", (item,)).fetchone()
            env = dict(os.environ, ROS_DOMAIN_ID="86", PROJECT_A_DB=str(dbpath))
            processes, logs, commands = [], [], []
            rclpy.init(domain_id=86)
            probe = rclpy.create_node("jarvis_actions_probe")
            probe.create_subscription(String, "/project_a/arm/command", lambda m: commands.append(json.loads(m.data)), 20)
            try:
                for name, args in [("state", ["--ros-args", "-p", "simulate_motion:="+str(simulation).lower()]),
                                   ("chat", ["--ros-args", "-p", "port:=18768"])]:
                    log = open(Path(directory)/(name+".log"), "w+")
                    logs.append(log)
                    processes.append(subprocess.Popen(["ros2", "run", "project_a_llm", name, *args],
                        env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
                def api(path, data=None):
                    req = Request("http://127.0.0.1:18768"+path, data=None if data is None else json.dumps(data).encode(),
                                  headers={"Content-Type":"application/json"})
                    with urlopen(req, timeout=5) as response:
                        return json.load(response)
                for _ in range(100):
                    try:
                        sid = api("/api/session", {"user_id":"U_A"})["session_id"]
                        break
                    except OSError:
                        time.sleep(.3)
                else:
                    raise TimeoutError("chat startup")
                for _ in range(100):
                    rclpy.spin_once(probe, timeout_sec=.1)
                    if api("/api/state?session_id="+sid)["service_ready"]:
                        break
                def turn(text, action):
                    tid = api("/api/chat", {"session_id":sid,"text":text})["turn_id"]
                    for _ in range(1600):
                        rclpy.spin_once(probe, timeout_sec=.1)
                        state = api("/api/state?session_id="+sid)
                        if not state["busy"]:
                            break
                    else:
                        raise TimeoutError(text)
                    events = [e for e in state["events"] if e.get("turn_id")==tid]
                    if action is None:
                        assert not any(e["kind"] == "action_result" for e in events), events
                        assert not state["turns"][-1]["error"]
                        print("PASS hypothetical request does not execute", flush=True)
                        return
                    expected = (action,) if isinstance(action, str) else action
                    receipts = [e for e in events if e["kind"]=="action_result" and e["action"] in expected]
                    print(json.dumps({"simulation":simulation,"question":text,"turn":state["turns"][-1],"receipts":receipts},ensure_ascii=False),flush=True)
                    assert not state["turns"][-1]["error"]
                    assert receipts, events
                    return receipts[-1]["result"]
                first = turn("幫我拿我的筆到工作檯桌面。", "place")
                if simulation:
                    assert first["execution"]=="simulated"
                    assert position("P01")[0]=="工作檯桌面"
                    turn("如果我說清理桌面，你會做什麼？先不要執行。", None)
                    assert position("P01")[0]=="工作檯桌面"
                    turn("幫我拿我的橡皮擦。", ("pick", "place"))
                    assert position("E01")[0]=="工作檯桌面"
                    turn("清理桌面。", "tidy")
                    assert position("P01")[0]=="A1" and position("E01")[0]=="A2"
                    turn("把我的筆的預設位置改為 B2。", "set_home")
                    assert position("P01")[1]=="B2"
                    turn("把我的筆放到交付區。", "place")
                    assert position("P01")[0]=="交付區"
                    turn("把我的筆歸位。", "restore")
                    assert position("P01")[0]=="B2"
                    turn("將我的筆的位置登記為工作檯桌面，這是更新資料紀錄，不是搬運。", "move")
                    assert position("P01")[0]=="工作檯桌面"
                    assert not commands, "simulation sent physical command"
                else:
                    assert first["execution"]=="dispatched" and first["physical_completion"] is False
                    for _ in range(20):
                        rclpy.spin_once(probe, timeout_sec=.1)
                    assert len(commands)==1, commands
                    assert commands[0]["api"]=="place_object"
                    assert commands[0]["args"]["object_id"]=="P01"
                    assert position("P01")[0]=="A1", "dispatch prematurely updated DB"
                print("PASS actions", "simulation" if simulation else "dispatch contract", flush=True)
            finally:
                for proc in processes:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGINT)
                for proc in processes:
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                probe.destroy_node()
                rclpy.shutdown()
                for log in logs:
                    log.seek(0)
                    print(log.read())
                    log.close()


if __name__ == "__main__":
    main()
