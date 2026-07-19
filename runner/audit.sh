#!/usr/bin/env bash
# runner/audit.sh — auditoria de isenção, BLOCO A (spec §11), BLOQUEANTE.
#
# Roda os checks A1–A14 que dá para verificar MECANICAMENTE (sem o modelo).
# Emite $RUN_DIR/audit.json e sai != 0 se qualquer check APLICÁVEL falhar.
# A5 (canário de ferramentas) e A14 (overhead da noop) precisam do modelo:
# ficam "deferred" aqui e são satisfeitos por runner/canary.sh e pela noop.
#
# Entradas (via env, setadas pelo run.sh — ou manualmente para rodar standalone):
#   RUN_ID TASK_ID BENCH_ROOT SANDBOX_HOME WORK GOLDEN_DIR RUN_DIR LEB_ROOT
#   EXPECTED_KEYS EFFORT_PINNED THINKING_PINNED PROXY_HOST PROXY_PORT PROXY_BASE_URL
# Opcionais:
#   AUDIT_STRICT=1          deferred de itens exigíveis em campanha vira fail
#   AUDIT_REQUIRE_PROXY=1   proxy fora do ar vira fail (A12)
#   AUDIT_REQUIRE_CANARY=1  A5 sem resultado vira fail
#   CANARY_RESULT=<path>    JSON do canary.sh ({"leaked":bool}); satisfaz A5
#   NOOP_OVERHEAD=<int>     context_overhead_tokens medido; satisfaz A14
#   EXPECT_CLAUDE_VERSION=<v> versão fixada no manifesto; satisfaz A9
set -uo pipefail   # sem -e: a auditoria coleta TODOS os achados, não para no 1º

: "${RUN_DIR:?audit.sh precisa de RUN_DIR}"
: "${SANDBOX_HOME:?audit.sh precisa de SANDBOX_HOME}"
: "${BENCH_ROOT:?audit.sh precisa de BENCH_ROOT}"
RUN_ID="${RUN_ID:-?}"; TASK_ID="${TASK_ID:-adhoc}"
WORK="${WORK:-}"; GOLDEN_DIR="${GOLDEN_DIR:-}"; LEB_ROOT="${LEB_ROOT:-}"
EXPECTED_KEYS="${EXPECTED_KEYS:-}"; EFFORT_PINNED="${EFFORT_PINNED:-}"
THINKING_PINNED="${THINKING_PINNED:-}"; PROXY_HOST="${PROXY_HOST:-127.0.0.1}"
PROXY_PORT="${PROXY_PORT:-8787}"; PROXY_BASE_URL="${PROXY_BASE_URL:-http://$PROXY_HOST:$PROXY_PORT}"
AUDIT_STRICT="${AUDIT_STRICT:-0}"; AUDIT_REQUIRE_PROXY="${AUDIT_REQUIRE_PROXY:-0}"
AUDIT_REQUIRE_CANARY="${AUDIT_REQUIRE_CANARY:-0}"
SNAP="$RUN_DIR/env.snapshot"

