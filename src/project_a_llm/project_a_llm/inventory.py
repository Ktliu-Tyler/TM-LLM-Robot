"""Local workbench inventory commands."""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.request import Request, urlopen

DEFAULT_LOCATIONS = (
        ("A1", "A", 1), ("A2", "A", 1), ("B1", "B", 1), ("B2", "B", 1),
        ("工作檯桌面", "工作檯桌面", None), ("交付區", "交付區", None))

ACTIONS = ["move", "objects", "users", "status", "clarify", "set_home", "restore", "tidy", "place"]
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "item": {"type": "string"},
        "location": {"type": "string"},
        "person": {"type": "string"},
    },
    "required": ["action", "item", "location", "person"],
}


def storage_command(text):
    """Recognize explicit home rules before model interpretation."""
    phrase = re.sub(r"\s+", "", text).rstrip("。！")
    location = r"(A1|A2|B1|B2|工作檯桌面|工作台桌面|桌面|交付區)"
    item = r"(?:我想要|我想)?(?:請)?(?:幫我)?(?:把|將)?(?P<item>[^，。？！?]+?)"
    rule = r"(?:預設|默認|默认)?(?:儲存|收納|歸位|預設)(?:位置|位子|儲位)"
    change = r"(?:更新|更改|設定|改設|改)(?:成|為|到)"
    forms = [item + r"的?" + rule + change + location,
             r"(?:請)?(?:更新|更改|設定)" + item + r"的?" + rule + r"(?:成|為|到)" + location,
             item + r"以後(?:都)?(?:收在|放在|放到)" + location]
    if not re.search(r"不要|不准|禁止|取消|假設|如果|是否|可以嗎|[？?]", phrase):
        for form in forms:
            match = re.fullmatch(form, phrase)
            if match:
                name = match["item"].removeprefix("我的")
                return dict(action="set_home", item=name, location=match[2],
                            person="@me" if "我的" in phrase else "")
        if re.fullmatch(r"(請)?(幫我)?(清理|整理|清空)(工作[台檯])?桌面", phrase):
            return dict(action="tidy", item="", location="", person="")
        match = re.fullmatch(item + r"(?:拿去|拿到|放到|放在|放回|放進|搬到|移到)" + location, phrase)
        if match:
            return dict(action="place", item=match["item"].removeprefix("我的"),
                        location=match[2], person="@me" if "我的" in phrase else "")
        match = re.fullmatch(item + r"(?:歸位|放回原位|放回原處)", phrase)
        if match:
            return dict(action="restore", item=match["item"].removeprefix("我的"),
                        location="", person="@me" if "我的" in phrase else "")
    return None


def pickup_command(text):
    phrase = re.sub(r"\s+", "", text).rstrip("。！")
    prefix = r"(?:我想要|我想|我要)?(?:請)?(?:幫我)?(?:拿取|拿一下|拿)"
    target = r"(?:送|放|拿|移)?(?:去|到|至)(工作[台檯]桌面|桌面|交付區|[AB][12])(?:交付)?"
    match = re.fullmatch(prefix + r"(我的)?([^，。？！?]+?)(?:[，,]?" + target + r")?", phrase)
    if not match:
        return None
    mine, name, location = match.groups()
    if re.search(r"不要|別|然後|或|送到|放到|拿去|[，,]", name):
        return None
    return dict(action="place" if location else "pick", item=name, location=location or "",
                person="@me" if mine else "")


