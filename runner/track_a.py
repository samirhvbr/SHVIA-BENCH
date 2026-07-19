#!/usr/bin/env python3
"""track_a.py — Trilha A (modelo puro), spec §5. Stdlib only. MULTI-VENDOR.

Uma requisição, sem ferramentas, streaming. Cada modelo referencia um gateway
(config/gateways.json): `kind=anthropic` (Messages API) ou `kind=openai`
(/chat/completions OpenAI-compat — OpenAI, xAI/Grok, DeepSeek, Z.ai/GLM, Novita,
OpenRouter, Kilo…). Emite results.jsonl (results.schema.json §10.4).

⚠️ Sampling por-modelo: Anthropic 4.6+ e modelos de raciocínio (o*/gpt-5/
deepseek-reasoner) REJEITAM temperature/top_p (400). O track_a só envia sampling
se `gateway.sampling_ok` E `model.sampling_ok != false` (§5.2 corrigido).

Uso (dentro do run.sh): python3 runner/track_a.py --model M-opus48 --task T-000-noop --reps 5
"""
import argparse
import hashlib
import http.client
import json
import os
import statistics
import sys
import time
from urllib.parse import urlsplit


# ---------- corpo por vendor ----------
def build_body_anthropic(model_cfg, prompt):
    p = model_cfg.get("params", {})
    body = {"model": model_cfg["id"], "max_tokens": model_cfg.get("max_tokens", 8192),
            "stream": True, "messages": [{"role": "user", "content": prompt}]}
    if p.get("effort"):
        body["output_config"] = {"effort": p["effort"]}
    if p.get("thinking") == "adaptive":
        body["thinking"] = {"type": "adaptive"}
    return body


def build_body_openai(model_cfg, gateway, prompt):
    p = model_cfg.get("params", {})
    body = {"model": model_cfg["id"], "max_tokens": model_cfg.get("max_tokens", 8192),
            "stream": True, "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": prompt}]}
    if gateway.get("sampling_ok") and model_cfg.get("sampling_ok") is not False:
        if "temperature" in p:
            body["temperature"] = p["temperature"]
        if "top_p" in p:
            body["top_p"] = p["top_p"]
    if p.get("reasoning_effort"):
        body["reasoning_effort"] = p["reasoning_effort"]
    if gateway.get("provider_pin") and model_cfg.get("provider_pin"):
        body["provider"] = {"order": model_cfg["provider_pin"], "allow_fallbacks": False}
    return body


# ---------- parse SSE por vendor → usage normalizado (chaves estilo Anthropic) ----------
def parse_anthropic_sse(text):
    usage, model, stop, parts = {}, None, None, []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            o = json.loads(payload)
        except json.JSONDecodeError:
            continue
        msg = o.get("message") or {}
        if isinstance(msg, dict):
            model = model or msg.get("model")
            if isinstance(msg.get("usage"), dict):
                usage.update({k: v for k, v in msg["usage"].items() if v is not None})
        if isinstance(o.get("usage"), dict):
            usage.update({k: v for k, v in o["usage"].items() if v is not None})
        d = o.get("delta") or {}
        if isinstance(d, dict):
            if d.get("stop_reason"):
                stop = d["stop_reason"]
            if d.get("type") == "text_delta" and d.get("text"):
                parts.append(d["text"])
    return usage, model, stop, "".join(parts)


def parse_openai_sse(text):
    usage, model, stop, parts = {}, None, None, []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            o = json.loads(payload)
        except json.JSONDecodeError:
            continue
        model = model or o.get("model")
        u = o.get("usage")
        if isinstance(u, dict):
            usage["input_tokens"] = u.get("prompt_tokens", usage.get("input_tokens"))
            usage["output_tokens"] = u.get("completion_tokens", usage.get("output_tokens"))
            det = u.get("prompt_tokens_details") or {}
            if det.get("cached_tokens") is not None:
                usage["cache_read_input_tokens"] = det["cached_tokens"]
        for ch in (o.get("choices") or []):
            d = ch.get("delta") or {}
            if d.get("content"):
                parts.append(d["content"])
            if ch.get("finish_reason"):
                stop = ch["finish_reason"]
    return usage, model, stop, "".join(parts)


