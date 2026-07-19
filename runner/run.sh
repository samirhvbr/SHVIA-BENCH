#!/usr/bin/env bash
# runner/run.sh — entrypoint sanitizado (spec §4.2).
#
# A fronteira de isolamento é HOME + ambiente do processo, NÃO a pasta de
# trabalho (§4.0). Este script:
#   1. recria o HOME sandbox do template (a sua instalação real fica intacta);
#   2. monta o ambiente sanitizado com `env -i` + allowlist explícita;
#   3. grava env.snapshot (o ambiente EXATO que o filho verá — artefato de
#      auditoria e entrada do check A7);
#   4. roda a auditoria bloco A (bloqueante) — aborta se falhar;
#   5. exec do comando no ambiente sanitizado.
#
# Uso:
#   runner/run.sh [OPÇÕES] -- CMD [ARGS...]
#   runner/run.sh --audit-only            # monta ambiente + audita, sem exec
#   runner/run.sh -- env                  # prova de sanitização (imprime o env)
#
# Opções:
#   --task ID        rótulo da tarefa (default: adhoc)
#   --audit-only     não executa nada; só monta o ambiente e roda a auditoria
#   --no-audit       pula a auditoria (use só para debug)
#   --golden DIR     workspace copiado deste golden state (ativa A11)
#   -h | --help
#
# Variáveis de ambiente relevantes (override dos defaults): ver config/run.defaults.env
set -euo pipefail

BENCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TASK_ID="adhoc"
AUDIT_ONLY=0
DO_AUDIT=1
GOLDEN_DIR=""
CMD=()

while [ $# -gt 0 ]; do
  case "$1" in
    --task)       TASK_ID="${2:?--task requer um valor}"; shift 2 ;;
    --audit-only) AUDIT_ONLY=1; shift ;;
    --no-audit)   DO_AUDIT=0; shift ;;
    --golden)     GOLDEN_DIR="${2:?--golden requer um DIR}"; shift 2 ;;
    -h|--help)    sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
    --)           shift; CMD=("$@"); break ;;
    *)            CMD=("$@"); break ;;   # tolera CMD sem `--`
  esac
done

# shellcheck disable=SC1091
. "$BENCH_ROOT/config/run.defaults.env"

# --- TASK_ID sanitizado: entra em paths de rm -rf/mkdir/cp; recuse metacaráct ---
case "$TASK_ID" in
  *[!A-Za-z0-9._-]*|""|.|..) echo "run.sh: TASK_ID inválido ('$TASK_ID') — use só [A-Za-z0-9._-]." >&2; exit 2 ;;
esac

# --- IDs e diretórios ------------------------------------------------------
RUN_ID="$(date -u +%Y-%m-%dT%H-%M-%SZ)_$(openssl rand -hex 3)"
SANDBOX_HOME="$BENCH_ROOT/profile"
RUN_DIR="$BENCH_ROOT/runs/$RUN_ID"
WORK="$WORK_ROOT/$RUN_ID/$TASK_ID"

mkdir -p "$RUN_DIR"

# --- HOME sandbox: recriado do template a cada run -------------------------
# NUNCA toca no ~/.claude real: HOME e CLAUDE_CONFIG_DIR apontam para a cópia.
rm -rf "$SANDBOX_HOME"
cp -R "$BENCH_ROOT/config/profile.template" "$SANDBOX_HOME"
printf '%s\n' "$RUN_ID" > "$SANDBOX_HOME/.bench-run-id"   # marca A1
mkdir -p "$SANDBOX_HOME/.config" "$SANDBOX_HOME/.cache" "$SANDBOX_HOME/.local/share"

# Canário A5: se PLANT_CANARY=1, planta um MCP de usuário no sandbox (config
# escopo-usuário) que um run NÃO-isolado carregaria. Com --strict-mcp-config +
# mcp.empty.json, o isolamento deve descartá-lo. O canary.sh verifica isso.
if [ "${PLANT_CANARY:-0}" = "1" ]; then
  # JSON emitido com python (escapa o path com segurança — não interpola cru)
  SERVER_PY="$BENCH_ROOT/config/canary_mcp_server.py" python3 - > "$SANDBOX_HOME/.claude.json" <<'PY'
