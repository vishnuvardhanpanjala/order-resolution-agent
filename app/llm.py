"""Model layer. Models only read and write text. Nothing here can touch an order or money.

Every client has the same method: complete(step, messages, json_mode) -> (text, usage).
  ScriptedLLM (app/scripted.py)  keyword rules, no key. For wiring, unit tests and CI.
  OpenAICompatClient             Groq by default. Any OpenAI-style API, set in config/models.yaml.
  RecordingClient                wraps a live client and saves each call to recordings/calls.jsonl,
                                 or replays that file with no key, so CI sees real model outputs.

Check models before a live run:  python -m app.llm --check
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RECORDINGS = ROOT / "recordings" / "calls.jsonl"


class LLMUnavailable(Exception):
    """Provider down, timed out, or out of quota, after retries and the fallback model."""


class ReplayMiss(Exception):
    """Replay mode found no recording for this exact call. Usually a prompt changed: re-run live."""


def load_models_config(path: Path = ROOT / "config" / "models.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_prompt(name: str) -> tuple[str, str]:
    """Returns (version, text). The first line of each prompt file is <!-- version: name-vN -->."""
    text = (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    m = re.match(r"<!--\s*version:\s*([\w.-]+)\s*-->\s*", text)
    return (m.group(1), text[m.end():]) if m else (f"{name}-unversioned", text)


def call_key(step: str, messages: list[dict]) -> str:
    """Same step and same messages give the same key. A changed prompt gives a new key."""
    return hashlib.sha256((step + "\n" + json.dumps(messages, sort_keys=True)).encode()).hexdigest()[:24]


def price(cfg: dict, model: str, tokens_in: int, tokens_out: int) -> tuple[float, bool]:
    table = cfg.get("prices_usd_per_1m", {})
    stand_in = model not in table
    p_in, p_out = table.get(model, table.get("stand_in", [0, 0]))
    return round((tokens_in * p_in + tokens_out * p_out) / 1e6, 8), stand_in


def strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class RateLimiter:
    """Keeps each model under its requests-a-minute and tokens-a-minute limits (90% of each)."""

    def __init__(self, limits: dict):
        self.limits, self.hist, self.lock = limits, defaultdict(deque), threading.Lock()

    def wait(self, model: str, est_tokens: int) -> None:
        lim = self.limits.get(model, self.limits["default"])
        while True:
            with self.lock:
                h, now = self.hist[model], time.monotonic()
                while h and now - h[0][0] > 60:
                    h.popleft()
                if not h or (len(h) < lim["rpm"] * 0.9 and sum(t for _, t in h) + est_tokens <= lim["tpm"] * 0.9):
                    return
                pause = max(0.5, 60.5 - (now - h[0][0]))
            time.sleep(pause)

    def record(self, model: str, tokens: int) -> None:
        with self.lock:
            self.hist[model].append((time.monotonic(), tokens))


class OpenAICompatClient:
    def __init__(self, cfg: dict | None = None):
        load_dotenv()
        self.cfg = cfg or load_models_config()
        env_name = self.cfg["provider"]["api_key_env"]
        self.key = os.environ.get(env_name, "")
        if not self.key:
            raise SystemExit(f"{env_name} is not set. Copy .env.example to .env and add your key.")
        base = os.environ.get("LLM_BASE_URL") or self.cfg["provider"]["base_url"]  # env override, e.g. another provider
        self.url = base.rstrip("/") + "/chat/completions"
        self.limiter = RateLimiter(self.cfg["limits"])
        self.spent = set()  # models that hit a daily cap in this run: skipped until the process restarts

    def _post(self, model: str, messages: list[dict], sc: dict, json_mode: bool) -> dict:
        body = {"model": model, "messages": messages, "temperature": sc.get("temperature", 0),
                "max_completion_tokens": sc["max_tokens"], **self.cfg.get("model_options", {}).get(model, {})}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
            "User-Agent": "order-resolution-agent/0.1"})
        with urllib.request.urlopen(req, timeout=self.cfg["provider"].get("timeout_s", 40)) as r:
            return json.loads(r.read())

    def complete(self, step: str, messages: list[dict], json_mode: bool = False) -> tuple[str, dict]:
        sc = self.cfg["steps"][step]
        errors = []
        for model in [sc["model"]] + ([sc["fallback"]] if sc.get("fallback") else []):
            if model in self.spent:
                errors.append(f"{model}: daily limit reached earlier in this run")
                continue
            est = len(json.dumps(messages)) // 4 + sc["max_tokens"] // 2  # a 429 is handled anyway
            tries, rate_waits = 0, 0
            while tries < 2:  # one retry on timeout or 5xx, then the fallback model
                self.limiter.wait(model, est)
                t0 = time.perf_counter()
                try:
                    data = self._post(model, messages, sc, json_mode)
                except urllib.error.HTTPError as e:
                    body, wait = e.read()[:300], float(e.headers.get("retry-after") or 10)
                    if e.code == 429 and (wait > 65 or b"per day" in body):
                        # A daily cap, not a per-minute one. Waiting won't help today, so this model is
                        # skipped for the rest of the run and every call goes to the fallback at once.
                        self.spent.add(model)
                        errors.append(f"{model}: daily limit {body[:160]!r}")
                        break
                    if e.code == 429 and rate_waits < 4:  # per-minute limit: wait as told, not counted as a try
                        rate_waits += 1
                        time.sleep(min(wait, 65))
                        continue
                    errors.append(f"{model}: HTTP {e.code} {body[:160]!r}")
                    if e.code < 500 and e.code != 429:
                        break  # bad request or unknown model: retrying won't help, go to fallback
                    tries += 1
                    continue
                except (urllib.error.URLError, TimeoutError, OSError) as e:
                    errors.append(f"{model}: {type(e).__name__} {e}")
                    tries += 1
                    continue
                latency = round((time.perf_counter() - t0) * 1000)
                u = data.get("usage", {})
                t_in, t_out = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
                self.limiter.record(model, t_in + t_out)
                cost, stand_in = price(self.cfg, model, t_in, t_out)
                text = strip_thinking(data["choices"][0]["message"].get("content") or "")
                return text, {"model": model, "tokens_in": t_in, "tokens_out": t_out, "cost_usd": cost,
                              "price_is_stand_in": stand_in, "latency_ms": latency,
                              "fallback_used": model != sc["model"]}
        raise LLMUnavailable("; ".join(errors)[-600:])


class RecordingClient:
    """Record mode: reuse a saved call if there is one (so a stopped run resumes), else call live and save.
    Replay mode (inner=None): only saved calls, no key needed."""

    def __init__(self, inner=None, path: Path = RECORDINGS):
        self.inner, self.path, self.calls, self.lock = inner, path, {}, threading.Lock()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.calls[rec["key"]] = rec

    def complete(self, step: str, messages: list[dict], json_mode: bool = False) -> tuple[str, dict]:
        key = call_key(step, messages)
        rec = self.calls.get(key)
        if rec and (self.inner is None or "error" not in rec):  # in record mode, retry saved failures
            if "error" in rec:
                raise LLMUnavailable(rec["error"])
            return rec["output"], {**rec["usage"], "replayed": True}
        if self.inner is None:
            raise ReplayMiss(f"no recording for step={step} key={key}")
        try:
            out, usage = self.inner.complete(step, messages, json_mode)
            rec = {"key": key, "step": step, "output": out, "usage": usage}
        except LLMUnavailable as e:
            rec = {"key": key, "step": step, "error": str(e)}
        with self.lock:  # the baseline runs in a second thread during live runs
            self.calls[key] = rec
            self.path.parent.mkdir(exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        if "error" in rec:
            raise LLMUnavailable(rec["error"])
        return out, usage


def make_llm(mode: str):
    from app.scripted import ScriptedLLM
    if mode == "auto":
        mode = "replay" if RECORDINGS.exists() and RECORDINGS.stat().st_size > 0 else "scripted"
    if mode == "scripted":
        return ScriptedLLM(), "scripted"
    if mode == "replay":
        return RecordingClient(None), "replay"
    if mode == "live":
        return RecordingClient(OpenAICompatClient()), "live"
    raise ValueError(mode)


def check_models() -> None:
    """Ping every configured model once with a tiny prompt, so problems show up before a long run."""
    client = OpenAICompatClient()
    seen = set()
    for step, sc in client.cfg["steps"].items():
        for model in filter(None, [sc["model"], sc.get("fallback")]):
            if model in seen:
                continue
            seen.add(model)
            msgs = [{"role": "user", "content": 'Reply with JSON {"ok": true}'}]
            t0 = time.perf_counter()
            try:
                data = client._post(model, msgs, {"max_tokens": 50, "temperature": 0}, True)
                txt = strip_thinking(data["choices"][0]["message"].get("content") or "")[:40]
                print(f"OK    {model:32s} {round((time.perf_counter() - t0) * 1000):5d} ms  {txt!r}")
            except urllib.error.HTTPError as e:
                print(f"FAIL  {model:32s} HTTP {e.code} {e.read()[:200]!r}")
            except Exception as e:  # noqa: BLE001 - report anything, this is a diagnostic
                print(f"FAIL  {model:32s} {type(e).__name__}: {e}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check_models()