def compute_cost(usage, price):
    t = lambda k: usage.get(k, 0) or 0
    return round((t("input_tokens") * price.get("input", 0)
                  + t("output_tokens") * price.get("output", 0)
                  + t("cache_creation_input_tokens") * price.get("cache_write", 0)
                  + t("cache_read_input_tokens") * price.get("cache_read", 0)) / 1e6, 8)


def run_case(model_cfg, gateway, prompt, api_key, base_url, ids):
    """Uma requisição streaming (kind anthropic|openai). Retorna linha results.schema (Trilha A)."""
    kind = gateway.get("kind", "anthropic")
    body = json.dumps(build_body_anthropic(model_cfg, prompt) if kind == "anthropic"
                      else build_body_openai(model_cfg, gateway, prompt)).encode()
    if kind == "anthropic":
        headers = {"content-type": "application/json",
                   "anthropic-version": gateway.get("version", "2023-06-01"),
                   "x-api-key": api_key or ""}
    else:
        headers = {"content-type": "application/json",
                   "authorization": f"Bearer {api_key or ''}"}
        headers.update(gateway.get("extra_headers", {}))

    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    s = urlsplit(base_url.rstrip("/") + gateway["path"])
    scheme = s.scheme or "https"
    host, port = s.hostname, s.port or (443 if scheme == "https" else 80)
    rec = {"run_id": ids["run_id"], "case_id": ids["case_id"], "task_id": ids["task_id"],
           "model_alias": ids["model_alias"], "model_id_resolved": None, "track": "A",
           "repetition": ids["repetition"], "session_id": None, "provider_effective": None,
           "started_utc": round(time.time(), 3), "prompt_sha256": prompt_sha,
           "agents": None, "tools": None, "autonomy": None}
    t0 = time.perf_counter()
    ttft_ms, buf = None, bytearray()
    conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    try:
        conn = conn_cls(host, port, timeout=900)
        conn.request("POST", s.path, body=body, headers=headers)
        resp = conn.getresponse()
        provider = resp.getheader("x-openrouter-provider")
        request_id = resp.getheader("request-id") or resp.getheader("x-request-id")
        while True:
            chunk = resp.read1(65536)
            if not chunk:
                break
            if ttft_ms is None:
                ttft_ms = round((time.perf_counter() - t0) * 1000, 1)
            buf.extend(chunk)
        e2e_ms = round((time.perf_counter() - t0) * 1000, 1)
        conn.close()
        if resp.status != 200:
            rec.update({"status": "infra_error", "subtype": f"http_{resp.status}",
                        "finished_utc": round(time.time(), 3),
                        "error": bytes(buf[:500]).decode("utf-8", "replace")})
            return rec
        parse = parse_anthropic_sse if kind == "anthropic" else parse_openai_sse
        usage, model, stop, reply = parse(buf.decode("utf-8", "replace"))
        gen_ms = round(e2e_ms - ttft_ms, 1) if ttft_ms is not None else None
        out_tok = usage.get("output_tokens", 0) or 0
        total = sum(usage.get(k, 0) or 0 for k in
                    ("input_tokens", "output_tokens",
                     "cache_creation_input_tokens", "cache_read_input_tokens"))
        rec.update({
            "model_id_resolved": model or model_cfg["id"], "provider_effective": provider,
            "finished_utc": round(time.time(), 3), "status": "completed", "stop_reason": stop,
            "time": {"e2e_ms": e2e_ms, "ttft_ms_first_call": ttft_ms, "generation_ms": gen_ms},
            "tokens": {**{k: usage.get(k) for k in
                          ("input_tokens", "output_tokens",
                           "cache_creation_input_tokens", "cache_read_input_tokens")},
                       "total_tokens": total or None},
            "throughput": {"tps_generation": round(out_tok / (gen_ms / 1000), 2) if gen_ms else None,
                           "tps_call": round(out_tok / (e2e_ms / 1000), 2) if e2e_ms else None,
                           "tps_session": None},
            "cost": {"cost_usd_computed": compute_cost(usage, model_cfg["price_per_mtok"]),
                     "cost_usd_harness": None, "cost_delta_pct": None},
            "request_id": request_id, "reply_preview": reply[:200]})
        return rec
    except Exception as exc:  # noqa: BLE001
        rec.update({"status": "infra_error", "subtype": "exception",
                    "finished_utc": round(time.time(), 3), "error": repr(exc)})
        return rec


