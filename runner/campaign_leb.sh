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

# Posicionais e flags em qualquer ordem. Sem arrays: o bash do macOS é 3.2, e
# array vazio sob `set -u` aborta.
MODEL=""; INSTANCE=""; REPS=""; HARNESS=""; FROM_REP=1; OUT=""; NPOS=0; DRYRUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from-rep) FROM_REP="${2:?--from-rep exige um número}"; shift 2 ;;
    --out)      OUT="${2:?--out exige um caminho}"; shift 2 ;;
    --dry-run)  DRYRUN=1; shift ;;
    -*) echo "campaign_leb: flag desconhecida: $1" >&2; exit 2 ;;
    *)
      NPOS=$((NPOS + 1))
      case "$NPOS" in
        1) MODEL="$1" ;;
        2) INSTANCE="$1" ;;
        3) REPS="$1" ;;
        4) HARNESS="$1" ;;
        *) echo "campaign_leb: argumento extra: $1" >&2; exit 2 ;;
      esac
      shift ;;
  esac
done
: "${MODEL:?uso: campaign_leb.sh <model_alias> <LEB_INSTANCE> [reps] [harness] [--from-rep N] [--out arquivo]}"
: "${INSTANCE:?instância LEB (ex.: LEB-100-A)}"
REPS="${REPS:-3}"
HARNESS="${HARNESS:-claude-code}"
: "${LEB_ROOT:=$HOME/x/AI-BENCHMARK}"

# Argumento não-numérico rodava ZERO reps e saía 0 ("0 reps · 0 completed",
# sucesso aparente). Num driver que gasta dinheiro, entrada inválida aborta.
[[ "$REPS"     =~ ^[0-9]+$ ]] || { echo "campaign_leb: reps não numérico: $REPS" >&2; exit 2; }
[[ "$FROM_REP" =~ ^[0-9]+$ ]] || { echo "campaign_leb: --from-rep não numérico: $FROM_REP" >&2; exit 2; }
[ "$REPS" -ge 1 ] || { echo "campaign_leb: reps deve ser >= 1" >&2; exit 2; }
[ "$FROM_REP" -le "$REPS" ] || { echo "campaign_leb: --from-rep ($FROM_REP) > reps ($REPS)" >&2; exit 2; }

GOLDEN="$LEB_ROOT/instances/$INSTANCE/code"
[ -d "$GOLDEN" ] || { echo "campaign_leb: instância sem code/: $GOLDEN" >&2; exit 1; }
[ -f "$ROOT/.secrets/anthropic" ] || echo "campaign_leb: aviso — sem .secrets/anthropic (Claude Code vai falhar)." >&2

