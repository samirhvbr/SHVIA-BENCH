# Estado atual — SHVIA-BENCH

## Contexto (por que este repo existe)

Há **três camadas distintas** num benchmark de modelos de código; este repo é uma
delas, não as três:

- Um **harness/produto** (um CLI agêntico) — é uma das coisas que se *mede*, não o
  medidor. Ferramentas internas dessa camada **não entram aqui**.
- **LEB / [AI-BENCHMARK](https://github.com/samirhvbr/AI-BENCHMARK)** (público) —
  *o que se testa e como se pontua*: instância legada + matriz-gabarito +
  scorecard 1000 pts + juiz.
- **SHVIA-BENCH** (este repo) — *como rodar o modelo com isenção e medir tudo*:
  ambiente isolado + proxy de verdade-base + auditoria + catálogo de métricas.

Decisão de projeto (18/07/2026): **repo público novo `SHVIA-BENCH`**, standalone,
reusando só um *padrão de projeto* (version.md, docs/, commit
`versão - comentário pt-BR`). Consome o LEB como fonte de tarefas nº 1, via
`LEB_ROOT` (default: o repo LEB clonado ao lado).

## Fase 1 — fundação (18/07/2026, 0.1.0)

Feito, mapeado 1:1 com a spec §13 (Fase 1):

- **`runner/run.sh`** (§4.2) — entrypoint sanitizado: recria o HOME sandbox do
  template (`~/.claude` real intacto), monta o ambiente com `env -i` + allowlist
  explícita, grava `env.snapshot` (ambiente exato do filho, só a chave secreta
  redigida), roda a auditoria bloqueante e faz `exec` no ambiente limpo.
  Flags `--audit-only`, `--no-audit`, `--task`, `--golden`. `PLANT_CANARY=1`
  planta o MCP canário no sandbox (p/ o A5).
- **`runner/audit.sh`** (§11) — bloco A, bloqueante. Checks mecânicos:
  A1 (sandbox recriado), A2 (sem memória), A3 (sem contexto no HOME/WORK/**acima
  do WORK**), A4 (sem .env), A6 (sem managed-settings), A7 (env == allowlist
  exata), A8 (projects vazio), A13 (esforço/raciocínio explícitos). Condicionais:
  A10 (LEB git limpo), A11 (workspace==golden), A12 (proxy no ar + base_url).
  Deferred (precisam do modelo): A5, A9, A14. Emite `audit.json`; sai ≠0 se
  qualquer check aplicável falhar.
- **`runner/canary.sh`** (A5) — `--selftest` prova OFFLINE o servidor canário
  (expõe o tool) **e** o detector (fixtures with_canary/clean); `--live` roda o
  `claude` isolado e verifica que o tool canário não vazou (precisa de chave).
- **`proxy/logging_proxy.py`** (§4.4) — proxy passivo stdlib: encaminha
  byte-a-byte (só reescreve o `Host`, que é transporte), mede TTFT/e2e, parseia
  `usage`/`model`/`stop_reason` de SSE e JSON, checa allowlist de destino, grava
  `proxy.jsonl`.
- **`config/`** — `profile.template/` (HOME sandbox versionado, settings mínimo),
  `mcp.empty.json`, `mcp.canary.json` + `canary_mcp_server.py` (servidor MCP
  stdio real), `run.defaults.env` (esforço/raciocínio/limites/proxy/LEB_ROOT).
- **`tasks/T-000-noop/`** (§10.6) — prompt trivial que mede o overhead fixo do
  harness. **`manifest.schema.json`** (§8.1). **`preflight.sh`** (padrão da casa).
- **`tests/`** — `dummy_upstream.py` (SSE fake p/ testar o proxy offline) +
  fixtures do canário.

### Decisão de design (registrada): workspace FORA do repo

O `CLAUDE.md` da raiz deste repo (padrão da casa) contaminaria qualquer sessão
`claude` iniciada abaixo dele — a auditoria A3 rejeitaria, e com razão. Por isso
o workspace vivo default é `${TMPDIR:-/tmp}/shvia-bench-work/<run>/<task>`, fora
da árvore do repo, cuja cadeia de diretórios-pai é limpa. (Override: `WORK_ROOT`.)

## Pendente — critério de saída AO VIVO (bloqueio conhecido: chave)

O mesmo "smoke ao vivo" que persegue o ecossistema. Precisa de `.secrets/anthropic`
(chave do bench) + o `claude` instalado (temos, 2.1.207):

- [ ] **A5 ao vivo**: `runner/canary.sh --live` — plantar o MCP e provar que o
      `--strict-mcp-config` o descarta de fato (a flag tem bugs conhecidos, §6.2).
- [ ] **A14 / overhead**: rodar a `T-000-noop` pelo proxy e registrar o
      `context_overhead_tokens` do lote.
- [ ] **Validar o surface do Claude Code 2.1.207** (flags/campos que a spec
      *assume*: `--output-format stream-json`, `--effort`, `--max-budget-usd`,
      comportamento real do `--strict-mcp-config`, schema do JSONL) — vira parte
      do "manter-verde" antes de codar a Trilha B.

## Fase 2 — Trilha A / modelo puro (19/07/2026, 0.2.0)

- **`results.schema.json`** (§10.4) — schema de uma linha de resultado: tempo, os
  3 TPS, custo recalculado, esforço/raciocínio; regra `metric=null nunca 0`
  (§10.3); grupos agents/tools/autonomy = null na Trilha A.
- **`config/models.json`** — catálogo Anthropic (Opus 4.8 / Sonnet 5 / Haiku 4.5 /
  Fable 5) com pricing correto + entrada `M-dummy` p/ offline. Roster real = §14 Q1.
- **`runner/track_a.py`** (§5.3) — cliente streaming stdlib: uma requisição, sem
  ferramentas; mede TTFT/e2e, parseia `usage`/`stop_reason`, recalcula custo, roda
  N repetições e agrega (mediana + dispersão).
- **Correção da spec (via skill claude-api):** modelos Anthropic 4.6+ **rejeitam
  `temperature`/`top_p`/`top_k` e `budget_tokens` com 400**. A §5.2 ("congelar
  temperature=0/top_p=1") **não se aplica** a eles — o knob congelado é
  `output_config.effort` (+ thinking). O `track_a` **nunca** envia sampling param.
- ✅ **`tests/test_track_a_offline.py`** — 17/17 contra o dummy: usage 123/7, custo
  0.137 (123×1000 + 7×2000 /1e6), TTFT<e2e, 3 reps + variância (custo cv 0%).
- **Gated na chave** (`.secrets/anthropic`): a campanha real (5 reps por par,
  `cost_delta_pct<2%` — que compara custo do harness vs recalculado, uma métrica
  de *Trilha B*; na Trilha A o custo recalculado é o próprio instrumento).

## Próximo — Fase 3 (Trilha B)

`track_b.py` (invoca o harness — `claude -p` — no workspace efêmero, até concluir)
+ `collect.py` (funde C1 result / C2 transcript JSONL / C3 proxy) + verificadores
+ tarefas-armadilha de ambiguidade. **Antes**, validar o surface do Claude Code
2.1.207 (o item pendente acima). Decisões de operador do §14 (modelos, Kilo, nível
de esforço, juiz, publicação) travam aqui.
