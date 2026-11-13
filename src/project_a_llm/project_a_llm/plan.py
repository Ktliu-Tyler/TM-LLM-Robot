"""Plan the first object lookup for a Project A utterance using local Ollama."""

import argparse
import json
import os
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


def plan(text, user_id, target_zone, model):
    """Return a proposed lookup or a request for clarification."""
    if not text.strip() or not user_id.strip():
        raise ValueError("text and user_id must be nonempty")
    if user_id == "UNKNOWN":
        return {"clarification": "請先確認使用者身分。"}
    payload = {
        "model": model, "stream": False, "think": False,
        "options": {"temperature": 0},
        "format": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        "messages": [
            {"role": "system", "content": "你是工作台物品查詢規劃器。從句子擷取要查找或拿取的物品名稱，去掉所有格，例如「把我的筆拿給我」回傳 {\u0022name\u0022:\u0022筆\u0022}。若沒有明確物品或與查找、拿取無關，name 填空字串。"},
            {"role": "user", "content": json.dumps(
                {"text": text, "target_zone": target_zone}, ensure_ascii=False)},
        ],
    }
    request = Request("http://127.0.0.1:11434/api/chat",
                      data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=120) as response:
        result = json.loads(json.load(response)["message"]["content"])
    name = result["name"]
    if not isinstance(name, str):
        raise ValueError("invalid object name")
    if not name.strip():
        return {"clarification": "請說明想查找或拿取哪個物品。"}
    return {"api": "query_object", "args": {"user_id": user_id, "name": name.strip()}}


def main():
    """Run one planning turn from the shell."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--target-zone", default="UNKNOWN")
    parser.add_argument("--model", default=os.getenv("PROJECT_A_MODEL", "qwen3:4b"))
    args = parser.parse_args()
    try:
        result = plan(args.text, args.user_id, args.target_zone, args.model)
    except (URLError, OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Local planning failed ({args.model}): {error}")
    print(json.dumps(result, ensure_ascii=False))
