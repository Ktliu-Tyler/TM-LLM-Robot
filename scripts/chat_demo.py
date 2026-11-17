#!/usr/bin/env python3
"""Launch a local chat demo against a disposable workbench database copy."""
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    port = int(os.getenv("PROJECT_A_CHAT_PORT", "8767"))
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
    env = dict(os.environ, ROS_DOMAIN_ID=os.getenv("PROJECT_A_CHAT_DOMAIN", "88"))
    with tempfile.TemporaryDirectory(prefix="project-a-chat-demo-") as directory:
        path = Path(directory) / "workbench.sqlite3"
        with sqlite3.connect(f"file:{root / 'data/workbench.sqlite3'}?mode=ro", uri=True) as source:
            with sqlite3.connect(path) as dest:
                source.backup(dest)
        env["PROJECT_A_DB"] = str(path)
        processes = []
        print(f"http://localhost:{port} | ROS_DOMAIN_ID={env['ROS_DOMAIN_ID']} | DB copy={path}", flush=True)
        print("Ctrl+C stops both demo nodes and removes the disposable database.", flush=True)
        try:
            for name, extra in [("state", []), ("chat", ["--ros-args", "-p", f"port:={port}"])]:
                processes.append(subprocess.Popen(["ros2", "run", "project_a_llm", name, *extra],
                                                   env=env, start_new_session=True))
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
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()


if __name__ == "__main__":
    main()
