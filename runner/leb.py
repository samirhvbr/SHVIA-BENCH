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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status as status_mod  # noqa: E402


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


def patch_results(leb_root, results_path):
    """Pós-run, no AMBIENTE COMPLETO (fora do env -i): para cada linha com
    verify.pending=='leb', roda o verify do LEB (docker) contra o workspace e
    patcha `verification` + `status`. Reescreve o results.jsonl ATOMICAMENTE."""
    recs = [json.loads(l) for l in open(results_path, encoding="utf-8") if l.strip()]
    done = 0
    for rec in recs:
        v = rec.get("verification") or {}
        if v.get("pending") != "leb":
            continue
        ws, inst = v.get("workspace"), v.get("leb_instance")
        if not inst:
            # guard simétrico ao do workspace: sem instância, o verify nem monta o
            # caminho (`os.path.join(..., None)` → TypeError abortaria o patch das
            # DEMAIS reps pendentes, e o campaign morreria em `set -e`).
            rec["verification"] = {**v, "note": "registro sem leb_instance — verify não roda"}
            continue
        if not ws or not os.path.isdir(ws):
            # o verify NÃO rodou ⇒ continua PENDENTE. Perder o workspace não é um
            # veredito sobre a entrega, e não pode virar um.
            rec["verification"] = {**v, "note": f"workspace sumiu: {ws}"}
            continue
        res = verify(leb_root, inst, ws)
        # FUNDE, não substitui. Os retornos SOFT do `verify` (leb_harness ausente,
        # docker fora do PATH, timeout) não trazem `pending`/`workspace`/`leb_instance`;
        # substituir o dict apagaria o ponteiro do workspace PAGO e o registro nunca
        # mais poderia ser reverificado — uma falha transitória (Docker Desktop
        # parado no pós-run) viraria perda PERMANENTE do veredito de reps pagas.
        merged = {**v, **res}
        if res.get("passed") is None:
            merged["pending"] = "leb"    # nada foi medido ⇒ segue pendente, reverificável
        else:
            merged.pop("pending", None)  # veredito obtido
            done += 1                    # só conta o que FOI de fato verificado
        rec["verification"] = res = merged
        # Mesma combinadora do track_b ⇒ PROMOVE e REBAIXA pelo mesmo mecanismo.
        # Antes da 0.7.0 este ponto só sabia rebaixar (`passed is False` →
        # failed_verification) e nunca restaurava: por isso a rep1 do Opus seguiu
        # `failed_verification` mesmo com `passed:true`.
        # E o desfecho do harness continua mandando: uma rep que morreu de erro de
        # API mas passou o verify NÃO vira `completed` limpo — o trabalho pode
        # estar certo, mas a MEDIÇÃO (tempo/custo/turnos) está truncada, e é a
        # medição que o benchmark publica. O veredito fica anexo em `verification`.
        rec["status"] = status_mod.resolve_status(
            status_mod.harness_outcome_of_record(rec), res)
    # Escrita ATÔMICA: o results.jsonl é evidência PAGA. Uma exceção no meio do
    # laço de escrita truncava o arquivo in-place e destruía reps já pagas.
    tmp = results_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for rec in recs:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, results_path)
    return done


def reclassify_results(results_path, dry_run=False):
    """Reaplica a ARBITRAGEM sobre registros JÁ gravados, sem rodar verify de novo.

    Existe para reparar registros anteriores à 0.7.0, em que o HARNESS emitia o
    `status` e um erro de API podia ter virado `failed_verification` (incidente de
    19/07/2026). Sem isto, o `patch-results` não alcança esses registros — o filtro
    de entrada dele é `verification.pending == "leb"`, e um registro já patchado
    não tem mais `pending`; a reclassificação ficaria como código inalcançável.

    Não inventa dado: só recombina o que já está no próprio registro. Devolve a
    lista de mudanças (para o operador conferir ANTES de gravar, com --dry-run).
    """
    recs = [json.loads(l) for l in open(results_path, encoding="utf-8") if l.strip()]
    changes = []
    for rec in recs:
        # Só Trilha B. Um registro da Trilha A não tem os dois eixos (nem `subtype`,
        # nem `verification`): passá-lo pela combinadora reescreveria `completed`
        # como `infra_error`, inventando uma falha que nunca houve. A ferramenta de
        # reparo não pode ser ela própria uma fonte de dano.
        if rec.get("track") != "B":
            continue
        # `refused` / `invalid_isolation` são terminais e não deriváveis do C1 —
        # a combinadora não sabe produzi-los e os apagaria.
        if rec.get("status") in ("refused", "invalid_isolation"):
            continue
        old_status = rec.get("status")
        old_outcome = rec.get("harness_outcome")
        outcome = status_mod.harness_outcome_of_record(rec)
        new_status = status_mod.resolve_status(outcome, rec.get("verification") or {})
        if old_status == new_status and old_outcome == outcome:
            continue
        changes.append({"case_id": rec.get("case_id"),
                        "status": [old_status, new_status],
                        "harness_outcome": [old_outcome, outcome]})
        if not dry_run:
            rec["status"] = new_status
            rec["harness_outcome"] = outcome
            # Proveniência: um registro pago reescrito pós-hoc precisa dizer que
            # foi reescrito, e a partir de quê. Auditabilidade vale para nós também.
            rec.setdefault("_repairs", []).append(
                {"by": "leb.py reclassify", "status": [old_status, new_status]})
    if changes and not dry_run:
        tmp = results_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for rec in recs:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, results_path)
    return changes


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="adapter LEB (prepare/verify/patch-results/reclassify). "
                    "`reclassify` só toca registros da Trilha B; use --dry-run antes.")
    ap.add_argument("cmd", choices=["prepare", "verify", "patch-results", "reclassify"])
    ap.add_argument("--leb-root", default=os.path.expanduser("~/x/AI-BENCHMARK"))
    ap.add_argument("--instance")
    ap.add_argument("--submission")
    ap.add_argument("--results")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "prepare":
        r = prepare(a.leb_root, a.instance)
        print(f"golden: {r['golden_dir']}\n--- prompt ({len(r['prompt'])} chars) ---\n{r['prompt'][:600]}...")
    elif a.cmd == "verify":
        print(json.dumps(verify(a.leb_root, a.instance, a.submission or ".",
                                 dry_run=a.dry_run), ensure_ascii=False, indent=2))
    elif a.cmd == "patch-results":
        n = patch_results(a.leb_root, a.results)
        print(f"leb: {n} caso(s) verificado(s) → {a.results}")
    else:  # reclassify
        chs = reclassify_results(a.results, dry_run=a.dry_run)
        modo = "(dry-run, nada gravado)" if a.dry_run else "→ gravado"
        print(f"leb: {len(chs)} registro(s) reclassificado(s) {modo}")
        for c in chs:
            print(f"  {c['case_id']}: status {c['status'][0]} → {c['status'][1]}"
                  f" · harness_outcome {c['harness_outcome'][0]} → {c['harness_outcome'][1]}")
