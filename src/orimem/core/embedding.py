"""Pinned multilingual E5 inference, local files only, normalized 384-d vectors."""

from pathlib import Path
import json, hashlib, os, threading
import numpy as np

from orimem import config as _config
BASE = _config.home()
MODEL = BASE / "models/multilingual-e5-small"


class Embedder:
    def __init__(self, model_path=MODEL, device=None):
        self.path = Path(model_path)
        manifest = json.loads((self.path / "manifest.json").read_text(encoding="utf-8"))
        self.model_id = manifest["repository"] + "@" + manifest["revision"] + ":mean:384:prefix-v1"
        self.device = device
        self._model = None
        self._tokenizer = None
        self.lock = threading.RLock()

    @property
    def tokenizer(self):
        with self.lock:
            if self._tokenizer is None:
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                from transformers import AutoTokenizer

                self._tokenizer = AutoTokenizer.from_pretrained(
                    self.path, local_files_only=True, trust_remote_code=False, use_fast=True
                )
            return self._tokenizer

    def encode(self, texts, query=False):
        import torch

        with self.lock:
            if self._model is None:
                from transformers import AutoModel

                self.device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
                self._model = (
                    AutoModel.from_pretrained(
                        self.path, local_files_only=True, trust_remote_code=False
                    )
                    .to(self.device)
                    .eval()
                )
                if self.device == "cpu":
                    torch.set_num_threads(4)
            vectors = []
            for start in range(0, len(texts), 16):
                inputs = self.tokenizer(
                    [("query: " if query else "passage: ") + x for x in texts[start : start + 16]],
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                ).to(self.device)
                with torch.inference_mode():
                    hidden = self._model(**inputs).last_hidden_state
                    mask = inputs["attention_mask"].unsqueeze(-1)
                    pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
                    pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
                vectors.append(pooled.cpu().numpy().astype("<f4"))
            return np.vstack(vectors) if vectors else np.empty((0, 384), dtype="<f4")