def interpret(text, model, *, allow_pick=False):
    explicit = storage_command(text) or (allow_pick and pickup_command(text))
    if explicit:
        return explicit
    prompt = (
        "你解析單一工作台資料庫指令。明確要求登記或修改位置才用 move。"
        "將物品放到指定位置用 place，工具歸位用 restore，清理桌面用 tidy，修改預設收納規則用 set_home。"
        "想知道某處有什麼、是否為空，以及物品在哪裡，都用 objects。查地點內容時 location 填原句的地點，item 留空；詢問某物在哪時 item 填物品，location 留空。查使用者資料用 users；"
        "查整體資料庫現況用 status；不明、複合、假設或否定指令用 clarify。"
        "item 是物品名稱或 ID；location 是新位置；person 是指定的使用者名稱或 ID，"
        "「我」填 @me。各欄用原句中的確切文字，不含動詞和所有格。不適用填空字串。"
    )
    schema = SCHEMA
    if allow_pick:
        schema = {**SCHEMA, "properties": {**SCHEMA["properties"], "action": {
            "type": "string", "enum": [*ACTIONS, "pick"]}}}
        prompt += ("明確要求機械手臂拿取物品，例如「幫我拿我的筆」，action 必須填 pick，"
                   "item 填筆，person 填 @me，location 留空。單純詢問位置仍用 objects。")
    payload = dict(model=model, stream=False, think=False, format=schema,
                   options={"temperature": 0}, messages=[
                       {"role": "system", "content": prompt},
                       {"role": "user", "content": "把R02的位置更新為收納盒"},
                       {"role": "assistant", "content": json.dumps(dict(
                           action="move", item="R02", location="收納盒", person=""), ensure_ascii=False)},
                       {"role": "user", "content": "我的尺在哪裡"},
                       {"role": "assistant", "content": json.dumps(dict(
                           action="objects", item="尺", location="", person="@me"), ensure_ascii=False)},
                       {"role": "user", "content": "桌上現在擺了哪些工具"},
                       {"role": "assistant", "content": '{"action":"objects","item":"","location":"桌上","person":""}'},
                       {"role": "user", "content": "工具箱B裡面有什麼"},
                       {"role": "assistant", "content": '{"action":"objects","item":"","location":"工具箱B","person":""}'},
                       {"role": "user", "content": "查看小安的使用者資料"},
                       {"role": "assistant", "content": json.dumps(dict(
                           action="users", item="", location="", person="小安"), ensure_ascii=False)},
                       {"role": "user", "content": "顯示資料庫現況"},
                       {"role": "assistant", "content": json.dumps(dict(
                           action="status", item="", location="", person=""))},
                       {"role": "user", "content": text}])
    if allow_pick:
        payload["messages"][-1:-1] = [
            {"role": "user", "content": "請幫我拿我的尺"},
            {"role": "assistant", "content": json.dumps(dict(
                action="pick", item="尺", location="", person="@me"), ensure_ascii=False)},
        ]
    return ollama_json(payload)


def ollama_json(payload):
    request = Request("http://127.0.0.1:11434/api/chat",
                      data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=120) as response:
        return json.loads(json.load(response)["message"]["content"])


