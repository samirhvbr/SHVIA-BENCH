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

## Fase 3 — Trilha B / modelo + harness (19/07/2026, 0.3.0)

Precedido pela **validação empírica do surface do Claude Code 2.1.207** (0.2.1,
`config/harness-matrix.md`): flags e schemas C1/C2 confirmados rodando o `claude`
real — inclusive a correção de que `--max-turns` existe.

- **`runner/track_b.py`** (§6) — dirige o `claude -p` isolado no workspace efêmero:
  monta o argv (`--output-format json`, `--session-id` fixo p/ achar o C2,
  `--strict-mcp-config`+`--mcp-config` vazio, `--effort`, `--max-budget-usd`,
  `--max-turns`), limita por budget + **timeout de wall-clock**, roda `verify.sh`
  da tarefa, e delega a fusão ao collect. `--claude-bin` injeta um harness fake.
- **`runner/collect.py`** — funde as 3 camadas numa linha do schema:
  **C1** (result: custo, tokens, turnos, duration_*, ttft nativo, modelUsage →
  context_window/maxOutput, permission_denials, service_tier/speed/geo),
  **C2** (transcript: subagentes via `Task`, `isSidechain`, thinking, ferramentas
  por nome, pico de contexto), **C3** (proxy: TTFT real, usage bruto, provedor,
  hosts fora da allowlist). Precedência **C3>C1** p/ custo/tokens (§10.1);
  `métrica=null nunca 0` (§10.3); `subagent_link_confidence` heurístico.
- ✅ **`tests/test_track_b_offline.py`** (+ `tests/fake_claude.py` emulando o
  schema 2.1.207) — **25/25**: fusão C1+C2+C3, custo recalculado do C3 (0.04175)
  vs harness (0.042) com `cost_delta_pct` 0.6% (<2%), TTFT do C3 (850<900 do C1),
  subagente/thinking/tools/pico-de-contexto do C2, verify + files_written.
- **Gated na chave** (`.secrets/anthropic`): a campanha real (rodar o `claude`
  contra instâncias do LEB, 5 reps, mediana). O canário A5 ao vivo também.

## Multi-vendor / Trilha A (19/07/2026, 0.4.0)

`config/gateways.json` — registro de vendors com 2 shapes: `kind=anthropic`
(Messages API) e `kind=openai` (/chat/completions OpenAI-compat). Cobre
**Anthropic, OpenAI, xAI/Grok, DeepSeek, Z.ai/GLM, Novita, OpenRouter, Kilo**
(+ 2 dummies). Endpoints/auth marcados `validate:true` = melhor-conhecido, a
confirmar no 1º smoke de cada um (disciplina §15).

- `track_a.py` virou **gateway-aware**: monta corpo Anthropic OU OpenAI, parseia
  os dois SSE, auth x-api-key/version OU Bearer(+extra_headers), provider-pin do
  OpenRouter (V10, `x-openrouter-provider` → `provider_effective`). Sampling
  por-modelo: só manda `temperature`/`top_p` se `gateway.sampling_ok` E
  `model.sampling_ok != false` (Anthropic 4.6+ e raciocínio o*/gpt-5/reasoner = 400).
- `config/models.json` — Anthropic com pricing REAL; demais vendors = **templates**
  (id/preço `confirmar/preencher`, roster = operador §14 Q1).
- `run.sh` — segredos multi-vendor: cada `.secrets/<vendor>` → `<VENDOR>_API_KEY`
  injetado na allowlist; **todos** os `*_API_KEY` redigidos no env.snapshot.
- ✅ `tests/test_track_a_offline.py` (+ `dummy_openai.py`) — **18/18** nos dois
  shapes; e prova de que o valor de 2 segredos plantados **não vaza** em nenhum
  artefato do run.

## Multi-harness / Trilha B + fixes da revisão (19/07/2026, 0.5.0)

Decisão do operador (§14 Q2): **vários harnesses reais**. Criada a camada de
harness plugável (paralelo do gateways.json): `config/harnesses.json` +
adapter-dispatch no track_b/collect. **Claude Code** é o único adapter
implementado (surface validado); **Kilo Code / Cline / Cursor CLI** entram como
templates `validate:true` — o track_b **recusa com mensagem clara** até o adapter
de cada um existir (cada um precisa da validação de surface própria, como o
Claude Code teve).

**8 achados da 2ª revisão adversarial (diff 0.4.0) — aplicados e provados:**
- **HIGH** — `env.snapshot` vazava a 2ª+ linha de um segredo **multi-linha** (a
  redação por-linha só pegava a 1ª). Agora o snapshot usa **placeholder** (o valor
  real nunca entra no pipeline); + nome de segredo validado `[a-z0-9-]`. Provado:
  segredo multi-linha não vaza em nenhum artefato.
