#!/usr/bin/env python3
"""leb.py — adapter do LEB (AI-BENCHMARK) como fonte de tarefas da Trilha B.

O LEB dá ao modelo `code/` (legado a evoluir) + `manifest.md` (contrato de
superfície pública) + o **enunciado canônico** (PROTOCOL.md §2, neutro). NUNCA
`private/` (matriz-gabarito + probes). A avaliação mecânica roda o
`harness/leb_harness.py` (docker mysql8+php8.4) contra a entrega → exit 2 se
regrediu, 0 se ok, + relatório JSON (probes corrigidas, dificuldade).

Stdlib only. Lê o enunciado do PROTOCOL.md pra não parafrasear (§2 é fixo).
"""
import json
import os
import subprocess
import sys


def canonical_statement(leb_root):
    """Extrai o blockquote do §2 (enunciado canônico) do protocol/PROTOCOL.md."""
    p = os.path.join(leb_root, "protocol", "PROTOCOL.md")
    lines, grab, out = open(p, encoding="utf-8").read().splitlines(), False, []
    for ln in lines:
        if ln.startswith("## 2."):
            grab = True
            continue
        if grab and ln.startswith("## 3"):
            break
        if grab and ln.lstrip().startswith(">"):
            out.append(ln.lstrip()[1:].lstrip())
    txt = "\n".join(out).strip()
    if not txt:
        sys.exit("leb: não achei o enunciado §2 em PROTOCOL.md")
    return txt


def prepare(leb_root, instance):
    """Monta o caso: prompt (enunciado + manifesto) + golden (code/). Guarda contra
    vazar private/. Retorna dict — golden_dir é o que o run.sh --golden monta no WORK."""
    inst = os.path.join(leb_root, "instances", instance)
    code = os.path.join(inst, "code")
    manifest_p = os.path.join(inst, "manifest.md")
    if not os.path.isdir(code):
        sys.exit(f"leb: instância sem code/: {code}")
    if not os.path.exists(manifest_p):
        sys.exit(f"leb: instância sem manifest.md: {manifest_p}")

    # GUARD anti-contaminação: o golden (code/) não pode conter nada de private/
    # (matriz/probes). private/ é irmão de code/, não filho — mas checamos.
    leaked = [f for f in _walk(code)
              if "private" in f.lower() or "matrix" in f.lower() or "probe" in f.lower()]
    if leaked:
        sys.exit(f"leb: ABORT — golden contém artefato de gabarito: {leaked[:3]}")

    manifest = open(manifest_p, encoding="utf-8").read()
    prompt = (canonical_statement(leb_root)
              + "\n\n---\n\n# Manifesto de Superfície Pública (contrato — anexo)\n\n"
              + manifest)
    return {"prompt": prompt, "golden_dir": code, "instance_dir": inst}


def _walk(root):
    out = []
    for d, _, fs in os.walk(root):
        for f in fs:
            out.append(os.path.relpath(os.path.join(d, f), root))
    return out


def verify(leb_root, instance, submission_dir, out_json=None, dry_run=False, timeout=1800):
    """Roda o harness mecânico do LEB (docker) contra a entrega (submission_dir=WORK).
    Retorna {passed(=exit 0, sem regressão), regression, probes_corrigidas, difficulty,
    exit_code, ...}. dry_run=True só devolve o comando (p/ teste sem docker)."""
    inst = os.path.join(leb_root, "instances", instance)
    harness = os.path.join(leb_root, "harness", "leb_harness.py")
    if not os.path.exists(harness):
        return {"passed": None, "note": f"leb_harness ausente: {harness}"}
    if out_json is None and not dry_run:
        out_json = os.path.join(submission_dir, ".leb.mech.json")
    cmd = [sys.executable, harness, "--instance", inst, "--submission", submission_dir]
    if out_json:
        cmd += ["--out", out_json]
    if dry_run:
        return {"dry_run": True, "cmd": cmd, "cwd": leb_root}
    # docker precisa estar no PATH (BENCH_EXTRA_PATH sob run.sh). python3 = sys.executable.
    try:
        p = subprocess.run(cmd, cwd=leb_root, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"passed": None, "note": "python3/docker ausente no PATH sanitizado"}
    except subprocess.TimeoutExpired:
        return {"passed": None, "note": "leb_harness timeout"}
    rc = p.returncode
    report = None
    if out_json and os.path.exists(out_json):
        try:
            report = json.load(open(out_json, encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if report is None:
        try:
            report = json.loads(p.stdout)
        except json.JSONDecodeError:
            pass
    res = {"passed": rc == 0, "exit_code": rc}  # exit 0 = sem regressão (§harness)
    if isinstance(report, dict):
        char = report.get("characterization") or {}
        probes = report.get("probes") or []
        res.update({
            "regression": char.get("regression"),
            "probes_total": len(probes),
            "probes_corrigidas": sum(1 for pr in probes if pr.get("corrigida")),
            "difficulty_corrected": report.get("difficulty_corrected"),
            "pending_judge": report.get("pending_judge"),
        })
    else:
        res["note"] = "relatório JSON ilegível — ver stderr"
        res["stderr_tail"] = (p.stderr or "")[-300:]
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="adapter LEB (prepare/verify)")
    ap.add_argument("cmd", choices=["prepare", "verify"])
    ap.add_argument("--leb-root", default=os.path.expanduser("~/x/AI-BENCHMARK"))
    ap.add_argument("--instance", required=True)
    ap.add_argument("--submission")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "prepare":
        r = prepare(a.leb_root, a.instance)
        print(f"golden: {r['golden_dir']}\n--- prompt ({len(r['prompt'])} chars) ---\n{r['prompt'][:600]}...")
    else:
        print(json.dumps(verify(a.leb_root, a.instance, a.submission or ".",
                                 dry_run=a.dry_run), ensure_ascii=False, indent=2))
