"""Pinned multilingual E5 inference, local files only, normalized 384-d vectors.

Two interchangeable backends for the same pinned revision:

* ONNX (default): ``onnx/model.onnx`` + ``tokenizer.json`` through onnxruntime and
  tokenizers - a ~20 MB dependency footprint, CPU only.
* PyTorch (optional ``orimem[torch]``): ``model.safetensors`` through transformers,
  used when the ONNX files or onnxruntime are missing, or when ORIMEM_EMBEDDER=torch.

Both apply the same "query: "/"passage: " prefixes, 512-token truncation, mean
pooling and L2 normalization, so the model id (and stored vectors) are shared.
"""

from pathlib import Path
import json, os, re, threading
import numpy as np

from orimem import config as _config
BASE = _config.home()
MODEL = BASE / "models/multilingual-e5-small"
BATCH = 16
MAX_TOKENS = 512


def _available(module):
    import importlib.util
    return importlib.util.find_spec(module) is not None


def choose_backend(path: Path) -> str:
    wanted = os.environ.get("ORIMEM_EMBEDDER", "").lower()
    has_onnx = (path / "onnx" / "model.onnx").exists() and _available("onnxruntime") and _available("tokenizers")
    has_torch = (path / "model.safetensors").exists() and _available("torch") and _available("transformers")
    if wanted == "torch" and has_torch:
        return "torch"
    if has_onnx:
        return "onnx"
    if has_torch:
        return "torch"
    raise RuntimeError("Embedding model not installed: run `orimem model download` "
                       "(or, to search without vectors, `orimem config set recall.mode lexical`)")


LEXICAL_MODEL_ID = "lexical-only:fts5:v1"


class _RegexTokenizer:
    """Model-free stand-in for chunking: one token per CJK character or per non-space run."""
    _CJK = "぀-ヿ㐀-䶿一-鿿豈-﫿가-힯"
    PATTERN = re.compile("[" + _CJK + "]|[^\\s" + _CJK + "]+")

    def __call__(self, text, add_special_tokens=False, truncation=False, max_length=None,
                 return_offsets_mapping=False, **_ignored):
        found = list(self.PATTERN.finditer(text))
        if truncation and max_length is not None:
            found = found[:max_length]
        out = {"input_ids": [m.group() for m in found], "attention_mask": [1] * len(found)}
        if return_offsets_mapping:
            out["offset_mapping"] = [(m.start(), m.end()) for m in found]
        return out

    @staticmethod
    def decode(ids, skip_special_tokens=False):
        return " ".join(ids)


class LexicalOnly:
    """Degraded mode (``recall.mode = "lexical"``): no model, no vectors; FTS5 + links only.

    Its model id differs from the vector model's, so switching back to ``hybrid``
    makes the next sync re-chunk and embed everything.
    """
    path = None
    backend = "lexical"

    def __init__(self):
        self.model_id = LEXICAL_MODEL_ID
        self.tokenizer = _RegexTokenizer()

    def encode(self, texts, query=False):
        return np.zeros((len(texts), 0), dtype="<f4")


def recall_mode() -> str:
    return "lexical" if str(_config.get("recall.mode") or "hybrid").lower() == "lexical" else "hybrid"


def make_embedder():
    return LexicalOnly() if recall_mode() == "lexical" else Embedder()


