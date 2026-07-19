#!/usr/bin/env bash
# preflight.sh — checagem de ambiente + sanidade do repo (padrão da casa,
# adaptado de um build-local (padrão de projeto): aqui não há binário a compilar,
# faz lint de sintaxe + checa dependências stdlib).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fail=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=1; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

echo "== SHVIA-BENCH preflight =="

echo "[1] dependências obrigatórias"
command -v bash    >/dev/null 2>&1 && ok "bash $(bash --version | head -1 | grep -o '[0-9][0-9.]*' | head -1)" || bad "bash ausente"
if command -v python3 >/dev/null 2>&1; then
  pv="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
  python3 -c 'import sys;sys.exit(0 if sys.version_info[:2]>=(3,7) else 1)' \
    && ok "python3 $pv (>=3.7)" || bad "python3 $pv < 3.7"
  python3 -c 'import http.server,http.client,json,ssl,urllib.parse,threading' 2>/dev/null \
    && ok "python stdlib (http/json/ssl/threading)" || bad "stdlib incompleta"
else bad "python3 ausente"; fi
command -v git     >/dev/null 2>&1 && ok "git $(git --version | grep -o '[0-9][0-9.]*' | head -1)" || bad "git ausente"
command -v openssl >/dev/null 2>&1 && ok "openssl $(openssl version 2>/dev/null | awk '{print $2}')" || bad "openssl ausente"

echo "[2] dependências opcionais"
command -v docker  >/dev/null 2>&1 && ok "docker (necessário só p/ instâncias LEB)" || warn "docker ausente — ok p/ Fase 1; necessário p/ Trilha B com LEB"
command -v claude  >/dev/null 2>&1 && ok "claude $(claude --version 2>/dev/null | head -1)" || warn "claude ausente — necessário p/ Trilha B"
command -v nc      >/dev/null 2>&1 && ok "nc (liveness do proxy no A12)" || warn "nc ausente — A12 usa fallback /dev/tcp"

echo "[3] sanidade do repo"
[ -f "$ROOT/version.md" ] && ok "version.md = $(cat "$ROOT/version.md")" || bad "version.md ausente"
grep -q '^\.secrets/\*' "$ROOT/.gitignore" 2>/dev/null && ok ".secrets/ está no .gitignore" || bad ".secrets/ NÃO está gitignored — risco de vazar chave"
grep -q '^/runs/\*' "$ROOT/.gitignore" 2>/dev/null && ok "runs/ gitignored" || warn "runs/ não gitignored"

echo "[4] lint de sintaxe"
for s in runner/run.sh runner/audit.sh runner/canary.sh preflight.sh; do
  bash -n "$ROOT/$s" 2>/dev/null && ok "bash -n $s" || bad "erro de sintaxe em $s"
done
for p in proxy/logging_proxy.py config/canary_mcp_server.py tests/dummy_upstream.py; do
  python3 -m py_compile "$ROOT/$p" 2>/dev/null && ok "py_compile $p" || bad "erro de sintaxe em $p"
done
for j in config/mcp.empty.json config/mcp.canary.json manifest.schema.json tasks/T-000-noop/task.json config/profile.template/.claude/settings.json; do
  python3 -c "import json,sys;json.load(open('$ROOT/$j'))" 2>/dev/null && ok "json ok $j" || bad "json inválido $j"
done

echo "[5] fonte de tarefas LEB"
# shellcheck disable=SC1091
. "$ROOT/config/run.defaults.env"
if [ -d "$LEB_ROOT/.git" ]; then ok "LEB_ROOT=$LEB_ROOT (repo git)"; else warn "LEB_ROOT=$LEB_ROOT ausente — Fase 1 não precisa; Trilha B sim"; fi

echo
[ "$fail" -eq 0 ] && { echo "== preflight OK =="; exit 0; } || { echo "== preflight FALHOU =="; exit 1; }
