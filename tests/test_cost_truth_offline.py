#!/usr/bin/env python3
"""Offline: de onde vêm os tokens/custo recomputados (§10.1) — C3 → C2-dedup → C1.

Motivação (1ª campanha oficial, 20/07/2026, dado PAGO): o `usage` top-level do C1
não é sempre o agregado do run — fechou exato em 2 reps e **subestimou 38% numa
terceira**, do mesmo modelo, no mesmo caso. O C2 deduplicado por `message.id`
reproduziu o `total_cost_usd` do harness na 6ª casa decimal.

E deduplicar não é opcional: o transcript repete a mesma mensagem (streaming) — 29
linhas de `usage` para 14 ids distintos na rep medida. Somar linha a linha **inflaria
o custo em 72%**. A fixture `c2_usage_dedup_opus48_rep3.json` congela esse caso real
(só os números; nenhum prompt, código ou texto do modelo).

Stdlib only. Sem rede, sem chave, sem docker.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))
import collect as collect_mod  # noqa: E402

PRICE = {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.5}
MODEL_CFG = {"id": "dummy-model-1", "max_tokens": 8192, "price_per_mtok": PRICE}
IDS = {"run_id": "t", "task_id": "T-x", "model_alias": "M-dummy",
       "repetition": 1, "case_id": "T-x/M-dummy/B/rep1"}


def escreve_transcript(path, entradas):
    """entradas: lista de (message_id|None, usage_dict). Escreve um C2 mínimo."""
    with open(path, "w", encoding="utf-8") as f:
        for mid, u in entradas:
            msg = {"role": "assistant", "content": [], "usage": u}
            if mid is not None:
                msg["id"] = mid
            f.write(json.dumps({"type": "assistant", "message": msg}) + "\n")


def envelope(transcript=None, c1_usage=None, cost_harness=None):
    return {"harness_outcome": "ok", "session_id": "s", "e2e_ms": 1000.0,
            "started_utc": 1000.0, "finished_utc": 2000.0,
            "transcript_path": transcript, "transcript_kind": "claude-code",
            "transcript_discovery": "slug" if transcript else "none",
            "harness_anomaly": None,
            "c1": {"subtype": "success", "is_error": False, "num_turns": 3,
                   "total_cost_usd": cost_harness,
                   "usage": c1_usage or {}}}


def main():
    checks = {}
    tmp = tempfile.mkdtemp(prefix="sb-cost-")

    # ---------- o caso REAL, congelado da rep3 paga ----------
    fx = json.load(open(os.path.join(ROOT, "tests", "fixtures",
                                     "c2_usage_dedup_opus48_rep3.json"), encoding="utf-8"))
    prov = fx["_provenance"]
    real = os.path.join(tmp, "real.jsonl")
    escreve_transcript(real, [(e["id"], e["usage"]) for e in fx["usage_lines"]])

    c2 = collect_mod.parse_c2(real)
    custo_dedup = collect_mod._cost(c2["usage"], PRICE)
    # soma CRUA (o que um leitor desavisado faria) — para provar o tamanho do erro
    cru = {k: sum(e["usage"].get(k, 0) for e in fx["usage_lines"])
           for k in ("input_tokens", "output_tokens",
                     "cache_creation_input_tokens", "cache_read_input_tokens")}
    custo_cru = collect_mod._cost(cru, PRICE)

    checks["fixture real: 29 linhas de usage"] = c2["usage_lines"] == 29
    checks["fixture real: 14 mensagens distintas"] = c2["usage_messages"] == 14
    # igualdade EXATA, não tolerância: o dedup reproduz o valor pago em todos os
    # dígitos. Uma tolerância aqui esconderia erro de dado como se fosse ruído de
    # float — foi o que aconteceu quando a fixture trazia o valor truncado.
    checks["C2-dedup reproduz o total_cost_usd do harness (EXATO)"] = (
        custo_dedup == prov["harness_total_cost_usd"])
    checks["soma CRUA infla o custo (é o erro que o dedup evita)"] = (
        abs(custo_cru - prov["soma_crua_esperada"]) < 1e-5 and custo_cru > custo_dedup * 1.5)
    checks["tokens_per_turn tem 1 entrada por MENSAGEM"] = len(c2["tokens_per_turn"]) == 14

    # o registro final: com C3 ausente, a verdade tem de ser o C2-dedup
    rec = collect_mod.collect(
        envelope(transcript=real, c1_usage={"input_tokens": 3235, "output_tokens": 8388,
                                            "cache_creation_input_tokens": 21163,
                                            "cache_read_input_tokens": 557088},
                 cost_harness=prov["harness_total_cost_usd"]),
        None, MODEL_CFG, IDS, {"passed": True})
    checks["registro: usage_source == c2_transcript_dedup"] = (
        rec["cost"]["usage_source"] == "c2_transcript_dedup")
    checks["registro: cost_delta_pct cai para 0.0 (era 38.14)"] = rec["cost"]["cost_delta_pct"] == 0.0
    checks["registro: tokens vêm do C2-dedup"] = rec["tokens"]["output_tokens"] == 14637
    # o fator de dedup vai no registro: sem ele, o custo não é auditável a partir
    # de si mesmo (quem confere precisa saber que houve colapso, e de quanto)
    checks["registro: publica o fator de dedup (29 linhas → 14 mensagens)"] = (
        rec["instrumentation"]["c2_usage_lines"] == 29
        and rec["instrumentation"]["c2_usage_messages"] == 14)

    # o mesmo envelope SEM transcript cai no C1 — e o delta de 38% reaparece.
    # É a prova de que a mudança de precedência é o que corrige o número.
    rec_c1 = collect_mod.collect(
        envelope(c1_usage={"input_tokens": 3235, "output_tokens": 8388,
                           "cache_creation_input_tokens": 21163,
                           "cache_read_input_tokens": 557088},
                 cost_harness=prov["harness_total_cost_usd"]),
        None, MODEL_CFG, IDS, {"passed": True})
    checks["sem C2: cai no C1 e o delta de 38% reaparece"] = (
        rec_c1["cost"]["usage_source"] == "c1_harness"
        and rec_c1["cost"]["cost_delta_pct"] > 35)

    # ---------- precedência ----------
    # C3 continua no topo por PRINCÍPIO (§4.4: única camada externa ao harness),
    # mas só com COBERTURA TOTAL da janela.
    def escreve_proxy(path, linhas):
        with open(path, "w", encoding="utf-8") as f:
            for u in linhas:
                f.write(json.dumps({"ttft_ms": 100.0, "host_allowed": True,
                                    "usage": u}) + "\n")

    cheio = {"input_tokens": 1, "output_tokens": 1,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    proxy = os.path.join(tmp, "proxy.jsonl")
    escreve_proxy(proxy, [cheio, cheio])
    rec_c3 = collect_mod.collect(envelope(transcript=real, cost_harness=1.0),
                                 proxy, MODEL_CFG, IDS, {"passed": True})
    checks["C3 com cobertura TOTAL vence o C2 (§10.1)"] = (
        rec_c3["cost"]["usage_source"] == "c3_proxy")

    # C3 PARCIAL não pode sequestrar a verdade. Sem esta guarda, 1 chamada
    # instrumentada entre 53 publicava US$0,0004 no lugar de US$1,03 — com o selo
    # da camada de maior confiança. E "SSE meio consertado" é justamente o próximo
    # passo declarado do projeto, não um caso de laboratório.
    proxy_parcial = os.path.join(tmp, "proxy_parcial.jsonl")
    escreve_proxy(proxy_parcial, [cheio] + [{}] * 52)
    rec_pp = collect_mod.collect(
        envelope(transcript=real, cost_harness=prov["harness_total_cost_usd"]),
        proxy_parcial, MODEL_CFG, IDS, {"passed": True})
    checks["C3 PARCIAL (1/53) NÃO vence — cai p/ C2-dedup"] = (
        rec_pp["cost"]["usage_source"] == "c2_transcript_dedup")
    checks["C3 parcial: o número publicado continua certo"] = (
        rec_pp["cost"]["cost_usd_computed"] == prov["harness_total_cost_usd"])
    checks["C3 parcial: a cobertura fica registrada (1/53)"] = (
        rec_pp["instrumentation"]["c3_usage_calls"] == 1
        and rec_pp["instrumentation"]["c3_calls"] == 53)

    # proxy que NÃO capturou usage (o caso real do Claude Code hoje: 0/53 chamadas)
    proxy_vazio = os.path.join(tmp, "proxy_vazio.jsonl")
    with open(proxy_vazio, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ttft_ms": 100.0, "host_allowed": True, "usage": {}}) + "\n")
    rec_pv = collect_mod.collect(envelope(transcript=real, cost_harness=1.0),
                                 proxy_vazio, MODEL_CFG, IDS, {"passed": True})
    checks["C3 sem usage NÃO sequestra a verdade (cai p/ C2)"] = (
        rec_pv["cost"]["usage_source"] == "c2_transcript_dedup")

    # ---------- robustez do dedup ----------
    # sem message.id não dá para deduplicar: cada linha é distinta (conservador —
    # nunca funde o que não se provou ser a mesma mensagem)
    sem_id = os.path.join(tmp, "sem_id.jsonl")
    u = {"input_tokens": 10, "output_tokens": 10,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    escreve_transcript(sem_id, [(None, u), (None, u)])
    c2_sem = collect_mod.parse_c2(sem_id)
    checks["sem message.id: não funde (2 linhas = 2 mensagens)"] = (
        c2_sem["usage_messages"] == 2 and c2_sem["usage"]["output_tokens"] == 20)

    # duplicata IDÊNTICA (o observado no 2.1.207) → conta uma vez
    ident = os.path.join(tmp, "ident.jsonl")
    escreve_transcript(ident, [("m1", u), ("m1", u), ("m1", u)])
    checks["duplicata idêntica conta UMA vez"] = (
        collect_mod.parse_c2(ident)["usage"]["output_tokens"] == 10)

    # HIPÓTESE (§15) — formato CUMULATIVO: a 2ª linha repete tudo com output maior.
    prog = os.path.join(tmp, "prog.jsonl")
    escreve_transcript(prog, [
        ("m1", {"input_tokens": 100, "output_tokens": 5,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}),
        ("m1", {"input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})])
    c2_prog = collect_mod.parse_c2(prog)
    checks["cumulativo: fica com o maior output"] = c2_prog["usage"]["output_tokens"] == 50
    checks["cumulativo: não SOMA o input repetido"] = c2_prog["usage"]["input_tokens"] == 100

    # HIPÓTESE (§15) — formato DELTA-SHAPED, que é a forma real do SSE da Anthropic:
    # `message_start` traz input/cache com output ~1, `message_delta` traz SÓ o
    # output final. "Linha de maior total" pegaria o message_start (dominado pelo
    # cache_read) e descartaria o output inteiro — o bucket mais caro (US$25/Mtok
    # contra US$0,50), subestimando o custo. Máximo por bucket acerta.
    delta_sse = os.path.join(tmp, "delta.jsonl")
    escreve_transcript(delta_sse, [
        ("m1", {"input_tokens": 30, "output_tokens": 1,
                "cache_creation_input_tokens": 5000, "cache_read_input_tokens": 45000}),
        ("m1", {"output_tokens": 6249})])
    c2_delta = collect_mod.parse_c2(delta_sse)
    checks["delta-shaped: preserva o output do message_delta"] = (
        c2_delta["usage"]["output_tokens"] == 6249)
    checks["delta-shaped: preserva input/cache do message_start"] = (
        c2_delta["usage"]["input_tokens"] == 30
        and c2_delta["usage"]["cache_read_input_tokens"] == 45000)
    checks["delta-shaped: conta como CONFLITO (falsifica a hipótese §15)"] = (
        c2_delta["usage_conflicts"] == 1)
    checks["duplicata idêntica NÃO conta como conflito"] = (
        collect_mod.parse_c2(ident)["usage_conflicts"] == 0)

    # o pico é max sobre TODAS as linhas: uma linha delta-shaped (janela pequena)
    # não pode derrubar o pico registrado pelo message_start.
    checks["pico não regride com linha delta-shaped"] = (
        c2_delta["context_peak_tokens"] == 50030)

    # usage com valor não numérico não pode derrubar a coleta de um run PAGO
    sujo = os.path.join(tmp, "sujo.jsonl")
    escreve_transcript(sujo, [("m1", {"input_tokens": "30", "output_tokens": 10,
                                      "cache_creation_input_tokens": None,
                                      "cache_read_input_tokens": 5})])
    c2_sujo = collect_mod.parse_c2(sujo)
    checks["usage não numérico não estoura (vira 0)"] = (
        c2_sujo["usage"]["input_tokens"] == 0 and c2_sujo["usage"]["output_tokens"] == 10)

    # NENHUMA fonte (ex.: timeout → c1=None e transcript não achado). O check
    # anterior se chamava "custo não é inventado" e não olhava o custo: passava
    # com `cost_usd_computed: 0.0` e `usage_source: "c1_harness"` — procedência
    # falsa + um run PAGO entrando como grátis na mediana (§10.3).
    rec_nada = collect_mod.collect(envelope(cost_harness=None), None, MODEL_CFG, IDS,
                                   {"passed": True})
    checks["sem fonte: cost_usd_computed é null, NÃO 0.0 (§10.3)"] = (
        rec_nada["cost"]["cost_usd_computed"] is None)
    checks["sem fonte: usage_source é null (não mente procedência)"] = (
        rec_nada["cost"]["usage_source"] is None)
    checks["sem fonte: tokens todos null"] = (
        rec_nada["tokens"]["total_tokens"] is None
        and rec_nada["tokens"]["output_tokens"] is None)
    checks["sem fonte: cost_delta_pct null"] = rec_nada["cost"]["cost_delta_pct"] is None

    print()
    allok = True
    for k, v in checks.items():
        print(f"  [{'pass' if v else 'FAIL'}] {k}")
        allok = allok and bool(v)
    print(f"\nCOST TRUTH OFFLINE ({len(checks)} checks):",
          "ALL PASS" if allok else "FAILURES ABOVE")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
