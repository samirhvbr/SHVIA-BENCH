# SHVIA-BENCH — Ambiente Isolado para Benchmark de Modelos

**Projeto:** SHVIA-BENCH
**Versão do documento:** 0.2 (revisão com catálogo completo de métricas)
**Data:** 18/07/2026
**Nível de isolamento adotado:** perfil/diretório separado na mesma máquina, execução CLI-only
**Escopo de teste:** Trilha A (modelo puro via API) + Trilha B (modelo dentro de harness de agente), comparadas

**Mudanças da v0.1 → v0.2:** instrumentação em três camadas (§10.1), catálogo completo de métricas com 60+ campos (§10.2), proxy local de API para métricas de verdade-base (§4.4), esforço/raciocínio como parâmetro congelado (V17), medição de autonomia (§10.5), contexto e capacidade (§10.6), captura de pensamento (§10.7), seção explicando por que isolar pela pasta não basta (§4.0).

---

## 1. Objetivo e princípio de isenção

Construir um ambiente onde a **única variável entre execuções seja o modelo** (na Trilha A) ou **o par modelo+harness** (na Trilha B). Todo o resto — prompt, ferramentas, estado do disco, variáveis de ambiente, nível de esforço, ordem de execução, critério de nota — precisa ser idêntico e reprodutível.

**Princípio norteador:**

> Nenhuma execução pode ter acesso a informação que outra execução não tenha. Se um artefato não pode ser recriado do zero a partir do repositório de benchmark, ele não entra na execução.

Isso implica cortar: `ai-memory` e qualquer MCP de memória, histórico de conversa, arquivos de contexto do agente, cache local, RAG/indexação de repositório e telemetria.

### 1.1 Um alerta metodológico

Isolamento **não** produz isenção sozinho. Um ambiente estéril rodando tarefas de benchmark público continua enviesado por contaminação de treino, e N=1 mede ruído amostral. As seções 8 e 9 tratam disso; sem elas, o isolamento vira teatro.

---

## 2. Escopo

### Dentro do escopo

- Perfil de execução isolado (HOME sandbox, config própria, ambiente sanitizado), CLI-only
- Proxy local de API para instrumentação e auditoria de tráfego
- Runner com manifesto e hash de reprodutibilidade
- Trilha A: chamadas diretas de API, sem ferramentas
- Trilha B: mesma tarefa dentro de um harness de agente
- Suíte de tarefas versionada, com verificadores automáticos e rubrica
- Catálogo completo de métricas: tempo, tokens, throughput, custo, agentes/subagentes, contexto, esforço, raciocínio, autonomia
- Checklist de auditoria pré/durante/pós execução

### Fora do escopo (nesta fase)

- Containerização ou VM dedicada (Fase 4 — §13)
- Bloqueio de rede em nível de firewall
- Benchmark de custo de infraestrutura self-hosted
- Avaliação de segurança/alinhamento
- Sessões interativas ou com humano no loop

---

## 3. Modelo de contaminação: vetores a cortar

