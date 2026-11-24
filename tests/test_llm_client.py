"""Tests the Groq client against a fake OpenAI-style server on localhost: no key, no network.

Covers the paths a free-tier run will hit: per-minute and daily 429s, 5xx errors, fallback, and record then replay.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.llm import LLMUnavailable, OpenAICompatClient, RecordingClient

QUEUE = []   # (status, body, headers) to send next. Empty means a normal 200 reply.
SEEN = []    # models requested, in order


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body["model"])
        status, out, headers = QUEUE.pop(0) if QUEUE else (200, None, {})
        out = out or {"choices": [{"message": {"content": '{"ok": true, "model": "%s"}' % body["model"]}}],
                      "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())


@pytest.fixture(scope="module")
def server():
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def client(server, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "k")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)  # the env override would send these calls elsewhere
    QUEUE.clear(), SEEN.clear()
    cfg = {"provider": {"base_url": server, "api_key_env": "TEST_KEY", "timeout_s": 5},
           "steps": {"draft": {"model": "openai/gpt-oss-20b", "fallback": "openai/gpt-oss-120b", "max_tokens": 50}},
           "limits": {"default": {"rpm": 1000, "tpm": 10**7}},
           "prices_usd_per_1m": {"openai/gpt-oss-20b": [0.075, 0.30], "stand_in": [0.15, 0.60]}}
    return OpenAICompatClient(cfg)


MSG = [{"role": "user", "content": "hi, reply in JSON"}]


def test_normal_call_reports_tokens_and_list_price_cost(client):
    text, u = client.complete("draft", MSG)
    assert json.loads(text)["ok"] and u["tokens_in"] == 1000 and u["tokens_out"] == 200
    assert u["cost_usd"] == pytest.approx((1000 * 0.075 + 200 * 0.30) / 1e6) and not u["price_is_stand_in"]


def test_rate_limit_429_waits_and_retries_same_model(client):
    QUEUE.append((429, {"error": "rate"}, {"retry-after": "0"}))
    _, u = client.complete("draft", MSG)
    assert SEEN == ["openai/gpt-oss-20b", "openai/gpt-oss-20b"] and not u["fallback_used"]


def test_server_errors_retry_once_then_use_fallback_with_stand_in_price(client):
    QUEUE.extend([(500, {"error": "x"}, {}), (503, {"error": "x"}, {})])
    _, u = client.complete("draft", MSG)
    assert SEEN == ["openai/gpt-oss-20b"] * 2 + ["openai/gpt-oss-120b"]
    assert u["fallback_used"] and u["price_is_stand_in"]


def test_daily_limit_skips_the_model_for_the_rest_of_the_run(client, monkeypatch):
    # Groq's daily cap says "try again in 8m". Waiting 65s four times per call would turn a 25-minute run into hours.
    monkeypatch.setattr("app.llm.time.sleep", lambda s: pytest.fail(f"slept {s}s on a daily limit"))
    QUEUE.append((429, {"error": {"message": "Rate limit reached on tokens per day (TPD)"}}, {"retry-after": "485"}))
    _, u = client.complete("draft", MSG)
    _, u2 = client.complete("draft", MSG)
    assert SEEN == ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "openai/gpt-oss-120b"]
    assert u["fallback_used"] and u2["fallback_used"]


def test_unknown_model_goes_straight_to_fallback(client):
    QUEUE.append((404, {"error": "model_not_found"}, {}))
    client.complete("draft", MSG)
    assert SEEN == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]


def test_everything_down_raises_unavailable(client):
    QUEUE.extend([(500, {}, {})] * 4)
    with pytest.raises(LLMUnavailable):
        client.complete("draft", MSG)


def test_record_then_replay_without_a_key(client, tmp_path):
    path = tmp_path / "calls.jsonl"
    live = RecordingClient(client, path)
    first, _ = live.complete("draft", MSG)
    calls_after_record = len(SEEN)
    again, u = RecordingClient(None, path).complete("draft", MSG)
    assert again == first and u["replayed"] and len(SEEN) == calls_after_record
