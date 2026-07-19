# harness-matrix — controles por harness (spec §6.2)

Para cada harness sob teste na Trilha B, mapeamos: (a) diretório de config, (b)
arquivos de regras/contexto, (c) mecanismo de MCP, (d) armazenamento de histórico,
(e) variáveis de ambiente reconhecidas, (f) quais métricas do §10 são obteníveis.
**Revalidar a cada versão** — nomes de flag/campo mudam entre versões pontuais.

> Regra da casa (spec §15): flags e schema do harness são **hipóteses a validar**.
> O canário A5 (§11) e o proxy C3 (§4.4) são as duas verificações que não envelhecem.

---

## Claude Code — versão **2.1.207** (validado 19/07/2026)

### (a) diretório de config
`CLAUDE_CONFIG_DIR` (o run.sh aponta pro HOME sandbox: `$SANDBOX_HOME/.claude`).
A instalação real (`~/.claude`) fica intacta.

### (b) arquivos de regras/contexto (vetores V3)
`CLAUDE.md` (hierarquia do CWD **e acima** — por isso o workspace vai pra fora do
repo), `AGENTS.md`, `.claude/rules/`, `.claude/agents/`, `.cursorrules`. A
auditoria A3 varre HOME + WORK + acima do WORK.

### (c) mecanismo de MCP
`--mcp-config <configs...>` + `--strict-mcp-config` (só carrega os do --mcp-config).
**Não confiar cegamente** (bugs conhecidos §6.2) → o canário A5 valida empiricamente.

### (d) armazenamento de histórico (fonte C2)
Transcript JSONL por sessão em `$CLAUDE_CONFIG_DIR/projects/<proj>/<sessão>.jsonl`
(a confirmar com o agente). `--session-id <uuid>` fixa o id (facilita localizar o
transcript). `--no-session-persistence` e ausência de `--continue`/`--resume`
garantem sessão nova (V2). A8 exige `projects/` vazio no sandbox.

