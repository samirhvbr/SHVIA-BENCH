# Como rodar — runbook do operador

Guia prático pra rodar o SHVIA-BENCH. Conceitos/spec: [ambiente-isolado.md](ambiente-isolado.md).
Invariantes que **não se relitiga**: [../CLAUDE.md](../CLAUDE.md).

## 0. Pré-requisitos

```bash
./preflight.sh          # checa python3 (stdlib), git, openssl, docker, claude, nc
bash tests/run_all.sh   # suíte offline inteira (sem rede, sem chave, sem docker)
```
- **Trilha A** (modelo puro / API): só precisa da chave do vendor.
- **Trilha B** (modelo + harness): precisa do harness (ex.: `claude`) e, pro LEB,
  do **Docker** (mysql8+php8.4) e do repo LEB ao lado (`~/x/AI-BENCHMARK`).

## 1. Chaves — uma por vendor, nunca versionadas

Cada `.secrets/<vendor>` vira `<VENDOR>_API_KEY` (nome só `[a-z0-9-]`). Gitignored.
```bash
printf '%s' 'sk-ant-...' > .secrets/anthropic     # → ANTHROPIC_API_KEY
printf '%s' 'sk-...'     > .secrets/openai         # → OPENAI_API_KEY
# xai · deepseek · zai · novita · openrouter · kilo
```
O `run.sh` injeta na allowlist do `env -i` e **redige** todo `*_API_KEY` no
`env.snapshot` (o valor real nunca vai pra artefato).

## 2. Trilha A — modelo puro (API direta)

Roda **dentro** do `run.sh` (env sanitizado + proxy). Vendors/modelos em
[../config/gateways.json](../config/gateways.json) e [../config/models.json](../config/models.json).
```bash
# proxy (verdade-base C3) apontando pro vendor:
SHVIA_PROXY_LOG=runs/a.jsonl python3 proxy/logging_proxy.py --upstream https://api.anthropic.com --allow api.anthropic.com &
# 5 reps de um modelo numa tarefa (CAMINHO ABSOLUTO — o run.sh faz cd pro workspace):
./runner/run.sh --task T-000-noop -- \
  python3 "$PWD/runner/track_a.py" --model M-opus48 --task T-000-noop --reps 5 --out "$PWD/runs/a.jsonl"
```
Vendors OpenAI-compat (OpenAI/xAI/DeepSeek/GLM/Novita/OpenRouter/Kilo) funcionam
igual — só trocar `--model` pro alias do vendor. **Sampling**: o `track_a` só manda
`temperature/top_p` se o gateway E o modelo permitirem (Anthropic 4.6+ e raciocínio
= 400 se mandar).

## 3. Trilha B — modelo + harness, contra o LEB

Um caso oficial (PROTOCOL §4: **3 reps, nota = mediana**), via o driver:
```bash
runner/campaign_leb.sh M-opus48 LEB-100-A 3      # <model_alias> <instância> [reps] [harness]
```
O driver cuida de tudo: proxy, workspace **fresco por rep** (do `code/` da
instância), `run.sh` (isolado) → `claude -p` edita o legado → **verify do LEB**
(docker, no ambiente completo, pós-run) → `results.jsonl`. Kill-switch `--budget`
por caso.

**Retomar uma campanha interrompida** — cada rep custa dinheiro (ordem de US$1/rep
no Opus), então o driver **nunca** sobrescreve um `results.jsonl` que já tem rep
paga: ele recusa e ensina as duas saídas.
```bash
runner/campaign_leb.sh M-opus48 LEB-100-A 3 claude-code --from-rep 2   # continua da rep 2 (append)
runner/campaign_leb.sh M-opus48 LEB-100-A 3 --out runs/opus-limpo.jsonl # campanha nova, preserva a antiga
```
Quem decide onde retomar é **você**, não o driver: ele não lê o `results.jsonl`
pra descobrir onde parou (§4.1 — `runs/` é write-only). Se a campanha morreu antes
do verify, rode o pós-run sozinho:
```bash
python3 runner/leb.py patch-results --leb-root ~/x/AI-BENCHMARK --results runs/<arquivo>.jsonl
```
Ele é idempotente e só toca reps com veredito pendente. Uma falha transitória
(Docker parado) **não** consome a rep: ela continua pendente e reverificável.

Antes de gastar, confira o plano — passa por todos os guards e para antes da 1ª rep:
```bash
runner/campaign_leb.sh M-opus48 LEB-100-A 3 claude-code --dry-run
```

