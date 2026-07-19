#!/usr/bin/env python3
"""Offline end-to-end test of logging_proxy.py — no network, no TLS, no API key.

Runs the dummy Anthropic-style upstream and the proxy in-process (threads, ephemeral
ports), sends one request through the proxy, then asserts proxy.jsonl captured the
ground-truth metrics: TTFT, usage, stop_reason, models, request-id, allowlist flag.
"""
import http.client
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "proxy"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import logging_proxy as lp          # noqa: E402
import dummy_upstream as du         # noqa: E402


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def post_through(pport, body):
    conn = http.client.HTTPConnection("127.0.0.1", pport, timeout=30)
    conn.request("POST", "/v1/messages", body=body, headers={
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "anthropic-version": "2023-06-01"})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def last_log(path, want_lines=1, timeout=3.0):
    # The proxy writes the log line in its handler's `finally`, which can lag the
    # client's read of the final chunk. Wait (bounded) for the line to land.
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        try:
            lines = open(path, encoding="utf-8").read().strip().splitlines()
        except FileNotFoundError:
            lines = []
        if len(lines) >= want_lines:
            return json.loads(lines[-1])
        time.sleep(0.02)
    raise AssertionError(f"proxy.jsonl não teve {want_lines} linha(s) em {timeout}s")


def main():
    _, dport = serve(du.H)
    logpath = os.path.join(ROOT, "runs", "_offline_proxy_test", "proxy.jsonl")
    os.makedirs(os.path.dirname(logpath), exist_ok=True)
    open(logpath, "w").close()

    # --- happy path: host in allowlist ---
    cfg = lp.Cfg(f"http://127.0.0.1:{dport}", logpath, ["127.0.0.1"])
    _, pport = serve(lp.make_handler(cfg))
    body = json.dumps({"model": "req-model-x", "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    status, data = post_through(pport, body)

    print(f"client: status={status} bytes={len(data)} "
          f"has_OK={b'OK' in data} has_stop={b'message_stop' in data}")
    rec = last_log(logpath, want_lines=1)
    print("proxy.jsonl:", json.dumps(rec, ensure_ascii=False))

    checks = {
        "status==200": rec["status"] == 200,
        "ttft_ms>0": (rec["ttft_ms"] or 0) > 0,
        "e2e>=ttft": rec["e2e_ms"] >= (rec["ttft_ms"] or 0),
        "input_tokens==123": rec["usage"].get("input_tokens") == 123,
        "output_tokens==7": rec["usage"].get("output_tokens") == 7,
        "stop_reason==end_turn": rec["stop_reason"] == "end_turn",
        "response_model==dummy-model-1": rec["response_model"] == "dummy-model-1",
        "request_model==req-model-x": rec["request_model"] == "req-model-x",
        "host_allowed==True": rec["host_allowed"] is True,
        "request_id captured": rec["request_id"] == "req_dummy_123",
        "body preserved (client saw OK)": b"OK" in data,
    }

    # --- allowlist teeth: same upstream, host NOT in allowlist ---
    cfg2 = lp.Cfg(f"http://127.0.0.1:{dport}", logpath, ["api.anthropic.com"])
    _, pport2 = serve(lp.make_handler(cfg2))
    post_through(pport2, body)
    rec2 = last_log(logpath, want_lines=2)
    checks["host_allowed==False when off-allowlist"] = rec2["host_allowed"] is False

    print()
    allok = True
    for k, v in checks.items():
        print(f"  [{'pass' if v else 'FAIL'}] {k}")
        allok = allok and v
    print("\nPROXY OFFLINE TEST:", "ALL PASS" if allok else "FAILURES ABOVE")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