# --- utilidades ------------------------------------------------------------
sha256() { if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$@"; else sha256sum "$@"; fi; }
json_esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g' | tr -d '\r\n'; }
# hash de CONTEÚDO+paths de uma árvore. A função sha256 roda IN-PROCESS (loop
# while-read) — nunca via xargs, que faz execvp e não enxerga a função de shell.
hash_tree() { ( cd "$1" 2>/dev/null && find . -type f -not -path './.git/*' | LC_ALL=C sort \
    | while IFS= read -r f; do sha256 "$f"; done | sha256 | awk '{print $1}' ); }

RESULTS=()   # cada item: {"id":..,"status":..,"detail":..}
FAILS=0
DEFERRED=0
add() { # add <id> <status> <detail>
  local id="$1" st="$2" dt="$3"
  RESULTS+=("{\"id\":\"$id\",\"status\":\"$st\",\"detail\":\"$(json_esc "$dt")\"}")
  [ "$st" = "fail" ] && FAILS=$((FAILS+1))
  [ "$st" = "deferred" ] && DEFERRED=$((DEFERRED+1))
  printf '  [%-4s] %-3s  %s\n' "$st" "$id" "$dt" >&2
}
# converte deferred→fail quando exigido
defer() { # defer <id> <detail> <require_flag>
  local id="$1" dt="$2" req="$3"
  if [ "$req" = "1" ] || [ "$AUDIT_STRICT" = "1" ]; then add "$id" fail "$dt (exigido)"; else add "$id" deferred "$dt"; fi
}

echo "== audit.sh bloco A · run $RUN_ID · task $TASK_ID ==" >&2

# --- A1: sandbox recriado NESTE run ---------------------------------------
marker="$SANDBOX_HOME/.bench-run-id"
if [ -f "$marker" ] && [ "$(cat "$marker" 2>/dev/null)" = "$RUN_ID" ]; then
  add A1 pass "sandbox recriado do template neste run ($RUN_ID)"
else
  add A1 fail "marcador .bench-run-id ausente ou != run atual — sandbox não recriado"
fi

# --- A2: sem arquivos de memória no HOME sandbox ---------------------------
# Cobre tanto os stores JSON quanto o store MARKDOWN do ai-memory MCP real
# (MEMORY.md + pages em .../memory/*.md), que a v1 não pegava (V1).
mem="$(find "$SANDBOX_HOME" \
  \( -name 'memory*.json' -o -name '*.memory' -o -iname 'ai-memory' \
     -o -name 'MEMORY.md' -o -path '*/memory/*.md' -o -path '*/ai-memory/*' \) \
  2>/dev/null)"
if [ -z "$mem" ]; then add A2 pass "sem store de memória (json/markdown/ai-memory) no HOME sandbox"
else add A2 fail "arquivos de memória no HOME sandbox: $(echo "$mem" | tr '\n' ' ')"; fi

# --- A3: sem arquivos de contexto no HOME, no WORK e nos DIRETÓRIOS ACIMA ---
ctx=""
scan_ctx_dir() { # nomes de contexto num único diretório
  local d="$1"
  for n in CLAUDE.md AGENTS.md .cursorrules .clauderc; do [ -e "$d/$n" ] && ctx="$ctx $d/$n"; done
  [ -d "$d/.claude/rules" ] && ctx="$ctx $d/.claude/rules"
  [ -d "$d/.claude/agents" ] && ctx="$ctx $d/.claude/agents"
}
# HOME sandbox (nível do CLAUDE_CONFIG_DIR e da raiz do HOME)
scan_ctx_dir "$SANDBOX_HOME"
scan_ctx_dir "$SANDBOX_HOME/.claude"
# workspace + tudo dentro dele
if [ -n "$WORK" ] && [ -d "$WORK" ]; then
  found_in_work="$(find "$WORK" \( -name CLAUDE.md -o -name AGENTS.md -o -name .cursorrules -o -name .clauderc \) 2>/dev/null)"
  [ -n "$found_in_work" ] && ctx="$ctx $(echo "$found_in_work" | tr '\n' ' ')"
  # dirs de contexto ESCOPO-PROJETO dentro do workspace (.claude/rules, .claude/agents)
  found_dirs="$(find "$WORK" -type d \( -path '*/.claude/rules' -o -path '*/.claude/agents' \) 2>/dev/null)"
  [ -n "$found_dirs" ] && ctx="$ctx $(echo "$found_dirs" | tr '\n' ' ')"
  # diretórios ACIMA do workspace, até a raiz (o CC herda CLAUDE.md da hierarquia)
  d="$(dirname "$WORK")"
  while :; do
    scan_ctx_dir "$d"
    [ "$d" = "/" ] && break
    nd="$(dirname "$d")"; [ "$nd" = "$d" ] && break; d="$nd"
  done
fi
if [ -z "$ctx" ]; then add A3 pass "sem CLAUDE.md/AGENTS.md/.cursorrules/.claude{rules,agents} no HOME, no WORK e acima do WORK"
else add A3 fail "arquivos de contexto herdáveis encontrados:$ctx"; fi

# --- A4: sem .env no WORK; sem ~/.claude/env no HOME sandbox ----------------
a4=""
[ -n "$WORK" ] && [ -f "$WORK/.env" ] && a4="$a4 $WORK/.env"
[ -f "$SANDBOX_HOME/.claude/env" ] && a4="$a4 $SANDBOX_HOME/.claude/env"
if [ -z "$a4" ]; then add A4 pass "sem .env no workspace e sem ~/.claude/env no sandbox"
else add A4 fail "arquivos .env presentes:$a4"; fi

# --- A5: canário de ferramentas (precisa do modelo) ------------------------
if [ -n "${CANARY_RESULT:-}" ] && [ -f "${CANARY_RESULT:-}" ]; then
  if grep -q '"leaked"[[:space:]]*:[[:space:]]*false' "$CANARY_RESULT"; then
    add A5 pass "canário: nenhum MCP não previsto vazou"
  elif grep -q '"leaked"[[:space:]]*:[[:space:]]*true' "$CANARY_RESULT"; then
    add A5 fail "canário: MCP não previsto VAZOU — isolamento inválido"
  else
    defer A5 "CANARY_RESULT ilegível" "$AUDIT_REQUIRE_CANARY"
  fi
else
  defer A5 "canário de ferramentas não executado (precisa do modelo; use canary.sh)" "$AUDIT_REQUIRE_CANARY"
fi

# --- A6: sem managed-settings.json / managed-mcp.json ----------------------
a6=""
for p in \
  "/Library/Application Support/ClaudeCode/managed-settings.json" \
  "/Library/Application Support/ClaudeCode/managed-mcp.json" \
  "/Library/Application Support/ClaudeCode/managed-settings.d" \
  "/Library/Managed Preferences/com.anthropic.claude-code.plist" \
  "/etc/claude-code/managed-settings.json" \
  "/etc/claude-code/managed-mcp.json" \
  "/etc/claude-code/managed-settings.d" \
  "$SANDBOX_HOME/.claude/managed-settings.json"; do
  [ -e "$p" ] && a6="$a6 $p"
done
if [ -z "$a6" ]; then add A6 pass "sem camada gerenciada (managed-settings/mcp) que sobreponha flags"
else add A6 fail "camada gerenciada presente (piso de política):$a6"; fi

# --- A7: ambiente sanitizado — chaves == allowlist exata -------------------
if [ ! -f "$SNAP" ]; then
  add A7 fail "env.snapshot ausente ($SNAP)"
else
  snap_keys="$(sed 's/=.*//' "$SNAP" | sort -u)"
  exp_keys="$(printf '%s\n' $EXPECTED_KEYS | sort -u)"
  extra="$(comm -23 <(printf '%s\n' "$snap_keys") <(printf '%s\n' "$exp_keys"))"
  missing="$(comm -13 <(printf '%s\n' "$snap_keys") <(printf '%s\n' "$exp_keys"))"
  if [ -z "$extra" ] && [ -z "$missing" ]; then
    add A7 pass "env do filho == allowlist ($(printf '%s' "$snap_keys" | grep -c . ) chaves, nada a mais)"
  else
    add A7 fail "env divergente — extra:[$(echo $extra)] faltando:[$(echo $missing)]"
  fi
fi

# --- A8: $CLAUDE_CONFIG_DIR/projects/ vazio --------------------------------
proj="$SANDBOX_HOME/.claude/projects"
if [ ! -d "$proj" ] || [ -z "$(ls -A "$proj" 2>/dev/null)" ]; then
  add A8 pass "\$CLAUDE_CONFIG_DIR/projects vazio (sem transcrições herdadas)"
else
  add A8 fail "projects/ não vazio: $(ls -A "$proj" | tr '\n' ' ')"
fi

# --- A9: versão do harness bate com o manifesto ----------------------------
cc_ver="$(command -v claude >/dev/null 2>&1 && claude --version 2>/dev/null | head -1 || echo 'claude ausente')"
if [ -n "${EXPECT_CLAUDE_VERSION:-}" ]; then
  case "$cc_ver" in
    *"$EXPECT_CLAUDE_VERSION"*) add A9 pass "claude $cc_ver == manifesto ($EXPECT_CLAUDE_VERSION)";;
    *) add A9 fail "claude '$cc_ver' != manifesto '$EXPECT_CLAUDE_VERSION'";;
  esac
