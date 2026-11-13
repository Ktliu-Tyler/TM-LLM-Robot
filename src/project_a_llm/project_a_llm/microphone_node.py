"""Record a short utterance via WSLg and publish its Qwen3-ASR transcript."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def main():
    # colcon creates its launcher with the system Python. ASR lives in a venv.
    python = os.environ.get("PROJECT_A_ASR_PYTHON")
    if python and os.path.abspath(sys.executable) != os.path.abspath(python):
        os.execv(python, [python, *sys.argv])

    import numpy as np
    import rclpy
    import torch
    from qwen_asr import Qwen3ASRModel
    from std_msgs.msg import String

    rclpy.init()
    node = rclpy.create_node("workbench_microphone")
    try:
        seconds = node.declare_parameter("seconds", 5).value
        source = node.declare_parameter("source", "RDPSource").value
        user = node.declare_parameter("user_id", "").value
        wav = node.declare_parameter("wav", "").value
        if not 1 <= seconds <= 30:
            raise ValueError("seconds 須介於 1 到 30 秒")
        if wav and not Path(wav).is_file():
            raise ValueError(f"找不到音檔 {wav}")
        publisher = node.create_publisher(String, "/project_a/agent/input", 10)
        gpu = torch.cuda.is_available()
        model = Qwen3ASRModel.from_pretrained(
            "Qwen/Qwen3-ASR-1.7B", device_map="cuda:0" if gpu else "cpu",
            dtype=torch.bfloat16 if gpu else torch.float32, max_new_tokens=128)
        web = node.declare_parameter("web_control", False).value

        def status(phase, text=""):
            if web:
                print("MIC_STATUS " + json.dumps([phase, text]), flush=True)

        while rclpy.ok():
            status("ready")
            if wav:
                audio = wav
            else:
                if web:
                    user = json.loads(input())["user_id"]
                else:
                    input(f"按 Enter 錄音 {seconds} 秒，Ctrl+C 結束：")
                status("recording")
                print("開始說話…", flush=True)
                with subprocess.Popen(
                    ["parec", "--raw", "--format=s16le", "--channels=1",
                     "--rate=16000", "--device", source], stdout=subprocess.PIPE,
                ) as recorder:
                    try:
                        raw = recorder.stdout.read(32000 * seconds)
                    finally:
                        recorder.terminate()
                if len(raw) != 32000 * seconds:
                    raise RuntimeError("錄音中斷，請用 pactl list short sources 檢查輸入")
                samples = np.frombuffer(raw, dtype="<i2")
                if not samples.any():
                    status("empty", "麥克風沒有訊號")
                    print("麥克風沒有訊號，請檢查 Windows 輸入裝置。")
                    continue
                audio = (samples.astype(np.float32) / 32768, 16000)
            status("recognizing")
            transcript = model.transcribe(audio=audio, language="Chinese")[0].text.strip()
            if transcript:
                event = {"utterance_id": str(uuid4()), "text": transcript}
                if user:
                    event["user_id"] = user
                publisher.publish(String(data=json.dumps(event, ensure_ascii=False)))
                status("done", transcript)
                node.get_logger().info(f"辨識結果：{transcript}")
                if wav:
                    from rclpy.duration import Duration
                    publisher.wait_for_all_acked(Duration(seconds=2))
            else:
                status("empty", "未辨識到文字")
                node.get_logger().info("未辨識到文字")
            if wav:
                break
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