**Reparar registros antigos** (anteriores à 0.7.0, quando o harness podia emitir
veredito — ver §10.4). Recombina o que já está no registro, **sem** rodar verify de
novo. Só toca Trilha B; preserva `refused`/`invalid_isolation`; grava proveniência
em `_repairs`. **Rode com `--dry-run` primeiro** — é dado pago:
```bash
python3 runner/leb.py reclassify --results runs/<arquivo>.jsonl --dry-run
```

**Gotchas embutidos no driver** (se for rodar `track_b` na mão):
- caminho **absoluto** pro `track_b.py` (o `run.sh` faz `cd` pro workspace);
- `BENCH_EXTRA_PATH` com o dir do `claude`/`node` (harness) e do `docker` (verify);
- o **verify do LEB roda FORA do `env -i`** (o `docker compose` precisa do HOME
  real) — por isso é um passo pós-run (`leb.py patch-results`), não dentro do `track_b`.

## 4. Ler os resultados

`results.jsonl` — uma linha por rep, schema em [../results.schema.json](../results.schema.json).
Campos-chave:
- `status` — arbitragem de **dois eixos** (§10.4): `harness_outcome` (a execução
  produziu medição válida?) × `verification` (a entrega passou?). Só o verificador
  emite `failed_verification`; erro de API é `infra_error`, nunca nota do modelo.
  `pending_verification` = veredito ainda não medido (≠ aprovado),
- `harness_outcome` + `harness_anomaly`: desfecho da execução e, quando não
  classificável, os sinais crus do C1 (subtype/is_error/terminal_reason),
- `instrumentation`: `c2_found`/`transcript_discovery` — se vier `c2_found:false`,
  subagentes/ferramentas/thinking daquela rep **não foram medidos** (não são zero).
  Para conferir o custo: `c2_usage_lines`/`c2_usage_messages` mostram o colapso do
  dedup (ex.: 29 → 14) e `c3_calls`/`c3_usage_calls` mostram a cobertura do proxy —
  é o que explica **por que** aquela fonte foi escolhida. **`c2_usage_conflicts > 0`
  pede inspeção**: significa que duas linhas do mesmo `message.id` discordaram, ou
  seja, o formato do transcript mudou (§15),
- `cost`: `cost_usd_harness` vs `cost_usd_computed` + `cost_delta_pct`
  (**>2% → inspeção**, §10.1 — costuma revelar chamada não contabilizada). **Leia
  junto com `cost.usage_source`** (`c3_proxy` > `c2_transcript_dedup` > `c1_harness`):
  o mesmo delta significa coisas diferentes conforme a fonte (Trilha A grava
  `a_api_response`). Registro **sem** esse campo é anterior à 0.8.0, quando o `usage`
  do C1 era usado direto — e ele **não** é agregado confiável do run. `usage_source`
  e `cost_usd_computed` em `null` = nenhuma camada trouxe tokens (≠ custo zero),
- `time`: `ttft_ms_first_call`, `e2e_ms`, `api_duration_ms`,
- `agents`/`tools`/`effort`: subagentes, ferramentas, thinking (Trilha B, via C2),
- `verification` (LEB): `passed`, `regression`, `probes_corrigidas/total`.
- **métrica ausente = `null`, nunca `0`** (§10.3).

## 5. Adicionar um vendor (Trilha A)

Em [../config/gateways.json](../config/gateways.json): um gateway `kind:openai` (ou
`anthropic`) com `base_url`/`path`/`auth`/`key_env`. Marque `validate:true` até
confirmar o endpoint num smoke de 1 chamada. Em `models.json`: entradas referenciando
o gateway, com **id datado + preço** do provider (roster = decisão sua, §14 Q1).

## 6. Adicionar um harness (Trilha B)

Em [../config/harnesses.json](../config/harnesses.json): registre o harness. Só o
`claude-code` tem adapter — pros outros (Kilo/Cline/Cursor) é preciso **validar o
surface** (rodar `--help` + um `-p`, ver o formato de saída machine + onde grava o
transcript) e **implementar o adapter** (`build_argv`/`parse_result`/`parse_c2`),
igual foi feito pro Claude Code ([harness-matrix.md](../config/harness-matrix.md)).
Enquanto não tiver adapter, o `track_b` recusa com mensagem clara.

## 7. Auditoria de isenção (sempre)

Todo `run.sh` roda o **bloco A** (A1–A14) antes de executar — se falhar, **aborta**.
`audit_passed` = mecanicamente limpo; `campaign_ready` = também com A5 (canário)/A9/
A12/A14 satisfeitos. Pra uma campanha pontuada, exija `campaign_ready=true`.
```bash
./runner/canary.sh --live        # A5 ao vivo: prova que MCP não previsto não vaza
./runner/run.sh --task noop --audit-only   # só audita, sem executar
```
