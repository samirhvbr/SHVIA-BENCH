#!/usr/bin/env python3
"""Offline test of track_a.py — MULTI-VENDOR. No network, no API key.

Exercises both gateway shapes against in-process dummies:
  - kind=anthropic  (dummy_upstream.py, Messages-API SSE)
  - kind=openai     (dummy_openai.py, /chat/completions SSE)
with a synthetic non-zero price table proving the cost recompute, plus the
N-reps aggregate. Stdlib only.
"""
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import track_a          # noqa: E402
import dummy_upstream   # noqa: E402
import dummy_openai     # noqa: E402

PRICE = {"input": 1000.0, "output": 2000.0, "cache_write": 0.0, "cache_read": 0.0}
EXP_COST = round((123 * 1000.0 + 7 * 2000.0) / 1_000_000, 8)  # 0.137


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def run_path(name, gateway, handler, model_id, expect_stop):
    port = serve(handler)
    base_url = f"http://127.0.0.1:{port}"
    model_cfg = {"id": model_id, "max_tokens": 16, "sampling_ok": gateway["kind"] == "openai",
                 "params": ({"temperature": 0.0, "top_p": 1.0} if gateway["kind"] == "openai"
                            else {"effort": "high"}),
                 "price_per_mtok": PRICE}
    cases = []
    for rep in range(1, 4):
        ids = {"run_id": "testrun", "task_id": "T-000-noop", "model_alias": name,
               "repetition": rep, "case_id": f"T-000-noop/{name}/A/rep{rep}"}
        cases.append(track_a.run_case(model_cfg, gateway, "Responda OK.\n",
                                      "test-key", base_url, ids))
    c0 = cases[0]
    agg = track_a.aggregate(cases)
    print(f"\n[{name}] rep1:", json.dumps({k: c0.get(k) for k in
          ("status", "stop_reason", "model_id_resolved", "provider_effective",
           "tokens", "cost")}, ensure_ascii=False))
    return {
        f"{name}: status completed (3x)": all(c["status"] == "completed" for c in cases),
        f"{name}: input_tokens==123": c0["tokens"]["input_tokens"] == 123,
        f"{name}: output_tokens==7": c0["tokens"]["output_tokens"] == 7,
        f"{name}: stop_reason=={expect_stop}": c0["stop_reason"] == expect_stop,
        f"{name}: model_id=={model_id}": c0["model_id_resolved"] == model_id,
        f"{name}: ttft>0 & e2e>=ttft": (c0["time"]["ttft_ms_first_call"] or 0) > 0
            and c0["time"]["e2e_ms"] >= (c0["time"]["ttft_ms_first_call"] or 0),
        f"{name}: cost=={EXP_COST}": abs(c0["cost"]["cost_usd_computed"] - EXP_COST) < 1e-9,
        f"{name}: reply==OK": c0["reply_preview"] == "OK",
        f"{name}: 3 reps agg, cost cv 0": agg["completed"] == 3
            and agg["cost_usd_computed"]["cv_pct"] == 0.0,
    }


def main():
    checks = {}
    checks.update(run_path(
        "anthropic",
        {"kind": "anthropic", "path": "/v1/messages", "version": "2023-06-01"},
        dummy_upstream.H, "dummy-model-1", "end_turn"))
    checks.update(run_path(
        "openai",
        {"kind": "openai", "path": "/v1/chat/completions", "sampling_ok": True},
        dummy_openai.H, "dummy-oai-1", "stop"))
    # provider_effective vem do header x-openrouter-provider (só no path openai)
    print()
    allok = True
    for k, v in checks.items():
        print(f"  [{'pass' if v else 'FAIL'}] {k}")
        allok = allok and v
    print("\nTRACK_A OFFLINE (multi-vendor):", "ALL PASS" if allok else "FAILURES ABOVE")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