mkdir -p "$ROOT/runs"
[ -n "$OUT" ] || OUT="$ROOT/runs/leb-${INSTANCE}-${MODEL}.jsonl"
# ABSOLUTIZA o --out: o `run.sh` faz `cd` pro workspace efêmero antes de exec'ar o
# track_b, então um caminho relativo cairia DENTRO do workspace (que é descartado a
# cada rep) — a campanha inteira gravaria no vazio e o resultado pago se perderia.
# Mesmo gotcha que já valia pro caminho do track_b.py.
case "$OUT" in
  /*) ;;
  *)  OUT="$ROOT/$OUT" ;;
esac

# NUNCA truncar: cada linha aqui é uma rep PAGA (ordem de US$1/rep no Opus). Até a
# 0.6.1 este ponto fazia `: > "$OUT"` incondicionalmente — uma campanha interrompida
# não podia ser retomada sem destruir as reps já pagas, e reexecutar o driver
# apagava silenciosamente a evidência do run anterior.
if [ -s "$OUT" ]; then
  if [ "$FROM_REP" -eq 1 ]; then
    echo "campaign_leb: $OUT já tem resultado (rep paga). Recusando sobrescrever." >&2
    echo "  retomar campanha:  --from-rep <N>   (acrescenta ao arquivo)" >&2
    echo "  campanha nova:     --out runs/<outro-nome>.jsonl" >&2
    exit 3
  fi
  echo "campaign_leb: retomando em $OUT a partir da rep $FROM_REP (append)." >&2
fi

# --dry-run: resolve tudo e passa por TODOS os guards, mas para antes de subir o
# proxy e de gastar a primeira rep. Serve pro operador conferir o plano de uma
# campanha cara (e é o único jeito de testar o caminho de RETOMADA sem pagar por ele).
if [ "$DRYRUN" -eq 1 ]; then
  echo "== plano (dry-run, nada executado, nada gasto) =="
  echo "  instância : $INSTANCE"
  echo "  modelo    : $MODEL · harness: $HARNESS"
  echo "  reps      : $FROM_REP..$REPS"
  echo "  golden    : $GOLDEN"
  echo "  out       : $OUT"
  if [ -s "$OUT" ]; then
    echo "  modo      : APPEND (arquivo já tem $(wc -l < "$OUT" | tr -d ' ') linha(s) — preservadas)"
  else
    echo "  modo      : arquivo novo"
  fi
  exit 0
fi

# Log do proxy por INVOCAÇÃO: o C3 também é evidência paga e não pode ser truncado.
# (Arquivo próprio por campanha; o collect já filtra por janela de tempo do caso.)
PROXYLOG="$ROOT/runs/_leb_proxy-$(date -u +%Y%m%dT%H%M%SZ).jsonl"

# PATH sanitizado precisa de: claude/node (o harness) + docker (o verify do LEB).
EXTRA=""
for b in claude node docker; do
  d="$(command -v "$b" 2>/dev/null || true)"; [ -n "$d" ] && EXTRA="$EXTRA:$(dirname "$d")"
done
EXTRA="${EXTRA#:}"

# proxy (verdade-base C3) — sobe e derruba no fim
SHVIA_PROXY_LOG="$PROXYLOG" python3 "$ROOT/proxy/logging_proxy.py" \
  --upstream https://api.anthropic.com --allow api.anthropic.com \
  > "${PROXYLOG%.jsonl}.log" 2>&1 &
PROXY_PID=$!; trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT
for i in $(seq 1 50); do nc -z 127.0.0.1 8787 2>/dev/null && break; done

echo "== caso LEB: instância=$INSTANCE · modelo=$MODEL · harness=$HARNESS · reps $FROM_REP..$REPS =="
# Reps INDEPENDENTES (PROTOCOL §4): cada rep = um run.sh com workspace FRESCO do
# golden. Caminho ABSOLUTO pro track_b (run.sh faz cd pro workspace). O verify do
# LEB (docker) NÃO roda aqui — fica pendente e é feito pós-run (env completo).
#
# A retomada é do OPERADOR (--from-rep), não do driver: o driver NÃO lê o
# results.jsonl para descobrir onde parou. Isso preserva o invariante §4.1
# ("runs/ é write-only; nenhum processo de execução lê de runs/") — o que o
# invariante protege é a execução do MODELO não ser realimentada por resultado
# anterior, e derivar controle de fluxo do próprio ledger seria exatamente isso.
FAILED=0
for rep in $(seq "$FROM_REP" "$REPS"); do
  echo "-- rep $rep/$REPS (workspace fresco) --"
  BENCH_EXTRA_PATH="$EXTRA" \
    "$ROOT/runner/run.sh" --task "leb-${INSTANCE}-r${rep}" --golden "$GOLDEN" -- \
    python3 "$ROOT/runner/track_b.py" \
      --harness "$HARNESS" --model "$MODEL" --task "leb-${INSTANCE}" --rep "$rep" \
      --leb-root "$LEB_ROOT" --leb-instance "$INSTANCE" \
      --reps 1 --proxy-log "$PROXYLOG" --out "$OUT" \
    || { echo "  rep $rep: run.sh saiu != 0 (segue)"; FAILED=$((FAILED + 1)); }
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
# Reps que NÃO entram na nota, discriminadas por quê. Um `pending_verification`
# ou um `infra_error` sumindo em silêncio da mediana é como o incidente de
# 19/07/2026 passou despercebido: o agregador só sabia contar "completed".
resto = {}
for r in rows:
    if r.get("status") != "completed":
        resto[r.get("status")] = resto.get(r.get("status"), 0) + 1
print(f"  {len(rows)} reps · {len(oks)} completed"
      + (f" · fora da nota: {resto}" if resto else ""))
for r in rows:
    v = r.get("verification") or {}
    inst = r.get("instrumentation") or {}
    print(f"    {r['case_id']}: {r['status']} (harness={r.get('harness_outcome')}) · "
          f"verify(passed={v.get('passed')} regressao={v.get('regression')} "
          f"probes={v.get('probes_corrigidas')}/{v.get('probes_total')}) · "
          f"US${(r.get('cost') or {}).get('cost_usd_harness')} · C2={inst.get('c2_found')}")
    if r.get("harness_anomaly"):
        print(f"      anomalia do harness: {r['harness_anomaly']}")
if oks:
    costs = [ (r.get('cost') or {}).get('cost_usd_harness') for r in oks if (r.get('cost') or {}).get('cost_usd_harness') is not None]
    if costs: print(f"  custo mediano: US${round(statistics.median(costs),4)}")
if len(oks) < 3:
    print(f"  AVISO: {len(oks)} rep(s) utilizável(is) — PROTOCOL §4 pede 3 p/ a mediana.")
PY

# Exit code honesto: uma campanha com rep perdida não é sucesso. (Antes, o driver
# saía 0 mesmo com TODAS as reps falhando, porque o último comando era o resumo.)
[ "$FAILED" -eq 0 ] || { echo "campaign_leb: $FAILED rep(s) falharam." >&2; exit 1; }
