# 即時對話與 LLM 監控測試版

2026-11-17。入口 `ros2 run project_a_llm chat`；預設模型 `qwen3.5:4b`，監控頁 `http://localhost:8767`。

已實作：瀏覽器文字多輪對話、模型串流回覆、原生 tool calling、ROS Inventory 只讀查詢、逐步事件監控，以及可選的 Ollama thinking 顯示。此版先驗證 LLM 與工具架構，尚未串入麥克風、TTS、聲紋、語音打斷、實體手臂或跨重啟長期記憶。

## 一個終端啟動示範

在 Ubuntu-D 執行（Ollama 須已在本機 11434 運行）：

```bash
cd /mnt/d/Robotics/project_a_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select workbench_interfaces project_a_llm
source install/setup.bash
python3 scripts/chat_demo.py
```

Windows 開啟 [對話觀測台](http://localhost:8767)。選「小安 U_A」後按「開始新對話」，可以連續問：

1. 「你好，請叫我阿七。」
2. 「我的筆在哪裡？」
3. 「那它的預設歸位位置呢？」
4. 「工具箱 B 裡面有什麼？」
5. 「整理成簡短的表格給我。」

Enter 送出，Shift+Enter 換行。模型忙碌時等本輪完成再送下一輪。預設關閉思考；勾選後下一輪傳入 `think:true`。右下「模型 thinking 輸出」只顯示 Ollama 實際傳回的文字，不把它當執行證據。

啟動器以 SQLite backup 建立臨時資料庫副本，在 ROS domain 88 啟動 state 與 chat，Ctrl+C 停止兩個程序並清除副本。不同 domain 的既有正式節點不會收到此測試流量。domain 88 必須留給本示範，不要同時啟動另一個 state；若該 domain 已有其他測試，改用未使用的 domain。

```bash
PROJECT_A_CHAT_DOMAIN=89 PROJECT_A_CHAT_PORT=8768 python3 scripts/chat_demo.py
# 換模型：
PROJECT_A_CHAT_MODEL=qwen3:1.7b python3 scripts/chat_demo.py
```

本機 Windows 已有 8766 → WSL IP 的 portproxy，但服務只綁定 loopback，故選未衝突的 8767；沒有修改系統轉發規則或防火牆。

重複執行啟動器時，若 8767 已是本專案的對話頁，會顯示網址並正常結束，不建立新節點、不更改既有模型或會話。直接開啟網址即可；這次指令不會接管原程序，因此要停止服務仍須在原啟動終端按 Ctrl+C。若占用者是其他服務或尚未就緒，顯示明確提示並以非零狀態結束，不強制停止任何程序；要另開示範須同時選不同埠及未使用的 ROS domain。

## 現行架構

```mermaid
flowchart LR
    UI[瀏覽器 chat.html] -->|POST /api/chat| HTTP[chat_node HTTP server]
    HTTP --> SESSION[Session / 近期完整輪次]
    SESSION --> CORE[chat_core 自然對話工具迴圈]
    CORE <-->|串流 /api/chat| MODEL[Ollama qwen3.5:4b]
    CORE -->|白名單及參數檢查| CLIENT[ROS Inventory client]
    CLIENT <-->|Service| STATE[state_node]
    STATE --> DB[(工作台 SQLite 副本)]
    CORE --> EVENTS[模型 / 工具 / 錯誤事件]
    EVENTS -->|GET /api/state 每 350ms| UI
    EVENTS --> TRACE[/project_a/chat/trace]
```

模型自由選擇直接回覆或呼叫 `inventory`；工具回覆以 role=tool 放回 messages，模型可繼續查詢再回答。沒有固定意圖分類、固定答覆模板或每輪確認。程式只控制工具權限、輸入大小、逾時與失控循環。

| 模組 | 責任 |
| --- | --- |
| [chat_core.py](../src/project_a_llm/project_a_llm/chat_core.py) | 系統提示、工具 schema、Ollama NDJSON 串流、工具驗證與往返 |
| [chat_node.py](../src/project_a_llm/project_a_llm/chat_node.py) | ROS service adapter、session、背景 worker、HTTP、trace topic |
| [chat.html](../src/project_a_llm/project_a_llm/chat.html) | 文字對話、thinking 面板、API 時序與服務連線狀態 |
| [chat_demo.py](../scripts/chat_demo.py) | 建立副本、啟動兩節點、退出清理 |

工具只允許 `objects / users / status`，不暴露 SQL、寫入、搬運或 shell。保留既有 Inventory.srv。跨輪解析後的查詢欄位經只讀驗證，再合成 canonical req.text 相容舊服務的文字存在性檢查；這不是允許繞過寫入驗證。`我的` 若模型漏掉 person，程式補上 `@me`，未選身分則回工具錯誤。

這是本機可信使用者的測試介面，手動 user_id 不是聲紋驗證或登入。可查其他人的公開工作台物品，不宣稱實作資料列存取控制。HTTP 僅 loopback、校驗 Host/Origin、不開 CORS。

## 監控內容及記憶

| 事件 | 意義 |
| --- | --- |
| turn_start | 模型、保留的歷史輪數 |
| model_start | 正在等待 Ollama、呼叫次數、messages 數、思考模式 |
| model_metrics | Ollama 回傳的載入／推論時間、token 數、完成原因；duration 單位 ns |
| tool_start | 模型原始工具名稱與參數；進入待處理狀態 |
| tool_validated | 實際送入 service 的正規化參數 |
| tool_end / tool_error | 真實工具結果或錯誤、往返毫秒 |
| turn_end / turn_error | 本輪完成或中止；錯誤時串流文字屬未完成輸出 |

頁面另顯示本輪成功查詢次數，避免把上一輪 tool_end 當成本輪證據。瀏覽器實測 thinking 模式的追問曾直接沿用上一輪結果；尚未增加強制重查的流程限制。

thinking/content 增量即時累積於頁面；ROS trace 發布上述結構化事件，沒有逐 token 洪水。查看：

```bash
source /mnt/d/Robotics/project_a_ws/install/setup.bash
ROS_DOMAIN_ID=88 ros2 topic echo /project_a/chat/trace std_msgs/msg/String --full-length
```

近期記憶保留最多 10 個完整成功輪次；每輪前再將歷史縮到約 10000 個 JSON 字元內，保留完整 tool call/result 組。頁面最多顯示 20 輪、300 個事件；最多 32 個 session，額滿淘汰最早建立的非忙碌 session。這是字元預算而非精準 tokenizer 計數，長對話可能忘記較早內容。thinking 不重播到下一輪上下文；失敗輪次不寫入模型歷史。

所有記憶、對話與事件存在 RAM，節點重啟消失。新 session 隔離歷史；重新整理頁面沿用 sessionStorage 內的 session ID。身分切換必須開新對話。失效會話按「開始新對話」恢復。

模型輸出上限 1024 tokens、context 8192；每輪最多 6 次模型往返／8 次工具呼叫。模型串流 deadline 120 秒，socket 等待上限 30 秒，Inventory discovery 2 秒／回覆等待 10 秒。全域同時只處理一輪，避免小 GPU 同時堆積多個推論；沒有實作取消正在生成的回覆。deadline 是迴圈檢查，阻塞 I/O 仍可能多等一個 socket timeout。

## 直接接既有 state

已經啟動 state 時不用示範啟動器，只在同一 ROS domain 啟動 chat：

```bash
source /mnt/d/Robotics/project_a_ws/install/setup.bash
ros2 run project_a_llm chat --ros-args -p model:=qwen3.5:4b -p port:=8767
```

chat 不訂閱正式 `/project_a/agent/input`、不發布 `/project_a/arm/command`；與舊 agent 的輸入分離。原管理頁仍是 8765。不要在同一 domain 重複啟動 state。

## 驗證結果與限制

2026-11-17 在 Ubuntu-D 完成 colcon 建置、10 項核心 pytest，以及真實 Ollama + ROS + SQLite 副本四輪測試。查詢與代名詞追問都有實際 tool_end；新 session 無前一場歷史；前後 SQLite dump 相同。測試腳本啟動自己的 domain 87 / port 18766，結束清理程序與副本：

```bash
python3 -m pytest src/project_a_llm/test/test_chat_core.py -q
python3 src/project_a_llm/test/chat_live_smoke.py
```

首次冷載入那輪約 17.7 秒，其中載入約 15.8 秒；修正提示後的暖機樣本為 2.03、2.03、4.08、4.05 秒（整輪完成時間，不是首 token）。這是少量功能測試，並非 p95 基準。另以 Windows Chrome 驗證身分選單、新對話、送出查詢、工具結果展開、thinking 顯示；ROS trace 以 std_msgs/msg/String 訂閱確認收到 turn_start。

測試中模型記得「阿七」卻曾把誰要求稱呼誰說反；第一次也曾沿用上一輪工具資料而未重新查詢。已補充短提示並看到追問重新查詢，但不保證所有語句都能遵循。這版不使用額外 LLM 驗證器或硬編碼回覆來掩蓋小模型的語意錯誤；應利用監控逐句核對工具證據。架構正常不代表模型回答必然正確。

原生工具串流與 thinking 欄位參考 [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling)、[Ollama thinking](https://docs.ollama.com/capabilities/thinking)。頁面顯示的是外部本機模型提供的 thinking，不是對模型內部狀態的完整可觀測性。
