#!/usr/bin/env python3
"""collect.py — funde as 3 camadas de instrumentação numa linha results.jsonl.

  C1  result JSON do harness (`--output-format json`)  — nativo
  C2  transcript JSONL ($CLAUDE_CONFIG_DIR/projects/<cwd>/<sessão>.jsonl)
  C3  proxy.jsonl (única camada EXTERNA ao harness: TTFT, usage bruto, provedor)

Precedência p/ custo/tokens (§10.1, revista na 0.8.0): **C3 → C2-dedup → C1**, e a
fonte usada vai gravada em `cost.usage_source` — sem ela o `cost_delta_pct` não é
interpretável. O C3 só é eleito com **cobertura total** da janela (presença não
basta: 1 chamada instrumentada entre 53 publicaria 4 ordens de grandeza a menos).
O C2 é agregado por `message.id` (o streaming repete a mesma mensagem: somar linha
a linha conta 2–3×). O `usage` top-level do C1 **não** é agregado confiável do run.

Nenhuma camada com tokens ⇒ `usage_source: null` e `cost_usd_computed: null` —
métrica ausente = null, NUNCA 0 (§10.3), senão um run PAGO entra como grátis na
mediana. Schemas confirmados empiricamente no Claude Code 2.1.207
(config/harness-matrix.md).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status as status_mod  # noqa: E402


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
        # PERSISTIR (0.7.0): antes, `is_error` e `api_error_status` eram lidos p/
        # decidir o status e DESCARTADOS. O registro pago do Opus ficou
        # autocontraditório e não-diagnosticável a partir de si mesmo — dava p/ ver
        # `terminal_reason:"api_error"` mas não o gatilho. Num repo que se vende
        # como auditável, perder o dado bruto da decisão é perder a evidência.
        "is_error": c1.get("is_error"),
        "api_error_status": c1.get("api_error_status"),
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


BUCKETS = ("input_tokens", "output_tokens",
           "cache_creation_input_tokens", "cache_read_input_tokens")


def _num(v):
    """Valor de usage → número. Campo ausente/None/não numérico vira 0 em vez de
    estourar TypeError no meio da coleta e perder o registro de um run PAGO."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _usage_merge(a, b):
    """Funde duas linhas de usage do MESMO `message.id`: máximo POR BUCKET.

    Não é soma — as linhas descrevem a MESMA mensagem, então somar contaria 2×
    (foi o bug que motivou o dedup: 29 linhas para 14 ids, custo inflado em 72%).

    Por que máximo por bucket e não "a linha de maior total": o SSE da Anthropic é
    **delta-shaped** — `message_start` traz input/cache com `output_tokens` ≈ 1, e
    `message_delta` traz **só** o output final. Escolher "a linha de maior total"
    pegaria o `message_start` (dominado pelo cache_read) e **descartaria o output
    inteiro** — justo o bucket mais caro (US$25/Mtok contra US$0,50 do cache_read),
    e o erro seria para MENOS: o modelo pareceria mais barato do que é.
    O máximo por bucket dá o mesmo resultado quando as duplicatas são idênticas (o
    observado no 2.1.207, 7/7 grupos) e continua certo nos formatos cumulativo e
    delta-shaped (§15).
    """
    return {k: max(_num(a.get(k)), _num(b.get(k))) for k in BUCKETS}


def _usage_conflict(a, b):
    """As duas linhas do mesmo id discordam em algum bucket? (≠ de uma ser subconjunto
    da outra por campo ausente). Publicado como `c2_usage_conflicts` — é o que
    falsifica a hipótese "duplicatas são idênticas" (§15) sem erro mudo."""
    return any(_num(a.get(k)) != _num(b.get(k)) for k in BUCKETS)


