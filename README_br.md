# SHVIA-BENCH — Ambiente isolado para benchmark de modelos de código

> ⚠️ **Antes de mexer neste repositório: `git pull`.**

🇺🇸 [English version](README.md)

**Um harness reprodutível e com controle de contaminação para avaliar LLMs em
tarefas de engenharia de software.** Spec: [`docs/ambiente-isolado.md`](docs/ambiente-isolado.md) (v0.2).

O SHVIA-BENCH **não** é rubrica de pontuação nem suíte de tarefas. É a camada
debaixo das duas: o ambiente estéril e a instrumentação que permitem comparar
modelos com isenção. Ele responde *"como rodar o modelo sem nenhuma informação
injusta, e medir tudo que aconteceu?"* — enquanto um projeto separado, o
**[LEB / AI-BENCHMARK](https://github.com/samirhvbr/AI-BENCHMARK)**, responde
*"qual é a tarefa e como se pontua a resposta?"*. O SHVIA-BENCH roda instâncias
do LEB (sua primeira fonte de tarefas) dentro de um sandbox controlado.

## O princípio único

> Nenhuma execução pode ter acesso a informação que outra não tenha. Se um
> artefato não pode ser recriado do zero a partir do repositório de benchmark,
> ele não entra na execução.

Isso corta memória persistente, histórico de conversa, arquivos de contexto do
agente, cache local, RAG/indexação e telemetria. Veja os 18 vetores de
contaminação (V1–V18) na spec.

## Duas trilhas

- **Trilha A — modelo puro:** uma requisição, sem ferramentas, sem sistema de
  arquivos. Mede capacidade bruta em resposta única.
- **Trilha B — modelo + harness:** a mesma tarefa dentro de um agente de código
  (ex.: Claude Code), em workspace efêmero, não-interativo, até concluir ou
  bater no limite. Mede o par *modelo + harness*.

Comparar A vs B do mesmo modelo quantifica o **ganho de harness**.

## Por que o isolamento é `HOME` + ambiente do processo, não a pasta de trabalho

Um CLI iniciado dentro de `~/bench/tarefa-01/` ainda lê, por fora dessa pasta:
settings de usuário, config de MCP, ambiente herdado, transcrições anteriores e
uma hierarquia de `CLAUDE.md`. Pasta nova, contaminação velha. A fronteira real é
**`HOME` + ambiente do processo** — por isso o runner usa `env -i` + um `HOME`
sandbox + `CLAUDE_CONFIG_DIR`, na mesma máquina, sem container, **sem tocar na
sua instalação real do Claude Code**. (Spec §4.0.)

## Estrutura

```
runner/run.sh        env -i + HOME sandbox + CLAUDE_CONFIG_DIR — entrypoint sanitizado (§4.2)
runner/audit.sh      auditoria pré-run bloqueante, bloco A (A1–A14) → audit.json (§11)
runner/canary.sh     canário A5: prova que um MCP plantado NÃO vaza pra dentro (live + --selftest)
runner/status.py     arbitragem ÚNICA do `status` — dois eixos ortogonais (§10.4)
runner/campaign_leb.sh   um caso LEB ponta a ponta; nunca sobrescreve resultado pago
proxy/logging_proxy.py   proxy passivo → proxy.jsonl: TTFT, usage, allowlist de destino (§4.4)
config/profile.template/ HOME sandbox versionado (settings mínimo e explícito)
config/mcp.empty.json    {"mcpServers": {}}
tasks/T-000-noop/        tarefa trivial — mede o overhead fixo de contexto do harness (§10.6)
manifest.schema.json     manifesto do run (§8.1); o run aborta se audit_passed for false
docs/rodar.md            runbook do operador (como rodar uma campanha de verdade)
tests/run_all.sh         a suíte offline inteira, num comando
runs/                    SOMENTE ESCRITA para execução. Nenhum processo que roda um
                         MODELO lê daqui — é isso que impede resultado anterior
                         realimentar run futuro (§4.1). Passos de PÓS-RUN (patch do
                         verify, reclassify, sumário) leem: rodam depois do fato e
                         não alimentam modelo nenhum.
work/                    workspaces efêmeros por tarefa
```

**`status` não é um eixo só.** `harness_outcome` (a execução produziu medição
válida?) e `verification` (a entrega passou no verificador?) são separados. Só o
verificador emite `failed_verification` — erro de API/infra é `infra_error` e nunca
vira nota do modelo. Isso é garantido por estrutura em `runner/status.py`, depois de
um erro transitório de API ter sido registrado como falha de benchmark numa rep paga
que na verdade havia passado.

## Status — Fase 1 (fundação)

- [x] `run.sh` sanitizado, perfil sandbox, auditoria bloqueante do bloco A
- [x] Proxy passivo de logging (validado offline contra um upstream dummy local)
- [x] `T-000-noop`, schema do manifesto, preflight
- [x] **Detector** do canário A5 provado com fixtures (offline)
- [ ] **Critério de saída ao vivo** — plantar um MCP real e rodar o `claude` pra
      provar o A5, e medir `context_overhead_tokens` — precisa da chave do bench
      (`.secrets/anthropic`). É o mesmo gate "smoke ao vivo" que persegue o projeto.
- [x] **Runner da Trilha A** (`track_a.py`) — **multi-vendor**: `config/gateways.json`
      cobre Anthropic (nativo) + vendors OpenAI-compat (OpenAI, xAI/Grok, DeepSeek,
      Z.ai/GLM, Novita, OpenRouter, Kilo). Streaming, corpo/auth por vendor, custo
      recalculado, N-reps + variância. Offline 18/18. Campanha real gated na chave
      de cada vendor (`.secrets/<vendor>`).
- [x] **Runner da Trilha B** (`track_b.py` + `collect.py`) — dirige o `claude -p`
      isolado, funde C1 (result) + C2 (transcript) + C3 (proxy); surface do Claude
      Code 2.1.207 validado empiricamente (`config/harness-matrix.md`); teste
      offline 30/30. Campanha real gated na chave.
- [x] **LEB wirado como fonte de tarefas da Trilha B** (`leb.py`, `campaign_leb.sh`)
      — casos reais de evolução de legado, avaliados pelo harness docker do próprio
      LEB no pós-run.
- [x] **Integridade da medição (0.7.0)** — `status` de dois eixos
      (`runner/status.py`), transcript achado por session id, resultado pago nunca
      sobrescrito, campanha retomável com `--from-rep`. Suíte offline:
      `bash tests/run_all.sh` (67 checks só na suíte de taxonomia).
- [ ] Fase 4 — campanha real (A5/A14 ao vivo, instâncias LEB, decisões §14)

## Requisitos

`bash`, `python3` (só stdlib — sem deps externas), `git`, `openssl`; `docker`
só para instâncias do LEB (não para a Fase 1). Rode `./preflight.sh` pra conferir.

## Segredos

A chave do bench mora em `.secrets/anthropic` (gitignored), injetada
individualmente pelo `run.sh`. Nunca `source`ada, nunca versionada, nunca em
argumento de CLI.
