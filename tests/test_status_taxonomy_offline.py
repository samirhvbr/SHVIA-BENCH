#!/usr/bin/env python3
"""Offline: taxonomia de status, reparo pós-run e guards do driver.

Cobre o INCIDENTE de 19/07/2026, em que um erro transitório de API (`is_error`
com `subtype:"success"` e `terminal_reason:"api_error"`) foi registrado como
`failed_verification` — um juízo sobre a entrega do modelo. A rep1 do Opus custou
US$0,94, PASSOU no verify do LEB (2/4 probes, sem regressão) e ficou marcada como
reprovada; e o `patch_results` só sabia rebaixar, nunca restaurar.

O que estes testes travam:
  (a) nenhum desfecho de HARNESS alcança `failed_verification` — a garantia é
      estrutural, não uma linha de `if` que alguém pode reintroduzir;
  (b) o reparo pós-run promove E rebaixa, e uma falha transitória do verificador
      não pode apagar o ponteiro do workspace PAGO;
  (c) o driver não destrói nem sobrescreve resultado já pago;
  (d) o C2 é achado por session_id, mesmo quando a regra de slug do harness muda.

Stdlib only. Sem rede, sem chave, sem docker.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))
import status as status_mod   # noqa: E402
import track_b                # noqa: E402
import leb                    # noqa: E402
import collect as collect_mod  # noqa: E402

FAKE = os.path.join(ROOT, "tests", "fake_claude.py")
MODEL_CFG = {"id": "dummy-model-1", "max_tokens": 8192,
             "price_per_mtok": {"input": 5.0, "output": 25.0,
                                "cache_write": 6.25, "cache_read": 0.5}}


def run_fake(scenario, env_extra=None, ws_name="work"):
    """Roda o harness fake num cenário e devolve o envelope do run_harness.

    `ws_name` permite forçar não-alfanuméricos no caminho do workspace: é o que faz
    a regra de slug `legacy` (só '/') divergir da `real` (todo não-alfanumérico) e,
    portanto, o que exercita de fato o fallback por session_id.
    """
    tmp = tempfile.mkdtemp(prefix="sb-tax-")
    workspace = os.path.join(tmp, ws_name); os.makedirs(workspace)
    config_dir = os.path.join(tmp, "config"); os.makedirs(config_dir)
    opts = {"effort": "high", "budget_usd": 2.0, "max_turns": 30,
            "permission_mode": "acceptEdits", "timeout_s": 60,
            "mcp_config": os.path.join(ROOT, "config", "mcp.empty.json"),
            "settings": os.path.join(config_dir, "settings.json")}
    harness = {"name": "claude-code", "bin": FAKE, "result_format": "claude-code-json",
               "transcript": {"kind": "claude-code"}}
    saved = dict(os.environ)
    os.environ["FAKE_CLAUDE_SCENARIO"] = scenario
    os.environ.update(env_extra or {})
    try:
        return track_b.run_harness(harness, MODEL_CFG, "corrija o bug",
                                   workspace, config_dir, opts)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def rec_pendente(case_id, workspace, instance="LEB-100-A", **extra):
    """Registro como o track_b grava para o LEB: veredito DIFERIDO pro pós-run."""
    r = {"case_id": case_id, "status": "pending_verification", "harness_outcome": "ok",
         "verification": {"passed": None, "pending": "leb", "workspace": workspace,
                          "leb_instance": instance}}
    r.update(extra)
    return r


def escreve_jsonl(path, recs):
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    checks = {}

    # ---------- (a) taxonomia: desfecho de harness nunca vira veredito ----------
    esperado = {
        "success": "ok",
        "api_error": "infra_error",        # O CASO DO INCIDENTE
        "budget": "budget_exceeded",
        "turns": "max_turns",
        "unknown_subtype": "infra_error",  # não sei medir ⇒ conservador, não punitivo
        "garbage_json": "infra_error",
        "nonzero_exit": "infra_error",
    }
    envelopes = {}
    for cen, esp in esperado.items():
        hr = run_fake(cen)
        envelopes[cen] = hr
        checks[f"cenário {cen} → harness_outcome {esp}"] = hr["harness_outcome"] == esp
        checks[f"cenário {cen}: run_harness NÃO emite status"] = "status" not in hr

    # a garantia estrutural: NENHUM cenário de harness alcança o veredito
    checks["NENHUM desfecho de harness alcança failed_verification"] = all(
        hr["harness_outcome"] != "failed_verification" for hr in envelopes.values())
    checks["desfechos ∈ HARNESS_OUTCOMES"] = all(
        hr["harness_outcome"] in status_mod.HARNESS_OUTCOMES for hr in envelopes.values())

    # o caso do incidente carrega a anomalia CRUA (evidência, não fallback mudo)
    anom = envelopes["api_error"].get("harness_anomaly") or {}
    checks["api_error anexa a anomalia crua"] = (
        anom.get("subtype") == "success" and anom.get("terminal_reason") == "api_error"
        and anom.get("is_error") is True)

    # arbitragem: erro de API + verify que PASSOU não vira `completed` limpo — a
    # medição está truncada, e é a medição que o benchmark publica.
    # Valor DERIVADO do envelope real (não a string literal): assim o check cobre a
    # fiação run_harness→resolve_status, não só a função isolada.
    checks["api_error + verify passou → infra_error (não completed)"] = (
        status_mod.resolve_status(envelopes["api_error"]["harness_outcome"],
                                  {"passed": True}) == "infra_error")

    # A FIAÇÃO de verdade: o registro que o collect grava. Sem este check, um
    # `resolve_status("ok", verify)` hardcoded no collect passa a suíte inteira e
    # produz o FALSO POSITIVO simétrico ao incidente — erro de API publicado como
    # `completed`. No caminho `tasks/` (sem patch pós-run) o dano é permanente.
    ids_fio = {"run_id": "t", "task_id": "T-x", "model_alias": "M-dummy",
               "repetition": 1, "case_id": "T-x/M-dummy/B/rep1"}
    rec_fio = collect_mod.collect(envelopes["api_error"], None, MODEL_CFG, ids_fio,
                                  {"passed": True})
    checks["collect: desfecho ruim manda no status do REGISTRO"] = rec_fio["status"] == "infra_error"
    checks["collect: registro carrega o harness_outcome"] = rec_fio["harness_outcome"] == "infra_error"
    checks["collect: veredito aprovado fica anexo como evidência"] = (
        rec_fio["verification"]["passed"] is True)
    # envelope sem desfecho é bug de programação — estoura alto, não adivinha `ok`
    try:
        collect_mod.collect({"c1": None}, None, MODEL_CFG, ids_fio, {"passed": True})
        checks["collect: envelope sem harness_outcome estoura (não assume ok)"] = False
    except KeyError:
        checks["collect: envelope sem harness_outcome estoura (não assume ok)"] = True

    # a arbitragem recusa desfecho inválido em vez de inventar um status
    try:
        status_mod.resolve_status("nao_existe", {"passed": True})
        checks["resolve_status recusa desfecho inválido"] = False
    except ValueError:
        checks["resolve_status recusa desfecho inválido"] = True
    checks["ok + verify passou → completed"] = (
        status_mod.resolve_status("ok", {"passed": True}) == "completed")
    checks["ok + verify reprovou → failed_verification"] = (
        status_mod.resolve_status("ok", {"passed": False}) == "failed_verification")
    checks["ok + veredito não medido → pending_verification"] = (
        status_mod.resolve_status("ok", {"passed": None}) == "pending_verification")
    checks["ok + tarefa sem verificador → completed"] = (
        status_mod.resolve_status("ok", {"passed": None, "applicable": False}) == "completed")
    checks["status emitidos ∈ enum do schema"] = all(
        status_mod.resolve_status(o, v) in status_mod.STATUS_ENUM
        for o in status_mod.HARNESS_OUTCOMES
        for v in ({"passed": True}, {"passed": False}, {"passed": None}))

    # registro LEGADO (< 0.7.0): reclassificado pela MESMA política, sem `is_error`
    legado_opus = {"status": "failed_verification", "subtype": "success",
                   "terminal_reason": "api_error", "stop_reason": "stop_sequence"}
    checks["registro legado do incidente → infra_error"] = (
        status_mod.harness_outcome_of_record(legado_opus) == "infra_error")
    checks["legado com subtype desconhecido NÃO é promovido a ok"] = (
        status_mod.harness_outcome_of_record({"status": "completed",
                                              "subtype": "error_during_execution"})
        == "infra_error")

    # ---------- (b) reparo pós-run: promove, rebaixa e não perde evidência ----------
    tmp = tempfile.mkdtemp(prefix="sb-patch-")
    ws_vivo = os.path.join(tmp, "ws"); os.makedirs(ws_vivo)
    res_path = os.path.join(tmp, "results.jsonl")

    original_verify = leb.verify
    try:
        # b1: verify PASSA ⇒ o status tem de ser RESTAURADO (o 2º bug do incidente)
        leb.verify = lambda *a, **k: {"passed": True, "exit_code": 0, "regression": False,
                                      "probes_total": 4, "probes_corrigidas": 2}
        escreve_jsonl(res_path, [rec_pendente("c/promove", ws_vivo)])
        n = leb.patch_results(os.path.join(tmp, "leb-root"), res_path)
        r = [json.loads(l) for l in open(res_path)][0]
        checks["patch: verify passou ⇒ status promovido a completed"] = r["status"] == "completed"
        checks["patch: conta 1 verificação real"] = n == 1
        checks["patch: `pending` some quando há veredito"] = "pending" not in r["verification"]

        # b2: verify REPROVA ⇒ rebaixa (o único emissor legítimo do veredito)
        leb.verify = lambda *a, **k: {"passed": False, "exit_code": 2, "regression": True}
        escreve_jsonl(res_path, [rec_pendente("c/rebaixa", ws_vivo)])
        leb.patch_results(os.path.join(tmp, "leb-root"), res_path)
        r = [json.loads(l) for l in open(res_path)][0]
        checks["patch: verify reprovou ⇒ failed_verification"] = r["status"] == "failed_verification"

        # b3: desfecho de harness ruim MANDA, mesmo com verify passando
        leb.verify = lambda *a, **k: {"passed": True, "exit_code": 0}
        escreve_jsonl(res_path, [rec_pendente("c/infra", ws_vivo, harness_outcome="infra_error")])
        leb.patch_results(os.path.join(tmp, "leb-root"), res_path)
        r = [json.loads(l) for l in open(res_path)][0]
        checks["patch: harness_outcome ruim manda no status"] = r["status"] == "infra_error"
        checks["patch: veredito fica anexo como evidência"] = r["verification"]["passed"] is True

        # b4: falha TRANSITÓRIA do verificador (docker parado) NÃO pode apagar o
        # ponteiro do workspace pago — senão a rep vira não-reverificável para sempre
        leb.verify = lambda *a, **k: {"passed": None, "note": "docker ausente no PATH"}
        escreve_jsonl(res_path, [rec_pendente("c/transitorio", ws_vivo)])
        n = leb.patch_results(os.path.join(tmp, "leb-root"), res_path)
        r = [json.loads(l) for l in open(res_path)][0]
        v = r["verification"]
        checks["patch: falha transitória preserva workspace"] = v.get("workspace") == ws_vivo
        checks["patch: falha transitória preserva pending (reverificável)"] = v.get("pending") == "leb"
        checks["patch: falha transitória NÃO conta como verificada"] = n == 0
        checks["patch: falha transitória mantém pending_verification"] = r["status"] == "pending_verification"

        # b5: idempotência — repatch depois de verificado não remexe
        leb.verify = lambda *a, **k: {"passed": True, "exit_code": 0}
        escreve_jsonl(res_path, [rec_pendente("c/idem", ws_vivo)])
        leb.patch_results(os.path.join(tmp, "leb-root"), res_path)
        antes = open(res_path, encoding="utf-8").read()
        n2 = leb.patch_results(os.path.join(tmp, "leb-root"), res_path)
        checks["patch: idempotente (2ª rodada não verifica nada)"] = n2 == 0
        checks["patch: idempotente (arquivo idêntico)"] = open(res_path, encoding="utf-8").read() == antes

        # b6: workspace sumido continua PENDENTE (não é veredito sobre a entrega)
        escreve_jsonl(res_path, [rec_pendente("c/sem-ws", os.path.join(tmp, "nao-existe"))])
        leb.patch_results(os.path.join(tmp, "leb-root"), res_path)
        r = [json.loads(l) for l in open(res_path)][0]
        checks["patch: workspace sumido não vira reprovação"] = (
            r["status"] == "pending_verification" and r["verification"]["passed"] is None)

        # b7: registro sem leb_instance não derruba o patch das DEMAIS reps
        escreve_jsonl(res_path, [
            {"case_id": "c/sem-inst", "status": "pending_verification", "harness_outcome": "ok",
             "verification": {"passed": None, "pending": "leb", "workspace": ws_vivo}},
            rec_pendente("c/ok", ws_vivo)])
        n = leb.patch_results(os.path.join(tmp, "leb-root"), res_path)
        rs = [json.loads(l) for l in open(res_path)]
        checks["patch: rep sem instância não aborta as outras"] = n == 1 and len(rs) == 2
    finally:
        leb.verify = original_verify

    # b8: reclassify repara registro legado sem rodar verify de novo
    escreve_jsonl(res_path, [{"case_id": "c/legado", "track": "B",
                              "status": "failed_verification",
                              "subtype": "success", "terminal_reason": "api_error",
                              "verification": {"passed": True, "exit_code": 0}}])
    chs = leb.reclassify_results(res_path, dry_run=True)
    checks["reclassify: dry-run não grava"] = (
        json.loads(open(res_path).readline())["status"] == "failed_verification")
    checks["reclassify: detecta o registro do incidente"] = (
        len(chs) == 1 and chs[0]["status"] == ["failed_verification", "infra_error"])
    leb.reclassify_results(res_path, dry_run=False)
    r = json.loads(open(res_path).readline())
    checks["reclassify: repara o status"] = r["status"] == "infra_error"
    checks["reclassify: registra proveniência do reparo"] = bool(r.get("_repairs"))

    # A ferramenta de reparo não pode ser fonte de dano. Um registro da Trilha A não
    # tem os dois eixos (sem subtype, sem verification): passá-lo pela combinadora
    # reescreveria um `completed` legítimo como `infra_error` — inventando uma falha
    # que nunca houve, no arquivo que o benchmark publica.
    escreve_jsonl(res_path, [{"case_id": "T-000/M-opus48/A/rep1", "track": "A",
                              "status": "completed"}])
    chs_a = leb.reclassify_results(res_path, dry_run=False)
    checks["reclassify: NÃO toca registro da Trilha A"] = (
        chs_a == [] and json.loads(open(res_path).readline())["status"] == "completed")

    # `refused`/`invalid_isolation` são terminais e não deriváveis do C1 — a
    # combinadora não sabe produzi-los e os apagaria.
    escreve_jsonl(res_path, [{"case_id": "c/recusa", "track": "B", "status": "refused"}])
    leb.reclassify_results(res_path, dry_run=False)
    checks["reclassify: preserva status terminal (refused)"] = (
        json.loads(open(res_path).readline())["status"] == "refused")

    # ---------- (c) o driver não destrói resultado PAGO ----------
    drv = os.path.join(ROOT, "runner", "campaign_leb.sh")
    pago = os.path.join(tmp, "pago.jsonl")
    escreve_jsonl(pago, [{"case_id": "pago/rep1", "status": "completed"}])
    conteudo_antes = open(pago, encoding="utf-8").read()

    # LEB FALSO em tmp: sem isto o driver aborta no guard do GOLDEN (exit 1) antes
    # de chegar no guard de sobrescrita, e os checks abaixo passariam VAZIOS —
    # inclusive o que carrega o invariante "dado pago não se destrói". A suíte se
    # vende como offline e hermética; depender do AI-BENCHMARK clonado a tornaria
    # vermelha no primeiro clone de um terceiro.
    fake_leb = os.path.join(tmp, "leb-falso")
    os.makedirs(os.path.join(fake_leb, "instances", "LEB-100-A", "code"))
    with open(os.path.join(fake_leb, "instances", "LEB-100-A", "code", "x.php"), "w") as f:
        f.write("<?php\n")

    def driver(*args):
        return subprocess.run(["bash", drv] + list(args), capture_output=True, text=True,
                              cwd=ROOT, timeout=60,
                              env=dict(os.environ, LEB_ROOT=fake_leb))

    # o guard do GOLDEN tem de estar satisfeito — senão os checks seguintes passam
    # por abortar cedo demais, provando nada
    checks["driver: LEB falso satisfaz o guard do golden"] = (
        driver("M-dummy", "LEB-100-A", "1", "claude-code",
               "--out", os.path.join(tmp, "novo.jsonl"), "--dry-run").returncode == 0)

    p = driver("M-dummy", "LEB-100-A", "3", "claude-code", "--out", pago)
    checks["driver: recusa sobrescrever arquivo com rep paga (exit 3)"] = p.returncode == 3
    checks["driver: arquivo pago intacto"] = open(pago, encoding="utf-8").read() == conteudo_antes
    checks["driver: ensina como retomar"] = "--from-rep" in (p.stdout + p.stderr)

    # --out RELATIVO tem de ser absolutizado ANTES de chegar ao track_b: o run.sh
    # faz `cd` pro workspace efêmero, então um caminho relativo gravaria dentro de
    # um diretório descartado a cada rep — a campanha inteira escreveria no vazio.
    # Prova indireta e barata: o guard já reporta o caminho absoluto.
    rel = os.path.relpath(pago, ROOT)
    p_rel = driver("M-dummy", "LEB-100-A", "3", "claude-code", "--out", rel)
    checks["driver: --out relativo é absolutizado (guard vê o mesmo arquivo)"] = (
        p_rel.returncode == 3 and pago in (p_rel.stdout + p_rel.stderr))

    # RETOMADA: o caminho que o bug `: > "$OUT"` destruía. Provar que ele RECUSA
    # (acima) não prova que ele PRESERVA — um truncamento reintroduzido dentro do
    # ramo de append passaria despercebido. Este é o ativo mais caro do repo.
    p_ret = driver("M-dummy", "LEB-100-A", "3", "claude-code",
                   "--out", pago, "--from-rep", "2", "--dry-run")
    checks["driver: retomada aceita --from-rep 2 (exit 0)"] = p_ret.returncode == 0
    checks["driver: retomada preserva as linhas pagas"] = (
        open(pago, encoding="utf-8").read() == conteudo_antes)
    checks["driver: retomada anuncia modo APPEND"] = "APPEND" in p_ret.stdout
    checks["driver: retomada começa na rep certa"] = "2..3" in p_ret.stdout

    checks["driver: reps não numérico aborta (exit 2)"] = driver(
        "M-dummy", "LEB-100-A", "3x").returncode == 2
    checks["driver: flag desconhecida aborta (exit 2)"] = driver(
        "M-dummy", "LEB-100-A", "--turbo").returncode == 2
    checks["driver: --from-rep > reps aborta (exit 2)"] = driver(
        "M-dummy", "LEB-100-A", "3", "claude-code", "--from-rep", "9").returncode == 2
    checks["driver: --from-rep não numérico aborta (exit 2)"] = driver(
        "M-dummy", "LEB-100-A", "3", "claude-code", "--from-rep", "x").returncode == 2

    # ---------- (d) o C2 sobrevive à regra de slug do harness ----------
    # Até a 0.6.1 o slug era `cwd.replace('/','-')`, mas o CC também troca '_' — e
    # o TMPDIR do macOS tem '_'. O C2 sumiu em 100% dos runs pagos, em silêncio.
    # `w_s.1` força o slug legacy a DIVERGIR do real ('_' e '.' viram '-' na regra
    # real, não na legacy). Sem isso o fast-path acerta e o fallback nunca roda —
    # o teste passaria sem exercitar a correção, e passava por acidente de
    # ambiente (o $TMPDIR do macOS tem '_' fixo; em /tmp seria flaky).
    hr_legacy = run_fake("success", {"FAKE_CLAUDE_SLUG_MODE": "legacy"}, ws_name="w_s.1")
    checks["C2 achado mesmo com slug fora da regra (por session_id)"] = (
        hr_legacy.get("transcript_path") is not None)
    checks["C2: fallback por session_id REALMENTE exercitado (glob)"] = (
        hr_legacy.get("transcript_discovery") == "glob")
    # e o fast-path continua funcionando quando a regra bate
    hr_slug = run_fake("success")
    checks["C2: fast-path por slug quando a regra bate"] = (
        hr_slug.get("transcript_discovery") == "slug")
    hr_sem = run_fake("success", {"FAKE_CLAUDE_NO_TRANSCRIPT": "1"})
    checks["C2 ausente é reportado como 'none' (não silencioso)"] = (
        hr_sem.get("transcript_path") is None and hr_sem.get("transcript_discovery") == "none")

    print()
    allok = True
    for k, v in checks.items():
        print(f"  [{'pass' if v else 'FAIL'}] {k}")
        allok = allok and bool(v)
    print(f"\nSTATUS TAXONOMY OFFLINE ({len(checks)} checks):",
          "ALL PASS" if allok else "FAILURES ABOVE")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
