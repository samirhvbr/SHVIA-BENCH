#!/usr/bin/env bash
# run_all.sh — a suíte offline inteira, num comando.
#
# Existe porque "suíte verde" não pode depender de o operador lembrar de rodar
# seis arquivos na mão: na 0.6.2 um teste ficou VERMELHO sem ninguém notar,
# porque só os outros foram rodados.
#
# Tudo aqui é OFFLINE: sem rede, sem chave de API, sem docker. Sai != 0 se
# qualquer suíte falhar.
#
# Num repo de MEDIÇÃO, "verde" precisa significar "N checks rodaram", não
# "ninguém gritou". Por isso:
#   - suíte PULADA é reportada, não somada ao verde;
#   - a suíte de taxonomia tem PISO de checks (apagar checks vira falha);
#   - cada suíte roda sob watchdog (travar = vermelho, não pendurar o CI).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || { echo "run_all: não consegui entrar em $ROOT" >&2; exit 1; }

WATCHDOG_S="${WATCHDOG_S:-300}"
# piso da suíte de taxonomia: cobre o incidente de 19/07/2026. Se cair abaixo
# disto, alguém removeu cobertura — é falha, não "verde".
TAXONOMY_MIN_CHECKS=60

FAILED=0
SKIPPED=0

# macOS não traz `timeout(1)`; perl+alarm é o equivalente portátil.
watchdog() { perl -e 'alarm shift; exec @ARGV' "$WATCHDOG_S" "$@"; }

run() {
  local out rc
  out="$(mktemp -t sb-test)"   # não /tmp/<previsível>: dir world-writable
  echo "── $* ─────────────────────────────────────────"
  if watchdog "$@" > "$out" 2>&1; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    tail -1 "$out"
    if grep -q "SKIP" "$out"; then
      echo "   ^^ PULADA (dependência ausente) — não conta como verde"
      SKIPPED=$((SKIPPED + 1))
    fi
  else
    cat "$out"
    [ "$rc" -eq 142 ] && echo "   ^^ TRAVOU (watchdog ${WATCHDOG_S}s)"
    echo "   ^^ FALHOU: $*"
    FAILED=$((FAILED + 1))
  fi
  # piso de cobertura da taxonomia
  case "$*" in
    *test_status_taxonomy_offline.py*)
      n="$(sed -n 's/.*(\([0-9]\{1,\}\) checks).*/\1/p' "$out" | tail -1)"
      if [ -z "${n:-}" ] || [ "$n" -lt "$TAXONOMY_MIN_CHECKS" ]; then
        echo "   ^^ COBERTURA ABAIXO DO PISO: ${n:-?} < $TAXONOMY_MIN_CHECKS checks"
        FAILED=$((FAILED + 1))
      fi ;;
  esac
  rm -f "$out"
}

run python3 tests/test_proxy_offline.py            # C3 / proxy passivo
run python3 tests/test_track_a_offline.py          # Trilha A multi-vendor
run python3 tests/test_track_b_offline.py          # Trilha B (harness fake) + collect
run python3 tests/test_status_taxonomy_offline.py  # taxonomia + reparo + guards do driver
run python3 tests/test_cost_truth_offline.py       # precedência C3→C2-dedup→C1 (§10.1)
run python3 tests/test_leb_offline.py              # adapter LEB (SKIP sem LEB_ROOT)
run bash    runner/canary.sh --selftest            # A5 offline (fixtures)

echo
if [ "$FAILED" -eq 0 ] && [ "$SKIPPED" -eq 0 ]; then
  echo "SUÍTE OFFLINE: TUDO VERDE"
elif [ "$FAILED" -eq 0 ]; then
  echo "SUÍTE OFFLINE: verde, mas $SKIPPED suíte(s) PULADA(s) — cobertura incompleta"
else
  echo "SUÍTE OFFLINE: $FAILED suíte(s) FALHARAM${SKIPPED:+ · $SKIPPED pulada(s)}"
fi
exit "$FAILED"