- **HIGH** — OpenAI gpt-5/o-series rejeitam `max_tokens` (400) → `max_tokens_field`
  por-gateway (`max_completion_tokens`); `prompt_tokens` da OpenAI **já inclui**
  cached → não contar 2x (input = prompt − cached).
- **HIGH** — Haiku 4.5 não tem `output_config.effort` (400) → removido do M-haiku45.
- **MED/LOW** — custo = **null** (não 0) quando falta preço de um bucket usado
  (§10.3); templates de vendor com preço `null`; `proxy_bypassed` sinalizado na
  Trilha A direta (vendor sem proxy = sem C3); Kilo `sampling_ok:false` (o id é
  Anthropic 4.6+).

## Smoke ao vivo — Anthropic (19/07/2026, 0.5.1)

Com a chave em `.secrets/anthropic`, rodei o pipeline INTEIRO ao vivo:
- **Proxy → Anthropic** (TLS ok no python macOS): 200, C3 capturado (TTFT/usage/host).
- **A5 ao vivo**: `leaked:false` — o MCP canário plantado NÃO vazou; o
  `--strict-mcp-config` descarta de fato. **Isolamento real, não teatro.**
- **Trilha A** (Haiku, noop, 2 reps, run.sh+proxy): 2/2, TTFT ~743ms (cv 3.18%),
  US$0.000046/rep, `proxy_bypassed=false`, C3 capturou as 2 chamadas.
- **Trilha B** (Haiku, noop, run.sh → `claude -p` real → proxy → fusão C1+C2+C3):
  completou; C1 (turnos/duration/contextWindow/permission_denials), C3 (TTFT).
  **Reconciliação de custo: `cost_delta 0.0%`** (harness == recalculado).

**Dois bugs que só o smoke ao vivo pegaria** (log sintético de 1 chamada não pega):
1. `collect.parse_c3` agregava o `proxy.jsonl` **inteiro** (todas as chamadas de
   todos os runs no log) → `cost_delta 99.69%`. **Corrigido:** filtro por janela de
   tempo (`started_utc..finished_utc`) do caso. Re-rodado ao vivo → `cost_delta 0.0%`.
2. O proxy captura `usage:{}` na chamada do Claude Code (o parse SSE não pega o
   usage do CC) → cost/tokens caem corretamente pro **C1** (contabilidade do
   próprio harness). Follow-up: investigar o shape do SSE do CC.

**Gotcha de integração:** o `run.sh` faz `cd` pro workspace efêmero → o comando do
`--` precisa de **caminho absoluto** pro `track_a/b.py`, e o dir do `claude`/`node`
no `BENCH_EXTRA_PATH` (senão o harness não é achado no PATH sanitizado).

## LEB wirado como fonte de tarefas da Trilha B (19/07/2026, 0.6.0)

O LEB (`~/x/AI-BENCHMARK`, benchmark de evolução de legado) vira fonte de tarefas
reais da Trilha B — lido do repo, **nunca duplicado** aqui.

- **`runner/leb.py`** — adapter: `prepare(leb_root, instance)` monta o prompt
  (**enunciado canônico §2**, lido do PROTOCOL.md pra não parafrasear + o
  `manifest.md`) e aponta o golden pro `code/`; **guard anti-contaminação** recusa
  se o golden tiver qualquer coisa de `private/`/matriz/probe. `verify(...)` roda o
  `harness/leb_harness.py` do LEB (docker mysql8+php8.4) contra a entrega → exit 0
  = sem regressão; parseia probes corrigidas + dificuldade.
- **`track_b` com `--leb-root/--leb-instance`**: prompt e verify vêm do LEB (o
  golden é montado pelo `run.sh --golden`); o status vira `failed_verification` se
  o LEB acusar regressão.
- **`runner/campaign_leb.sh`** — driver de 1 caso (PROTOCOL §4: 3 reps, mediana):
  proxy + `run.sh --golden code/` + `track_b --leb-instance` + verify LEB.
  Encapsula os gotchas do smoke (caminho absoluto, `BENCH_EXTRA_PATH` com
  claude/node/docker).
- ✅ **`tests/test_leb_offline.py` (7/7)** — prova a preparação (enunciado+manifesto,
  golden=code/, matriz NÃO vaza) e o comando de verify, sem docker/chave.

### Caso LEB AO VIVO — completo (0.6.1)

Rodado `campaign_leb.sh M-haiku45 LEB-100-A 1` ao vivo:
- **self-test do LEB** (docker, sem API): `regression:false`, 4 probes PLANTADA no
  legado, 61s. Metade docker validada.
