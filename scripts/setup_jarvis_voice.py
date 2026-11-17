#!/usr/bin/env python3
"""Install the official offline wake-word runtime/model in the ASR venv."""
import os
from pathlib import Path
import subprocess
import tarfile
from urllib.request import urlretrieve

NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
python = os.getenv("PROJECT_A_ASR_PYTHON", "/home/ktliu/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/.venv/bin/python")
subprocess.run([python, "-m", "pip", "install", "sherpa-onnx==2.0.1", "sentencepiece==0.2.3"], check=True)
root = Path.home()/".cache/project_a/kws"
root.mkdir(parents=True, exist_ok=True)
archive = root/(NAME+".tar.bz2")
if not (root/NAME/"tokens.txt").exists():
    urlretrieve("https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"+NAME+".tar.bz2", archive)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (root/member.name).resolve()
            if not target.is_relative_to(root.resolve()) or member.issym() or member.islnk():
                raise ValueError("Unsafe model archive member")
        tar.extractall(root)
    archive.unlink()
print("Jarvis wake model ready:", root/NAME)
