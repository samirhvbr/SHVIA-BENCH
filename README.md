# SHVIA-BENCH — Isolated environment for benchmarking coding models

> ⚠️ **Before working on this repository: `git pull`.**

🇧🇷 [Versão em português](README_br.md)

**A reproducible, contamination-controlled harness for benchmarking LLMs on
software-engineering tasks.** Spec: [`docs/ambiente-isolado.md`](docs/ambiente-isolado.md) (v0.2).

SHVIA-BENCH is **not** a scoring rubric and **not** a task suite. It is the layer
underneath both: the sterile environment and the instrumentation that let you
compare models fairly. It answers *"how do we run the model with zero unfair
information, and measure everything that happened?"* — while a separate project,
the **[LEB / AI-BENCHMARK](https://github.com/samirhvbr/AI-BENCHMARK)**, answers
*"what is the task, and how is the answer scored?"*. SHVIA-BENCH runs LEB
instances (its first task source) inside a controlled sandbox.

## The one principle

> No run may have access to information another run lacks. If an artifact cannot
> be recreated from scratch from the benchmark repository, it does not enter the
> run.

That cuts persistent memory, conversation history, agent context files, local
cache, RAG/indexing and telemetry. See the 18 contamination vectors (V1–V18) in
the spec.

## Two tracks

- **Track A — raw model:** one request, no tools, no filesystem. Measures raw
  single-response capability.
- **Track B — model + harness:** the same task inside a coding agent (e.g. Claude
  Code), in an ephemeral workspace, non-interactive, to completion or limit.
  Measures the *model + harness* pair.

Comparing A vs B of the same model quantifies **harness gain**.

## Why isolation is `HOME` + process env, not the working directory

A CLI started inside `~/bench/task-01/` still reads user settings, MCP config,
inherited env, prior transcripts and a `CLAUDE.md` hierarchy from *outside* that
folder. New folder, old contamination. The real boundary is **`HOME` + process
environment** — so the runner uses `env -i` + a sandbox `HOME` +
`CLAUDE_CONFIG_DIR`, on the same machine, no container, without touching your
real Claude Code install. (See spec §4.0.)

## Layout

```
runner/run.sh        env -i + sandbox HOME + CLAUDE_CONFIG_DIR — sanitized entrypoint (§4.2)
runner/audit.sh      blocking pre-run audit, block A (A1–A14) → audit.json (§11)
runner/canary.sh     A5 tool canary: proves a planted MCP does NOT leak in (live + --selftest)
proxy/logging_proxy.py   passive logging proxy → proxy.jsonl: TTFT, usage, dest allowlist (§4.4)
config/profile.template/ versioned sandbox HOME (minimal, explicit settings)
config/mcp.empty.json    {"mcpServers": {}}
tasks/T-000-noop/        trivial task — measures the harness's fixed context overhead (§10.6)
manifest.schema.json     run manifest (§8.1); a run aborts if audit_passed is false
runs/                    WRITE-ONLY. No execution process reads from here.
work/                    ephemeral per-task workspaces
```

## Status — Phase 1 (foundation)

- [x] Sanitized `run.sh`, sandbox profile, blocking audit block A
- [x] Passive logging proxy (validated offline against a local dummy upstream)
- [x] `T-000-noop`, manifest schema, preflight
- [x] Canary A5 **detector** proven with fixtures (offline)
- [ ] **Live exit criterion** — planting a real MCP and running `claude` to prove
      A5, plus measuring `context_overhead_tokens` — needs the bench API key
      (`.secrets/anthropic`). This is the project's standing "smoke ao vivo" gate.
- [x] **Track A runner** (`track_a.py`) + `models.json` + `results.schema.json` —
      streaming, cost recompute, N-reps + variance; validated offline (17/17). Real
      campaign gated on the bench key.
- [x] **Track B runner** (`track_b.py` + `collect.py`) — drives isolated
      `claude -p`, fuses C1 (result) + C2 (transcript) + C3 (proxy); Claude Code
      2.1.207 surface validated empirically (`config/harness-matrix.md`); offline
      test 25/25. Real campaign gated on the bench key.
- [ ] Phase 4 — real campaign (live A5/A14, LEB instances, §14 operator decisions)

## Requirements

`bash`, `python3` (stdlib only — no external deps), `git`, `openssl`; `docker`
only for LEB instances (not for Phase 1). Run `./preflight.sh` to check.

## Secrets

The bench API key lives in `.secrets/anthropic` (gitignored), injected
individually by `run.sh`. Never `source`d, never versioned, never passed as a CLI
argument.
