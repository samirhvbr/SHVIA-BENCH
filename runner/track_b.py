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
import glob
import json
import os
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as collect_mod  # noqa: E402
import status as status_mod  # noqa: E402


def transcript_slug(workspace):
    """cwd REAL → nome do diretório em $CLAUDE_CONFIG_DIR/projects/.

    HIPÓTESE A VALIDAR (§6.2/§15). Contra a ÚNICA amostra ao vivo do 2.1.207 que
    temos (run 2026-07-19T20-36-24Z_a06ea3), a regra observada é "todo caractere
    não-alfanumérico vira '-'". A amostra prova '/'→'-' e '_'→'-'; NÃO prova nada
    sobre '.', porque o path da amostra não tinha ponto. Três regras candidatas
    (`[/._]`, `[/_]`, `[^A-Za-z0-9]`) reproduzem a amostra igualmente bem.

    Por isso este slug é só FAST-PATH: quem acha o transcript de verdade é o
    `find_transcript`, por session_id — imune à regra de slug.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(workspace))


def find_transcript(config_dir, workspace, session_id):
    """Localiza o C2. Retorna (path|None, discovery ∈ slug|glob|glob_ambiguous|none).

    O bug que isto corrige: até a 0.6.1 o slug era `cwd.replace('/','-')`, que NÃO
    é a regra do Claude Code (ele também troca '_'). Em TODO run real o cwd era
    `$TMPDIR/...` — e o TMPDIR do macOS contém '_' —, então o C2 NUNCA foi achado:
    subagentes, ferramentas, thinking e pico de contexto saíram `null` em 100% dos
    runs pagos, sem que nada acusasse a perda.

    A correção não é acertar a regra do slug — é não depender dela. O
    `--session-id` é um uuid4 que NÓS geramos, então o NOME do arquivo é único e
    conhecido; achar por ele sobrevive a mudanças de slug entre versões do CC.
    """
    projects = os.path.join(config_dir, "projects")
    fast = os.path.join(projects, transcript_slug(workspace), f"{session_id}.jsonl")
    if os.path.exists(fast):
        return fast, "slug"
    hits = sorted(glob.glob(os.path.join(projects, "*", f"{session_id}.jsonl")))
    if len(hits) == 1:
        return hits[0], "glob"
    if hits:
        return hits[0], "glob_ambiguous"   # não deveria ocorrer com uuid4
    return None, "none"


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
    """Invoca o harness uma vez.

    Retorna `harness_outcome` (∈ status.HARNESS_OUTCOMES) e DELIBERADAMENTE **não**
    retorna `status`: o harness não tem acesso a veredito e não pode arbitrar o
    status do registro. Quem arbitra é `status.resolve_status`, já com o veredito
    do verificador em mãos. Os testes checam a AUSÊNCIA da chave `status` aqui —
    é a garantia estrutural de que o bug de 19/07/2026 não volta por outro caminho.
    """
    session_id = str(uuid.uuid4())
    argv = build_argv(harness, prompt, model_cfg["id"], session_id, opts)
    t0 = time.perf_counter()
    started = time.time()
    outcome, c1, err, anomaly = "ok", None, None, None
    tk = (harness.get("transcript") or {}).get("kind")

    def envelope(**extra):
        base = {"harness_outcome": outcome, "session_id": session_id, "c1": c1,
                "transcript_path": None, "transcript_discovery": "none",
                "transcript_kind": tk, "workspace": workspace,
                "e2e_ms": None, "started_utc": round(started, 3),
                "finished_utc": round(time.time(), 3),
                "error": err, "harness_anomaly": anomaly}
        base.update(extra)
        return base

    try:
        # garante que o harness escreva o transcript no MESMO config dir que
        # o track_b vai ler (senão o C2 some). Idempotente sob run.sh.
        child_env = dict(os.environ, CLAUDE_CONFIG_DIR=config_dir)
        proc = subprocess.run(argv, cwd=workspace, capture_output=True, text=True,
                              timeout=opts["timeout_s"], env=child_env)
        e2e_ms = round((time.perf_counter() - t0) * 1000, 1)
        if proc.returncode != 0 and not proc.stdout.strip():
            outcome, err = "infra_error", (proc.stderr or "")[:500]
        else:
            try:
                c1 = parse_result(harness, proc.stdout)
            except json.JSONDecodeError:
                outcome, err = "infra_error", "result JSON ilegível: " + proc.stdout[:300]
    except subprocess.TimeoutExpired:
        e2e_ms = round((time.perf_counter() - t0) * 1000, 1)
        outcome = "timeout"
    except FileNotFoundError:
        outcome, err = "infra_error", f"harness ausente: {harness['bin']}"
        return envelope()

    # C1 → desfecho de harness. Sem acesso a veredito, por construção (status.py).
    if c1 is not None and outcome == "ok":
        outcome, anomaly = status_mod.classify_c1(c1)

    tpath, disc = (None, "none")
    if tk == "claude-code":
        tpath, disc = find_transcript(config_dir, workspace, session_id)
    return envelope(e2e_ms=e2e_ms, transcript_path=tpath, transcript_discovery=disc)


def run_verify(task_dir, workspace):
    """Roda tasks/<id>/verify.sh contra o workspace (LEB_CODE_DIR = workspace)."""
    vs = os.path.join(task_dir, "verify.sh")
    if not os.path.exists(vs):
        # `applicable:false` = a tarefa NÃO TEM verificador (nada a medir) — é
        # diferente de "o verificador ainda não rodou". O resolve_status distingue
        # os dois: o 1º pode ser `completed`, o 2º vira `pending_verification`.
        return {"passed": None, "exit_code": None, "applicable": False,
                "note": "sem verify.sh"}
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
        # VERIFY SEMPRE, em QUALQUER desfecho de harness. Um run que estourou o
        # orçamento ou morreu de erro de API ainda pode ter entregue trabalho
        # correto — e foi PAGO; o veredito é evidência anexa a todo registro.
        # (Pular o verify fora do caminho feliz teria destruído justamente a
        # evidência `passed:true, probes 2/4` que revelou o bug do incidente.)
        # Só a ARBITRAGEM muda: resolve_status faz o desfecho de harness mandar.
        if args.leb_instance:
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
        # o exit code responde "esta rep produziu uma MEDIÇÃO utilizável?" — isto é,
        # o desfecho do harness. O veredito pode chegar depois (patch pós-run), e um
        # `pending_verification` legítimo não é falha do track_b.
        completed += hr["harness_outcome"] == "ok"
        print(f"  rep{rep}: {rec.get('status')} (harness={hr['harness_outcome']})"
              f" · turns={(rec.get('agents') or {}).get('main_agent_turns')}"
              f" · US${(rec.get('cost') or {}).get('cost_usd_computed')}"
              f" · verify={(rec.get('verification') or {}).get('passed')}"
              f" · C2={hr.get('transcript_discovery')}", file=sys.stderr)
        if hr.get("harness_anomaly"):
            print(f"        anomalia do harness (não é veredito): {hr['harness_anomaly']}",
                  file=sys.stderr)
    return 0 if completed == args.reps else 1


if __name__ == "__main__":
    sys.exit(main())
