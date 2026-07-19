#!/usr/bin/env python3
"""Offline test of track_a.py — no network, no API key.

Drives track_a.run_case against the dummy Anthropic SSE upstream (in-process),
with a synthetic non-zero price table to prove the cost recompute, then checks
the N-reps aggregate (variance). Stdlib only.
"""
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import track_a                # noqa: E402
import dummy_upstream as du    # noqa: E402


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), du.H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    gateway = {"kind": "anthropic", "path": "/v1/messages",
               "version": "2023-06-01", "key_env": "ANTHROPIC_API_KEY"}
    # preço fictício NÃO-zero → prova o recálculo de custo
    model_cfg = {"id": "dummy-model-1", "max_tokens": 16, "params": {"effort": "high"},
                 "price_per_mtok": {"input": 1000.0, "output": 2000.0,
                                    "cache_write": 0.0, "cache_read": 0.0}}
    base_url = f"http://127.0.0.1:{port}"
    prompt = "Responda apenas com a palavra OK.\n"

    cases = []
    for rep in range(1, 4):
        ids = {"run_id": "testrun", "task_id": "T-000-noop", "model_alias": "M-dummy",
               "repetition": rep, "case_id": f"T-000-noop/M-dummy/A/rep{rep}"}
        rec = track_a.run_case(model_cfg, gateway, prompt, "test-key", base_url, ids)
        cases.append(rec)
        print("rep", rep, json.dumps({k: rec.get(k) for k in
              ("status", "stop_reason", "model_id_resolved", "time", "tokens", "cost")},
              ensure_ascii=False))

    c0 = cases[0]
    expected_cost = round((123 * 1000.0 + 7 * 2000.0) / 1_000_000, 8)  # 0.137
    agg = track_a.aggregate(cases)
    print("\nagregado:", json.dumps(agg, ensure_ascii=False))

    checks = {
        "status==completed (3x)": all(c["status"] == "completed" for c in cases),
        "input_tokens==123": c0["tokens"]["input_tokens"] == 123,
        "output_tokens==7": c0["tokens"]["output_tokens"] == 7,
        "total_tokens==130": c0["tokens"]["total_tokens"] == 130,
        "stop_reason==end_turn": c0["stop_reason"] == "end_turn",
        "model_id_resolved==dummy-model-1": c0["model_id_resolved"] == "dummy-model-1",
        "ttft>0": (c0["time"]["ttft_ms_first_call"] or 0) > 0,
        "e2e>=ttft": c0["time"]["e2e_ms"] >= (c0["time"]["ttft_ms_first_call"] or 0),
        "generation_ms>=0": (c0["time"]["generation_ms"] or 0) >= 0,
        "tps_generation set": c0["throughput"]["tps_generation"] is not None,
        f"cost=={expected_cost} (123*1000 + 7*2000 /1e6)":
            abs(c0["cost"]["cost_usd_computed"] - expected_cost) < 1e-9,
        "reply_preview==OK": c0["reply_preview"] == "OK",
        "agents null (Trilha A §10.3)": c0["agents"] is None,
        "tools null (Trilha A §10.3)": c0["tools"] is None,
        "prompt_sha256 len 64": len(c0["prompt_sha256"]) == 64,
        "3 reps aggregated": agg["completed"] == 3,
        "cost cv_pct == 0 (usage fixo)": agg["cost_usd_computed"]["cv_pct"] == 0.0,
    }
    print()
    allok = True
    for k, v in checks.items():
        print(f"  [{'pass' if v else 'FAIL'}] {k}")
        allok = allok and v
    print("\nTRACK_A OFFLINE TEST:", "ALL PASS" if allok else "FAILURES ABOVE")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