def aggregate(cases):
    ok = [c for c in cases if c.get("status") == "completed"]
    if not ok:
        return {"completed": 0, "of": len(cases)}

    def field(path):
        vals = []
        for c in ok:
            v = c
            for k in path:
                v = (v or {}).get(k) if isinstance(v, dict) else None
            if isinstance(v, (int, float)):
                vals.append(v)
        return vals

    def stats(vals):
        if not vals:
            return None
        med = statistics.median(vals)
        disp = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        return {"median": round(med, 4), "pstdev": round(disp, 4),
                "cv_pct": round(100 * disp / med, 2) if med else 0.0, "n": len(vals)}

    return {"completed": len(ok), "of": len(cases),
            "ttft_ms": stats(field(["time", "ttft_ms_first_call"])),
            "e2e_ms": stats(field(["time", "e2e_ms"])),
            "tps_generation": stats(field(["throughput", "tps_generation"])),
            "cost_usd_computed": stats(field(["cost", "cost_usd_computed"]))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--models", default=None)
    ap.add_argument("--gateways", default=None)
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    models = json.load(open(args.models or os.path.join(root, "config", "models.json")))
    gateways = json.load(open(args.gateways or os.path.join(root, "config", "gateways.json")))["gateways"]
    if args.model not in models["models"]:
        sys.exit(f"track_a: modelo '{args.model}' ausente")
    model_cfg = models["models"][args.model]
    gw_id = model_cfg.get("gateway", "anthropic")
    if gw_id not in gateways:
        sys.exit(f"track_a: gateway '{gw_id}' do modelo '{args.model}' ausente")
    gateway = gateways[gw_id]

    prompt_path = os.path.join(root, "tasks", args.task, "prompt.md")
    if not os.path.exists(prompt_path):
        sys.exit(f"track_a: prompt ausente: {prompt_path}")
    prompt = open(prompt_path, encoding="utf-8").read().rstrip("\n") + "\n"

    base_url = os.environ.get(gateway.get("base_url_env", ""), "") or gateway["base_url"]
    api_key = os.environ.get(gateway.get("key_env", ""), "")
    run_id = os.environ.get("SHVIA_RUN_ID", "adhoc")

    cases = []
    for rep in range(1, args.reps + 1):
        ids = {"run_id": run_id, "task_id": args.task, "model_alias": args.model,
               "repetition": rep, "case_id": f"{args.task}/{args.model}/A/rep{rep}"}
        rec = run_case(model_cfg, gateway, prompt, api_key, base_url, ids)
        cases.append(rec)
        line = json.dumps(rec, ensure_ascii=False)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        else:
            print(line)
        st = rec.get("status")
        print(f"  rep{rep} [{gw_id}]: {st}"
              + (f" · ttft={rec['time']['ttft_ms_first_call']}ms · US${rec['cost']['cost_usd_computed']}"
                 if st == "completed" else f" · {rec.get('subtype')}: {rec.get('error','')[:80]}"),
              file=sys.stderr)

    print("== agregado Trilha A ==", file=sys.stderr)
    print(json.dumps(aggregate(cases), ensure_ascii=False, indent=1), file=sys.stderr)
    return 0 if aggregate(cases).get("completed") == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