def initialize(db):
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS objects (
               object_id TEXT PRIMARY KEY, name TEXT NOT NULL,
               owner TEXT NOT NULL REFERENCES users(user_id), location TEXT NOT NULL)""")


    db.execute("""CREATE TABLE IF NOT EXISTS locations (
               location TEXT PRIMARY KEY, zone TEXT NOT NULL, capacity INTEGER)""")
    db.executemany("INSERT OR IGNORE INTO locations VALUES (?,?,?)", DEFAULT_LOCATIONS)
    if "home_location" not in {r[1] for r in db.execute("PRAGMA table_info(objects)")}:
        db.execute("ALTER TABLE objects ADD COLUMN home_location TEXT REFERENCES locations(location)")
        db.executemany("UPDATE objects SET home_location=? WHERE object_id=?", [
            ("A1", "P01"), ("A2", "E01"), ("B1", "R01")])
    db.commit()


def seed_objects(db):
    db.executemany("INSERT INTO objects (object_id,name,owner,location,home_location) VALUES (?,?,?,?,?)", [
        ("P01", "筆", "U_A", "A1", "A1"), ("E01", "橡皮擦", "U_A", "A2", "A2"),
        ("R01", "尺", "U_B", "B1", "B1")])


def destination(db, requested, obj):
    aliases = {"桌面": "工作檯桌面", "工作台桌面": "工作檯桌面",
               "A區": "A", "B區": "B", "A 區": "A", "B 區": "B"}
    name = aliases.get(requested, requested)
    slots = db.execute("""SELECT l.location FROM locations l
        WHERE (l.location=? OR l.zone=?) AND (l.capacity IS NULL OR
        (SELECT count(*) FROM objects o WHERE o.location=l.location
         AND o.object_id<>?) < l.capacity)
        ORDER BY (l.location=?) DESC, l.location""",
        (name, name, obj["object_id"], obj["location"])).fetchall()
    if not slots:
        raise ValueError("位置不存在或儲位已滿，請選擇其他位置。")
    return slots[0][0]


def rows(cursor):
    return [dict(row) for row in cursor]


def lookup_scope(value):
    key = re.sub(r"\s+", "", value).upper()
    if key in ("桌上", "桌面", "桌面上", "工作台", "工作檯", "工作台上", "工作檯上", "工作台桌面", "工作檯桌面"):
        return "工作檯桌面"
    match = re.fullmatch(r"(?:工具箱|工具盒)?([AB])(?:區|工具箱|工具盒)?", key)
    return match[1] if match else key


def execute(db, command, text, actor):
    action = command["action"]
    item, location, person = (command[key].strip() for key in ("item", "location", "person"))
    if action not in (*ACTIONS, "pick") or action == "clarify":
        raise ValueError("請明確指定一項查詢或位置更新。")
    if action == "objects" and location:
        scope = lookup_scope(location)
        mentions = re.findall(r"[AB][12]|(?:工具箱|工具盒)\s*[AB]|[AB]\s*(?:區|工具箱|工具盒)|工作[檯台](?:桌面|上)?|桌面上?|桌上|交付區", text, re.I)
        if location not in text and scope not in {lookup_scope(word) for word in mentions}:
            raise ValueError("請確認要查詢的位置。")
    if any(value and value not in text for value in (item, location if action != "objects" else "")):
        raise ValueError("無法從原句確認物品或位置。")
    if person == "@me":
        if "我" not in text or not actor:
            raise ValueError("查詢自己的資料時請設定 --user-id。")
        person = actor
    elif person and person not in text:
        raise ValueError("無法確認使用者。")
    users = rows(db.execute(
        "SELECT * FROM users WHERE ?='' OR user_id=? OR name=? ORDER BY user_id",
        (person, person, person)))
    if action == "users":
        return {"users": users}
    if action == "set_home" and storage_command(text) != command:
        raise ValueError("變更規則請明確指定，例如：把筆的預設位置改為 B2。")
    if action in ("move", "set_home") and (not item or not location):
        raise ValueError("更新需要物品名稱或 ID，以及新位置。")
    if action in ("move", "set_home", "restore", "tidy", "place", "pick"):
        db.execute("BEGIN IMMEDIATE")
    objects = rows(db.execute("""
        SELECT o.* FROM objects o JOIN users u ON u.user_id=o.owner
        WHERE (?='' OR o.object_id=? OR o.name=?) AND (?='' OR u.user_id=? OR u.name=?)
        ORDER BY o.object_id""", (item, item, item, person, person, person)))
    if action == "objects" and location:
        slots = {r[0] for r in db.execute(
            "SELECT location FROM locations WHERE location=? OR zone=?", (scope, scope))}
        if not slots:
            raise ValueError("查無這個位置，請再說一次位置名稱。")
        return {"location": scope, "objects": [o for o in objects if o["location"] in slots]}
    if action == "set_home":
        if len(objects) != 1:
            raise ValueError("請指定唯一物品。")
        target = destination(db, location, objects[0])
        if target in ("工作檯桌面", "交付區"):
            raise ValueError("桌面與交付區只供暫放，請指定收納儲位。")
        reserved = db.execute("""SELECT 1 FROM objects JOIN locations AS l ON l.location=home_location
            WHERE home_location=? AND object_id<>? AND capacity=1""",
            (target, objects[0]["object_id"])).fetchone()
        if reserved:
            raise ValueError("此儲位已指定給其他物品。")
        db.execute("UPDATE objects SET home_location=? WHERE object_id=?", (target, objects[0]["object_id"]))
        return dict(object_id=objects[0]["object_id"], home_location=target, location=objects[0]["location"])
    if action in ("restore", "tidy", "place", "pick"):
        if location and action != "place":
            raise ValueError("歸位目的地由已登記的預設位置決定。")
        if action == "pick" and (not item or len(objects) != 1):
            return {"clarification": "請指定唯一的物品名稱或編號。"}
        if action in ("place", "restore", "pick") and (not item or len(objects) != 1):
            raise ValueError("請說明要歸位的物品名稱或編號。")
        tasks = []
        for obj in objects:
            if action == "tidy" and obj["location"] != "工作檯桌面":
                continue
            home = obj["home_location"]
            if action == "pick":
                if obj["location"].strip().upper() in ("", "UNKNOWN", "未知"):
                    return {"clarification": "物品位置未知，請先確認位置。"}
                if re.search(r"交付區|(?:到|去|放在)\s*[AB][12]|(?:送|放|移)(?:到|去)", text):
                    raise ValueError("請明確指定目的地。")
                target = "工作檯桌面"
            elif action == "place":
                target = destination(db, location, obj)
                if target == "交付區" and "交付區" not in text:
                    raise ValueError("請明確指定交付區。")
                if target not in (home, "工作檯桌面", "交付區"):
                    raise ValueError(f"禁止放置：{obj['name']} 的預設位置是 {home}。")
            else:
                if not home:
                    raise ValueError("物品尚未設定預設位置。")
                target = destination(db, home, obj)
            if obj["location"] != target:
                tasks.append(dict(object_id=obj["object_id"], source_location=obj["location"],
                                  target_location=target))
        return {"tasks": tasks}
    if action == "move":
        if len(objects) != 1:
            raise ValueError("找不到唯一物品，請指定正確的物品 ID。")
        obj = objects[0]
        home = obj["home_location"]
        zone = db.execute("SELECT zone FROM locations WHERE location=?", (home,)).fetchone()
        if zone and location.replace(" ", "").removesuffix("區") == zone[0]:
            location = home
        location = destination(db, location, obj)
        if location not in (home, "工作檯桌面", "交付區"):
            raise ValueError(f"禁止放置：{obj['name']} 的預設位置是 {home}，請先明確變更預設位置。")
        db.execute("UPDATE objects SET location=? WHERE object_id=?", (location, obj["object_id"]))
        return {"object_id": obj["object_id"], "previous_location": obj["location"],
                "location": location}
    result = {"objects": objects}
    if action == "status":
        result["users"] = users
        result["locations"] = rows(db.execute("SELECT * FROM locations ORDER BY location"))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="?")
    parser.add_argument("--db", type=Path, default=Path(os.environ.get(
        "PROJECT_A_DB", "/mnt/d/Robotics/project_a_ws/data/workbench.sqlite3")))
    parser.add_argument("--user-id")
    parser.add_argument("--model", default=os.getenv("PROJECT_A_MODEL", "qwen3:4b"))
    parser.add_argument("--init-demo", action="store_true")
    args = parser.parse_args()
    if not args.init_demo and not (args.text or "").strip():
        parser.error("請輸入指令")
    try:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.db) as db:
            db.row_factory = sqlite3.Row
            initialize(db)
            if args.init_demo:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT count(*) FROM users").fetchone()[0]:
                    raise ValueError("資料庫已有使用者，未匯入示範資料。")
                db.executemany("INSERT INTO users VALUES (?,?)", [
                    ("U_A", "小安"), ("U_B", "小林")])
                seed_objects(db)
                db.commit()
            result = execute(db, interpret(args.text, args.model), args.text, args.user_id
                             ) if args.text else {"database": str(args.db)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, AttributeError, TypeError, sqlite3.Error) as error:
        sys.exit(f"資料庫指令失敗：{error}")










