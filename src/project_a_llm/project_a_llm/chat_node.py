"""ROS read-only conversation node with a local live monitoring console."""
import copy
import json
import os
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from workbench_interfaces.srv import Inventory

from project_a_llm.chat_core import Ollama, run_turn
from project_a_llm.chat_voice import VoiceLoop


class ChatNode(Node):
    def __init__(self):
        super().__init__("workbench_chat")
        self.model_name = self.declare_parameter("model", os.getenv("PROJECT_A_CHAT_MODEL", "qwen3.5:4b")).value
        self.port = self.declare_parameter("port", 8767).value
        self.model = Ollama(self.model_name)
        self.inventory = self.create_client(Inventory, "/project_a/workbench/inventory")
        self.trace = self.create_publisher(String, "/project_a/chat/trace", 100)
        self.results = self.create_publisher(String, "/project_a/agent/result", 10)
        self.legacy_speech = self.create_publisher(String, "/project_a/speech/command", 10)
        self.sessions = {}
        self.lock = threading.RLock()
        self.capacity = threading.BoundedSemaphore(1)
        self.voice = VoiceLoop(self)
        self.legacy_sessions = {}
        self.recent_inputs = {}
        self.create_subscription(String, "/project_a/agent/input", self.receive, 10)

    def receive(self, msg):
        # The control room prevents coexistence; also fail closed for manual starts.
        if "workbench_agent" in self.get_node_names():
            self.get_logger().warning("舊版 Agent 正在運行，Jarvis 不重複處理正式輸入")
            return
        event_id = ""
        try:
            event = json.loads(msg.data)
            if not isinstance(event, dict):
                raise ValueError("輸入須為 JSON 物件")
            event_id, text = event.get("utterance_id"), event.get("text")
            actor = event.get("user_id", "")
            if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 128:
                raise ValueError("無效 utterance_id")
            if not isinstance(actor, str) or len(actor) > 64:
                raise ValueError("無效 user_id")
            fingerprint = json.dumps(event, sort_keys=True, ensure_ascii=False)
            if event_id in self.recent_inputs:
                previous, result = self.recent_inputs[event_id]
                if previous != fingerprint:
                    raise ValueError("ID_CONFLICT")
                if result:
                    self.results.publish(String(data=json.dumps(result, ensure_ascii=False)))
                return
            sid = self.legacy_sessions.get(actor)
            if sid not in self.sessions:
                sid = self.new_session(actor)
                self.legacy_sessions[actor] = sid
            self.recent_inputs[event_id] = (fingerprint, None)
            try:
                self.submit(sid, text, False, event_id=event_id, legacy=True)
            except Exception:
                self.recent_inputs.pop(event_id, None)
                raise
        except (ValueError, TypeError, KeyError) as error:
            self.results.publish(String(data=json.dumps(dict(utterance_id=event_id, status="error",
                message=str(error), error=str(error)), ensure_ascii=False)))

    def voice_event(self, sid, phase, **fields):
        if not rclpy.ok():
            return
        with self.lock:
            session = self.sessions.get(sid)
            if session is None:
                return
            session["seq"] += 1
            event = dict(seq=session["seq"], timestamp=time.time(), session_id=sid,
                         kind="voice_" + phase, **fields)
            session["events"].append(event)
            self.trace.publish(String(data=json.dumps(event, ensure_ascii=False)))

    def new_session(self, actor):
        if not isinstance(actor, str) or len(actor) > 64:
            raise ValueError("user_id 格式錯誤")
        with self.lock:
            # Bounded RAM; evict only inactive sessions.
            if len(self.sessions) >= 32:
                oldest = next((k for k, v in self.sessions.items()
                               if not v["busy"] and k != self.voice.sid), None)
                if oldest is None:
                    raise ValueError("會話已滿")
                del self.sessions[oldest]
                self.legacy_sessions = {k: v for k, v in self.legacy_sessions.items() if v != oldest}
            sid = str(uuid.uuid4())
            self.sessions[sid] = dict(actor=actor, groups=[], turns=deque(maxlen=20),
                                      busy=False, phase="idle", events=deque(maxlen=300), seq=0)
            return sid

    def emit(self, sid, tid, kind, **fields):
        with self.lock:
            session = self.sessions[sid]
            turn = session["turns"][-1]
            if kind == "delta":
                turn[fields["field"]] += fields["text"]
            else:
                session["seq"] += 1
                event = dict(seq=session["seq"], timestamp=time.time(), session_id=sid,
                             turn_id=tid, kind=kind, **fields)
                session["events"].append(event)
                if rclpy.ok():
                    self.trace.publish(String(data=json.dumps(event, ensure_ascii=False)))
            phases = {"model_start": "等待模型", "tool_start": "等待 Inventory Service",
                      "tool_end": "工具已回覆", "tool_error": "工具失敗", "model_end": "模型已回覆",
                      "turn_end": "idle", "turn_error": "error"}
            session["phase"] = phases.get(kind, session["phase"])

    def query(self, args, actor):
        req = Inventory.Request()
        for key, value in args.items():
            setattr(req, key, value)
        req.user_id = actor
        # Existing Inventory validates literal presence in req.text. This is a
        # canonical READ-ONLY query after tool validation, not user authorization.
        req.text = "查詢 我 " + " ".join(args.values())
        if not self.inventory.wait_for_service(timeout_sec=2):
            raise RuntimeError("Inventory Service 尚未啟動")
        future = self.inventory.call_async(req)
        ready = threading.Event()
        future.add_done_callback(lambda _: ready.set())
        if not ready.wait(10):
            self.inventory.remove_pending_request(future)
            raise TimeoutError("Inventory Service 等待超過 10 秒")
        response = future.result()
        if not response.ok:
            raise RuntimeError(response.error)
        return json.loads(response.result_json)

    def submit(self, sid, text, think, from_voice=False, event_id=None, legacy=False):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("請輸入 1–2000 字")
        if not isinstance(think, bool):
            raise ValueError("think 必須為布林值")
        if not from_voice and self.voice.status["active"]:
            raise ValueError("語音對話進行中；請先停止語音再輸入文字")
        with self.lock:
            session = self.sessions[sid]
            if not self.capacity.acquire(blocking=False):
                raise ValueError("模型正在處理上一輪，請等待完成")
            tid = str(uuid.uuid4())
            session["busy"] = True
            session["phase"] = "準備中"
            session["turns"].append(dict(turn_id=tid, user=text.strip(), content="", thinking="", error=""))
        threading.Thread(target=self.process, args=(sid, tid, text.strip(), think, from_voice, event_id, legacy), daemon=True).start()
        return tid

    def process(self, sid, tid, text, think, from_voice=False, event_id=None, legacy=False):
        emit = lambda kind, **fields: self.emit(sid, tid, kind, **fields)
        try:
            with self.lock:
                session = self.sessions[sid]
                groups = copy.deepcopy(session["groups"])
                # Keep whole tool exchanges; never orphan tool results.
                while groups and len(json.dumps(groups, ensure_ascii=False)) > 10000:
                    groups.pop(0)
                history = [message for group in groups for message in group]
            emit("turn_start", model=self.model_name, history_turns=len(groups))
            group = run_turn(self.model, history, text, session["actor"], think, self.query, emit, voice=from_voice)
            # Thinking is visible in the monitor but is not replayed as memory.
            for message in group:
                message.pop("thinking", None)
            with self.lock:
                session["groups"] = (groups + [group])[-10:]
                session["turns"][-1]["final"] = group[-1]["content"]
            emit("turn_end")
        except Exception as error:
            with self.lock:
                self.sessions[sid]["turns"][-1]["error"] = str(error)
            emit("turn_error", error=str(error))
        finally:
            with self.lock:
                turn = self.sessions[sid]["turns"][-1]
                result = dict(utterance_id=event_id or tid, session_id=sid, turn_id=tid,
                    status="error" if turn["error"] else "ok", tool="chat",
                    message=turn["error"] or turn.get("final", turn["content"]))
                self.sessions[sid]["busy"] = False
                if event_id in self.recent_inputs:
                    fingerprint, _ = self.recent_inputs[event_id]
                    self.recent_inputs[event_id] = (fingerprint, result)
                    if len(self.recent_inputs) > 512:
                        del self.recent_inputs[next(iter(self.recent_inputs))]
            if rclpy.ok():
                self.results.publish(String(data=json.dumps(result, ensure_ascii=False)))
            if legacy and rclpy.ok():
                self.legacy_speech.publish(String(data=json.dumps(
                    {"utterance_id": event_id, "text": result["message"]}, ensure_ascii=False)))
            self.capacity.release()

    def snapshot(self, sid):
        voice = self.voice.snapshot(sid)
        with self.lock:
            session = self.sessions[sid]
            return copy.deepcopy(dict(session_id=sid, model=self.model_name, actor=session["actor"],
                busy=session["busy"], phase=session["phase"], turns=list(session["turns"]),
                events=list(session["events"]), service_ready=self.inventory.service_is_ready(), voice=voice))