- **caso real**: Haiku editou o legado em **28 turnos** (US$0,30); o LEB pontuou:
  **`passed=True, regressao=False, probes=2/4`** (2 das 4 falhas probadas corrigidas,
  sem quebrar compat). **Resultado de benchmark legítimo, ponta a ponta ao vivo.**

**Dois bugs arquiteturais que só o caso LEB ao vivo pegaria — corrigidos:**
1. O **verify do LEB (docker) quebrava sob `env -i`** (`docker compose` precisa do
   HOME real / `~/.docker/cli-plugins`) → erro `unknown flag: --rm`. **Fix:** o
   verify saiu do `track_b` (sanitizado) — vira **pendente** e roda no **driver
   pós-run** (`leb.py patch-results`, ambiente completo). O isolamento (`env -i`) é
   pro MODELO; o avaliador precisa do ambiente cheio.
2. **Reps não eram independentes** (`--reps N` rodava no MESMO workspace →
   rep2 via as edições da rep1). **Fix:** o loop de reps foi pro **driver** —
   cada rep = um `run.sh` com workspace FRESCO do golden (PROTOCOL §4). `track_b`
   ganhou `--rep` p/ rotular.

Custo/tokens da Trilha B via `collect`: `cost_delta` ~5% neste caso multi-turno
(C3 do proxy pega só parte do usage do CC — o follow-up #2; o C1 do harness é a
verdade e o custo fica certo).

## Integridade da medição — o incidente do Opus (19/07/2026, 0.7.0)

**Y, não Z:** mudou o schema de resultados (novo enum + campos novos).

Rodando `campaign_leb.sh M-opus48 LEB-100-A 3` ao vivo, a sessão foi interrompida
no meio da rep2. Ao recuperar a rep1 (US$0,94, 215s, 21 turnos) com o
`patch-results`, o registro saiu **autocontraditório**: `status:"failed_verification"`
com `verification:{passed:true, regression:false, probes 2/4}`. O modelo tinha
entregue trabalho válido e estava marcado como reprovado.

**Três bugs, todos do tipo que só o caso pago ao vivo revela:**

1. **Erro de API virando nota do modelo.** `track_b.run_harness` mapeava o desfecho
   com `.get(subtype, "failed_verification")` — um **fallback punitivo**. O C1 real
   do Opus tinha `subtype:"success"` **com** `is_error:true` e
   `terminal_reason:"api_error"`: no 2.1.207 `subtype` e `is_error` são eixos
   **independentes**, e o código tratava os dois como um só. Um erro transitório de
   API virou juízo sobre a entrega. A spec **já proibia** isso em prosa (§10.4,
   "Nunca colapse `infra_error` em `failed_verification`") — prosa não segurou.
2. **O reparo pós-run só sabia rebaixar.** `leb.patch_results` fazia
   `if passed is False: status = failed_verification` e nunca restaurava: por isso a
   rep1 seguiu reprovada mesmo com o verify passando.
3. **O C2 nunca foi achado — em 100% dos runs pagos.** O slug do transcript era
   `cwd.replace('/','-')`, mas o Claude Code converte **todo** não-alfanumérico, e o
   `TMPDIR` do macOS tem `_` (`...v1_qr40000gn`). Subagentes, ferramentas, thinking e
   pico de contexto saíram `null` em todos os runs, **sem que nada acusasse a perda**.

**A correção é estrutural, não um `if` a mais** — `runner/status.py`, fonte única:
`status` virou função PURA de dois eixos ortogonais, `harness_outcome` (a execução
produziu medição válida?) × `verification` (a entrega passou?). `classify_c1` não
recebe veredito e é **incapaz** de escrever `failed_verification`; a string aparece
uma única vez no runner, atrás de `passed is False`. Desfecho de harness ruim manda
no status mesmo com verify aprovado (a medição está truncada, e é ela que se
publica), com o veredito anexo como evidência. Subtype desconhecido → `infra_error`
**com `harness_anomaly` crua**, nunca fallback mudo. O C2 passou a ser achado por
`session_id` (imune à regra de slug), e `instrumentation.c2_found` distingue
"métrica ausente" de "transcript não encontrado".

**Durabilidade do dado pago** (o driver destruía trabalho pago): `campaign_leb.sh`
fazia `: > "$OUT"` incondicional — retomar a campanha apagaria as reps já pagas.
Agora **recusa** sobrescrever e ensina as saídas (`--from-rep N` para retomar,
`--out` para campanha nova); log do proxy por invocação; entrada não-numérica aborta
(antes rodava 0 reps e saía **0**, sucesso aparente); exit code honesto; o sumário
discrimina o que ficou **fora da nota** (contar só `completed` foi parte de como o
incidente passou despercebido). `patch_results` **funde** em vez de substituir: uma
falha transitória (Docker parado) não apaga mais o ponteiro do workspace pago — a
rep continua pendente e reverificável, em vez de virar perda permanente.
`leb.py reclassify` repara registros anteriores à 0.7.0 sem rodar verify de novo
(com `--dry-run` e proveniência em `_repairs`).