else
  defer A9 "versão do harness = '$cc_ver' (sem manifesto de campanha p/ comparar)" "0"
fi

# --- A10: suíte congelada — git limpo em instances/ (LEB) e tasks/ (local) --
a10=""
if [ -n "$LEB_ROOT" ] && [ -d "$LEB_ROOT/.git" ]; then
  d1="$(git -C "$LEB_ROOT" status --porcelain -- instances 2>/dev/null)"
  [ -n "$d1" ] && a10="$a10 LEB/instances:$(printf '%s\n' "$d1" | grep -c .)"
fi
if [ -d "$BENCH_ROOT/.git" ]; then
  # só mudanças RASTREADAS (untracked pré-commit-inicial não conta como drift)
  d2="$(git -C "$BENCH_ROOT" status --porcelain -- tasks 2>/dev/null | grep -v '^??')"
  [ -n "$d2" ] && a10="$a10 tasks/:$(printf '%s\n' "$d2" | grep -c .)"
fi
if [ -z "$a10" ]; then add A10 pass "suíte congelada (git limpo em instances/ do LEB e em tasks/ local)"
else add A10 fail "suíte com mudanças rastreadas não commitadas:$a10"; fi

# --- A11: workspace idêntico ao golden (hash de CONTEÚDO) ------------------
if [ -n "$GOLDEN_DIR" ] && [ -d "$GOLDEN_DIR" ] && [ -n "$WORK" ] && [ -d "$WORK" ]; then
  hw="$(hash_tree "$WORK")"; hg="$(hash_tree "$GOLDEN_DIR")"
  if [ -n "$hw" ] && [ "$hw" = "$hg" ]; then add A11 pass "workspace == golden (hash de conteúdo+paths)"
  else add A11 fail "workspace difere do golden (hash: work=$hw golden=$hg)"; fi