class Embedder:
    def __init__(self, model_path=MODEL, device=None):
        self.path = Path(model_path)
        manifest_path = self.path / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            repository, revision = manifest["repository"], manifest["revision"]
        else:
            # Model not installed yet: the server still starts (settings, capture, lexical search);
            # embedding raises a clear error until `orimem model download` has run. The id is the
            # pinned revision, identical to the one a downloaded manifest yields.
            from orimem.model import REPOSITORY as repository, REVISION as revision
        self.model_id = repository + "@" + revision + ":mean:384:prefix-v1"
        self.device = device
        self.backend = None
        self._model = None
        self._tokenizer = None
        self.lock = threading.RLock()

    # -- shared -------------------------------------------------------------
    def _backend(self):
        if self.backend is None:
            self.backend = choose_backend(self.path)
        return self.backend

    @property
    def tokenizer(self):
        with self.lock:
            if self._tokenizer is None:
                if self._backend() == "onnx":
                    self._tokenizer = _FastTokenizerAdapter(self.path / "tokenizer.json")
                else:
                    os.environ["HF_HUB_OFFLINE"] = "1"
                    os.environ["TRANSFORMERS_OFFLINE"] = "1"
                    from transformers import AutoTokenizer
                    self._tokenizer = AutoTokenizer.from_pretrained(
                        self.path, local_files_only=True, trust_remote_code=False, use_fast=True)
            return self._tokenizer

    def encode(self, texts, query=False):
        prefix = "query: " if query else "passage: "
        texts = [prefix + x for x in texts]
        with self.lock:
            if self._backend() == "onnx":
                return self._encode_onnx(texts)
            return self._encode_torch(texts)

    # -- ONNX -------------------------------------------------------------------
    def _encode_onnx(self, texts):
        import onnxruntime as ort
        if self._model is None:
            options = ort.SessionOptions()
            options.intra_op_num_threads = min(4, os.cpu_count() or 1)
            self._model = ort.InferenceSession(str(self.path / "onnx" / "model.onnx"), options,
                                               providers=["CPUExecutionProvider"])
            self._inputs = {i.name for i in self._model.get_inputs()}
        tok = self.tokenizer
        vectors = []
        for start in range(0, len(texts), BATCH):
            ids, mask = tok.batch(texts[start:start + BATCH])
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self._model.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            vectors.append(pooled.astype("<f4"))
        return np.vstack(vectors) if vectors else np.empty((0, 384), dtype="<f4")

    # -- PyTorch ----------------------------------------------------------------
    def _encode_torch(self, texts):
        import torch
        if self._model is None:
            from transformers import AutoModel
            self.device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
            self._model = (AutoModel.from_pretrained(self.path, local_files_only=True, trust_remote_code=False)
                           .to(self.device).eval())
            if self.device == "cpu":
                torch.set_num_threads(4)
        vectors = []
        for start in range(0, len(texts), BATCH):
            inputs = self.tokenizer(texts[start:start + BATCH], padding=True, truncation=True,
                                    max_length=MAX_TOKENS, return_tensors="pt").to(self.device)
            with torch.inference_mode():
                hidden = self._model(**inputs).last_hidden_state
                mask = inputs["attention_mask"].unsqueeze(-1)
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            vectors.append(pooled.cpu().numpy().astype("<f4"))
        return np.vstack(vectors) if vectors else np.empty((0, 384), dtype="<f4")


class _FastTokenizerAdapter:
    """The part of the transformers fast-tokenizer API OriMem uses, on the same tokenizer.json.

    transformers (5.x) rebuilds this XLM-R backend instead of using tokenizer.json verbatim:
    the normalizer keeps only the Precompiled charsmap (no " {2,}" -> " " replace) and
    pre-tokenization is WhitespaceSplit followed by Metaspace. aligned_spec() applies the same
    two changes so token ids, offsets and therefore chunks and vector keys are identical.

    ``raw`` has no truncation/padding (chunk offsets, header tokens, decode); ``batched``
    truncates to 512 and pads for inference. decode() applies transformers'
    clean_up_tokenization_spaces, which this tokenizer config enables.
    """

    CLEANUP = ((" .", "."), (" ?", "?"), (" !", "!"), (" ,", ","), (" ' ", "'"), (" n't", "n't"),
               (" 'm", "'m"), (" 's", "'s"), (" 've", "'ve"), (" 're", "'re"))

    def __init__(self, path):
        from tokenizers import Tokenizer
        spec = self.aligned_spec(path)
        self.raw = Tokenizer.from_str(spec)
        self.raw.no_truncation()
        self.raw.no_padding()
        self.batched = Tokenizer.from_str(spec)
        self.batched.enable_truncation(max_length=MAX_TOKENS)
        pad = self.batched.token_to_id("<pad>")
        self.batched.enable_padding(pad_id=1 if pad is None else pad, pad_token="<pad>")

    def batch(self, texts):
        encodings = self.batched.encode_batch(list(texts))
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        return ids, mask

    def __call__(self, text, add_special_tokens=True, truncation=False, max_length=None,
                 return_offsets_mapping=False, **_ignored):
        enc = self.raw.encode(text, add_special_tokens=add_special_tokens)
        ids, offsets = enc.ids, enc.offsets
        if truncation and max_length is not None and len(ids) > max_length:
            ids, offsets = ids[:max_length], offsets[:max_length]
        out = {"input_ids": ids, "attention_mask": [1] * len(ids)}
        if return_offsets_mapping:
            out["offset_mapping"] = [tuple(o) for o in offsets]
        return out

    @staticmethod
    def aligned_spec(path):
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        norm = spec.get("normalizer") or {}
        if norm.get("type") == "Sequence":
            kept = [n for n in norm.get("normalizers", []) if n.get("type") == "Precompiled"]
            spec["normalizer"] = kept[0] if len(kept) == 1 else {"type": "Sequence", "normalizers": kept}
        pre = spec.get("pre_tokenizer") or {}
        if pre.get("type") == "Metaspace":
            spec["pre_tokenizer"] = {"type": "Sequence", "pretokenizers": [{"type": "WhitespaceSplit"}, pre]}
        return json.dumps(spec, ensure_ascii=False)

    def decode(self, ids, skip_special_tokens=False):
        text = self.raw.decode(list(ids), skip_special_tokens=skip_special_tokens)
        for old, new in self.CLEANUP:
            text = text.replace(old, new)
        return text
