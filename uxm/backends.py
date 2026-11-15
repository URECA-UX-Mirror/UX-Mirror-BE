"""Zoro-1 의사결정 백엔드.

- HttpBackend : 공식 trida-stack `serve_decide.py`의 POST /v1/decide (행사 GPU 서버, SGLang). 발표 수치는 이쪽.
- LocalBackend: Windows 로컬 개발용. trida-stack의 decide_request(인코딩)와 같은 포인터 헤드를
  transformers로 직접 돌린다. 원리는 decide_engine.py와 같다: 질문마다 토큰 행 하나를 만들고,
  각 옵션의 </opt>와 <decide> 위치의 마지막 층 은닉 상태를 헤드에 넣는다.
  행이 causal이라 한 번의 forward로 모든 위치의 은닉 상태를 얻는다(엔진의 prefix 방식과 수학적으로 동일).

두 백엔드 모두 {"answers": {qid: answer}, "timing_ms": ...} 형식을 돌려준다.
"""
from __future__ import annotations

import os
import sys
import time

VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "vendor", "trida-stack", "inference", "zoro-1")


class HttpBackend:
    name = "http"

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.url = base_url.rstrip("/") + "/v1/decide"
        self.timeout = timeout

    def info(self) -> dict:
        return {"backend": self.name, "endpoint": self.url}

    def decide(self, request: dict, temperature: float | None = None) -> dict:
        import requests
        body = {"request": request, "reasoning": False}
        if temperature is not None:
            body["temperature"] = temperature
        r = requests.post(self.url, json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


class LocalBackend:
    name = "local"

    def __init__(self, ckpt: str, device: str = "cpu", temperature: float = 1.0):
        import torch
        from transformers import AutoTokenizer

        if VENDOR not in sys.path:
            sys.path.insert(0, VENDOR)
        from decide_engine import PointerHead  # noqa: E402  (trida-stack, Apache-2.0)

        self.torch = torch
        self.ckpt, self.device, self.temperature = ckpt, device, temperature
        self.tok = AutoTokenizer.from_pretrained(ckpt)

        saved = torch.load(os.path.join(ckpt, "jev_head.pt"), map_location="cpu")
        self.head = PointerHead(saved["head"]["q.weight"].shape[1], saved["head_dim"]).eval()
        self.head.load_state_dict(saved["head"])

        t0 = time.perf_counter()
        self.model = self._load_model(ckpt, device)
        self.load_ms = round((time.perf_counter() - t0) * 1000)

    def _load_model(self, ckpt, device):
        torch = self.torch
        from transformers import AutoModelForImageTextToText

        if device == "cpu":
            # transformers 5.23은 커널이 없으면 스스로 순수 torch 구현으로 대체한다.
            # (trida 패리티 스크립트처럼 모듈 함수를 None으로 바꾸면 이 버전에선 TypeError)
            m = AutoModelForImageTextToText.from_pretrained(ckpt, dtype=torch.bfloat16, device_map="cpu")
        elif device == "cuda-4bit":
            from transformers import BitsAndBytesConfig
            q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_compute_dtype=torch.float16,
                                   llm_int8_skip_modules=["visual", "lm_head"])
            m = AutoModelForImageTextToText.from_pretrained(ckpt, quantization_config=q, device_map="cuda:0")
        else:
            raise ValueError(f"unknown device {device!r} (cpu | cuda-4bit)")
        return m.eval()

    def info(self) -> dict:
        return {"backend": self.name, "device": self.device, "checkpoint": self.ckpt,
                "temperature": self.temperature, "loadMs": self.load_ms}

    def _hidden(self, ids: list[int]):
        torch = self.torch
        x = torch.tensor([ids], device=self.model.device)
        with torch.no_grad():
            out = self.model.model(input_ids=x, use_cache=False)
        return out.last_hidden_state[0].float().cpu()   # 최종 norm 이후 = SGLang이 돌려주는 "last" 은닉 상태

    def decide(self, request: dict, temperature: float | None = None) -> dict:
        torch = self.torch
        from decide_request import parse, to_answer

        t0 = time.perf_counter()
        req = parse(request)
        enc = req.encode(self.tok)
        T = temperature or self.temperature
        answers = {}
        for q, row in zip(req.questions, enc.rows):
            h = self._hidden(row["ids"])
            h_dec, h_opt = h[row["decide"]].unsqueeze(0), h[row["opts"]]
            with torch.no_grad():
                z = self.head(h_dec, h_opt, torch.zeros(len(row["opts"]), dtype=torch.long))
            answers[q.qid] = to_answer(q, torch.softmax(z / T, -1).tolist())
        return {"answers": answers, "timing_ms": round((time.perf_counter() - t0) * 1000, 1)}


def make_backend(cfg: dict):
    if cfg["backend"] == "http":
        return HttpBackend(cfg["base_url"], cfg["timeout_s"])
    return LocalBackend(cfg["checkpoint"], cfg["device"], cfg["temperature"])
