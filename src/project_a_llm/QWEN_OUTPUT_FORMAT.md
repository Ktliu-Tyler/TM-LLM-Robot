# Qwen 輸出 JSON 格式（現況草稿）

本文件記錄 2026-11-11 的實作，供修改與確認。兩個入口都從 Ollama 回應的 `message.content` 讀取字串，再解析成下列 JSON 物件。範例中的物品、位置與人名可替換。

## inventory · 資料庫指令

例如輸入「把 P01 的位置更新為工作檯桌面」，Qwen 預期產生：

```json
{
  "action": "move",
  "item": "P01",
  "location": "工作檯桌面",
  "person": ""
}
```

### 目前傳給 Ollama 的 JSON Schema

```json
{
  "type": "object",
  "properties": {
    "action": {"type": "string", "enum": ["move", "objects", "users", "status", "clarify", "set_home", "restore", "tidy", "place"]},
    "item": {"type": "string"},
    "location": {"type": "string"},
    "person": {"type": "string"}
  },
  "required": ["action", "item", "location", "person"]
}
```

四個欄位都必填。不適用的字串填 `""`。目前 schema 允許額外欄位，套件只讀取上列四欄。

| 輸入範例 | action | item | location | person |
| --- | --- | --- | --- | --- |
| 把 P01 的位置更新為工作檯桌面 | `move` | `P01` | `工作檯桌面` | `""` |
| 我的筆在哪裡？ | `objects` | `筆` | `""` | `@me` |
| 列出所有物品 | `objects` | `""` | `""` | `""` |
| 查詢小林的使用者資料 | `users` | `""` | `""` | `小林` |
| 查看整個資料庫的狀態 | `status` | `""` | `""` | `""` |
| 幫我處理一下 | `clarify` | `""` | `""` | `""` |

- `item` 可填物品 ID 或名稱。`move` 需要唯一符合的物品及非空的新位置。`location` 用於寫入新位置，objects 也可用 location 限定桌面、交付區、單格或整個工具箱。
- `person` 可填使用者 ID 或姓名。空字串表示不限使用者。`@me` 由套件依 CLI 的 `--user-id` 解析，且原句必須包含「我」。
- 非空的物品、位置及一般使用者名稱或 ID 都必須出現在原句中。提示詞要求不明、複合、假設或否定指令使用 `clarify`。

### inventory 的 CLI 回傳

下表是套件執行指令後交給呼叫端的結果。查無紀錄時，對應的陣列為空。

| action | CLI 回傳範例 |
| --- | --- |
| `move` | `{"object_id":"P01","previous_location":"A1","location":"工作檯桌面"}` |
| `objects` | `{"objects":[{"object_id":"P01","name":"筆","owner":"U_A","location":"工作檯桌面","home_location":"A1"}]}` |
| `users` | `{"users":[{"user_id":"U_B","name":"小林"}]}` |
| `status` | `{"objects":[{"object_id":"P01","name":"筆","owner":"U_A","location":"工作檯桌面","home_location":"A1"}],"users":[{"user_id":"U_A","name":"小安"}],"locations":[{"location":"工作檯桌面","zone":"工作檯桌面","capacity":null}]}` |
| `set_home` | `{"object_id":"P01","home_location":"B2","location":"A1"}` |
| `place / restore / tidy` | `{"tasks":[{"object_id":"P01","source_location":"工作檯桌面","target_location":"A1"}]}`。CLI 只回傳規劃清單。 |
| `clarify` | CLI 以非零狀態結束，在標準錯誤輸出文字「資料庫指令失敗：請明確指定一項查詢或位置更新。」 |

## plan · 擷取要查找或拿取的物品

輸入「把我的筆拿給我」時，Qwen 預期輸出：

```json
{"name": "筆"}
```

目前的 JSON Schema：

```json
{
  "type": "object",
  "properties": {"name": {"type": "string"}},
  "required": ["name"]
}
```

物品不明或指令與查找、拿取無關時，提示詞要求輸出 `{"name":""}`。CLI 依結果產生以下回覆：

| 條件 | CLI 回傳範例 |
| --- | --- |
| 成功擷取物品名稱，呼叫者指定 U_A | `{"api":"query_object","args":{"user_id":"U_A","name":"筆"}}` |
| 物品名稱為空或只有空白 | `{"clarification":"請說明想查找或拿取哪個物品。"}` |
| 呼叫者指定 UNKNOWN，套件直接回覆 | `{"clarification":"請先確認使用者身分。"}` |

實作對照：[inventory.py](project_a_llm/inventory.py) · [plan.py](project_a_llm/plan.py)

## 我的修訂草案（待確認）

可在此貼上你希望模型產生的 JSON，並寫下一句對應的輸入範例，以便和現況逐欄比對。

## ROS Agent 的取物擴充

Agent 在上述四欄格式的 action 列舉中另開放 `pick`，例如「幫我拿我的筆」可解析為 `{"action":"pick","item":"筆","location":"","person":"@me"}`。獨立 inventory CLI 仍使用原列舉。Agent 向資料庫查出唯一物品與位置後才組成手臂命令。所有 ROS 訊息格式與架構圖見 [ROS_AGENT.md](ROS_AGENT.md)。

工作台配置更新後，status 成功回覆與快照都額外包含 locations 陣列（location、zone、capacity）。move 只接受已配置的位置或支援的區域別名。

歸位擴充沿用原四欄。set_home 與 place 使用 item、location；restore 使用 item；tidy 清理桌面。明確的歸位與變更預設語句會直接解析，資料庫服務再驗證。具體範例見 ROS_AGENT.md。
「我想要把橡皮擦拿去桌面」會解析成 {"action":"place","item":"橡皮擦","location":"桌面","person":""}，桌面是暫放位置。
pick 未指定目的地時，Service 以工作檯桌面規劃搬運；明確指定交付區時以 place 指定該目的地。兩者均不修改預設儲位。

## Ollama 傳輸模板

Agent 優先解析明確句型。需要模型時送出 POST 到 `http://127.0.0.1:11434/api/chat`。以下是縮短提示詞的有效請求示例；實作另帶 few-shot 對話與完整提示詞。

```json
{
  "model": "qwen3:4b",
  "stream": false,
  "think": false,
  "options": {
    "temperature": 0
  },
  "format": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": [
          "move",
          "objects",
          "users",
          "status",
          "clarify",
          "set_home",
          "restore",
          "tidy",
          "place",
          "pick"
        ]
      },
      "item": {
        "type": "string"
      },
      "location": {
        "type": "string"
      },
      "person": {
        "type": "string"
      }
    },
    "required": [
      "action",
      "item",
      "location",
      "person"
    ]
  },
  "messages": [
    {
      "role": "system",
      "content": "解析工作台指令並輸出指定 JSON。"
    },
    {
      "role": "user",
      "content": "我的筆在哪裡"
    }
  ]
}
```

Ollama 回覆節錄。應先解析 message.content，再驗證與執行工具。

```json
{
  "message": {
    "role": "assistant",
    "content": "{\"action\":\"objects\",\"item\":\"筆\",\"location\":\"\",\"person\":\"@me\"}"
  }
}
```


## 口語查詢

「桌上有甚麼」使用 `{"action":"objects","item":"","location":"桌上","person":""}`。「工具箱A裡有甚麼」填 location="工具箱A"。查「筆在哪裡」填 item="筆" 並讓 location 留空。位置查詢回傳標準化 location 與符合的 objects 陣列。Agent 將查詢結果轉為有資料依據的中文候選句，有多種表達時交由 Qwen 選擇；單一候選或模型無法選句時使用資料庫結果組成的句子。