import json, os
print(json.dumps({"mcpServers": {"shvia-bench-canary": {
    "command": "python3", "args": [os.environ["SERVER_PY"]]}}}))
PY
fi

# --- Workspace efêmero (fora da árvore do repo; §4.0/§4.3) -----------------
rm -rf "$WORK"; mkdir -p "$WORK"
if [ -n "$GOLDEN_DIR" ]; then
  cp -R "$GOLDEN_DIR/." "$WORK/"
  # baseline com datas FIXAS (§4.3): timestamp não vaza e o hash é reprodutível
  GIT_AUTHOR_DATE="2026-01-01T00:00:00Z" GIT_COMMITTER_DATE="2026-01-01T00:00:00Z" \
    git -C "$WORK" init -q 2>/dev/null || true
  GIT_AUTHOR_DATE="2026-01-01T00:00:00Z" GIT_COMMITTER_DATE="2026-01-01T00:00:00Z" \
  GIT_AUTHOR_NAME="bench" GIT_AUTHOR_EMAIL="bench@local" \
  GIT_COMMITTER_NAME="bench" GIT_COMMITTER_EMAIL="bench@local" \
    git -C "$WORK" -c commit.gpgsign=false add -A 2>/dev/null && \
  GIT_AUTHOR_DATE="2026-01-01T00:00:00Z" GIT_COMMITTER_DATE="2026-01-01T00:00:00Z" \
  GIT_AUTHOR_NAME="bench" GIT_AUTHOR_EMAIL="bench@local" \
  GIT_COMMITTER_NAME="bench" GIT_COMMITTER_EMAIL="bench@local" \
    git -C "$WORK" -c commit.gpgsign=false commit -q --allow-empty -m baseline 2>/dev/null || true
fi

# --- Segredos MULTI-VENDOR: cada .secrets/<vendor> → <VENDOR>_API_KEY, injetado
#     individualmente, tolerante à ausência (offline). Nunca `source`, nunca argv.
SECRET_NAMES=(); SECRET_VALUES=(); SECRET_COUNT=0
if [ -d "$BENCH_ROOT/.secrets" ]; then
  for sf in "$BENCH_ROOT"/.secrets/*; do
    [ -f "$sf" ] || continue
    b="$(basename "$sf")"; [ "$b" = ".gitkeep" ] && continue
    kn="$(printf '%s' "$b" | tr '[:lower:]-' '[:upper:]_')_API_KEY"
    SECRET_NAMES+=("$kn"); SECRET_VALUES+=("$(cat "$sf")"); SECRET_COUNT=$((SECRET_COUNT+1))
  done
fi
[ "$SECRET_COUNT" -eq 0 ] && echo "run.sh: aviso — nenhum segredo em .secrets/ (ok p/ checks offline; chamadas ao modelo falharão)." >&2

# --- PATH sanitizado -------------------------------------------------------
SAFE_PATH="/usr/local/bin:/usr/bin:/bin"
[ -n "${BENCH_EXTRA_PATH:-}" ] && SAFE_PATH="$BENCH_EXTRA_PATH:$SAFE_PATH"

# --- Ambiente sanitizado (allowlist EXPLÍCITA) -----------------------------
ENVCMD=(env -i
  PATH="$SAFE_PATH"
  HOME="$SANDBOX_HOME"
  XDG_CONFIG_HOME="$SANDBOX_HOME/.config"
  XDG_CACHE_HOME="$SANDBOX_HOME/.cache"
  XDG_DATA_HOME="$SANDBOX_HOME/.local/share"
  TERM="dumb" LANG="C.UTF-8" TZ="UTC"
  CLAUDE_CONFIG_DIR="$SANDBOX_HOME/.claude"
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
  DISABLE_AUTOUPDATER=1
  DISABLE_AUTOCOMPACT=1
  CLAUDE_CODE_EFFORT_LEVEL="$EFFORT_PINNED"
  MAX_THINKING_TOKENS="$THINKING_PINNED"
  ANTHROPIC_BASE_URL="http://$PROXY_HOST:$PROXY_PORT"
  SHVIA_RUN_ID="$RUN_ID"
  SHVIA_TASK_ID="$TASK_ID"
)
# Chaves esperadas no ambiente do filho (entrada do check A7).
EXPECTED_KEYS="PATH HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME TERM LANG TZ CLAUDE_CONFIG_DIR CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC DISABLE_AUTOUPDATER DISABLE_AUTOCOMPACT CLAUDE_CODE_EFFORT_LEVEL MAX_THINKING_TOKENS ANTHROPIC_BASE_URL SHVIA_RUN_ID SHVIA_TASK_ID"
for i in "${!SECRET_NAMES[@]}"; do
  ENVCMD+=("${SECRET_NAMES[$i]}=${SECRET_VALUES[$i]}")
  EXPECTED_KEYS="$EXPECTED_KEYS ${SECRET_NAMES[$i]}"
done

# --- env.snapshot: o ambiente EXATO do filho, com TODA chave *_API_KEY redigida.
#     Artefato de auditoria + entrada dos checks A7/A12/A13. ------------------
"${ENVCMD[@]}" env \
  | sed -E 's/^([A-Za-z0-9_]*_API_KEY)=.*/\1=***REDACTED***/' \
  | sort > "$RUN_DIR/env.snapshot"

