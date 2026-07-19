#!/usr/bin/env python3
"""track_b.py — Trilha B (modelo + harness), spec §6. Stdlib only.

Dirige um harness de agente (Claude Code, `claude -p`) num workspace efêmero, em
modo não-interativo, até concluir ou bater no limite. Roda DENTRO do run.sh (env
sanitizado; cwd = WORK; CLAUDE_CONFIG_DIR = sandbox). Captura o result JSON (C1),
localiza o transcript (C2) e delega a fusão das 3 camadas ao collect.py.

Uso (dentro do run.sh):
  runner/run.sh --task T-001 --golden tasks/T-001/golden -- \
    python3 runner/track_b.py --model M-opus48 --task T-001 \
      --out "$RUN_DIR/results.jsonl" --proxy-log "$RUN_DIR/proxy.jsonl"

Flags/campos do Claude Code confirmados no 2.1.207 (config/harness-matrix.md).
`--claude-bin` permite injetar um harness fake p/ teste offline.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as collect_mod  # noqa: E402


def transcript_path(config_dir, workspace, session_id):
    """$CLAUDE_CONFIG_DIR/projects/<cwd-slug>/<session>.jsonl (confirmado 2.1.207).
    O slug é o caminho REAL do cwd com '/' → '-'."""
    real = os.path.realpath(workspace)
    slug = real.replace("/", "-")
    return os.path.join(config_dir, "projects", slug, f"{session_id}.jsonl")


def build_argv_claude_code(bin_, prompt, model_id, session_id, opts):
    argv = [bin_, "-p", prompt,
            "--model", model_id,
            "--output-format", "json",
            "--session-id", session_id,
            "--effort", opts["effort"],
            "--max-budget-usd", str(opts["budget_usd"]),
            "--max-turns", str(opts["max_turns"]),
            "--permission-mode", opts["permission_mode"],
            "--strict-mcp-config",
            "--mcp-config", opts["mcp_config"]]
    if opts.get("settings"):
        argv += ["--settings", opts["settings"]]
    return argv


def build_argv(harness, prompt, model_id, session_id, opts):
    """Dispatch por adapter (result_format). Só claude-code-json implementado."""
    rf = harness.get("result_format")
    if rf == "claude-code-json":
        return build_argv_claude_code(harness["bin"], prompt, model_id, session_id, opts)
    raise NotImplementedError(
        f"harness '{harness.get('name', '?')}' (result_format={rf}) sem adapter — "
        f"valide o surface (config/harness-matrix.md) e implemente")


def parse_result(harness, stdout):
    """Result object → C1. Só claude-code-json (JSON único) implementado."""
    if harness.get("result_format") == "claude-code-json":
        return json.loads(stdout)
    raise NotImplementedError(f"parse_result: sem adapter p/ {harness.get('result_format')}")


def run_harness(harness, model_cfg, prompt, workspace, config_dir, opts):
    """Invoca o harness uma vez. Retorna dict com C1 (result), paths e timing/status."""
    session_id = str(uuid.uuid4())
    argv = build_argv(harness, prompt, model_cfg["id"], session_id, opts)
    t0 = time.perf_counter()
    started = time.time()
    status, c1, err = "completed", None, None
    try:
        # garante que o harness escreva o transcript no MESMO config dir que
        # o track_b vai ler (senão o C2 some). Idempotente sob run.sh.
        child_env = dict(os.environ, CLAUDE_CONFIG_DIR=config_dir)
        proc = subprocess.run(argv, cwd=workspace, capture_output=True, text=True,
                              timeout=opts["timeout_s"], env=child_env)
        e2e_ms = round((time.perf_counter() - t0) * 1000, 1)
        if proc.returncode != 0 and not proc.stdout.strip():
            status, err = "infra_error", (proc.stderr or "")[:500]
        else:
            try:
                c1 = parse_result(harness, proc.stdout)
            except json.JSONDecodeError:
                status, err = "infra_error", "result JSON ilegível: " + proc.stdout[:300]
    except subprocess.TimeoutExpired:
        e2e_ms = round((time.perf_counter() - t0) * 1000, 1)
        status = "timeout"
    except FileNotFoundError:
        return {"status": "infra_error", "error": f"harness ausente: {harness['bin']}"}

    # limites → status (subtype REAL do 2.1.207 só confirmado p/ 'success'; ver §matrix)
    if c1 and status == "completed":
        sub = c1.get("subtype")
        if c1.get("is_error") or (sub and sub != "success"):
            status = {"error_max_budget_usd": "budget_exceeded",
                      "error_max_turns": "max_turns"}.get(sub, "failed_verification")
    tk = (harness.get("transcript") or {}).get("kind")
    tpath = transcript_path(config_dir, workspace, session_id) if tk == "claude-code" else None
    return {
        "status": status, "session_id": session_id, "c1": c1,
        "transcript_path": tpath, "transcript_kind": tk,
        "workspace": workspace, "e2e_ms": e2e_ms,
        "started_utc": round(started, 3), "finished_utc": round(time.time(), 3),
        "error": err,
    }


def run_verify(task_dir, workspace):
    """Roda tasks/<id>/verify.sh contra o workspace (LEB_CODE_DIR = workspace)."""
    vs = os.path.join(task_dir, "verify.sh")
    if not os.path.exists(vs):
        return {"passed": None, "exit_code": None, "note": "sem verify.sh"}
    env = dict(os.environ, LEB_CODE_DIR=workspace, WORK=workspace)
    try:
        p = subprocess.run(["bash", vs], cwd=workspace, env=env,
                          capture_output=True, text=True, timeout=600)
        return {"passed": p.returncode == 0, "exit_code": p.returncode}
    except subprocess.TimeoutExpired:
        return {"passed": False, "exit_code": None, "note": "verify timeout"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--rep", type=int, default=None,
                    help="rótulo do nº da rep (p/ o driver rodar reps independentes com --reps 1)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--models", default=None)
    ap.add_argument("--proxy-log", default=os.environ.get("SHVIA_PROXY_LOG"))
    ap.add_argument("--harness", default="claude-code", help="id em config/harnesses.json")
    ap.add_argument("--harnesses", default=None)
    ap.add_argument("--bin", default=None, help="override do binário do harness (ex.: fake p/ teste)")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("TIMEOUT_S", "900")))
    ap.add_argument("--budget", type=float, default=float(os.environ.get("BUDGET_PER_CASE_USD", "2.0")))
    ap.add_argument("--max-turns", type=int, default=int(os.environ.get("MAX_TURNS", "30")))
    ap.add_argument("--effort", default=os.environ.get("CLAUDE_CODE_EFFORT_LEVEL", "high"))
    ap.add_argument("--permission-mode", default="acceptEdits")
    ap.add_argument("--leb-root", default=os.path.expanduser("~/x/AI-BENCHMARK"),
                    help="raiz do LEB/AI-BENCHMARK (fonte de tarefas)")
    ap.add_argument("--leb-instance", default=None,
                    help="id da instância LEB (ex.: LEB-100-A). Se setado, prompt e verify vêm do LEB.")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(args.models or os.path.join(root, "config", "models.json")))
    if args.model not in cfg["models"]:
        sys.exit(f"track_b: modelo '{args.model}' ausente")
    model_cfg = cfg["models"][args.model]

    hs = json.load(open(args.harnesses or os.path.join(root, "config", "harnesses.json")))["harnesses"]
    if args.harness not in hs:
        sys.exit(f"track_b: harness '{args.harness}' ausente em harnesses.json")
    harness = dict(hs[args.harness], name=args.harness)
    if args.bin:
        harness["bin"] = args.bin
    if harness.get("result_format") != "claude-code-json":
        sys.exit(f"track_b: harness '{args.harness}' sem adapter implementado — valide o "
                 f"surface (config/harness-matrix.md) e implemente o adapter antes de rodar.")

    if args.leb_instance:
        import leb  # fonte de tarefas = LEB (o golden já foi montado pelo run.sh --golden)
        prompt = leb.prepare(args.leb_root, args.leb_instance)["prompt"]
        task_dir = None
    else:
        task_dir = os.path.join(root, "tasks", args.task)
        prompt_path = os.path.join(task_dir, "prompt.md")
        if not os.path.exists(prompt_path):
            sys.exit(f"track_b: prompt ausente: {prompt_path}")
        prompt = open(prompt_path, encoding="utf-8").read()

    workspace = os.getcwd()  # o run.sh já fez cd pro WORK efêmero
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
    opts = {"effort": args.effort, "budget_usd": args.budget, "max_turns": args.max_turns,
            "permission_mode": args.permission_mode, "timeout_s": args.timeout,
            "mcp_config": os.path.join(root, "config", "mcp.empty.json"),
            "settings": os.path.join(config_dir, "settings.json")}
    run_id = os.environ.get("SHVIA_RUN_ID", "adhoc")

    completed = 0
    for rep in range(1, args.reps + 1):
        hr = run_harness(harness, model_cfg, prompt, workspace, config_dir, opts)
        if hr["status"] not in ("completed", "failed_verification"):
            verify = {"passed": None, "exit_code": None}
        elif args.leb_instance:
            # verify do LEB roda docker → NÃO pode ser aqui (env -i quebra o
            # `docker compose`, que precisa do HOME real). Fica PENDENTE; o driver
            # (campaign_leb.sh) roda o leb.verify pós-run, no ambiente completo.
            verify = {"passed": None, "pending": "leb", "workspace": workspace,
                      "leb_instance": args.leb_instance}
        else:
            verify = run_verify(task_dir, workspace)
        rep_lbl = args.rep if args.rep is not None else rep
        ids = {"run_id": run_id, "task_id": args.task, "model_alias": args.model,
               "repetition": rep_lbl, "case_id": f"{args.task}/{args.model}/B/rep{rep_lbl}"}
        rec = collect_mod.collect(hr, args.proxy_log, model_cfg, ids, verify)
        line = json.dumps(rec, ensure_ascii=False)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        else:
            print(line)
        completed += rec.get("status") == "completed"
        print(f"  rep{rep}: {rec.get('status')} · turns={(rec.get('agents') or {}).get('main_agent_turns')}"
              f" · US${(rec.get('cost') or {}).get('cost_usd_computed')}"
              f" · verify={(rec.get('verification') or {}).get('passed')}", file=sys.stderr)
    return 0 if completed == args.reps else 1


if __name__ == "__main__":
    sys.exit(main())
