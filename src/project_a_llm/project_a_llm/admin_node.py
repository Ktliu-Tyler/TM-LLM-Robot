"""Loopback web console for Project A on WSL."""
import json
import logging
import os
import signal
import subprocess
import threading
import uuid
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

import rclpy
from ament_index_python.packages import get_package_prefix
from rcl_interfaces.msg import Log
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String

NODES = {key: "workbench_" + name for key, name in [
    ("state", "state"), ("agent", "agent"), ("monitor", "monitor"), ("mock_speech", "mock_speech"), ("microphone", "microphone")]}
STATE = "/project_a/workbench/state"


class Console(Node):
    def __init__(self):
        super().__init__("workbench_admin")
        self.port = self.declare_parameter("port", 8765).value
        self.lock = threading.RLock()
        self.children = {}
        self.modes = {}
        self.mic = {"phase": "offline", "text": ""}
        self.snapshot = None
        self.received = None
        self.events = deque(maxlen=40)
        self.directory = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "project_a/logs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logs = {}
        for key in NODES:
            logger = logging.getLogger("workbench." + key)
            handler = RotatingFileHandler(self.directory / (key + ".log"), maxBytes=1048576,
                                          backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            self.logs[key] = logger
        self.test = self.create_publisher(String, "/project_a/test/utterance", 10)
        self.create_subscription(String, STATE, self.update_snapshot, QoSProfile(
            depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_subscription(String, "/project_a/agent/result", self.result, 10)
        self.create_subscription(Log, "/rosout", self.roslog, 100)

    def update_snapshot(self, msg):
        data = json.loads(msg.data)
        with self.lock:
            self.snapshot = data
            self.received = datetime.now().astimezone().isoformat(timespec="seconds")

    def result(self, msg):
        event = json.loads(msg.data)
        with self.lock:
            self.events.appendleft(event)
        self.logs["agent"].info("RESULT %s", msg.data)

    def roslog(self, msg):
        for key, name in NODES.items():
            if msg.name == name:
                self.logs[key].info("ROS %s %s", msg.level, msg.msg)

    def report(self):
        with self.lock:
            names = set(self.get_node_names())
            nodes = {}
            for key, name in NODES.items():
                child = self.children.get(key)
                running = child is not None and child.poll() is None
                nodes[key] = dict(name=name, online=name in names, managed=running,
                                  exit_code=child.poll() if child else None,
                                  mode=self.modes.get(key) if running else None)
            return dict(nodes=nodes, microphone=self.mic.copy(), snapshot=self.snapshot, received=self.received,
                        results=list(self.events), admin=self.get_name(), logs=str(self.directory))

    def capture(self, key, process):
        with process.stdout:
            for line in process.stdout:
                if key == "microphone" and line.startswith("MIC_STATUS "):
                    phase, text = json.loads(line.removeprefix("MIC_STATUS "))
                    with self.lock:
                        if self.children.get(key) is process:
                            self.mic["phase"] = phase
                            if text:
                                self.mic["text"] = text
                self.logs[key].info("PROCESS %s", line.rstrip())
        self.logs[key].info("EXIT %s", process.wait())

    def control(self, key, action, mode):
        if key not in NODES or action not in ("start", "stop"):
            raise ValueError("未知節點操作")
        with self.lock:
            proc = self.children.get(key)
            if action == "stop":
                if proc is None or proc.poll() is not None:
                    raise ValueError("僅能停止由此管理介面啟動的程序")
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                return
            if (proc and proc.poll() is None) or NODES[key] in self.get_node_names():
                raise ValueError("節點已啟動")
            exe = Path(get_package_prefix("project_a_llm")) / "lib/project_a_llm" / key
            args = [str(exe)]
            if key == "state":
                if mode not in ("simulation", "normal"):
                    raise ValueError("請選擇運行模式")
                args += ["--ros-args", "-p", "simulate_motion:=" + str(mode == "simulation").lower()]
                self.modes[key] = mode
            env = dict(os.environ, PYTHONUNBUFFERED="1")
            if key == "microphone":
                env.setdefault("PROJECT_A_ASR_PYTHON",
                               "/home/ktliu/MakeNTU/emotion_robot_controller/voice_stt_remote/"
                               "windows_desktop_server_bundle/.venv/bin/python")
                if not os.access(env["PROJECT_A_ASR_PYTHON"], os.X_OK):
                    raise ValueError("請在啟動 admin 前設定 PROJECT_A_ASR_PYTHON")
                args += ["--ros-args", "-p", "web_control:=true"]
                self.mic = {"phase": "loading", "text": ""}
            proc = subprocess.Popen(args, stdin=subprocess.PIPE if key == "microphone" else None,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace", env=env)
            self.children[key] = proc
            self.logs[key].info("START pid=%s", proc.pid)
            threading.Thread(target=self.capture, args=(key, proc), daemon=True).start()

    def record(self, data):
        user = data.get("user_id", "")
        if not isinstance(user, str):
            raise ValueError("使用者須為字串")
        with self.lock:
            proc = self.children.get("microphone")
            if not proc or proc.poll() is not None or self.mic["phase"] != "ready":
                raise ValueError("請先從管理頁啟動麥克風並等待錄音就緒")
            proc.stdin.write(json.dumps({"user_id": user.strip()}) + "\n")
            proc.stdin.flush()
            self.mic = {"phase": "requested", "text": ""}

    def send(self, data):
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("請輸入發話文字")
        if not self.test.get_subscription_count() or not self.count_subscribers("/project_a/agent/input"):
            raise ValueError("請先啟動測試語音與 Agent 節點")
        event = dict(utterance_id=str(uuid.uuid4()), text=text.strip())
        for key in ("user_id", "target_zone"):
            value = data.get(key, "")
            if not isinstance(value, str):
                raise ValueError("身分與方位須為字串")
            event[key] = value
        self.test.publish(String(data=json.dumps(event, ensure_ascii=False)))
        self.logs["mock_speech"].info("SEND %s", json.dumps(event, ensure_ascii=False))
        return event


def serve(node):
    page = Path(__file__).with_name("console.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, data, kind="application/json; charset=utf-8"):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def route(self, post=False):
            host = self.headers.get("Host", "")
            if host not in {f"localhost:{node.port}", f"127.0.0.1:{node.port}"}:
                return self.respond(403, {"error": "僅提供本機連線"})
            path = urlsplit(self.path).path
            try:
                if post:
                    if (self.headers.get("Origin") not in (None, "http://" + host)
                            or self.headers.get("X-Workbench") != "1"):
                        return self.respond(403, {"error": "請從本機管理介面操作"})
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 16384:
                        raise ValueError("請求大小無效")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("預期 JSON 物件")
                    if path == "/api/control":
                        node.control(data.get("node"), data.get("action"), data.get("mode", "simulation"))
                        return self.respond(200, {"ok": True})
                    if path == "/api/record":
                        node.record(data)
                        return self.respond(200, {"ok": True})
                    if path == "/api/send":
                        return self.respond(200, node.send(data))
                elif path == "/":
                    return self.respond(200, page, "text/html; charset=utf-8")
                elif path == "/api/state":
                    return self.respond(200, node.report())
                elif path.startswith("/logs/"):
                    key = path.removeprefix("/logs/")
                    if key in NODES:
                        return self.respond(200, (node.directory / (key + ".log")).read_bytes(),
                                            "text/plain; charset=utf-8")
                return self.respond(404, {"error": "找不到資源"})
            except (ValueError, OSError, TypeError) as error:
                self.respond(400, {"error": str(error)})

        do_GET = route

        def do_POST(self):
            self.route(post=True)

    return ThreadingHTTPServer(("127.0.0.1", node.port), Handler)


def main():
    rclpy.init()
    node = Console()
    server = serve(node)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    node.get_logger().info(f"http://localhost:{node.port}")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        for key, process in node.children.items():
            if process.poll() is None:
                node.control(key, "stop", None)
        node.destroy_node()
        rclpy.try_shutdown()