def parse_c2(path, kind="claude-code"):
    """transcript JSONL → subagentes, thinking, ferramentas, pico de contexto.
    Adapter por harness (kind). Só 'claude-code' implementado (schema validado
    em config/harness-matrix.md); outros harnesses → None até ter adapter próprio."""
    if kind != "claude-code" or not path or not os.path.exists(path):
        return None
    subagents, sub_types, thinking, by_name = 0, [], 0, {}
    compaction, sidechain = 0, False
    # usage por MENSAGEM, não por linha: o transcript repete a mesma mensagem
    # (mesmo `message.id`) várias vezes — na rep3 da 1ª campanha foram 29 linhas
    # com usage para 14 ids distintos. Somar linha a linha conta 2–3× (US$1,7692
    # cru vs US$1,0293 dedup, contra US$1,029316 do harness). Ver §10.1.
    usage_by_msg, usage_lines, usage_conflicts, peak = {}, 0, 0, 0
    for idx, line in enumerate(open(path, encoding="utf-8")):
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
            usage_lines += 1
            # Sem `message.id` (harness/fixture que não emite) não dá para deduplicar:
            # cada linha vira sua própria chave. Conservador — nunca funde o que não
            # se provou ser a mesma mensagem.
            key = msg.get("id") or ("_line", idx)
            prev = usage_by_msg.get(key)
            if prev is None:
                usage_by_msg[key] = {k: _num(u.get(k)) for k in BUCKETS}
            else:
                if _usage_conflict(prev, u):
                    usage_conflicts += 1
                usage_by_msg[key] = _usage_merge(prev, u)
            # o PICO é max sobre TODAS as linhas: duplicata nunca infla um máximo,
            # então o dedup aqui só criaria risco (uma linha delta-shaped podia
            # derrubar o pico) sem ganho nenhum.
            janela = _num(u.get("input_tokens")) + _num(u.get("cache_read_input_tokens")) \
                + _num(u.get("cache_creation_input_tokens"))
            peak = max(peak, janela)
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
    # contexto/tokens saem do usage JÁ deduplicado — senão o `tokens_per_turn`
    # publicaria a mesma mensagem várias vezes.
    per_turn = []
    agg = {k: 0 for k in BUCKETS}
    for u in usage_by_msg.values():
        for k in agg:
            agg[k] += _num(u.get(k))
        janela = _num(u.get("input_tokens")) + _num(u.get("cache_read_input_tokens")) \
            + _num(u.get("cache_creation_input_tokens"))
        if janela:
            per_turn.append(janela)
    return {
        "subagent_count": subagents, "subagent_types": sub_types or None,
        "subagent_link_confidence": "heuristic" if sidechain else "exact",
        "thinking_blocks_count": thinking,
        "tool_calls_total": sum(by_name.values()) or None,
        "tool_calls_by_name": by_name or None,
        "context_peak_tokens": peak or None,
        "tokens_per_turn": per_turn or None,
        "compaction_events": compaction,
        "usage": agg if any(agg.values()) else None,
        "usage_messages": len(usage_by_msg) or None,
        "usage_lines": usage_lines or None,
        "usage_conflicts": usage_conflicts,
    }