def make_server(node):
    page = Path(__file__).with_name("chat.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, value, content_type="application/json; charset=utf-8"):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            host = self.headers.get("Host", "")
            valid = {f"localhost:{node.port}", f"127.0.0.1:{node.port}"}
            origin = self.headers.get("Origin")
            return host in valid and (not origin or origin in {"http://" + h for h in valid})

        def do_GET(self):
            if not self.allowed():
                return self.respond(403, {"error": "僅允許同源本機請求"})
            url = urlsplit(self.path)
            if url.path == "/":
                return self.respond(200, page, "text/html; charset=utf-8")
            if url.path == "/api/state":
                try:
                    sid = parse_qs(url.query).get("session_id", [""])[0]
                    return self.respond(200, node.snapshot(sid))
                except KeyError:
                    return self.respond(404, {"error": "會話不存在，請開始新對話"})
            self.respond(404, {"error": "Not found"})

        def do_POST(self):
            if not self.allowed():
                return self.respond(403, {"error": "僅允許同源本機請求"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16000:
                    raise ValueError("請求大小錯誤")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("需要 JSON 物件")
                if self.path == "/api/session":
                    return self.respond(200, {"session_id": node.new_session(data.get("user_id", ""))})
                if self.path == "/api/chat":
                    tid = node.submit(data["session_id"], data.get("text"), data.get("think", False))
                    return self.respond(202, {"turn_id": tid})
                if self.path == "/api/voice":
                    sid = data["session_id"]
                    if sid not in node.sessions:
                        raise ValueError("會話不存在")
                    if data.get("action") == "stop":
                        node.voice.stop(sid)
                    elif data.get("action") == "start":
                        if node.sessions[sid]["busy"]:
                            raise ValueError("請等待當前回覆完成")
                        think = data.get("think", False)
                        if not isinstance(think, bool):
                            raise ValueError("think 必須為布林值")
                        node.voice.start(sid, think)
                    else:
                        raise ValueError("未知語音操作")
                    return self.respond(200, {"ok": True})
                self.respond(404, {"error": "Not found"})
            except (ValueError, KeyError) as error:
                self.respond(400, {"error": str(error)})

    return ThreadingHTTPServer(("127.0.0.1", node.port), Handler)


def main():
    rclpy.init()
    node = ChatNode()
    server = make_server(node)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    node.get_logger().info(f"Chat monitor: http://localhost:{node.port} model={node.model_name}")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.voice.stop_event.set()
        if node.voice.thread:
            node.voice.thread.join(timeout=5)
        server.shutdown()
        server.server_close()
        node.destroy_node()
        rclpy.try_shutdown()
