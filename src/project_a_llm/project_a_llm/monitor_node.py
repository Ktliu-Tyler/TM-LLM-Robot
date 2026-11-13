"""WSLg monitor for the workbench state topic."""

import json
import tkinter as tk
from collections import defaultdict
from datetime import datetime

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String


from project_a_llm.inventory import DEFAULT_LOCATIONS


PAPER = "#f2f4ee"
INK = "#182f35"
MUTED = "#536b6c"
TOPIC = "/project_a/workbench/state"


def label(parent, text, size=12, color=INK, bold=False, **options):
    return tk.Label(parent, text=text, font=("DejaVu Sans", size, "bold" if bold else "normal"),
                    fg=color, bg=parent["bg"], anchor="w", **options)


class Monitor(Node):
    def __init__(self, root):
        super().__init__("workbench_monitor")
        self.root = root
        root.title("工作台監看")
        root.geometry("920x680")
        root.minsize(640, 420)
        root.configure(bg=PAPER)
        header = tk.Frame(root, bg=INK, padx=30, pady=20)
        header.pack(fill="x")
        label(header, "工作台監看", 23, "white", True).pack(side="left")
        self.status = label(header, "等待工作台狀態", 11, "#c6dfce")
        self.status.pack(side="right")
        self.summary = label(root, "等待第一次快照", 12, MUTED)
        self.summary.pack(fill="x", padx=30, pady=12)

        viewport = tk.Frame(root, bg=PAPER)
        viewport.pack(fill="both", expand=True, padx=22, pady=(0, 18))
        canvas = tk.Canvas(viewport, bg=PAPER, highlightthickness=0)
        scroll = tk.Scrollbar(viewport, command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(fill="both", expand=True)
        self.board = tk.Frame(canvas, bg=PAPER)
        window = canvas.create_window(0, 0, window=self.board, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        self.board.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        for col in (0, 1):
            self.board.columnconfigure(col, weight=1, uniform="zone")
        self.received = False
        self.sub = self.create_subscription(String, TOPIC, self.receive, QoSProfile(
            depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_timer(1, self.check_source)

    def check_source(self):
        if not self.count_publishers(TOPIC):
            self.status.config(text="來源離線 · 顯示最後快照" if self.received else "等待狀態節點")

    def receive(self, msg):
        try:
            data = json.loads(msg.data)
            users = {u["user_id"]: u["name"] for u in data["users"]}
            zone_slots = defaultdict(list)
            known = set()
            locations = data.get("locations")
            if locations is None:
                locations = [dict(location=loc, zone=zone, capacity=cap)
                             for loc, zone, cap in DEFAULT_LOCATIONS]
            for slot in locations:
                zone_slots[slot["zone"]].append(slot)
                known.add(slot["location"])
            contents = defaultdict(list)
            for obj in data["objects"]:
                loc = obj["location"]
                contents[loc].append((obj["name"], obj["object_id"], users.get(obj["owner"], obj["owner"])))
            for loc in contents.keys() - known:
                zone_slots["未登錄位置"].append({"location": loc, "capacity": None})
        except (KeyError, TypeError, ValueError):
            self.status.config(text="快照格式錯誤")
            return
        for child in self.board.winfo_children():
            child.destroy()
        order = ["A", "B", "工作檯桌面", "交付區"]
        zones = [z for z in order if z in zone_slots] + sorted(zone_slots.keys() - set(order))
        for index, zone in enumerate(zones):
            panel = tk.Frame(self.board, bg="white", padx=18, pady=14,
                             highlightthickness=1, highlightbackground="#cdd7cb")
            panel.grid(row=index // 2, column=index % 2, sticky="nsew", padx=6, pady=6)
            slots = zone_slots[zone]
            count = sum(len(contents[s["location"]]) for s in slots)
            cap = [s["capacity"] for s in slots]
            total = f"{count} / {sum(cap)}" if all(isinstance(v, int) for v in cap) else f"{count} 件"
            line = tk.Frame(panel, bg="white")
            line.pack(fill="x", pady=(0, 9))
            label(line, zone + " 區" if zone in ("A", "B") else zone, 17, INK, True).pack(side="left")
            label(line, total, 12, MUTED).pack(side="right")
            for slot in slots:
                loc = slot["location"]
                items = contents[loc]
                row = tk.Frame(panel, bg="#e8f2ec" if items else "#f3f4f0", padx=12, pady=8)
                row.pack(fill="x", pady=3)
                if loc != zone:
                    label(row, loc, 10, MUTED, True).pack(fill="x")
                for name, oid, owner in items:
                    label(row, name, 16, INK, True).pack(fill="x")
                    label(row, f"{oid}  ·  {owner}", 10, MUTED).pack(fill="x")
                if not items:
                    label(row, "空位", 13, MUTED).pack(fill="x")
        self.received = True
        self.status.config(text="已連線")
        self.summary.config(text=f"共 {len(data['objects'])} 件物品    ·    最後接收 {datetime.now():%H:%M:%S}")


def main():
    rclpy.init()
    root = tk.Tk()
    node = Monitor(root)

    def poll():
        if rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0)
            root.after(50, poll)
        else:
            root.destroy()

    root.after(0, poll)
    try:
        root.mainloop()
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


