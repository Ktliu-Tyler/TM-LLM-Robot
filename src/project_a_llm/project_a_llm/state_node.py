"""Serve and publish the registered workbench state."""

import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String
from workbench_interfaces.srv import Inventory

from project_a_llm.inventory import execute, initialize, rows


class StateNode(Node):
    def __init__(self):
        super().__init__("workbench_state")
        path = Path(self.declare_parameter("db_path", os.getenv(
            "PROJECT_A_DB", "/mnt/d/Robotics/project_a_ws/data/workbench.sqlite3")).value)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        initialize(self.db)
        self.simulate_motion = self.declare_parameter("simulate_motion", False).value
        self.publisher = self.create_publisher(
            String, "/project_a/workbench/state",
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_service(Inventory, "/project_a/workbench/inventory", self.handle_request)
        self.version = None
        self.refresh()
        self.create_timer(1.0, self.refresh)

    def refresh(self, force=False):
        version = self.db.execute("PRAGMA data_version").fetchone()[0]
        if force or version != self.version:
            with self.db:
                self.db.execute("BEGIN")
                snapshot = {table: rows(self.db.execute(f"SELECT * FROM {table} ORDER BY 1"))
                            for table in ("objects", "users", "locations")}
            self.publisher.publish(String(data=json.dumps(snapshot, ensure_ascii=False)))
            self.version = version

    def handle_request(self, req, res):
        try:
            command = {key: getattr(req, key) for key in ("action", "item", "location", "person")}
            with self.db:
                data = execute(self.db, command, req.text, req.user_id)
                if self.simulate_motion and "tasks" in data:
                    for task in data["tasks"]:
                        changed = self.db.execute(
                            "UPDATE objects SET location=? WHERE object_id=? AND location=?",
                            (task["target_location"], task["object_id"], task["source_location"]))
                        if changed.rowcount != 1:
                            raise ValueError("物品來源位置已變更，請重試。")
                    data["simulated"] = True
            if req.action in ("move", "set_home") or (data.get("simulated") and data["tasks"]):
                self.refresh(force=True)
            res.result_json = json.dumps(data, ensure_ascii=False)
            res.ok = True
        except (ValueError, sqlite3.Error) as error:
            res.error = str(error)
        return res


def main():
    rclpy.init()
    node = StateNode()
    try:
        with closing(node.db):
            rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()




