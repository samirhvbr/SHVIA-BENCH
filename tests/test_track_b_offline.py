#!/usr/bin/env python3
"""Offline test of track_b + collect — fake harness, no network, no API key.

Drives track_b.run_harness against tests/fake_claude.py (emulating the real
2.1.207 result JSON + transcript JSONL), synthesizes a proxy.jsonl (C3), then
fuses via collect.collect and checks the results line (C1+C2+C3). Stdlib only.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))
import track_b   # noqa: E402
import collect   # noqa: E402


def main():
    tmp = tempfile.mkdtemp(prefix="sb-trackb-")
    workspace = os.path.join(tmp, "work"); os.makedirs(workspace)
    config_dir = os.path.join(tmp, "config"); os.makedirs(config_dir)
    fake = os.path.join(ROOT, "tests", "fake_claude.py")

    # preço estilo Opus (input 5 / output 25 / cache_write 6.25 / cache_read 0.5)
    model_cfg = {"id": "dummy-model-1", "max_tokens": 8192,
                 "price_per_mtok": {"input": 5.0, "output": 25.0,
                                    "cache_write": 6.25, "cache_read": 0.5}}
    opts = {"effort": "high", "budget_usd": 2.0, "max_turns": 30,
            "permission_mode": "acceptEdits", "timeout_s": 60,
            "mcp_config": os.path.join(ROOT, "config", "mcp.empty.json"),
            "settings": os.path.join(config_dir, "settings.json")}

    harness = {"name": "claude-code", "bin": fake, "result_format": "claude-code-json",
               "transcript": {"kind": "claude-code"}}
    hr = track_b.run_harness(harness, model_cfg, "corrija o bug",
                             workspace, config_dir, opts)
    print("harness:", json.dumps({k: hr.get(k) for k in
          ("status", "session_id", "e2e_ms", "transcript_path")}, ensure_ascii=False))

    # C3 sintético: 2 chamadas; usage soma = input100/out200/cc5000/cr10000
    proxy_log = os.path.join(tmp, "proxy.jsonl")
    with open(proxy_log, "w") as f:
        f.write(json.dumps({"ttft_ms": 850.0, "provider": None, "host_allowed": True,
                "usage": {"input_tokens": 100, "cache_creation_input_tokens": 5000,
                          "cache_read_input_tokens": 10000, "output_tokens": 0}}) + "\n")
        f.write(json.dumps({"ttft_ms": 1200.0, "provider": None, "host_allowed": True,
                "usage": {"output_tokens": 200}}) + "\n")

    ids = {"run_id": "testrun", "task_id": "T-x", "model_alias": "M-dummy",
           "repetition": 1, "case_id": "T-x/M-dummy/B/rep1"}
    verify = {"passed": True, "exit_code": 0}
    rec = collect.collect(hr, proxy_log, model_cfg, ids, verify)
    print("\nresults line:\n", json.dumps(rec, ensure_ascii=False, indent=1))

    exp_cost = round((100 * 5 + 200 * 25 + 5000 * 6.25 + 10000 * 0.5) / 1e6, 8)  # 0.04175
    t = rec.get("time", {}); tok = rec.get("tokens", {}); cost = rec.get("cost", {})
    ag = rec.get("agents", {}); ctx = rec.get("context", {}); tl = rec.get("tools", {})
    ef = rec.get("effort", {})
    checks = {
        "status==completed": hr["status"] == "completed" and rec["status"] == "completed",
        "track==B": rec["track"] == "B",
        "model_id_resolved==dummy-model-1[1m]": rec["model_id_resolved"] == "dummy-model-1[1m]",
        # C1
        "main_agent_turns==3": ag.get("main_agent_turns") == 3,
        "harness_duration_ms==5000": t.get("harness_duration_ms") == 5000,
        "api_duration_ms==3000": t.get("api_duration_ms") == 3000,
        "tool_time_ms==2000": t.get("tool_time_ms") == 2000,
        "context_window==1e6": ctx.get("context_window") == 1000000,
        "max_output_tokens==64000": ctx.get("max_output_tokens") == 64000,
        "service_tier==standard": rec.get("service_tier") == "standard",
        "inference_geo captured (V18)": rec.get("inference_geo") == "not_available",
        "cost_harness==0.042": cost.get("cost_usd_harness") == 0.042,
        # C3 precedência + reconciliação
        "cost_computed==0.04175 (C3 usage)": abs(cost.get("cost_usd_computed") - exp_cost) < 1e-9,
        "cost_delta_pct < 2% (C1 vs C3)": cost.get("cost_delta_pct") is not None and cost["cost_delta_pct"] < 2.0,
        "ttft from C3 (850, < C1 900)": t.get("ttft_ms_first_call") == 850.0,
        # C2
        "subagent_count==1": ag.get("subagent_count") == 1,
        "subagent_types==[general-purpose]": ag.get("subagent_types") == ["general-purpose"],
        "link_confidence==heuristic (sidechain)": ag.get("subagent_link_confidence") == "heuristic",
        "thinking_blocks_count==1": ef.get("thinking_blocks_count") == 1,
        "tool_calls_total==3": tl.get("tool_calls_total") == 3,
        "by_name Task/Read/Edit": tl.get("by_name") == {"Task": 1, "Read": 1, "Edit": 1},
        "context_peak==15100 (input+cache do turno)": ctx.get("context_peak_tokens") == 15100,
        # autonomia + verificação
        "permission_blocked_count==0": (rec.get("autonomy") or {}).get("permission_blocked_count") == 0,
        "verification.passed==True": (rec.get("verification") or {}).get("passed") is True,
        "SOLUTION.txt criado no workspace": os.path.exists(os.path.join(workspace, "SOLUTION.txt")),
    }
    print()
    allok = True
    for k, v in checks.items():
        print(f"  [{'pass' if v else 'FAIL'}] {k}")
        allok = allok and v
    print("\nTRACK_B OFFLINE TEST:", "ALL PASS" if allok else "FAILURES ABOVE")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
