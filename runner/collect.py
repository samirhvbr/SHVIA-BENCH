#!/usr/bin/env python3
"""collect.py — funde as 3 camadas de instrumentação numa linha results.jsonl.

  C1  result JSON do harness (`--output-format json`)  — nativo
  C2  transcript JSONL ($CLAUDE_CONFIG_DIR/projects/<cwd>/<sessão>.jsonl)
  C3  proxy.jsonl (verdade-base: TTFT, usage bruto, provedor)

Precedência p/ custo/tokens: C3 é a verdade, C1 é conferência; divergência > 2%
→ inspeção (§10.1). Métrica ausente = null, NUNCA 0 (§10.3). Schemas confirmados
empiricamente no Claude Code 2.1.207 (config/harness-matrix.md).
"""
import json
import os


def _cost(usage, price):
    t = lambda k: usage.get(k, 0) or 0
    return round((t("input_tokens") * price.get("input", 0)
                  + t("output_tokens") * price.get("output", 0)
                  + t("cache_creation_input_tokens") * price.get("cache_write", 0)
                  + t("cache_read_input_tokens") * price.get("cache_read", 0)) / 1e6, 8)


def parse_c1(c1):
    """result object → grupos de métrica (nativo)."""
    if not c1:
        return {}
    usage = c1.get("usage") or {}
    mu = c1.get("modelUsage") or {}
    mk = next(iter(mu), None)
    m = mu.get(mk, {}) if mk else {}
    dur, api = c1.get("duration_ms"), c1.get("duration_api_ms")
    tool_time = (dur - api) if (isinstance(dur, (int, float)) and isinstance(api, (int, float))) else None
    return {
        "model_id_resolved": mk,
        "num_turns": c1.get("num_turns"),
        "stop_reason": c1.get("stop_reason"),
        "terminal_reason": c1.get("terminal_reason"),
        "subtype": c1.get("subtype"),
        "service_tier": usage.get("service_tier"),
        "speed": usage.get("speed"),
        "inference_geo": usage.get("inference_geo"),
        "duration_ms": dur, "api_duration_ms": api, "tool_time_ms": tool_time,
        "ttft_ms": c1.get("ttft_ms"),
        "cost_harness": c1.get("total_cost_usd"),
        "permission_denials": len(c1.get("permission_denials") or []),
        "context_window": m.get("contextWindow"),
        "max_output_tokens": m.get("maxOutputTokens"),
        "usage": {k: usage.get(k) for k in
                  ("input_tokens", "output_tokens",
                   "cache_creation_input_tokens", "cache_read_input_tokens")},
    }


