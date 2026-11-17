"""Small offline English keyword spotter for the name Jarvis."""
import os
from pathlib import Path

MODEL_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"


class JarvisSpotter:
    def __init__(self):
        import sentencepiece as spm
        import sherpa_onnx
        root = Path(os.getenv("PROJECT_A_KWS_MODEL_DIR", str(Path.home()/".cache/project_a/kws"/MODEL_NAME)))
        if not (root/"bpe.model").is_file():
            raise RuntimeError("缺少 Jarvis 喚醒模型，請先執行 scripts/setup_jarvis_voice.py")
        tokenizer = spm.SentencePieceProcessor(model_file=str(root/"bpe.model"))
        keywords = root/"jarvis_keywords.txt"
        keywords.write_text("\n".join(" ".join(tokenizer.encode(word, out_type=str))+" @Jarvis"
                                      for word in ("JARVIS", "HEY JARVIS"))+"\n", encoding="utf-8")
        suffix = "epoch-12-avg-2-chunk-16-left-64"
        self.engine = sherpa_onnx.KeywordSpotter(
            tokens=str(root/"tokens.txt"), encoder=str(root/f"encoder-{suffix}.int8.onnx"),
            decoder=str(root/f"decoder-{suffix}.onnx"), joiner=str(root/f"joiner-{suffix}.int8.onnx"),
            keywords_file=str(keywords), num_threads=1, keywords_score=1.5,
            keywords_threshold=float(os.getenv("PROJECT_A_WAKE_THRESHOLD", "0.25")))
        self.reset()

    def reset(self):
        self.stream = self.engine.create_stream()

    def accept(self, samples):
        self.stream.accept_waveform(16000, samples)
        detected = False
        while self.engine.is_ready(self.stream):
            self.engine.decode_stream(self.stream)
            if self.engine.get_result(self.stream):
                detected = True
                self.engine.reset_stream(self.stream)
        return detected