else
  add A11 skip "sem --golden — nada a comparar (esperado na noop/Trilha A)"
fi

# --- A12: proxy no ar + ANTHROPIC_BASE_URL aponta pra ele ------------------
base_ok=0
if [ -f "$SNAP" ] && grep -q "^ANTHROPIC_BASE_URL=$PROXY_BASE_URL$" "$SNAP"; then base_ok=1; fi
# tenta nc; se nc faltar OU falhar, cai no /dev/tcp do bash (não só quando ausente)
live=0
if command -v nc >/dev/null 2>&1 && nc -z -w2 "$PROXY_HOST" "$PROXY_PORT" >/dev/null 2>&1; then
  live=1
elif (exec 3<>"/dev/tcp/$PROXY_HOST/$PROXY_PORT") 2>/dev/null; then
  live=1; exec 3>&- 2>/dev/null
fi
if [ "$base_ok" -eq 1 ] && [ "$live" -eq 1 ]; then
  add A12 pass "ANTHROPIC_BASE_URL→$PROXY_BASE_URL e proxy respondendo"
elif [ "$base_ok" -ne 1 ]; then
  add A12 fail "ANTHROPIC_BASE_URL não aponta pro proxy ($PROXY_BASE_URL) no env do filho"
else
  defer A12 "base_url ok, mas proxy $PROXY_HOST:$PROXY_PORT fora do ar" "$AUDIT_REQUIRE_PROXY"
fi

# --- A13: esforço e raciocínio explícitos e batendo ------------------------
if [ -f "$SNAP" ] \
   && grep -q "^CLAUDE_CODE_EFFORT_LEVEL=$EFFORT_PINNED$" "$SNAP" \
   && grep -q "^MAX_THINKING_TOKENS=$THINKING_PINNED$" "$SNAP"; then
  add A13 pass "effort=$EFFORT_PINNED, max_thinking=$THINKING_PINNED explícitos no env do filho"
else
  got_e="$(grep '^CLAUDE_CODE_EFFORT_LEVEL=' "$SNAP" 2>/dev/null | sed 's/.*=//')"
  got_t="$(grep '^MAX_THINKING_TOKENS=' "$SNAP" 2>/dev/null | sed 's/.*=//')"
  add A13 fail "esforço/raciocínio não batem — env:[effort=$got_e thinking=$got_t] esperado:[$EFFORT_PINNED/$THINKING_PINNED]"
fi

# --- A14: noop executada; context_overhead registrado (precisa do modelo) --
if [ -n "${NOOP_OVERHEAD:-}" ]; then
  add A14 pass "context_overhead_tokens do lote = ${NOOP_OVERHEAD}"
else
  defer A14 "T-000-noop não executada (precisa do modelo; mede overhead do harness)" "0"
fi

# --- veredito + audit.json -------------------------------------------------
# audit_passed  = nenhum check MECÂNICO falhou (o gate por-run, sempre exigido).
# campaign_ready = audit_passed E zero deferred — ou seja, os checks que precisam
#   do modelo (A5 canário, A9 versão, A12 proxy, A14 overhead) foram TODOS
#   satisfeitos. Uma campanha pontuada deve exigir campaign_ready=true; rodar em
#   não-strict com deferred>0 dá verde MECÂNICO, não verde de campanha (§11).
PASSED=$([ "$FAILS" -eq 0 ] && echo true || echo false)
CAMPAIGN_READY=$([ "$FAILS" -eq 0 ] && [ "$DEFERRED" -eq 0 ] && echo true || echo false)
{
  printf '{\n  "run_id": "%s",\n  "task_id": "%s",\n' "$RUN_ID" "$TASK_ID"
  printf '  "audit_passed": %s,\n  "campaign_ready": %s,\n' "$PASSED" "$CAMPAIGN_READY"
  printf '  "blocking_failures": %s,\n  "deferred_count": %s,\n  "strict": %s,\n' "$FAILS" "$DEFERRED" "$AUDIT_STRICT"
  printf '  "checks": [\n'
  for i in "${!RESULTS[@]}"; do
    printf '    %s' "${RESULTS[$i]}"
    [ "$i" -lt $((${#RESULTS[@]}-1)) ] && printf ',\n' || printf '\n'
  done
  printf '  ]\n}\n'
} > "$RUN_DIR/audit.json"

echo "== veredito: audit_passed=$PASSED · campaign_ready=$CAMPAIGN_READY (falhas: $FAILS · deferred: $DEFERRED) → $RUN_DIR/audit.json ==" >&2
[ "$DEFERRED" -gt 0 ] && echo "   nota: $DEFERRED check(s) aguardam o modelo (A5/A9/A12/A14); exija campaign_ready=true antes de uma campanha pontuada." >&2
[ "$FAILS" -eq 0 ] && exit 0 || exit 1