def parse_c2(path, kind="claude-code"):
    """transcript JSONL → subagentes, thinking, ferramentas, pico de contexto.
    Adapter por harness (kind). Só 'claude-code' implementado (schema validado
    em config/harness-matrix.md); outros harnesses → None até ter adapter próprio."""
    if kind != "claude-code" or not path or not os.path.exists(path):
        return None
    subagents, sub_types, thinking, by_name = 0, [], 0, {}
    peak, per_turn, compaction, sidechain = 0, [], 0, False
    for line in open(path, encoding="utf-8"):
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("isSidechain"):
            sidechain = True
        t = o.get("type")
        if t in ("compaction", "context_compacted") or o.get("subtype") == "compact":
            compaction += 1
        msg = o.get("message") or {}
        if not isinstance(msg, dict):
            continue
        u = msg.get("usage")
        if isinstance(u, dict):
            used = (u.get("input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0) \
                + (u.get("cache_creation_input_tokens", 0) or 0)
            if used:
                per_turn.append(used)
                peak = max(peak, used)
        for b in (msg.get("content") or []):
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "thinking":
                thinking += 1
            elif bt == "tool_use":
                name = b.get("name", "?")
                by_name[name] = by_name.get(name, 0) + 1
                if name == "Task":
                    subagents += 1
                    st = (b.get("input") or {}).get("subagent_type")
                    if st:
                        sub_types.append(st)
    return {
        "subagent_count": subagents, "subagent_types": sub_types or None,
        "subagent_link_confidence": "heuristic" if sidechain else "exact",
        "thinking_blocks_count": thinking,
        "tool_calls_total": sum(by_name.values()) or None,
        "tool_calls_by_name": by_name or None,
        "context_peak_tokens": peak or None,
        "tokens_per_turn": per_turn or None,
        "compaction_events": compaction,
    }


def parse_c3(path):
    """proxy.jsonl → TTFT real, usage bruto, provedor (verdade-base)."""
    if not path or not os.path.exists(path):
        return None
    calls, ttfts, provider = [], [], None
    agg = {"input_tokens": 0, "output_tokens": 0,
           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    hosts_off = []
    for line in open(path, encoding="utf-8"):
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        calls.append(o)
        if o.get("ttft_ms") is not None:
            ttfts.append(o["ttft_ms"])
        provider = provider or o.get("provider")
        if o.get("host_allowed") is False:
            hosts_off.append(o.get("dest_host"))
        for k in agg:
            agg[k] += (o.get("usage") or {}).get(k, 0) or 0
    ttfts.sort()
    def pct(p):
        return ttfts[min(len(ttfts) - 1, int(p * len(ttfts)))] if ttfts else None
    return {
        "calls": len(calls), "provider_effective": provider,
        "ttft_ms_first_call": ttfts[0] if ttfts else None,
        "ttft_ms_p50": pct(0.5), "ttft_ms_p95": pct(0.95),
        "usage": agg, "hosts_off_allowlist": hosts_off or None,
    }


def collect(harness_result, proxy_log, model_cfg, ids, verify):
    """Funde C1+C2+C3 numa linha results.schema.json (Trilha B)."""
    hr = harness_result
    c1 = parse_c1(hr.get("c1"))
    c2 = parse_c2(hr.get("transcript_path"), hr.get("transcript_kind", "claude-code"))
    c3 = parse_c3(proxy_log)
    price = model_cfg["price_per_mtok"]

    # verdade p/ tokens/custo: C3 se tiver, senão C1 (§10.1)
    usage_truth = (c3 or {}).get("usage") if c3 and any((c3.get("usage") or {}).values()) else c1.get("usage", {})
    cost_computed = _cost(usage_truth or {}, price)
    cost_harness = c1.get("cost_harness")
    delta = (round(100 * abs(cost_harness - cost_computed) / cost_harness, 2)
             if isinstance(cost_harness, (int, float)) and cost_harness else None)

    e2e = hr.get("e2e_ms")
    dur = c1.get("duration_ms")
    startup = round(e2e - dur, 1) if isinstance(e2e, (int, float)) and isinstance(dur, (int, float)) else None

    rec = {
        "run_id": ids["run_id"], "case_id": ids["case_id"], "task_id": ids["task_id"],
        "model_alias": ids["model_alias"], "track": "B", "repetition": ids["repetition"],
        "model_id_resolved": c1.get("model_id_resolved"),
        "session_id": hr.get("session_id"),
        "provider_effective": (c3 or {}).get("provider_effective"),
        "service_tier": c1.get("service_tier"), "speed": c1.get("speed"),
        "inference_geo": c1.get("inference_geo"),
        "started_utc": hr.get("started_utc"), "finished_utc": hr.get("finished_utc"),
        "status": hr.get("status"), "stop_reason": c1.get("stop_reason"),
        "terminal_reason": c1.get("terminal_reason"), "subtype": c1.get("subtype"),
        "harness": {"name": "claude-code", "version": os.environ.get("EXPECT_CLAUDE_VERSION")},
        "time": {
            "e2e_ms": e2e, "harness_duration_ms": dur, "api_duration_ms": c1.get("api_duration_ms"),
            "tool_time_ms": c1.get("tool_time_ms"), "startup_overhead_ms": startup,
            "ttft_ms_first_call": (c3 or {}).get("ttft_ms_first_call") or c1.get("ttft_ms"),
            "ttft_ms_p50": (c3 or {}).get("ttft_ms_p50"), "ttft_ms_p95": (c3 or {}).get("ttft_ms_p95"),
        },
        "tokens": {**{k: (usage_truth or {}).get(k) for k in
                      ("input_tokens", "output_tokens",
                       "cache_creation_input_tokens", "cache_read_input_tokens")},
                   "total_tokens": (sum(v for v in (usage_truth or {}).values()
                                        if isinstance(v, (int, float))) or None)},
        "cost": {"cost_usd_harness": cost_harness, "cost_usd_computed": cost_computed,
                 "cost_delta_pct": delta},
        "context": {"context_window": c1.get("context_window"),
                    "max_output_tokens": c1.get("max_output_tokens"),
                    "context_peak_tokens": (c2 or {}).get("context_peak_tokens"),
                    "compaction_events": (c2 or {}).get("compaction_events")},
        "agents": ({"main_agent_turns": c1.get("num_turns"),
                    "subagent_count": c2.get("subagent_count"),
                    "subagent_types": c2.get("subagent_types"),
                    "subagent_link_confidence": c2.get("subagent_link_confidence")} if c2 else
                   ({"main_agent_turns": c1.get("num_turns")} if c1.get("num_turns") is not None else None)),
        "effort": {"reasoning_visible": None,
                   "thinking_blocks_count": (c2 or {}).get("thinking_blocks_count")},
        "autonomy": {"permission_blocked_count": c1.get("permission_denials"),
                     "hit_max_turns": hr.get("status") == "max_turns"},
        "tools": ({"tool_calls_total": c2.get("tool_calls_total"),
                   "by_name": c2.get("tool_calls_by_name")} if c2 else None),
        "verification": verify,
    }
    if (c3 or {}).get("hosts_off_allowlist"):
        rec["audit_flag"] = {"hosts_off_allowlist": c3["hosts_off_allowlist"]}
    return rec
