"""ROS 2 topic adapter for the local workbench database agent."""

import json
import os
from uuid import NAMESPACE_URL, uuid5
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from threading import BoundedSemaphore

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from workbench_interfaces.srv import Inventory
from project_a_llm.inventory import interpret, ollama_json


def spoken_query(model, question, command, data):
    """Give Qwen grounded spoken alternatives derived only from returned records."""
    if command["action"] == "users":
        names = "、".join(u["name"] + "（" + u["user_id"] + "）" for u in data["users"])
        choices = ["查到的使用者是" + names + "。"] if names else ["查無符合的使用者。"]
    else:
        items = data["objects"]
        scope = data.get("location", "")
        if scope in ("A", "B"):
            scope = "工具箱" + scope
        if items:
            contents = "，".join(o["name"] + "在" + o["location"] for o in items)
            prefix = scope + "裡" if scope else "工作台上"
            choices = [contents + "。", prefix + "登記的物品如下，" + contents + "。"]
        elif scope and not (command["item"] or command["person"]):
            choices = [scope + "目前是空的。", scope + "目前沒有放置物品。"]
        else:
            choices = ["查無符合條件的物品。"]
    if len(choices) == 1:
        return choices[0]
    try:
        response = ollama_json(dict(model=model, stream=False, think=False,
            options={"temperature": 0}, format={"type":"object","properties":{
                "message":{"type":"string","enum":choices}}, "required":["message"]},
            messages=[{"role":"system","content":"依問題與資料庫結果，選擇最自然且直接的一句繁體中文播報。"},
                      {"role":"user","content":json.dumps({"question":question,"result":data,
                                                         "phrases":choices}, ensure_ascii=False)}]))
        if response["message"] in choices:
            return response["message"]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return choices[0]


class WorkbenchAgent(Node):
    def __init__(self):
        super().__init__("workbench_agent")
        self.model = self.declare_parameter(
            "model", os.getenv("PROJECT_A_MODEL", "qwen3:4b")).value
        self.inventory = self.create_client(Inventory, "/project_a/workbench/inventory")
        self.arm = self.create_publisher(String, "/project_a/arm/command", 10)
        self.speech = self.create_publisher(String, "/project_a/speech/command", 10)
        self.results = self.create_publisher(String, "/project_a/agent/result", 10)
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.capacity = BoundedSemaphore(16)
        self.recent = OrderedDict()
        self.create_subscription(String, "/project_a/agent/input", self.receive, 10)
        self.get_logger().info(f"model={self.model}")

    def reply(self, event_id, **fields):
        if fields.get("status") == "error":
            fields["message"] = "這次指令未能完成，" + str(fields["error"]).rstrip("。") + "。"
            if event_id:
                self.say(event_id, fields["message"])
        data = {"utterance_id": event_id, **fields}
        self.results.publish(String(data=json.dumps(data, ensure_ascii=False)))

    def receive(self, msg):
        event_id = ""
        try:
            event = json.loads(msg.data)
            if not isinstance(event, dict):
                raise ValueError("輸入必須是 JSON 物件")
            for key in ("utterance_id", "text"):
                if not isinstance(event.get(key), str) or not event[key].strip():
                    raise ValueError(f"缺少有效的 {key}")
                if key == "utterance_id":
                    event_id = event[key]
            for key in ("user_id", "target_zone"):
                if not isinstance(event.get(key, ""), str):
                    raise ValueError(f"{key} 必須是字串")
        except ValueError as error:
            self.reply(event_id, status="error", error=str(error))
            return
        if not self.capacity.acquire(blocking=False):
            self.reply(event_id, status="error", error="QUEUE_FULL")
            return
        self.worker.submit(self.process, event_id, event)

    def say(self, event_id, text):
        self.speech.publish(String(data=json.dumps(
            {"utterance_id": event_id, "text": text}, ensure_ascii=False)))

    def dispatch(self, event_id, event, command):
        pick = command["action"] == "pick"
        if pick and not command["item"].strip():
            return dict(status="clarify", message="請指定要拿取的物品。")
        req = Inventory.Request()
        for key in ("action", "item", "location", "person"):
            setattr(req, key, command[key])
        req.text = event["text"]
        req.user_id = event.get("user_id", "")
        if not self.inventory.wait_for_service(timeout_sec=2):
            raise ValueError("STATE_UNAVAILABLE")
        done = Event()
        future = self.inventory.call_async(req)
        future.add_done_callback(lambda _: done.set())
        if not done.wait(10):
            self.inventory.remove_pending_request(future)
            raise ValueError("STATE_TIMEOUT")
        response = future.result()
        if not response.ok:
            raise ValueError(response.error)
        data = json.loads(response.result_json)
        if "clarification" in data:
            return dict(status="clarify", message=data["clarification"])
        if req.action in ("restore", "tidy", "place", "pick"):
            if data.get("simulated"):
                message = "模擬搬運完成。" if data["tasks"] else "物品已在指定位置。"
                return dict(status="ok", tool=req.action, result=data, message=message)
            for args in data["tasks"]:
                task_id = str(uuid5(NAMESPACE_URL, f"project_a/{req.action}/{event_id}/{args['object_id']}"))
                task = dict(utterance_id=event_id, task_id=task_id, user_id=req.user_id,
                            api="place_object" if req.action in ("pick", "place") and args["target_location"] in ("工作檯桌面", "交付區") else "return_object", args=args)
                self.arm.publish(String(data=json.dumps(task, ensure_ascii=False)))
            if req.action in ("place", "pick"):
                message = "已發出放置指令。" if data["tasks"] else "物品已在指定位置。"
            else:
                message = "已發出歸位指令。" if data["tasks"] else "物品皆已歸位。"
        elif req.action in ("objects", "users", "status"):
            message = spoken_query(self.model, event["text"], command, data)
        elif req.action == "set_home":
            message = f"已將{req.item}的預設收納位置設為{data['home_location']}。"
        else:
            message = f"已將{req.item}的位置登記為{data['location']}。"
        return dict(status="ok", tool=req.action, result=data, message=message)

    def process(self, event_id, event):
        try:
            if event_id in self.recent:
                original, result = self.recent[event_id]
                if original != event:
                    raise ValueError("ID_CONFLICT")
            else:
                text = event["text"]
                command = interpret(text, self.model, allow_pick=True)
                if command["action"] == "clarify":
                    result = dict(status="clarify", message="請指定想查詢的資料或要登記的物品與位置。")
                else:
                    actor = event.get("user_id")
                    if command.get("person") == "@me" and actor in (None, "", "UNKNOWN"):
                        result = dict(status="clarify", message="請先確認使用者身分。")
                    else:
                        result = self.dispatch(event_id, event, command)
                self.say(event_id, result["message"])
                self.recent[event_id] = (event, result)
                if len(self.recent) > 512:
                    self.recent.popitem(last=False)
            self.reply(event_id, **result)
        except (OSError, RuntimeError, ValueError, KeyError, AttributeError, TypeError) as error:
            self.reply(event_id, status="error", error=str(error))
        finally:
            self.capacity.release()


def main():
    rclpy.init()
    node = WorkbenchAgent()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.worker.shutdown(wait=True, cancel_futures=True)
        node.destroy_node()
        rclpy.try_shutdown()







