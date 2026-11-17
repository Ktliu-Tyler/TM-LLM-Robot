#!/usr/bin/env python3
"""Launch a local chat demo against a disposable workbench database copy."""
import os
import errno
import json
import time
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import tempfile
from urllib.request import ProxyHandler, Request, build_opener


def is_chat_monitor(port):
    """Recognize the existing local UI without creating or altering a session."""
    try:
        with build_opener(ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/", timeout=2) as response:
            return "工作台控制室" in response.read(32768).decode("utf-8")
    except (OSError, UnicodeError):
        return False


def main():
    root = Path(__file__).resolve().parents[1]
    port = int(os.getenv("PROJECT_A_ADMIN_PORT", "8765"))
    chat_port = int(os.getenv("PROJECT_A_CHAT_PORT", "8767"))
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            if is_chat_monitor(port):
                print(f"工作台控制室已在運行，請直接開啟 http://localhost:{port}", flush=True)
                print("本次沒有啟動新節點，也沒有變更原會話、模型或資料庫。")
                print("需要重新啟動時，請先在原啟動終端按 Ctrl+C，再執行本指令。")
                return
            raise SystemExit(
                f"連接埠 {port} 已被其他服務占用，或既有服務尚未就緒。\n"
                "未停止任何程序；請確認占用服務，或使用不同的埠與 ROS domain：\n"
                "PROJECT_A_ADMIN_PORT=8775 PROJECT_A_CHAT_PORT=8777 PROJECT_A_CHAT_DOMAIN=89 python3 scripts/chat_demo.py")
    env = dict(os.environ, ROS_DOMAIN_ID=os.getenv("PROJECT_A_CHAT_DOMAIN", "88"))
    with tempfile.TemporaryDirectory(prefix="project-a-chat-demo-") as directory:
        path = Path(directory) / "workbench.sqlite3"
        with sqlite3.connect(f"file:{root / 'data/workbench.sqlite3'}?mode=ro", uri=True) as source:
            with sqlite3.connect(path) as dest:
                source.backup(dest)
        env["PROJECT_A_DB"] = str(path)
        processes = []
        print(f"http://localhost:{port} | ROS_DOMAIN_ID={env['ROS_DOMAIN_ID']} | DB copy={path}", flush=True)
        print("Ctrl+C stops the control room and its managed nodes, then removes the disposable database.", flush=True)
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", chat_port))
                except OSError as error:
                    raise SystemExit(f"Jarvis 埠 {chat_port} 已被占用；請先停止舊獨立示範，再啟動控制室。") from error
            processes.append(subprocess.Popen(["ros2", "run", "project_a_llm", "admin",
                "--ros-args", "-p", f"port:={port}", "-p", f"chat_port:={chat_port}"],
                env=env, start_new_session=True))
            opener = build_opener(ProxyHandler({}))
            base = f"http://127.0.0.1:{port}"
            for _ in range(120):
                if processes[0].poll() is not None:
                    raise RuntimeError("控制室啟動失敗")
                try:
                    with opener.open(base+"/api/state", timeout=1) as response:
                        json.load(response)
                    break
                except OSError:
                    time.sleep(0.25)
            else:
                raise RuntimeError("控制室啟動逾時")
            for name in ("state", "chat"):
                payload = json.dumps({"node":name,"action":"start","mode":"simulation"}).encode()
                request = Request(base+"/api/control", data=payload,
                    headers={"Content-Type":"application/json","X-Workbench":"1"})
                with opener.open(request, timeout=15) as response:
                    json.load(response)
            print("控制室已啟動；state 與 Jarvis 可在節點管理中啟停／切換。", flush=True)
            while all(proc.poll() is None for proc in processes):
                try:
                    processes[-1].wait(timeout=1)
                except subprocess.TimeoutExpired:
                    continue
            raise RuntimeError("A demo node exited; see output above")
        except KeyboardInterrupt:
            pass
        finally:
            for proc in processes:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGINT)
            for proc in processes:
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()


if __name__ == "__main__":
    main()