### (e) variáveis de ambiente reconhecidas
Controladas pelo `env -i` + allowlist do run.sh: `CLAUDE_CONFIG_DIR`,
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_AUTOUPDATER`,
`DISABLE_AUTOCOMPACT`, `CLAUDE_CODE_EFFORT_LEVEL`, `MAX_THINKING_TOKENS`,
`ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`. Ver spec §4.2 / A7.

### (f) invocação da Trilha B — flags CONFIRMADAS no `--help` (2.1.207)

| Flag | Estado | Uso na Trilha B |
|------|--------|-----------------|
| `-p` / `--print` | ✅ | modo não-interativo |
| `--output-format` | ✅ `text` \| `json` (result único = C1) \| `stream-json` (streaming) | `json` p/ o result object; `stream-json` p/ TTFT por turno |
| `--include-partial-messages` | ✅ | eventos incrementais (TTFT por turno) — só com stream-json |
| `--verbose` | ✅ | detalhe no stream |
| `--effort <level>` | ✅ | esforço PINADO por campanha (V17) |
| `--max-budget-usd <amount>` | ✅ | **kill-switch de orçamento** (§6.3) |
| `--strict-mcp-config` + `--mcp-config` | ✅ | isolamento de MCP (validar com A5) |
| `--settings <file-or-json>` | ✅ | settings explícito |
| `--model <model>` | ✅ | modelo do caso |
| `--permission-mode <mode>` | ✅ | modo automático restrito (§6.3) |
| `--session-id <uuid>` | ✅ | fixa a sessão → localizar o transcript C2 |
| `--no-session-persistence` | ✅ | reforça sessão nova (V2) |
| `--add-dir` | ✅ existe → **deliberadamente NÃO usamos** (§6.3: sem --add-dir) |

### Spec × 2.1.207 — validado EMPIRICAMENTE (rodando o `claude` real, 19/07/2026)

1. **`--max-turns` EXISTE e é aceito** — corrigindo achado anterior: não aparece
   no `--help`, mas `claude -p … --max-turns 1` roda normal (num_turns respeitado,
   sem "unknown option"). A spec §6.2 acerta. Mantemos `--max-budget-usd` +
   timeout de wall-clock como cinto-e-suspensório (§6.3).
2. `stream-json` é **valor** de `--output-format` (não flag solta) — spec ok.

### C1 — result object (`--output-format json`), campos REAIS 2.1.207

`type`, `subtype` (success/…), `is_error`, `result` (texto final), `stop_reason`,
`terminal_reason`, `num_turns`, `duration_ms`, `duration_api_ms`, **`ttft_ms`** +
`ttft_stream_ms` + `time_to_request_ms` (TTFT NATIVO), `total_cost_usd`,
`session_id`, `uuid`, `api_error_status`, `permission_denials[]`, `fast_mode_state`,
`usage{…}`, `modelUsage{<model>:{…}}`.
- `usage`: input_tokens, output_tokens, cache_creation_input_tokens,
  cache_read_input_tokens, cache_creation.{ephemeral_1h,ephemeral_5m}_input_tokens,
  server_tool_use.{web_search,web_fetch}_requests, **service_tier, speed,
  inference_geo** (V18), iterations[].
- `modelUsage["<model>"]`: inputTokens, outputTokens, cacheReadInputTokens,
  cacheCreationInputTokens, webSearchRequests, costUSD, **contextWindow,
  maxOutputTokens** (§10.6). Model id vem com sufixo, ex. `claude-opus-4-8[1m]`.
- **Todos os campos que a spec §10.1 assumia estão presentes** — + bônus.

### C2 — transcript JSONL, path + schema REAIS

Path: **`$CLAUDE_CONFIG_DIR/projects/<cwd-slug>/<session_id>.jsonl`** (confirmado).
`--session-id <uuid>` fixa o nome. `type` das linhas ∈ {user, assistant,
attachment, file-history-snapshot, queue-operation, last-prompt, …}; campos
presentes: **`isSidechain`**, **`message.usage`** (por turno), `uuid`/`parentUuid`
(cadeia), `sessionId`, `content[].type` (text; thinking/tool_use em sessões ricas).
Ligação pai↔subagente segue aproximada (§10.2) → janela+cwd; `subagent_link_confidence`.

### stream-json — eventos (com --include-partial-messages --verbose)

`system` (o `subtype:"init"` enumera `tools`/`mcp_servers` → detecção determinística
de MCP vazado, reforça o A5), `stream_event` (deltas → TTFT por turno), `assistant`,
`rate_limit_event`, `result`.

### Overhead do harness medido (§10.6, bônus)

Um `claude -p "responda OK"` a frio custou ~US$0.08–0.24 e consumiu **~24,5k tokens**
de entrada (input 2 + cache_creation 7204 + cache_read 17381) ANTES da tarefa — é o
`context_overhead_tokens` do Claude Code 2.1.207 (system prompt + defs de ferramenta).
Confirma o propósito da T-000-noop.

### (f') métricas §10 — obtenibilidade no Claude Code 2.1.207

| Família | Fonte |
|---|---|
| custo, tokens, cache, service_tier/speed/geo | **C1 nativo** (result.usage / modelUsage) |
| tempo (e2e/api/turnos), TTFT | **C1 nativo** (duration_*, ttft_ms) + C3 (proxy) |
| contexto (window, maxOutput, overhead) | **C1** (modelUsage) + C2/C3 (pico por turno) |
| subagentes (Task count, isSidechain) | **C2** (transcript) |
| autonomia (permission_denials, stop/terminal_reason) | **C1** |
| raciocínio (thinking blocks) | **C2** (content[].type=="thinking") |

### Ainda a confirmar (no 1º smoke com limite batido)

Strings de `subtype` em erro (`error_max_budget_usd` / `error_max_turns`) — só vi
`success`. O collect.py trata `is_error==true` OU `subtype!="success"` como erro e
mapeia os conhecidos.
