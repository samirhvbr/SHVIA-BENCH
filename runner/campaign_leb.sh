#!/usr/bin/env bash
# campaign_leb.sh — roda N repetições de UM caso da Trilha B contra uma instância
# do LEB (AI-BENCHMARK), com o pipeline completo: proxy (C3) + run.sh (sanitizado)
# + track_b (dirige o harness) + verify mecânico do LEB (docker).
#
# Uso: runner/campaign_leb.sh <model_alias> <LEB_INSTANCE> [reps] [harness]
#   ex.: runner/campaign_leb.sh M-opus48 LEB-100-A 3 claude-code
#
# Requer: a chave em .secrets/anthropic (Claude Code = harness Anthropic), Docker
# (mysql8+php8.4, o verify do LEB), e o LEB clonado (LEB_ROOT, default ~/x/AI-BENCHMARK).
# PROTOCOL §4: 1 run oficial = 3 execuções, nota = mediana. Modo S (turno único).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODEL="${1:?uso: campaign_leb.sh <model_alias> <LEB_INSTANCE> [reps] [harness]}"
INSTANCE="${2:?instância LEB (ex.: LEB-100-A)}"
REPS="${3:-3}"
HARNESS="${4:-claude-code}"
: "${LEB_ROOT:=$HOME/x/AI-BENCHMARK}"

GOLDEN="$LEB_ROOT/instances/$INSTANCE/code"
[ -d "$GOLDEN" ] || { echo "campaign_leb: instância sem code/: $GOLDEN" >&2; exit 1; }
[ -f "$ROOT/.secrets/anthropic" ] || echo "campaign_leb: aviso — sem .secrets/anthropic (Claude Code vai falhar)." >&2

mkdir -p "$ROOT/runs"
OUT="$ROOT/runs/leb-${INSTANCE}-${MODEL}.jsonl"; : > "$OUT"
PROXYLOG="$ROOT/runs/_leb_proxy.jsonl"; : > "$PROXYLOG"

# PATH sanitizado precisa de: claude/node (o harness) + docker (o verify do LEB).
EXTRA=""
for b in claude node docker; do
  d="$(command -v "$b" 2>/dev/null || true)"; [ -n "$d" ] && EXTRA="$EXTRA:$(dirname "$d")"
done
EXTRA="${EXTRA#:}"

# proxy (verdade-base C3) — sobe e derruba no fim
SHVIA_PROXY_LOG="$PROXYLOG" python3 "$ROOT/proxy/logging_proxy.py" \
  --upstream https://api.anthropic.com --allow api.anthropic.com \
  > "$ROOT/runs/_leb_proxy.log" 2>&1 &
PROXY_PID=$!; trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT
for i in $(seq 1 50); do nc -z 127.0.0.1 8787 2>/dev/null && break; done

echo "== caso LEB: instância=$INSTANCE · modelo=$MODEL · harness=$HARNESS · reps=$REPS =="
# Reps INDEPENDENTES (PROTOCOL §4): cada rep = um run.sh com workspace FRESCO do
# golden. Caminho ABSOLUTO pro track_b (run.sh faz cd pro workspace). O verify do
# LEB (docker) NÃO roda aqui — fica pendente e é feito pós-run (env completo).
for rep in $(seq 1 "$REPS"); do
  echo "-- rep $rep/$REPS (workspace fresco) --"
  BENCH_EXTRA_PATH="$EXTRA" \
    "$ROOT/runner/run.sh" --task "leb-${INSTANCE}-r${rep}" --golden "$GOLDEN" -- \
    python3 "$ROOT/runner/track_b.py" \
      --harness "$HARNESS" --model "$MODEL" --task "leb-${INSTANCE}" --rep "$rep" \
      --leb-root "$LEB_ROOT" --leb-instance "$INSTANCE" \
      --reps 1 --proxy-log "$PROXYLOG" --out "$OUT" || echo "  rep $rep: run.sh saiu != 0 (segue)"
done

# Verify do LEB no AMBIENTE COMPLETO (fora do env -i): o `docker compose` precisa
# do HOME real (~/.docker/cli-plugins). Patcha verification+status no results.jsonl.
echo "== verify do LEB (docker, ambiente completo, pós-run) =="
python3 "$ROOT/runner/leb.py" patch-results --leb-root "$LEB_ROOT" --instance "$INSTANCE" --results "$OUT"

echo "== resultados: $OUT =="
python3 - "$OUT" <<'PY'
import json, sys, statistics
rows = [json.loads(l) for l in open(sys.argv[1])]
oks = [r for r in rows if r.get("status") == "completed"]
print(f"  {len(rows)} reps · {len(oks)} completed")
for r in rows:
    v = r.get("verification") or {}
    print(f"    {r['case_id']}: {r['status']} · verify(passed={v.get('passed')} regressao={v.get('regression')} "
          f"probes={v.get('probes_corrigidas')}/{v.get('probes_total')}) · US${(r.get('cost') or {}).get('cost_usd_harness')}")
if oks:
    costs = [ (r.get('cost') or {}).get('cost_usd_harness') for r in oks if (r.get('cost') or {}).get('cost_usd_harness') is not None]
    if costs: print(f"  custo mediano: US${round(statistics.median(costs),4)}")
PY
