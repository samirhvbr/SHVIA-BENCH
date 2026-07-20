#!/usr/bin/env python3
"""fake_claude.py — harness FAKE que emula `claude -p ... --output-format json`
do Claude Code 2.1.207 (schema confirmado em config/harness-matrix.md). Prova o
track_b/collect offline, sem chave nem rede. Determinístico.

Emula: escreve o transcript JSONL (C2) em
$CLAUDE_CONFIG_DIR/projects/<cwd-slug>/<session>.jsonl com um subagente (Task),
um bloco thinking e ferramentas; cria um arquivo no workspace (simula trabalho);
imprime o result JSON (C1) no stdout.

KNOBS (env, não flag — o fake recebe a argv do `claude` e flag desconhecida seria
ruído; o precedente da casa é `PLANT_CANARY=1` no run.sh):

  FAKE_CLAUDE_SCENARIO   success (default) | api_error | budget | turns |
                         unknown_subtype | garbage_json | nonzero_exit
  FAKE_CLAUDE_SLUG_MODE  real (default, regra observada no CC 2.1.207: todo
                         não-alfanumérico → '-') | legacy (só '/' → '-', a regra
                         ERRADA que o track_b usava até 0.6.1). O modo `legacy`
                         existe p/ provar que a descoberta por session_id acha o
                         transcript mesmo quando a regra de slug muda.
  FAKE_CLAUDE_NO_TRANSCRIPT=1   não escreve o C2 (prova c2_found:false)
"""
import json
import os
import re
import sys


def arg(flag, default=None):
    a = sys.argv
    return a[a.index(flag) + 1] if flag in a and a.index(flag) + 1 < len(a) else default


def slug(cwd):
    if os.environ.get("FAKE_CLAUDE_SLUG_MODE", "real") == "legacy":
        return cwd.replace("/", "-")
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _api_error_c1():
    """O C1 do INCIDENTE, congelado da rep1 paga do Opus (ver _provenance na
    fixture). Preferimos o campo real à paráfrase dele."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fixtures", "c1_api_error_opus48.json")
    d = json.load(open(p, encoding="utf-8"))
    d.pop("_provenance", None)
    return d


# subtype e is_error são eixos INDEPENDENTES no 2.1.207 — os cenários existem
# justamente para cobrir as combinações que o código antigo colapsava.
SCENARIOS = {
    "success": {},
    "budget": {"subtype": "error_max_budget_usd", "is_error": True,
               "terminal_reason": "budget_exceeded"},          # hipótese (§15)
    "turns": {"subtype": "error_max_turns", "is_error": True,
              "terminal_reason": "max_turns"},                 # hipótese (§15)
    "unknown_subtype": {"subtype": "error_during_execution", "is_error": True,
                        "terminal_reason": "error", "api_error_status": 529},
}


def main():
    session = arg("--session-id", "00000000-0000-0000-0000-000000000000")
    model = arg("--model", "dummy-model-1")
    resolved = f"{model}[1m]"
    cwd = os.path.realpath(os.getcwd())
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "success")

    # falhas que acontecem ANTES de qualquer saída utilizável
    if scenario == "nonzero_exit":
        print("boom: harness morreu", file=sys.stderr)
        sys.exit(1)

    # simula trabalho no workspace → verify pode passar + files_written
    with open(os.path.join(cwd, "SOLUTION.txt"), "w", encoding="utf-8") as f:
        f.write("fix applied\n")

    if scenario == "garbage_json":
        print("not json")
        return

    # --- C2: transcript JSONL ---
    cfg = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
    tdir = os.path.join(cfg, "projects", slug(cwd))
    os.makedirs(tdir, exist_ok=True)
    lines = [
        {"type": "user", "sessionId": session, "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": [{"type": "text", "text": "task"}]}},
        {"type": "assistant", "sessionId": session, "uuid": "a1", "parentUuid": "u1",
         "isSidechain": False,
         "message": {"role": "assistant",
                     "usage": {"input_tokens": 100, "output_tokens": 200,
                               "cache_creation_input_tokens": 5000,
                               "cache_read_input_tokens": 10000},
                     "content": [
                         {"type": "thinking", "thinking": "..."},
                         {"type": "tool_use", "name": "Task",
                          "input": {"subagent_type": "general-purpose", "prompt": "sub"}},
                         {"type": "tool_use", "name": "Read", "input": {"file_path": "a"}},
                         {"type": "tool_use", "name": "Edit", "input": {"file_path": "a"}},
                         {"type": "text", "text": "done"}]}},
        {"type": "assistant", "sessionId": session, "uuid": "s1", "parentUuid": "a1",
         "isSidechain": True, "agentId": "sub-1",
         "message": {"role": "assistant",
                     "usage": {"input_tokens": 50, "output_tokens": 30,
                               "cache_read_input_tokens": 2000},
                     "content": [{"type": "text", "text": "subagent done"}]}},
    ]
    if os.environ.get("FAKE_CLAUDE_NO_TRANSCRIPT") != "1":
        with open(os.path.join(tdir, f"{session}.jsonl"), "w", encoding="utf-8") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")

    # --- C1: result JSON no stdout ---
    result = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": "OK — fix applied", "stop_reason": "end_turn",
        "terminal_reason": "completed", "num_turns": 3,
        "duration_ms": 5000, "duration_api_ms": 3000, "ttft_ms": 900,
        "ttft_stream_ms": 880, "time_to_request_ms": 90,
        "total_cost_usd": 0.042, "session_id": session, "uuid": "r1",
        "api_error_status": None, "permission_denials": [], "fast_mode_state": "off",
        "usage": {"input_tokens": 100, "output_tokens": 200,
                  "cache_creation_input_tokens": 5000, "cache_read_input_tokens": 10000,
                  "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
                  "service_tier": "standard", "speed": "standard",
                  "inference_geo": "not_available"},
        "modelUsage": {resolved: {"inputTokens": 100, "outputTokens": 200,
                                  "cacheReadInputTokens": 10000, "cacheCreationInputTokens": 5000,
                                  "webSearchRequests": 0, "costUSD": 0.042,
                                  "contextWindow": 1000000, "maxOutputTokens": 64000}},
    }
    if scenario == "api_error":
        result = _api_error_c1()   # o C1 real do incidente, não uma paráfrase
    else:
        result.update(SCENARIOS.get(scenario, {}))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