def parse_c3(path, window=None):
    """proxy.jsonl → TTFT real, usage bruto, provedor (verdade-base).
    O proxy.jsonl acumula chamadas de VÁRIOS runs que compartilham o log → filtra
    pelas do caso atual via janela de tempo (started_utc, finished_utc). Linhas
    sem `sent_utc` (ex.: fixtures de teste) passam."""
    if not path or not os.path.exists(path):
        return None
    calls, ttfts, provider, usage_calls = [], [], None, 0
    agg = {"input_tokens": 0, "output_tokens": 0,
           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    hosts_off = []
    w0, w1 = (window or (None, None))
    for line in open(path, encoding="utf-8"):
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        su = o.get("sent_utc")
        if w0 is not None and su is not None and not (w0 - 1 <= su <= w1 + 1):
            continue  # chamada de outro caso/run
        calls.append(o)
        if o.get("ttft_ms") is not None:
            ttfts.append(o["ttft_ms"])
        provider = provider or o.get("provider")
        if o.get("host_allowed") is False:
            hosts_off.append(o.get("dest_host"))
        u = o.get("usage") or {}
        if any(_num(v) for v in u.values()):
            usage_calls += 1
        for k in agg:
            agg[k] += _num(u.get(k))
    ttfts.sort()
    def pct(p):
        return ttfts[min(len(ttfts) - 1, int(p * len(ttfts)))] if ttfts else None
    return {
        "calls": len(calls), "provider_effective": provider,
        "ttft_ms_first_call": ttfts[0] if ttfts else None,
        "ttft_ms_p50": pct(0.5), "ttft_ms_p95": pct(0.95),
        "usage": agg, "hosts_off_allowlist": hosts_off or None,
        # COBERTURA, não presença: quantas das chamadas da janela trouxeram usage.
        # Sem isto a precedência elegia o C3 com UMA chamada instrumentada entre 53
        # e publicava US$0,0004 no lugar de US$1,03 — com o selo da camada de maior
        # confiança. E "SSE meio consertado" é o estado MAIS PROVÁVEL do próximo
        # passo do projeto, não um caso de laboratório.
        "usage_calls": usage_calls,
    }


def collect(harness_result, proxy_log, model_cfg, ids, verify):
    """Funde C1+C2+C3 numa linha results.schema.json (Trilha B)."""
    hr = harness_result
    c1 = parse_c1(hr.get("c1"))
    c2 = parse_c2(hr.get("transcript_path"), hr.get("transcript_kind", "claude-code"))
    c3 = parse_c3(proxy_log, (hr.get("started_utc"), hr.get("finished_utc")))
    price = model_cfg["price_per_mtok"]
    # SEM default: `hr.get(..., "ok")` seria exatamente o fallback permissivo que
    # esta versão existe para eliminar — um envelope sem desfecho viraria `ok` e,
    # com o verify passando, seria promovido a `completed`. Falso POSITIVO num
    # número publicado é tão ruim quanto o falso negativo do incidente. Ausência
    # da chave é bug de programação: estoure alto, não adivinhe.
    harness_outcome = hr["harness_outcome"]

    # Verdade p/ tokens/custo: C3 → C2-dedup → C1 (§10.1).
    #
    # O C2-dedup entrou na 0.8.0 depois da 1ª campanha oficial: o `usage` top-level
    # do C1 NÃO é sempre o agregado do run (subestimou 38% numa rep e fechou exato em
    # duas outras — inconsistente entre reps do MESMO modelo), enquanto o C2
    # deduplicado por `message.id` reproduziu o `total_cost_usd` do harness na 6ª
    # casa decimal. O C3 segue no topo por princípio (§4.4: só ele é externo ao
    # harness), mas hoje não captura `usage` do Claude Code — 0 de 53 chamadas na 1ª
    # campanha —, então na prática quem manda na Trilha B é o C2-dedup.
    c2_usage = (c2 or {}).get("usage") or {}
    c1_usage = c1.get("usage") or {}
    # O C3 só é eleito com COBERTURA TOTAL da janela. Presença não basta: uma única
    # chamada instrumentada entre 53 publicaria US$0,0004 no lugar de US$1,03 — com
    # o selo da camada de maior confiança. Cobertura parcial cai para o C2-dedup.
    c3_total = bool(c3) and c3.get("calls", 0) > 0 and c3.get("usage_calls", 0) == c3["calls"]
    if c3_total and any(_num(v) for v in (c3.get("usage") or {}).values()):
        usage_truth, usage_source = c3["usage"], "c3_proxy"
    elif any(_num(v) for v in c2_usage.values()):
        usage_truth, usage_source = c2_usage, "c2_transcript_dedup"
    elif any(_num(v) for v in c1_usage.values()):
        usage_truth, usage_source = c1_usage, "c1_harness"
    else:
        # NENHUMA camada trouxe tokens. Rotular `c1_harness` seria procedência
        # falsa, e `_cost({})` devolveria **0.0** — um run pago apareceria como
        # grátis para qualquer agregador, violando §10.3 ("ausente = null, nunca 0")
        # justamente no campo que esta versão promoveu a número auditado.
        usage_truth, usage_source = None, None
    cost_computed = _cost(usage_truth, price) if usage_truth else None
    cost_harness = c1.get("cost_harness")
    delta = (round(100 * abs(cost_harness - cost_computed) / cost_harness, 2)
             if isinstance(cost_harness, (int, float)) and cost_harness
             and cost_computed is not None else None)

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
        # `status` é FUNÇÃO PURA de (desfecho do harness, veredito do verificador).
        # O collect não copia mais um status pronto do harness — o harness não tem
        # como saber se a entrega passou, e não pode emitir juízo sobre ela.
        "status": status_mod.resolve_status(harness_outcome, verify),
        "harness_outcome": harness_outcome,
        "harness_anomaly": hr.get("harness_anomaly"),
        "stop_reason": c1.get("stop_reason"),
        "terminal_reason": c1.get("terminal_reason"), "subtype": c1.get("subtype"),
        "is_error": c1.get("is_error"), "api_error_status": c1.get("api_error_status"),
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
        # `usage_source` é obrigatório para ler o `cost_delta_pct`: um delta alto
        # significa coisas diferentes conforme a fonte. Sem ele, o número publicado
        # não é auditável a partir de si mesmo.
        "cost": {"cost_usd_harness": cost_harness, "cost_usd_computed": cost_computed,
                 "cost_delta_pct": delta, "usage_source": usage_source},
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
                     "hit_max_turns": harness_outcome == "max_turns"},
        # Qual camada foi de fato capturada. Sem isto, um `subagent_count:null` é
        # indistinguível de "não achei o transcript" — que foi o que aconteceu em
        # 100% dos runs pagos até a 0.6.1, silenciosamente (§10.3, no espírito:
        # um null sem causa registrada não é medida, é buraco).
        "instrumentation": {
            "c1_found": bool(hr.get("c1")),
            "c2_found": c2 is not None,
            "c3_found": c3 is not None,
            "transcript_discovery": hr.get("transcript_discovery"),
            "transcript_path": hr.get("transcript_path"),
            # fator de deduplicação do C2: quantas LINHAS de usage viraram quantas
            # MENSAGENS. Publicado para o custo ser auditável a partir do próprio
            # registro — quem quiser conferir precisa saber que houve colapso, e
            # de quanto (na rep3 medida: 29 → 14, e a soma crua inflaria 72%).
            "c2_usage_lines": (c2 or {}).get("usage_lines"),
            "c2_usage_messages": (c2 or {}).get("usage_messages"),
            # Falsificador da hipótese "duplicatas são idênticas" (§15). Enquanto
            # for 0, o registro PROVA que só houve repetição idêntica — o caso
            # validado no 2.1.207. Um valor > 0 marca o registro para inspeção em
            # vez de deixar o collect escolher em silêncio.
            "c2_usage_conflicts": (c2 or {}).get("usage_conflicts"),
            # cobertura do C3: quantas chamadas da janela trouxeram usage. Explica
            # POR QUE o C3 não foi eleito quando não foi.
            "c3_calls": (c3 or {}).get("calls"),
            "c3_usage_calls": (c3 or {}).get("usage_calls"),
        },
        "tools": ({"tool_calls_total": c2.get("tool_calls_total"),
                   "by_name": c2.get("tool_calls_by_name")} if c2 else None),
        "verification": verify,
    }
    if (c3 or {}).get("hosts_off_allowlist"):
        rec["audit_flag"] = {"hosts_off_allowlist": c3["hosts_off_allowlist"]}
    return rec
