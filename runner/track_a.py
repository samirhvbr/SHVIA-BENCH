#!/usr/bin/env python3
"""track_a.py — Trilha A (modelo puro), spec §5. Stdlib only.

Uma requisição, sem ferramentas, sem loop de agente, sem filesystem: a tarefa
inteira no prompt. Mede a capacidade bruta do modelo em resposta única. Roda
pelo run.sh (env sanitizado; ANTHROPIC_BASE_URL aponta pro proxy) e emite uma
linha results.jsonl por repetição (schema results.schema.json).

⚠️ Descoberta que corrige a spec (não confie na §5.2 cegamente): modelos
Anthropic 4.6+ (Opus 4.8, Sonnet 5, Fable 5, Haiku 4.5) REJEITAM
temperature/top_p/top_k e budget_tokens com 400. O "parâmetro congelado" da
Trilha A nesses modelos é output_config.effort (+ thinking), idêntico por
campanha (V17) — NUNCA sampling. track_a jamais envia temperature/top_p.

Uso (dentro do run.sh):
  runner/run.sh --task T-000-noop -- \
    python3 runner/track_a.py --model M-opus48 --task T-000-noop --reps 5 \
      --out "$RUN_DIR/results.jsonl"
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


def parse_anthropic_sse(text):
    """Merge usage/model/stop_reason/reply-text de um stream SSE da Messages API."""
    usage, model, stop_reason, parts = {}, None, None, []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") or {}
        if isinstance(msg, dict):
            model = model or msg.get("model")
            if isinstance(msg.get("usage"), dict):
                usage.update({k: v for k, v in msg["usage"].items() if v is not None})
        if isinstance(obj.get("usage"), dict):
            usage.update({k: v for k, v in obj["usage"].items() if v is not None})
        delta = obj.get("delta") or {}
        if isinstance(delta, dict):
            if delta.get("stop_reason"):
                stop_reason = delta["stop_reason"]
            if delta.get("type") == "text_delta" and delta.get("text"):
                parts.append(delta["text"])
    return usage, model, stop_reason, "".join(parts)


def compute_cost(usage, price):
    tok = lambda k: usage.get(k, 0) or 0
    return round((
        tok("input_tokens") * price.get("input", 0)
        + tok("output_tokens") * price.get("output", 0)
        + tok("cache_creation_input_tokens") * price.get("cache_write", 0)
        + tok("cache_read_input_tokens") * price.get("cache_read", 0)
    ) / 1_000_000, 8)


def build_body(model_cfg, prompt):
    """Corpo Anthropic — SEM sampling params (400 nos modelos 4.6+)."""
    params = model_cfg.get("params", {})
    body = {
        "model": model_cfg["id"],
        "max_tokens": model_cfg.get("max_tokens", 8192),
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }
    if params.get("effort"):
        body["output_config"] = {"effort": params["effort"]}
    if params.get("thinking") == "adaptive":
        body["thinking"] = {"type": "adaptive"}
    return body


def run_case(model_cfg, gateway, prompt, api_key, base_url, ids):
    """Uma requisição streaming. Retorna uma linha no formato results.schema.json (Trilha A)."""
    body = json.dumps(build_body(model_cfg, prompt)).encode()
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    s = urlsplit(base_url.rstrip("/") + gateway["path"])
    scheme = s.scheme or "https"
    host = s.hostname
    port = s.port or (443 if scheme == "https" else 80)
    headers = {
        "content-type": "application/json",
        "anthropic-version": gateway.get("version", "2023-06-01"),
        "x-api-key": api_key or "",
    }
    rec = {
        "run_id": ids["run_id"], "case_id": ids["case_id"], "task_id": ids["task_id"],
        "model_alias": ids["model_alias"], "model_id_resolved": None,
        "track": "A", "repetition": ids["repetition"], "session_id": None,
        "provider_effective": None, "started_utc": round(time.time(), 3),
        "prompt_sha256": prompt_sha,
        # Trilha A: estes grupos não se aplicam (§10.3) — null, nunca 0.
        "agents": None, "tools": None, "autonomy": None,
    }
    t0 = time.perf_counter()
    ttft_ms = None
    buf = bytearray()
    conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    try:
        conn = conn_cls(host, port, timeout=900)
        conn.request("POST", s.path, body=body, headers=headers)
        resp = conn.getresponse()
        provider = resp.getheader("x-openrouter-provider")  # útil quando via agregador
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

        usage, model, stop_reason, reply = parse_anthropic_sse(buf.decode("utf-8", "replace"))
        gen_ms = round(e2e_ms - ttft_ms, 1) if ttft_ms is not None else None
        out_tok = usage.get("output_tokens", 0) or 0
        total = sum(usage.get(k, 0) or 0 for k in
                    ("input_tokens", "output_tokens",
                     "cache_creation_input_tokens", "cache_read_input_tokens"))
        rec.update({
            "model_id_resolved": model or model_cfg["id"],
            "provider_effective": provider,
            "finished_utc": round(time.time(), 3),
            "status": "completed", "stop_reason": stop_reason,
            "time": {"e2e_ms": e2e_ms, "ttft_ms_first_call": ttft_ms, "generation_ms": gen_ms},
            "tokens": {**{k: usage.get(k) for k in
                          ("input_tokens", "output_tokens",
                           "cache_creation_input_tokens", "cache_read_input_tokens")},
                       "total_tokens": total or None},
            "throughput": {
                "tps_generation": round(out_tok / (gen_ms / 1000), 2) if gen_ms else None,
                "tps_call": round(out_tok / (e2e_ms / 1000), 2) if e2e_ms else None,
                "tps_session": None,
            },
            "cost": {"cost_usd_computed": compute_cost(usage, model_cfg["price_per_mtok"]),
                     "cost_usd_harness": None, "cost_delta_pct": None},
            "request_id": request_id,
            "reply_preview": reply[:200],
        })
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
        cv = round(100 * disp / med, 2) if med else 0.0
        return {"median": round(med, 4), "pstdev": round(disp, 4), "cv_pct": cv, "n": len(vals)}

    return {
        "completed": len(ok), "of": len(cases),
        "ttft_ms": stats(field(["time", "ttft_ms_first_call"])),
        "e2e_ms": stats(field(["time", "e2e_ms"])),
        "tps_generation": stats(field(["throughput", "tps_generation"])),
        "cost_usd_computed": stats(field(["cost", "cost_usd_computed"])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="alias em models.json (ex.: M-opus48)")
    ap.add_argument("--task", required=True, help="id da tarefa (lê tasks/<id>/prompt.md)")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out", default=None, help="results.jsonl (append). Default: stdout")
    ap.add_argument("--models", default=None, help="caminho do models.json")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    models_path = args.models or os.path.join(root, "config", "models.json")
    cfg = json.load(open(models_path, encoding="utf-8"))
    gateway = cfg["gateway"]
    if args.model not in cfg["models"]:
        sys.exit(f"track_a: modelo '{args.model}' não está em {models_path}")
    model_cfg = cfg["models"][args.model]

    prompt_path = os.path.join(root, "tasks", args.task, "prompt.md")
    prompt = open(prompt_path, encoding="utf-8").read().rstrip("\n") + "\n" \
        if os.path.exists(prompt_path) else None
    if prompt is None:
        sys.exit(f"track_a: prompt não encontrado: {prompt_path}")

    base_url = os.environ.get(gateway.get("base_url_env", ""), "") or gateway["base_url_default"]
    api_key = os.environ.get(gateway.get("key_env", "ANTHROPIC_API_KEY"), "")
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
        print(f"  rep{rep}: {st}"
              + (f" · ttft={rec['time']['ttft_ms_first_call']}ms"
                 f" · e2e={rec['time']['e2e_ms']}ms"
                 f" · out={rec['tokens'].get('output_tokens')}tok"
                 f" · US${rec['cost']['cost_usd_computed']}" if st == "completed"
                 else f" · {rec.get('subtype')}: {rec.get('error','')[:80]}"),
              file=sys.stderr)

    agg = aggregate(cases)
    print("== agregado Trilha A ==", file=sys.stderr)
    print(json.dumps(agg, ensure_ascii=False, indent=1), file=sys.stderr)
    return 0 if agg.get("completed") == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
