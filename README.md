# TM-LLM-Robot

以 ROS 2 Humble、Qwen 與 SQLite 實作的智慧工作台助理。Windows 麥克風經 WSLg 與 Qwen3-ASR 轉為文字，Agent 處理物品查詢、取物與歸位。管理頁提供節點控制、語音測試與工作台快照。

在已安裝 ROS 2 Humble 與 colcon 的 Ubuntu-D 中，從本儲存庫根目錄執行：

```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
export PROJECT_A_DB="$PWD/data/workbench.sqlite3"
ros2 run project_a_llm inventory --init-demo
ros2 run project_a_llm admin
```

示範資料只需初始化一次。啟動後開啟 [localhost 管理頁](http://localhost:8765)。語言模型查詢另需本機 Ollama 與 qwen3:4b；麥克風測試另需 Qwen3-ASR 執行環境。手冊中的絕對路徑為原開發機配置，請依安裝位置設定 PROJECT_A_DB 與 PROJECT_A_ASR_PYTHON。

- [架構圖、語音測試與 ROS 介面](src/project_a_llm/ROS_AGENT.md)
- [Qwen JSON 格式與草稿](src/project_a_llm/QWEN_OUTPUT_FORMAT.md)
- [連續對話、記憶與 Qwen Harness 架構提案](docs/CONVERSATIONAL_HARNESS.md)（設計稿，尚未實作）