| # | Vetor | Como contamina | Contramedida |
|---|-------|----------------|--------------|
| V1 | **Memória persistente** (`ai-memory`, MCP de memória) | Modelo A recebe fatos de execução anterior; B não | MCP allowlist vazia; HOME sandbox sem arquivos de memória |
| V2 | **Histórico de conversa / sessões** | Retomada injeta contexto anterior | Sessão nova sempre; nunca `--continue`/`--resume`; `$CLAUDE_CONFIG_DIR/projects/` efêmero |
| V3 | **Arquivos de contexto** (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.claude/rules/`, `.claude/agents/`) | Instruções invisíveis; herdadas do repo pai ou do HOME real | Workspace efêmero; varredura pré-execução (§11) |
| V4 | **Settings em camadas** | Claude Code mescla ~5 camadas: user, projeto, local, flags de CLI e a camada *managed*, que é o piso de política e não pode ser relaxada por flags | `CLAUDE_CONFIG_DIR` isolado + `--settings` explícito + ausência de `managed-settings.json` |
| V5 | **Servidores MCP herdados** | Ferramentas extras dão vantagem | `--mcp-config` + `--strict-mcp-config` — com ressalvas, §6.2 |
| V6 | **Variáveis de ambiente herdadas** | `ANTHROPIC_MODEL`, `CLAUDE_CODE_EFFORT_LEVEL`, `MAX_THINKING_TOKENS`, `ANTHROPIC_BASE_URL` mudam o que está sendo testado | `env -i` + allowlist explícita |
| V7 | **`.env` e `~/.claude/env`** | Claude Code lê `.env` na raiz do projeto e `~/.claude/env` | Workspace sem `.env`; HOME sandbox sem `env` |
| V8 | **Estado do FS entre tarefas** | `node_modules`, build cache, sobras da execução anterior | Workspace recriado do *golden state* a cada execução |
| V9 | **Cache de prompt do servidor** | Latência e custo despencam no segundo run — mede o cache, não o modelo | Desativar na Trilha A; registrar `cache_read_input_tokens` e tratar como covariável na B |
| V10 | **Roteamento não determinístico do agregador** | OpenRouter balanceia entre provedores do mesmo modelo, com quantizações e latências diferentes | `provider.order` + `allow_fallbacks: false`, ou `provider.only`; registrar provedor efetivo |
| V11 | **Telemetria e tráfego não essencial** | Ruído de latência e vazamento | `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` (agrupa autoupdater, bug command, error reporting, telemetria) |
| V12 | **Auto-update do harness** | Versão muda no meio da campanha | `DISABLE_AUTOUPDATER=1` + versão fixada no manifesto |
| V13 | **Autocompactação de contexto** | Compacta em pontos diferentes por modelo | `DISABLE_AUTOCOMPACT=1`; caso com compactação é inválido |
| V14 | **Ordem de execução** | Aquecimento, rate limit, horário | Ordem randomizada e intercalada, semente registrada |
| V15 | **Contaminação de treino** | Tarefa pública já vista no treinamento | ≥50% tarefas privadas; reportar separado |
| V16 | **Viés do avaliador** | Juiz sabe qual modelo produziu; ou é concorrente | Avaliação cega, anonimizada; juiz não concorrente |
| **V17** | **Nível de esforço e orçamento de raciocínio** | `--effort` / `CLAUDE_CODE_EFFORT_LEVEL` (low…max) e `MAX_THINKING_TOKENS` mudam radicalmente qualidade, custo e tempo. Comparar "max" contra padrão não é benchmark, é propaganda | Fixar explicitamente por campanha, registrar no manifesto; rodar campanhas separadas por nível |
| **V18** | **Tier de serviço e geografia de inferência** | O `usage` retorna `service_tier`, `speed` e `inference_geo`; tier prioritário ou região diferente muda latência sem mudar o modelo | Registrar sempre; descartar caso cujo tier divirja do resto do lote |

---

## 4. Arquitetura do ambiente isolado

### 4.0 Por que isolar "na pasta onde o CLI foi iniciado" não basta

Resposta direta à pergunta: **a execução pode ser CLI-only, sim — mas o diretório de trabalho não é a fronteira de isolamento.** Um `claude` iniciado dentro de `~/bench/tarefa-01/` continua lendo, por fora dessa pasta:

| O que ele lê fora do CWD | Onde fica | O que vaza |
|--------------------------|-----------|------------|
| Settings de usuário | `~/.claude/settings.json` | Permissões, env, modelo padrão, hooks |
| Estado de projetos e MCP desabilitados | `~/.claude.json` | `disabledMcpServers`, histórico de projetos |
| Variáveis de ambiente do harness | `~/.claude/env` | Qualquer variável, inclusive modelo e esforço |
| Transcrições de sessões anteriores | `~/.claude/projects/<projeto>/<sessão>.jsonl` | Base para retomada de sessão |
| Subagentes de usuário | `~/.claude/agents/*.md` | Comportamento extra não previsto |
| MCP de usuário | `~/.mcp.json` e config equivalente | Ferramentas extras, incluindo memória |
| Camada corporativa gerenciada | `managed-settings.json` / `managed-mcp.json` | Piso de política que sobrepõe até flags de CLI |
| Hierarquia de `CLAUDE.md` | pasta atual **e diretórios acima** | Instruções herdadas do repo pai |
| Ambiente do shell | processo pai | `ANTHROPIC_*`, `CLAUDE_CODE_*`, proxies |

Ou seja: pasta nova, contaminação velha. **A fronteira real é `HOME` + ambiente de processo.** É por isso que o runner usa `env -i` mais um `HOME` sandbox mais `CLAUDE_CONFIG_DIR` — tudo na mesma máquina, sem container, sem VM, e sem interferir na sua instalação normal do Claude Code, que continua intacta no seu `HOME` de verdade.

Efeito colateral positivo: com o `HOME` sandboxado, as transcrições JSONL da sessão caem dentro de `$CLAUDE_CONFIG_DIR/projects/` — que é justamente a fonte mais rica de métricas (§10.1) e é descartada/arquivada por execução automaticamente.

### 4.1 Estrutura de diretórios

```
~/shvia-bench/
├── README.md
├── manifest.schema.json
├── config/
│   ├── profile.template/             # HOME sandbox versionado
│   │   └── .claude/settings.json     # settings mínimo e explícito
│   ├── mcp.empty.json                # {"mcpServers": {}}
│   ├── models.yaml                   # modelos, pinagem de provedor, preços
│   ├── harness-matrix.md             # controles por harness
│   └── run.defaults.yaml
├── proxy/
│   └── logging_proxy.py              # instrumentação de verdade-base (§4.4)
├── tasks/
│   ├── T-000-noop/                   # tarefa vazia: mede overhead do harness
│   ├── T-001-refactor-parser/
│   │   ├── task.yaml
│   │   ├── prompt.md
│   │   ├── golden/                   # estado inicial imutável
│   │   └── verify.sh
│   └── ...
├── runner/
│   ├── run.sh                        # entrypoint sanitizado
│   ├── track_a.py
│   ├── track_b.py
│   ├── collect.py                    # consolida as 3 camadas de instrumentação
│   └── audit.sh
├── runs/                             # SOMENTE ESCRITA
│   └── 2026-07-18T14-02-11Z_a1b2c3/
│       ├── manifest.json
│       ├── results.jsonl
│       ├── proxy.jsonl
│       ├── transcripts/
│       ├── audit.json
│       └── artifacts/
└── work/                             # workspaces efêmeros
```

**Regra dura:** nenhum processo de execução lê de `runs/`. Isso impede que resultados anteriores realimentem execuções futuras.

### 4.2 Sanitização de ambiente

```bash
#!/usr/bin/env bash
# runner/run.sh
set -euo pipefail

BENCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX_HOME="$BENCH_ROOT/profile"
RUN_ID="$(date -u +%Y-%m-%dT%H-%M-%SZ)_$(openssl rand -hex 3)"

rm -rf "$SANDBOX_HOME"
cp -R "$BENCH_ROOT/config/profile.template" "$SANDBOX_HOME"

exec env -i \
  PATH="/usr/local/bin:/usr/bin:/bin" \
  HOME="$SANDBOX_HOME" \
  XDG_CONFIG_HOME="$SANDBOX_HOME/.config" \
  XDG_CACHE_HOME="$SANDBOX_HOME/.cache" \
  XDG_DATA_HOME="$SANDBOX_HOME/.local/share" \
  TERM="dumb" LANG="C.UTF-8" TZ="UTC" \
  CLAUDE_CONFIG_DIR="$SANDBOX_HOME/.claude" \
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
  DISABLE_AUTOUPDATER=1 \
  DISABLE_AUTOCOMPACT=1 \
  CLAUDE_CODE_EFFORT_LEVEL="$EFFORT_PINNED" \
  MAX_THINKING_TOKENS="$THINKING_PINNED" \
  ANTHROPIC_BASE_URL="http://127.0.0.1:8787" \
  SHVIA_RUN_ID="$RUN_ID" \
  SHVIA_TASK_ID="$TASK_ID" \
  ANTHROPIC_API_KEY="$(cat "$BENCH_ROOT/.secrets/anthropic")" \
  "$@"
```

Notas:

- `CLAUDE_CONFIG_DIR` é o mecanismo documentado para apontar a config para um perfil limpo.
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` funciona como interruptor único; mantive os granulares por redundância.
- `CLAUDE_CODE_EFFORT_LEVEL` e `MAX_THINKING_TOKENS` **sempre explícitos** — nunca herdados, nunca omitidos (V17).
- `ANTHROPIC_BASE_URL` aponta para o proxy local (§4.4).
- `TZ=UTC` e `LANG=C.UTF-8` eliminam variação de formatação nas saídas.
- Chaves vêm de arquivo fora do versionamento, injetadas individualmente — nunca `source .env`.

### 4.3 Workspace efêmero por tarefa

```bash
WORK="$BENCH_ROOT/work/$RUN_ID/$TASK_ID"
rm -rf "$WORK" && mkdir -p "$WORK"
cp -R "$BENCH_ROOT/tasks/$TASK_ID/golden/." "$WORK/"
git -C "$WORK" init -q
GIT_AUTHOR_DATE="2026-01-01T00:00:00Z" GIT_COMMITTER_DATE="2026-01-01T00:00:00Z" \
  git -C "$WORK" commit -q --allow-empty -m "baseline"
```

Datas fixas evitam inferência a partir de timestamp e mantêm hashes reprodutíveis.

### 4.4 Proxy local de API — a camada de verdade-base

O harness reporta métricas agregadas. Para medir TTFT, tokens por segundo reais e uso por chamada — e para **provar** que nenhum tráfego inesperado saiu —, insira um proxy de logging entre o CLI e a API:

```
CLI  ──►  127.0.0.1:8787 (proxy)  ──►  api.anthropic.com
              │
              └──► runs/<run_id>/proxy.jsonl
```

Para cada requisição, registrar: timestamp de envio, timestamp do primeiro byte de conteúdo (**TTFT**), timestamp final, host de destino, modelo, tamanho do corpo, `usage` completo da resposta, headers relevantes (`x-openrouter-provider`, request-id), e `stop_reason`.

Por que vale o esforço:

1. **TTFT e TPS reais** por chamada, que o resultado agregado do harness não dá.
2. **Auditoria de destino**: qualquer host fora da allowlist aparece no log. É a prova empírica de que a telemetria está mesmo desligada.
3. **Paridade entre trilhas**: as duas trilhas passam pelo mesmo instrumento, então as métricas são comparáveis por construção.
4. **Independência de versão do harness**: mudança de nome de campo no JSON do CLI não quebra a coleta.

O proxy não modifica corpo nem headers. Se modificar, ele vira uma variável do experimento.

---

## 5. Trilha A — Modelo puro

### 5.1 Definição

Uma requisição, sem ferramentas, sem loop de agente, sem sistema de arquivos. A tarefa é apresentada inteiramente no prompt.

### 5.2 Parâmetros congelados

| Parâmetro | Valor | Justificativa |
|-----------|-------|---------------|
| `temperature` | 0.0 | Reduz variância (não elimina — §12) |
| `top_p` | 1.0 | Não empilhar dois mecanismos de amostragem |
| `max_tokens` | fixo por tarefa | Um modelo não pode ser cortado e outro não |
| System prompt | idêntico, mínimo, versionado | Prompt "otimizado para o modelo X" invalida a comparação |
| Cache de prompt | desativado | V9 |
| Streaming | **ativado** | Necessário para TTFT; corpo remontado para avaliação |
| Raciocínio | budget explícito e igual | V17 |
| `seed` | quando suportado | Nem todo provedor honra |

### 5.3 Cliente mínimo

```python
# runner/track_a.py (esqueleto)
import time, hashlib, httpx

def run_case(model_cfg: dict, prompt: str, task_id: str) -> dict:
    body = {
        "model": model_cfg["id"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0, "top_p": 1.0,
        "max_tokens": model_cfg["max_tokens"],
        "stream": True,
    }
    if model_cfg.get("thinking_budget") is not None:
        body["thinking"] = {"type": "enabled",
                            "budget_tokens": model_cfg["thinking_budget"]}
    if model_cfg.get("provider_pin"):
        body["provider"] = {"order": model_cfg["provider_pin"],
                            "allow_fallbacks": False}

    t0 = time.perf_counter(); ttft = None
    chunks, usage = [], None
    with httpx.stream("POST", model_cfg["endpoint"], json=body,
                      headers=model_cfg["headers"], timeout=900) as r:
        r.raise_for_status()
        provider = r.headers.get("x-openrouter-provider")
        for line in r.iter_lines():
            if not line:
                continue
            if ttft is None:
                ttft = (time.perf_counter() - t0) * 1000
            chunks.append(line)   # parse do SSE omitido
    total_ms = (time.perf_counter() - t0) * 1000

    return {
        "task_id": task_id, "track": "A", "model": model_cfg["id"],
        "provider_effective": provider,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "ttft_ms": round(ttft, 1),
        "e2e_ms": round(total_ms, 1),
        "generation_ms": round(total_ms - ttft, 1),
        "usage": usage,
        "raw_stream": chunks,
    }
```

`provider_effective` permite provar depois que dois runs do "mesmo modelo" não foram servidos por backends diferentes — sem pinagem, o agregador balanceia entre provedores saudáveis, e alguns servem variantes mais quantizadas.

---

## 6. Trilha B — Modelo dentro do harness

### 6.1 Definição

Mesma tarefa, com ferramentas, em workspace efêmero, modo não interativo, até conclusão ou limite.

### 6.2 Invocação (Claude Code)

```bash
claude -p "$(cat prompt.md)" \
  --model "$MODEL" \
  --effort "$EFFORT_PINNED" \
  --settings "$BENCH_ROOT/config/cc.settings.json" \
  --mcp-config "$BENCH_ROOT/config/mcp.empty.json" \
  --strict-mcp-config \
  --max-turns 30 \
  --max-budget-usd 2.00 \
  --output-format stream-json --verbose --include-partial-messages \
  > "$RUN_DIR/stream/$CASE_ID.jsonl"
```

- `stream-json` + `--include-partial-messages` dá eventos incrementais, necessários para TTFT por turno.
- `--max-budget-usd` é kill-switch de orçamento; quando estoura, o resultado vem com `subtype: "error_max_budget_usd"`. Trate como `status: "budget_exceeded"`, nunca como falha do modelo.
- `--max-turns` evita loop infinito; estouro vem como `error_max_turns`.

**Ressalvas verificadas — não confie cegamente nas flags:**

1. `--strict-mcp-config` limita quais arquivos de configuração carregam, mas não contorna allowlists/denylists corporativas.
2. Há issues abertas relatando que `--strict-mcp-config` não sobrepõe `disabledMcpServers` do `~/.claude.json`, e versões em que `--mcp-config`/`--strict-mcp-config` foram ignorados por completo.
3. Portanto: **valide empiricamente**. O item A5 do checklist (§11) existe para isso.

**Outros harnesses:** mapear (a) diretório de config, (b) arquivos de regras, (c) mecanismo de MCP, (d) armazenamento de histórico, (e) variáveis reconhecidas, (f) **quais métricas do §10 são obteníveis**. Registrar em `config/harness-matrix.md` e revalidar a cada versão.

### 6.3 Limites de execução

| Limite | Valor | Motivo |
|--------|-------|--------|
| Turnos máximos | 30 | Evita loop consumindo orçamento |
| Timeout por tarefa | 15 min | Comparabilidade e previsibilidade |
| Orçamento por caso | US$ 2,00 | Kill-switch |
| Permissões | modo automático restrito ao workspace | Sem `--add-dir` |
| Rede dentro do agente | negada | Tarefa que exige rede é categoria à parte |
| Agent teams / paralelismo multiagente | desativado | Recurso experimental, adiciona variância |

---

## 7. Comparabilidade entre as trilhas

A e B medem coisas diferentes: **A** = capacidade bruta em resposta única; **B** = par modelo+harness (uso de ferramentas, recuperação de erro, decisão de parar).

| Comparação | Válida? | Observação |
|------------|---------|------------|
| Modelo X vs Y na trilha A | Sim | Comparação principal |
| Modelo X vs Y na trilha B, mesmo harness | Sim | Comparação principal |
| Trilha A vs B do mesmo modelo | Sim, como **delta de harness** | Quantifica o que o harness agrega |
| Modelo X em B vs modelo Y em A | **Não** | Confunde duas variáveis |
| Nota agregada A+B | **Não** | Sem justificativa para os pesos |

Métrica derivada: **ganho de harness** = `score_B(m) − score_A(m)`. Ganho negativo = harness atrapalha aquele modelo. Informação acionável para o SHVIA-BENCH.

Marque cada tarefa com `tracks: [A]`, `[B]` ou `[A, B]` — nem toda tarefa é expressável nas duas.

---

## 8. Protocolo de execução e reprodutibilidade

### 8.1 Manifesto

Gerado **antes** da primeira chamada:

```json
{
  "run_id": "2026-07-18T14-02-11Z_a1b2c3",
  "created_utc": "2026-07-18T14:02:11Z",
  "operator": "samir",
  "suite_version": "tasks@v0.3.1",
  "suite_sha256": "9f2c...",
  "git_commit": "e41ab77",
  "tracks": ["A", "B"],
  "models": [
    {
      "alias": "M1",
      "id": "anthropic/claude-opus-4-8",
      "endpoint": "https://api.anthropic.com/v1/messages",
      "provider_pin": null,
      "context_window_declared": 200000,
      "price_per_mtok": {"input": 15.0, "output": 75.0,
                         "cache_write": 18.75, "cache_read": 1.5},
      "params": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 8192,
                 "thinking_budget": 8000}
    }
  ],
  "harnesses": [
    {"name": "claude-code", "version": "2.1.212", "config_sha256": "1a7b..."}
  ],
  "effort": {"level": "high", "max_thinking_tokens": 8000},
  "repetitions": 5,
  "order_seed": 20260718,
  "budget_per_case_usd": 2.00,
  "isolation": {
    "profile": "sandbox-home", "env_sanitized": true,
    "mcp_servers": [], "memory_backends": [],
    "telemetry_disabled": true, "proxy_enabled": true
  },
  "audit_passed": true
}
```

Se `audit_passed` for `false`, o runner aborta. Auditoria é bloqueante.

### 8.2 Regras de reprodutibilidade

1. **Versão de modelo fixada** — identificador datado. Alias "latest" é proibido.
2. **Versão do harness fixada** e registrada; auto-update desligado (V12).
3. **Nível de esforço fixado e idêntico** entre modelos da mesma campanha (V17). Comparar níveis diferentes exige campanhas separadas, reportadas separadamente.
4. **N ≥ 5 repetições** por par (tarefa, modelo). Reporte mediana e dispersão, nunca só média.
5. **Ordem randomizada e intercalada**, semente registrada.
6. **Prompt por hash** — hashes diferentes entre modelos invalidam o resultado.
7. **Sem reparo manual** — falha de infra vira `status: "infra_error"`, reexecutada em lote separado, nunca substituída em silêncio.
8. **Suíte congelada em tag** antes da campanha.

### 8.3 Orçamento

`custo ≈ modelos × tarefas × repetições × trilhas × custo_médio`. Com 4 modelos, 30 tarefas, 5 repetições, 2 trilhas: 1.200 execuções. Use `--max-budget-usd` por caso e um teto agregado por run.

---

## 9. Suíte de tarefas e pontuação

### 9.1 Composição

| Categoria | Peso | Verificação |
|-----------|------|-------------|
| Correção funcional | 30% | Automática (`verify.sh`) |
| Refatoração em base existente | 20% | Automática + rubrica |
| Depuração a partir de stack trace | 15% | Automática |
| Compreensão e explicação de código | 10% | Rubrica cega |
| Aderência a instruções | 10% | Automática |
| Recuperação de erro e uso de ferramentas (só B) | 15% | Automática + logs |

Mais a tarefa `T-000-noop`, que não pontua: existe só para medir o overhead fixo do harness (§10.6).

### 9.2 Anatomia de uma tarefa

```yaml
# tasks/T-001-refactor-parser/task.yaml
id: T-001
title: "Refatorar parser de configuração para suportar TOML"
tracks: [A, B]
provenance: private          # private | public | derived
created: 2026-07-10
difficulty: medium
max_tokens: 8192
timeout_s: 900
max_turns: 30
budget_usd: 2.00
verification:
  type: automatic
  command: ./verify.sh
  weight: 0.7
rubric:
  - id: R1
    criterion: "Mantém compatibilidade com a API pública existente"
    levels: {0: "quebra a API", 1: "quebra parcialmente", 2: "preserva"}
    weight: 0.15
  - id: R2
    criterion: "Trata erro de parsing sem engolir exceção"
    levels: {0: "engole", 1: "trata parcialmente", 2: "propaga com contexto"}
    weight: 0.15
```

### 9.3 Regras de pontuação

- **Automático primeiro.** Rubrica é complemento, não substituto.
- **Rubrica com níveis ancorados** (0/1/2 descritos). "Qualidade: 1 a 5" não é rubrica, é opinião.
- **Avaliação cega**, saídas anonimizadas e embaralhadas — obrigatório também para juiz humano.
- **Juiz não concorrente**; calibre contra ≥20 casos avaliados manualmente e reporte a concordância.
- **Contaminação separada**: score em tarefas privadas reportado à parte do score em públicas.

---

## 10. Instrumentação e catálogo de métricas

### 10.1 Três camadas de coleta

| Camada | Fonte | O que só ela dá | Fragilidade |
|--------|-------|-----------------|-------------|
| **C1 — Resultado do harness** | objeto `result` do `--output-format json` | `total_cost_usd`, `num_turns`, `duration_ms`, `duration_api_ms`, `modelUsage[].contextWindow` e `maxOutputTokens`, `subtype` de erro | Nomes de campo mudam entre versões |
| **C2 — Transcrição JSONL** | `$CLAUDE_CONFIG_DIR/projects/<proj>/<sessão>.jsonl` | Blocos de `thinking`, cada `tool_use`/`tool_result`, `usage` por turno, cadeia `uuid`/`parentUuid`, sidechains de subagente (`isSidechain`, `agentId`) | Formato interno, sem contrato de estabilidade |
| **C3 — Proxy local** | `runs/<id>/proxy.jsonl` | TTFT real por chamada, tempo de rede, `usage` bruto da API, headers de provedor, destino de cada requisição | Nenhuma, se o proxy for passivo |

**Regra de precedência:** para custo e tokens, C3 é a verdade; C1 é conferência. Se divergirem além de 2%, o caso vai para inspeção — a divergência costuma revelar chamadas que o harness não contabiliza.

`collect.py` funde as três camadas em uma linha de `results.jsonl` por execução.

### 10.2 Catálogo de métricas

#### Identificação e contexto de execução

| Campo | Fonte | Notas |
|-------|-------|-------|
| `run_id`, `case_id`, `task_id`, `repetition` | runner | — |
| `model_alias`, `model_id_resolved` | C1/C3 | ID efetivamente servido, não o solicitado |
| `track` | runner | A ou B |
| `harness_name`, `harness_version` | manifesto | — |
| `session_id` | C1 | Chave para localizar a transcrição |
| `provider_effective` | C3 | Header do agregador |
| `service_tier`, `speed`, `inference_geo` | C3 (`usage`) | V18 |
| `started_utc`, `finished_utc` | runner | — |

#### Tempo

| Campo | Definição | Fonte |
|-------|-----------|-------|
| `e2e_ms` | **End-to-end**: do disparo do processo até o exit code. Inclui startup do CLI, MCP handshake, tudo | runner (relógio externo) |
| `harness_duration_ms` | `duration_ms` reportado pelo harness | C1 |
| `api_duration_ms` | `duration_api_ms` — só tempo em chamada de API | C1 |
| `tool_time_ms` | `harness_duration_ms − api_duration_ms` | derivado |
| `startup_overhead_ms` | `e2e_ms − harness_duration_ms` | derivado |
| `ttft_ms_first_call` | Time-to-first-token da primeira chamada | C3 |
| `ttft_ms_p50` / `p95` | Distribuição de TTFT entre todas as chamadas | C3 |
| `turn_duration_ms[]` | Duração de cada turno | C2/C3 |
| `longest_tool_call_ms` | Maior tempo de ferramenta | C2 |

`e2e_ms` é a métrica que o usuário sente; `api_duration_ms` é a que o modelo controla. Reporte as duas — a diferença entre elas é o custo do harness.

#### Tokens

| Campo | Fonte |
|-------|-------|
| `input_tokens`, `output_tokens` | C3 |
| `cache_creation_input_tokens`, `cache_read_input_tokens` | C3 |
| `cache_creation.ephemeral_5m_input_tokens` / `_1h_` | C3 |
| `reasoning_tokens` | C3, quando o provedor expõe |
| `total_tokens` | soma de todos os acima |
| `tokens_per_turn[]` | C2 |
| `server_tool_use.web_search_requests` / `web_fetch_requests` | C3 |
| `cache_hit_ratio` | `cache_read / (input + cache_read + cache_creation)` |

Não some `cache_read` com `input` num único "total" sem qualificar — têm preços muito diferentes.

#### Throughput (tokens por segundo)

Definição precisa importa aqui, porque três números diferentes são chamados de "TPS":

| Campo | Fórmula | O que mede |
|-------|---------|------------|
| `tps_generation` | `output_tokens / (tempo_de_geração_da_chamada)` | Velocidade bruta do modelo, excluindo TTFT. Comparável entre provedores |
| `tps_call` | `output_tokens / duração_total_da_chamada` | Inclui TTFT. Aproxima o que o usuário percebe por resposta |
| `tps_session` | `total_output_tokens / e2e_ms` | Vazão da sessão inteira, incluindo tempo de ferramenta. **Sempre muito menor em agentes** |

Reporte os três, rotulados. Em benchmark de agente, `tps_session` costuma ser 5 a 20 vezes menor que `tps_generation` — comparar o `tps_generation` de um harness com o `tps_session` de outro é o erro mais comum nessa família de métricas.

#### Custo

| Campo | Fonte |
|-------|-------|
| `cost_usd_harness` | `total_cost_usd` do C1 |
| `cost_usd_computed` | Recalculado: tokens (C3) × tabela de preços do manifesto |
| `cost_delta_pct` | Divergência entre os dois |
| `cost_per_task_passed` | Custo do lote ÷ tarefas aprovadas |
| `cost_by_component` | Entrada / saída / escrita de cache / leitura de cache / raciocínio |
| `budget_exceeded` | `subtype == "error_max_budget_usd"` |

Sempre recalcule o custo. O valor do harness pode refletir tabela desatualizada ou contabilidade de assinatura em vez de API. `cost_per_task_passed` é a métrica que mais reordena rankings na prática.

#### Agentes e subagentes

| Campo | Como obter |
|-------|------------|
| `subagent_count` | Nº de blocos `tool_use` com `name: "Task"` na transcrição principal (C2) |
| `subagent_types[]` | Campo `subagent_type` da entrada de cada Task |
| `subagent_max_depth` | Profundidade de aninhamento (subagente que chama subagente) |
| `subagent_max_parallel` | Máximo de Tasks simultâneas por janela de tempo |
| `subagent_tokens_total` | Soma do `usage` das transcrições sidechain |
| `subagent_tokens_share` | `subagent_tokens_total / total_tokens` |
| `subagent_wall_ms[]` | Duração de cada subagente |
| `main_agent_turns` | `num_turns` do C1 |

**Método recomendado:** contar as chamadas da ferramenta Task na transcrição principal. É robusto. Ler os arquivos sidechain dá mais detalhe, mas há uma limitação conhecida: a sessão-filha é marcada com `isSidechain: true` e `agentId`, porém **não carrega referência ao pai** (`parentSessionId`, `parentTurnId` ou `parentToolCallId`), o que torna a ligação determinística entre pai e filho impossível só pelos arquivos. Correlacione por janela de tempo e `cwd`, e trate a atribuição como aproximada. Registre `subagent_link_confidence` (`exact` | `heuristic`).

Nota adicional: eventos de streaming são emitidos apenas para a sessão principal — deltas de token de subagentes não são encaminhados. TTFT de subagente só sai do proxy (C3).

#### Contexto

| Campo | Definição | Fonte |
|-------|-----------|-------|
| `context_window` | Capacidade declarada do modelo naquela sessão | C1 (`modelUsage[].contextWindow`) |
| `max_output_tokens` | Teto de saída | C1 (`modelUsage[].maxOutputTokens`) |
| `context_peak_tokens` | Máximo, entre turnos, de `input + cache_read + cache_creation` | C2/C3 |
| `context_utilization_pct` | `context_peak_tokens / context_window` | derivado |
| `context_overhead_tokens` | Tamanho do prompt no turno 1 da tarefa `T-000-noop` — system prompt + definições de ferramenta + arquivos de contexto | C3 |
| `context_growth_curve[]` | Tokens de prompt por turno | C2 |
| `compaction_events` | Nº de compactações | C2 |

`context_overhead_tokens` é uma das métricas mais reveladoras da campanha: é o custo fixo que o harness cobra antes de o modelo ver a tarefa. Comparar esse número entre harnesses diz mais sobre eficiência de design do que qualquer benchmark de qualidade.

`compaction_events > 0` com `DISABLE_AUTOCOMPACT=1` significa que o isolamento falhou — marque o caso como inválido (V13).

#### Esforço e raciocínio

| Campo | Fonte |
|-------|-------|
| `effort_level_requested` | Manifesto (`low`/`medium`/`high`/`xhigh`/`max`/`auto`) |
| `effort_source` | `flag` \| `env` \| `setting` \| `default` |
| `max_thinking_tokens_requested` | Manifesto |
| `reasoning_tokens_actual` | C3 |
| `thinking_blocks_count` | Nº de blocos `thinking` na transcrição (C2) |
| `thinking_chars` | Total de caracteres de pensamento |
| `thinking_share` | `reasoning_tokens / output_tokens` |
| `thinking_visibility` | `raw` \| `summary` \| `count_only` \| `none` |

`effort_source` existe porque a variável de ambiente tem precedência sobre o setting equivalente. Se o campo vier como `default`, a execução não é comparável — o esforço não foi controlado.

#### Autonomia

| Campo | Como derivar |
|-------|--------------|
| `completed_without_asking` | Booleano composto — ver §10.5 |
| `clarification_requested` | Saída final é pergunta ao usuário sem trabalho executado |
| `permission_blocked_count` | Nº de ferramentas negadas por política |
| `tool_error_count` | Nº de `tool_result` com erro |
| `self_recovered_count` | Erros seguidos de tentativa alternativa bem-sucedida |
| `recovery_rate` | `self_recovered / tool_error_count` |
| `stop_reason`, `terminal_reason`, `subtype` | C1 |
| `hit_max_turns` | `subtype == "error_max_turns"` |
| `abandoned_early` | Encerrou sem tentar verificar o próprio trabalho |

#### Ferramentas

| Campo | Fonte |
|-------|-------|
| `tool_calls_total` | C2 |
| `tool_calls_by_name{}` | C2 — histograma (Read, Edit, Bash, Task…) |
| `files_read`, `files_written`, `lines_changed` | C2 + diff do workspace |
| `bash_commands[]` | C2 — útil para auditoria de comportamento |
| `redundant_reads` | Mesmo arquivo lido mais de uma vez sem alteração no meio |

`redundant_reads` é um bom indicador de eficiência de contexto: modelo que relê o que já tem está desperdiçando janela.

### 10.3 Paridade entre trilhas e entre harnesses

Nem toda métrica existe nas duas trilhas. Métrica ausente é `null`, **nunca zero** — um harness que não expõe contagem de subagente não tem "0 subagentes".

| Família | Trilha A | Trilha B |
|---------|----------|----------|
| Tempo, tokens, throughput, custo | Sim | Sim |
| Contexto usado / capacidade | Sim | Sim |
| Raciocínio | Sim | Sim |
| Subagentes, ferramentas, autonomia, recuperação | Não aplicável | Sim |
| Overhead de harness | Zero por definição | Medido |

Antes de comparar dois harnesses, preencha `config/harness-matrix.md` marcando cada métrica como `nativa`, `derivável do proxy` ou `indisponível`. Compare só o que os dois têm.

### 10.4 Esquema de `results.jsonl`

```json
{
  "run_id": "2026-07-18T14-02-11Z_a1b2c3",
  "case_id": "T-001/M1/B/rep3",
  "task_id": "T-001", "model_alias": "M1", "track": "B", "repetition": 3,
  "model_id_resolved": "claude-opus-4-8",
  "session_id": "75e2167f-...",
  "harness": {"name": "claude-code", "version": "2.1.212"},
  "provider_effective": "anthropic",
  "service_tier": "standard", "speed": "standard", "inference_geo": "",
  "started_utc": "2026-07-18T14:07:02Z",
  "status": "completed",
  "stop_reason": "end_turn", "terminal_reason": "completed", "subtype": "success",
  "prompt_sha256": "3fa8...",

  "time": {
    "e2e_ms": 191204, "harness_duration_ms": 184320, "api_duration_ms": 96110,
    "tool_time_ms": 88210, "startup_overhead_ms": 6884,
    "ttft_ms_first_call": 1420, "ttft_ms_p50": 980, "ttft_ms_p95": 2310,
    "longest_tool_call_ms": 21400
  },
  "tokens": {
    "input_tokens": 41230, "output_tokens": 6180,
    "cache_creation_input_tokens": 11699, "cache_read_input_tokens": 38400,
    "reasoning_tokens": 2140, "total_tokens": 99649,
    "cache_hit_ratio": 0.42
  },
  "throughput": {
    "tps_generation": 64.3, "tps_call": 51.7, "tps_session": 32.3
  },
  "cost": {
    "cost_usd_harness": 0.412, "cost_usd_computed": 0.409,
    "cost_delta_pct": 0.7, "budget_exceeded": false,
    "by_component": {"input": 0.061, "output": 0.093,
                     "cache_write": 0.219, "cache_read": 0.036}
  },
  "agents": {
    "main_agent_turns": 12, "subagent_count": 3,
    "subagent_types": ["general-purpose", "general-purpose", "code-reviewer"],
    "subagent_max_depth": 1, "subagent_max_parallel": 2,
    "subagent_tokens_total": 28400, "subagent_tokens_share": 0.29,
    "subagent_link_confidence": "heuristic"
  },
  "context": {
    "context_window": 200000, "max_output_tokens": 64000,
    "context_peak_tokens": 91320, "context_utilization_pct": 45.7,
    "context_overhead_tokens": 14820, "compaction_events": 0
  },
  "effort": {
    "effort_level_requested": "high", "effort_source": "flag",
    "max_thinking_tokens_requested": 8000,
    "thinking_blocks_count": 9, "thinking_chars": 8412,
    "thinking_share": 0.35, "thinking_visibility": "raw"
  },
  "autonomy": {
    "completed_without_asking": true, "clarification_requested": false,
    "permission_blocked_count": 0, "tool_error_count": 4,
    "self_recovered_count": 4, "recovery_rate": 1.0,
    "hit_max_turns": false, "abandoned_early": false
  },
  "tools": {
    "tool_calls_total": 27,
    "by_name": {"Read": 11, "Edit": 6, "Bash": 7, "Task": 3},
    "files_read": 9, "files_written": 4, "lines_changed": 213,
    "redundant_reads": 2
  },
  "verification": {"passed": true, "exit_code": 0},
  "rubric": {"R1": 2, "R2": 1},
  "score": 0.86,
  "artifacts_path": "artifacts/T-001_M1_B_rep3/"
}
```

`status` aceita: `completed`, `failed_verification`, `pending_verification`, `timeout`, `max_turns`, `budget_exceeded`, `infra_error`, `refused`, `invalid_isolation`. Nunca colapse `infra_error` em `failed_verification`.

**Dois eixos, não um (0.7.0).** Esta regra em prosa foi violada na prática — em
19/07/2026 um erro transitório de API virou `failed_verification` numa rep paga
que havia PASSADO no verify do LEB. A causa: `status` era emitido pelo harness,
que não tem acesso ao veredito, com um `.get(subtype, "failed_verification")` de
fallback. Prosa não basta; agora é estrutura (`runner/status.py`):

- **`harness_outcome`** (`ok`/`timeout`/`infra_error`/`budget_exceeded`/`max_turns`)
  responde *"a execução produziu uma medição válida?"*. É o que o harness sabe.
- **`verification`** responde *"a entrega passou no verificador da tarefa?"*. É a
  única fonte autorizada de `failed_verification` — juízo sobre o trabalho do modelo.
- **`status`** é função PURA de (`harness_outcome`, `verification`), arbitrada num
  único ponto (`status.resolve_status`). Desfecho de harness ruim manda no status
  mesmo com veredito aprovado: o trabalho pode estar certo, mas a medição
  (tempo/custo/turnos) está truncada — e é a medição que o benchmark publica. O
  veredito segue anexo em `verification`, como evidência.
- `pending_verification` = harness ok e veredito **ainda não medido** (ex.: verify
  do LEB diferido pro pós-run). Não medido ≠ aprovado (§10.3). Distinto de
  `verification.applicable:false`, que é *"a tarefa não tem verificador"*.

Subtype desconhecido cai em `infra_error` **com a anomalia crua anexada**
(`harness_anomaly`), nunca num fallback silencioso — `error_max_budget_usd` e
`error_max_turns` seguem sendo hipótese não validada (§15), e um dia a string real
pode ser outra.

**Agregadores devem discriminar o que ficou de fora da nota.** Contar só
`status == "completed"` foi parte de como o incidente passou despercebido: a rep
anômala sumiu da mediana sem aviso.

### 10.5 Medindo "conseguiu sem perguntar ao usuário"

Em modo `-p` o agente não consegue perguntar interativamente — ele termina. Então a detecção é por assinatura de saída, e precisa de mais de um sinal:

```python
def completed_without_asking(case) -> bool:
    return (
        case["verification"]["passed"]                      # entregou de fato
        and not case["autonomy"]["clarification_requested"] # não devolveu pergunta
        and case["autonomy"]["permission_blocked_count"] == 0
        and case["status"] == "completed"
        and case["tools"]["files_written"] > 0              # produziu artefato
    )

def clarification_requested(result_text, files_written) -> bool:
    # Pergunta ao usuário + nenhum trabalho executado
    ends_with_question = result_text.strip().endswith("?")
    asks_permission = any(p in result_text.lower() for p in
                          ["gostaria que", "posso prosseguir", "qual das",
                           "você prefere", "should i", "would you like"])
    return (ends_with_question or asks_permission) and files_written == 0
```

Cuidado com dois falsos positivos: (a) o modelo entrega o trabalho **e** termina com uma pergunta cortês — isso não é falta de autonomia, daí o `files_written > 0`; (b) a tarefa é genuinamente ambígua e perguntar é o comportamento correto. Para (b), inclua deliberadamente 2 ou 3 **tarefas-armadilha** com ambiguidade real, onde perguntar pontua **positivo** e adivinhar pontua negativo. Sem elas, você acaba premiando o modelo mais atropelador.

### 10.6 Contexto usado vs. capacidade

Três números distintos, frequentemente confundidos:

1. **Capacidade nominal** — o que o modelo suporta. Vem de `modelUsage[].contextWindow`. Registre por sessão, não da tabela de specs: o harness pode negociar variantes (uma janela de 1M, por exemplo) e o número real da sessão é o que importa.
2. **Capacidade útil** — capacidade nominal menos `context_overhead_tokens` e menos a reserva de saída. É o que sobra para a tarefa.
3. **Pico utilizado** — `context_peak_tokens`.

Meça o overhead com a tarefa `T-000-noop`: um prompt trivial ("responda OK"), executado no mesmo harness com a mesma config. O tamanho do prompt da primeira chamada, capturado no proxy, é o custo fixo do harness — system prompt, definições de ferramentas, arquivos de contexto. Compare entre harnesses; é uma das comparações mais úteis de toda a campanha e ninguém publica.

### 10.7 Captura de pensamento

O que dá para capturar depende do provedor, e a diferença é grande:

| Situação | O que se obtém | Campo `thinking_visibility` |
|----------|----------------|------------------------------|
| Extended thinking com blocos expostos | Texto do raciocínio, gravado na transcrição JSONL como blocos `thinking` | `raw` |
| Provedor que expõe resumo do raciocínio | Sumário gerado, não a cadeia literal | `summary` |
| Provedor que expõe só contagem | `reasoning_tokens` no `usage` | `count_only` |
| Sem raciocínio ou sem exposição | Nada | `none` |

Onde os blocos existem, eles ficam na transcrição JSONL junto com os blocos de texto e de `tool_use`, e podem ser extraídos com um `jq` sobre `message.content[] | select(.type=="thinking")`.

**Três ressalvas honestas:**

1. Quando o que vem é resumo, **não é a computação do modelo** — é uma descrição dela. Não trate como traço fiel de raciocínio nem compare comprimento de resumo entre provedores como se fosse "quanto o modelo pensou".
2. Pensamento é conteúdo bruto do modelo. Ele entra em `artifacts/`, com o mesmo cuidado de qualquer saída, e **não vai para o juiz** na avaliação cega — julgar o processo em vez do resultado abre uma porta enorme de viés.
3. Métrica útil derivada: `thinking_share` (raciocínio / saída total) correlacionada com o score. Se um modelo pensa muito e não pontua mais, isso é custo puro — e é exatamente o tipo de achado que justifica a campanha.

---

## 11. Checklist de auditoria de isenção

Em `runner/audit.sh`, automatizado e **bloqueante**.

### A. Pré-execução (aborta o run)

- [ ] **A1** — Perfil sandbox recriado do template nesta execução
- [ ] **A2** — Sem arquivos de memória em `$HOME`: `memory*.json`, `ai-memory`, `*.memory`
- [ ] **A3** — Sem arquivos de contexto: `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.claude/rules/`, `.claude/agents/` — em `$HOME` **e** no workspace **e** nos diretórios acima do workspace
- [ ] **A4** — Sem `.env` na raiz do workspace; sem `~/.claude/env`
- [ ] **A5** — **Canário de ferramentas**: tarefa que pede ao agente para listar suas ferramentas; falha se aparecer qualquer MCP não previsto (defesa real contra as falhas conhecidas de `--strict-mcp-config`)
- [ ] **A6** — Sem `managed-settings.json` / `managed-mcp.json`
- [ ] **A7** — Ambiente sanitizado: `env` no processo contém só a allowlist
- [ ] **A8** — `$CLAUDE_CONFIG_DIR/projects/` vazio
- [ ] **A9** — Versões de harness e modelo batem com o manifesto
- [ ] **A10** — Suíte na tag congelada; `git status` limpo em `tasks/`
- [ ] **A11** — Workspace idêntico ao `golden/` (hash recursivo)
- [ ] **A12** — Proxy no ar e `ANTHROPIC_BASE_URL` apontando para ele
- [ ] **A13** — `effort_level` e `max_thinking_tokens` explicitamente definidos e idênticos aos do manifesto
- [ ] **A14** — `T-000-noop` executada; `context_overhead_tokens` registrado para o lote

### B. Durante a execução

- [ ] **B1** — Sem sessão retomada (`--continue`/`--resume` ausentes do comando registrado)
- [ ] **B2** — `provider_effective` idêntico entre repetições do mesmo modelo
- [ ] **B3** — `prompt_sha256` idêntico entre modelos na mesma tarefa
- [ ] **B4** — Workspace destruído e recriado entre tarefas
- [ ] **B5** — `cache_read_input_tokens` dentro do esperado para a trilha
- [ ] **B6** — `compaction_events == 0`
- [ ] **B7** — Todo host em `proxy.jsonl` está na allowlist
- [ ] **B8** — `service_tier` e `speed` constantes no lote
- [ ] **B9** — `cost_delta_pct` < 2% entre custo do harness e recalculado

### C. Pós-execução

- [ ] **C1** — Nada escrito fora de `work/` e `runs/` (snapshot de `$HOME` antes/depois)
- [ ] **C2** — Todas as execuções da matriz presentes; ausências explicadas
- [ ] **C3** — Avaliação cega confirmada: sem metadados de modelo nos artefatos do juiz; blocos de pensamento removidos
- [ ] **C4** — Variância entre repetições dentro do limite; fora do limite → inspeção
- [ ] **C5** — Score público vs. privado reportado separadamente
- [ ] **C6** — Métricas ausentes gravadas como `null`, nunca como `0`
- [ ] **C7** — `manifest.json` + `results.jsonl` + `proxy.jsonl` + `transcripts/` + `audit.json` arquivados juntos e imutáveis

---

## 12. Riscos e limitações conhecidas

| Risco | Impacto | Mitigação | Residual |
|-------|---------|-----------|----------|
| Não determinismo mesmo com `temperature: 0` | Variância entre repetições | N ≥ 5, mediana e dispersão | Não eliminável |
| Flags de isolamento com bugs conhecidos | Isolamento falso | Canário A5; revalidar a cada versão | Médio |
| Formato JSONL interno muda entre versões | Coleta C2 quebra | Proxy C3 como fonte primária; testes de regressão do parser | Baixo |
| Ligação pai↔subagente não determinística | Atribuição de tokens aproximada | Contar Tasks no transcript principal; `subagent_link_confidence` | Médio |
| Contaminação de treino em tarefas públicas | Superestima um modelo | ≥50% privadas, reporte separado | Médio |
| Viés do LLM-as-judge | Distorce ranking | Juiz não concorrente, cego, calibrado | Médio |
| Isolamento por perfil, não container | Vazamento via processos/estado do SO | Auditoria C1 e B7; container na Fase 4 | Médio |
| Nível de esforço não comparável entre famílias de modelo | "high" de um ≠ "high" de outro | Reportar tokens de raciocínio reais junto do nível pedido | **Alto** |
| Suíte pequena | Não generaliza | ≥30 tarefas, ≥6 categorias | Médio |
| Deriva de modelo do lado do provedor | Não reproduz meses depois | Datar tudo; reexecutar baseline | Não eliminável |
| Custo estoura | Campanha interrompida | `--max-budget-usd` + teto agregado | Baixo |

Sobre o risco marcado como alto: níveis de esforço **não são padronizados entre fabricantes**. O "high" de um provedor pode alocar dez vezes mais raciocínio que o de outro. Por isso `reasoning_tokens_actual` é obrigatório no relatório — é ele, e não o rótulo do nível, que torna a comparação interpretável.

**O que este benchmark não mede:** bases de código reais e grandes, sessões longas, colaboração com humano no loop, comportamento sob prompts adversariais. Deixe isso escrito no relatório final.

---

## 13. Roadmap

**Fase 1 — Fundação (semana 1)**
Estrutura, `run.sh` sanitizado, perfil sandbox, proxy de logging, `audit.sh` bloco A. Tarefas `T-000-noop` e duas piloto. *Critério de saída:* canário A5 detecta um MCP plantado de propósito, e `context_overhead_tokens` medido.

**Fase 2 — Trilha A (semana 2)**
`track_a.py` com streaming, pinagem de provedor, esquema de resultados, custo recalculado. 10 tarefas. *Critério de saída:* mesmo modelo, 5 repetições, variância medida; `cost_delta_pct` < 2%.

**Fase 3 — Trilha B (semanas 3–4)**
`track_b.py`, `collect.py` fundindo as três camadas, matriz de harnesses, workspace efêmero, verificadores, tarefas-armadilha de ambiguidade. 30 tarefas. *Critério de saída:* campanha completa com auditoria verde e catálogo de métricas preenchido.

**Fase 4 — Endurecimento (semana 5+)**
Containerização por execução, allowlist de rede no proxy, relatório automatizado com intervalos de confiança, replicação por um segundo operador.

---

## 14. Questões em aberto

1. Conjunto exato de modelos da campanha inicial — define orçamento e `models.yaml`.
2. Kilo Code entra na Trilha B junto com Claude Code, ou a v1 fixa um harness e varia só o modelo?
3. Nível de esforço da campanha inicial: fixar em `high` para todos, ou rodar duas campanhas (padrão e máximo) e reportar a curva custo × qualidade?
4. Quem escreve as tarefas privadas, e como evitar viés para o estilo de um modelo?
5. Juiz humano, LLM-as-judge, ou híbrido?
6. Os resultados serão publicados? Se sim, definir desde já a política de divulgação de manifesto e suíte.

---

## 15. Referências

- Claude Code — variáveis de ambiente: https://code.claude.com/docs/en/env-vars
- Claude Code — settings e precedência: https://code.claude.com/docs/en/settings
- Claude Code — controle de MCP e config gerenciada: https://code.claude.com/docs/en/managed-mcp
- Claude Code — saída em streaming e `parent_tool_use_id`: https://code.claude.com/docs/en/agent-sdk/streaming-output
- Issue: `--mcp-config`/`--strict-mcp-config` ignorados: https://github.com/anthropics/claude-code/issues/10787
- Issue: `--strict-mcp-config` não sobrepõe `disabledMcpServers`: https://github.com/anthropics/claude-code/issues/14490
- Issue: sessão de subagente sem referência ao pai: https://github.com/anthropics/claude-code/issues/32175
- OpenRouter — roteamento de provedor e fallbacks: https://openrouter.ai/blog/insights/model-routing/
- OpenRouter — controle de roteamento por custo/provedor: https://openrouter.zendesk.com/hc/en-us/articles/51691947905051

> Nomes de campo, flags e formato interno de transcrição mudam entre versões pontuais do harness. Trate §6.2 e a camada C2 como **hipóteses a validar** a cada atualização. O canário A5 e o proxy C3 são as duas verificações que não envelhecem.