✅ **Suíte offline: TUDO VERDE** via o novo `tests/run_all.sh` (proxy, track_a,
track_b 30/30, LEB, canário + o novo `test_status_taxonomy_offline.py` com **67
checks** cobrindo o incidente: os 7 cenários de desfecho, a garantia estrutural de
que nenhum deles alcança o veredito, a fiação `collect`→`resolve_status`,
promoção/rebaixamento, preservação do workspace em falha transitória, idempotência,
os guards do driver, a RETOMADA preservando linhas pagas, e o C2 sob regra de slug
trocada). O agregador existe porque a 0.6.2 ficou com um teste **vermelho sem
ninguém notar** — a suíte era 5 arquivos rodados na mão. Ele roda cada suíte sob
watchdog, reporta suíte PULADA (não conta como verde) e tem **piso de cobertura**:
apagar checks da taxonomia é falha, não "verde".

**Achados da revisão adversarial da própria 0.7.0** (4 lentes; 2 morreram por erro
de API, 2 voltaram) — todos aplicados:
- **`--out` relativo gravaria no vazio.** O `run.sh` faz `cd` pro workspace efêmero,
  descartado a cada rep; o exemplo do runbook (`--out runs/x.jsonl`) era justamente
  o caminho que perdia tudo. Absolutizado + teste.
- **`collect` assumia `harness_outcome="ok"` por default** — o falso positivo
  simétrico ao incidente (erro de API publicado como `completed`). Um mutante que
  hardcodava `resolve_status("ok", ...)` passava a suíte inteira. Agora estoura alto
  e há check da fiação.
- **`reclassify` destruía registro da Trilha A** (sem os dois eixos, `completed`
  virava `infra_error`) e apagaria `refused`/`invalid_isolation`. Guardado por trilha
  e por status terminal.
- **O teste do C2 era cobertura acidental de ambiente**: só exercitava o fallback
  porque o `$TMPDIR` do macOS tem `_` fixo; em `/tmp` seria flaky (~12%). Agora força
  a divergência de slug e exige `discovery == "glob"`.
- **Os testes do driver dependiam do AI-BENCHMARK clonado** — sem ele o driver
  abortava antes do guard e o check "arquivo pago intacto" passava **vazio**,
  comparando o arquivo consigo mesmo. Agora montam um LEB falso em tmp.

**Registrado por honestidade:** a fixture do incidente
(`tests/fixtures/c1_api_error_opus48.json`) separa no `_provenance` o que foi
**observado** do que foi **inferido** (`is_error` não era persistido antes da 0.7.0)
e do que se **perdeu** (`api_error_status` ficou `null` — não inventamos, §15).

### Pendências desta rodada

- [ ] **Registro pago do Opus não foi reparado.** `runs/leb-LEB-100-A-M-opus48.jsonl`
      segue com o `status` errado, por decisão do operador (fica como evidência do
      incidente). `leb.py reclassify --dry-run` mostra o efeito:
      `failed_verification → infra_error`. Aplicar é decisão sua.
- [ ] **`error_max_budget_usd`/`error_max_turns` continuam hipótese** (§15): nunca
      observados ao vivo. Se as strings reais forem outras, os cenários caem em
      `infra_error` (conservador) — não mais num veredito, que era o risco antigo.
- [ ] **`refused` e `invalid_isolation` são status órfãos** — declarados no schema e
      nunca emitidos (auditoria reprovada aborta o run **sem** gravar linha, então o
      caso inválido some em vez de aparecer marcado).
- [ ] **`stop_reason:"stop_sequence"`** apareceu ao vivo e não está na
      `config/harness-matrix.md` — a matriz precisa absorver a tabela cruzada
      `is_error` × `subtype` × `terminal_reason`.

## Próximo — Fase 4 / campanha real

Rodar de verdade (precisa de `.secrets/<vendor>`): fechar A5 ao vivo + A14
(overhead da noop pelo proxy), wirar as instâncias do LEB (`LEB_ROOT`) como fonte
de tarefas da Trilha B, e as **decisões de operador do §14** (quais modelos, nível
de esforço, juiz, tarefas-armadilha de ambiguidade, política de publicação).
Endurecimento (§13 Fase 4): container por execução, allowlist de rede no proxy.