# --- Metadados do run (não é o manifesto de campanha; é o traço deste run) --
cat > "$RUN_DIR/run.meta.json" <<JSON
{
  "run_id": "$RUN_ID",
  "task_id": "$TASK_ID",
  "sandbox_home": "$SANDBOX_HOME",
  "claude_config_dir": "$SANDBOX_HOME/.claude",
  "workspace": "$WORK",
  "golden_dir": "${GOLDEN_DIR:-}",
  "proxy_base_url": "http://$PROXY_HOST:$PROXY_PORT",
  "effort_pinned": "$EFFORT_PINNED",
  "thinking_pinned": "$THINKING_PINNED",
  "secrets_present": $SECRET_COUNT
}
JSON

# --- Auditoria bloco A (bloqueante) ----------------------------------------
# Exporta as entradas p/ a audit.sh (subprocesso). Isto NÃO vaza para o comando
# final: o exec usa `env -i`, que zera tudo e recompõe só a allowlist.
if [ "$DO_AUDIT" -eq 1 ]; then
  export RUN_ID TASK_ID BENCH_ROOT SANDBOX_HOME WORK GOLDEN_DIR RUN_DIR \
         LEB_ROOT EXPECTED_KEYS EFFORT_PINNED THINKING_PINNED PROXY_HOST PROXY_PORT
  export PROXY_BASE_URL="http://$PROXY_HOST:$PROXY_PORT"
  if ! bash "$BENCH_ROOT/runner/audit.sh"; then
    echo "run.sh: AUDITORIA REPROVADA — run abortado (audit.json em $RUN_DIR)." >&2
    exit 3
  fi
fi

if [ "$AUDIT_ONLY" -eq 1 ]; then
  echo "run.sh: --audit-only ok. RUN_DIR=$RUN_DIR"
  exit 0
fi

if [ "${#CMD[@]}" -eq 0 ]; then
  echo "run.sh: nenhum comando para executar (use -- CMD, ou --audit-only)." >&2
  exit 2
fi

# --- Exec sanitizado -------------------------------------------------------
# cd para o workspace efêmero: o comando (claude, track_a…) roda DENTRO dele, e
# não no CWD de quem chamou o run.sh. Sem isto o agente herdaria o CLAUDE.md da
# árvore de invocação e a A3 auditaria a árvore errada (§4.0). env -i preserva
# o CWD; então o cd aqui vale para o processo filho.
cd "$WORK" || { echo "run.sh: não consegui cd para o workspace $WORK" >&2; exit 1; }
exec "${ENVCMD[@]}" "${CMD[@]}"
