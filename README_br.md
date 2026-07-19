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
proxy/logging_proxy.py   proxy passivo → proxy.jsonl: TTFT, usage, allowlist de destino (§4.4)
config/profile.template/ HOME sandbox versionado (settings mínimo e explícito)
config/mcp.empty.json    {"mcpServers": {}}
tasks/T-000-noop/        tarefa trivial — mede o overhead fixo de contexto do harness (§10.6)
manifest.schema.json     manifesto do run (§8.1); o run aborta se audit_passed for false
runs/                    SOMENTE ESCRITA. Nenhum processo de execução lê daqui.
work/                    workspaces efêmeros por tarefa
```

## Status — Fase 1 (fundação)

- [x] `run.sh` sanitizado, perfil sandbox, auditoria bloqueante do bloco A
- [x] Proxy passivo de logging (validado offline contra um upstream dummy local)
- [x] `T-000-noop`, schema do manifesto, preflight
- [x] **Detector** do canário A5 provado com fixtures (offline)
- [ ] **Critério de saída ao vivo** — plantar um MCP real e rodar o `claude` pra
      provar o A5, e medir `context_overhead_tokens` — precisa da chave do bench
      (`.secrets/anthropic`). É o mesmo gate "smoke ao vivo" que persegue o projeto.
- [ ] Runners da Trilha A / Trilha B (Fase 2/3)

## Requisitos

`bash`, `python3` (só stdlib — sem deps externas), `git`, `openssl`; `docker`
só para instâncias do LEB (não para a Fase 1). Rode `./preflight.sh` pra conferir.

## Segredos

A chave do bench mora em `.secrets/anthropic` (gitignored), injetada
individualmente pelo `run.sh`. Nunca `source`ada, nunca versionada, nunca em
argumento de CLI.
