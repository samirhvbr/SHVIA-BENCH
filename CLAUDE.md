# SHVIA-BENCH — Instruções para Claude Code

> **Leia também:** [README.md](README.md) / [README_br.md](README_br.md) ·
> [docs/ambiente-isolado.md](docs/ambiente-isolado.md) (a spec, v0.2) ·
> [.continue/estado-atual.md](.continue/estado-atual.md) (log de progresso).
>
> `CLAUDE.md` e `AGENTS.md` são **espelhados** abaixo do H1 — editar os dois.

---

## 🔄 Antes de começar: `git pull`

**SEMPRE** verifique atualizações remotas antes de escrever ou alterar qualquer
coisa neste repositório.

---

## O que é este repo

**Ambiente isolado + instrumentação** para benchmark de LLMs em tarefas de
engenharia de software. NÃO é rubrica nem suíte de tarefas — é a camada de baixo:
sandbox estéril (`env -i` + HOME sandbox + `CLAUDE_CONFIG_DIR`) + proxy de
verdade-base + catálogo de métricas + auditoria de isenção. Consome o **LEB**
(repo público [AI-BENCHMARK](https://github.com/samirhvbr/AI-BENCHMARK)) como fonte de tarefas nº 1.

Stack: **bash + python (só stdlib)**. Sem deps externas — portável e auditável.

---

## Padrão de Commits (obrigatório)

Formato: `versão - comentário em português`. A versão **sempre** vem de
`version.md` (bumpe no mesmo commit). Critério: **Z** = ajuste de runner/UX/doc;
**Y** = nova métrica, mudança no protocolo de coleta ou no schema; **X** =
estável/campanha publicada. Proibido `feat:`/`fix:`/`chore:` e mensagens vagas.

---

## Invariantes do produto (não relitigar sem registrar na spec)

- **A fronteira de isolamento é `HOME` + ambiente do processo**, não a pasta de
  trabalho (§4.0). Todo run passa por `env -i` + HOME sandbox recriado do
  template + `CLAUDE_CONFIG_DIR` isolado. **Nunca** tocar no `~/.claude` real.
- **`runs/` é WRITE-ONLY.** Nenhum processo de execução lê de `runs/` — isso
  impede resultado anterior realimentar execução futura (§4.1). O que o invariante
  protege é a **execução do modelo**: por isso a retomada de campanha é do
  OPERADOR (`--from-rep N`), não do driver — derivar controle de fluxo do próprio
  ledger seria exatamente a realimentação proibida. Passos de **pós-run**
  (`patch-results`, `reclassify`, sumário) leem `runs/` legitimamente: rodam depois
  da execução e não alimentam modelo nenhum.
- **Dado pago não se destrói.** Cada linha de `results.jsonl` custou dinheiro real
  (ordem de US$1/rep no Opus). Nada no repo pode truncar ou sobrescrever resultado
  existente: o driver **recusa** e ensina a retomar; escrita de resultado é sempre
  atômica (`os.replace`); falha transitória do verificador mantém a rep
  **pendente e reverificável**, nunca a consome.
- **`failed_verification` é do VERIFICADOR, nunca do harness** (§10.4). `status` é
  função pura de dois eixos ortogonais — `harness_outcome` (a execução produziu
  medição válida?) × `verification` (a entrega passou?) —, arbitrada num único
  ponto (`runner/status.py`). Erro de API/infra é `infra_error`: nunca vira nota do
  modelo. Desfecho desconhecido cai em `infra_error` **com a anomalia crua anexada**,
  jamais num fallback silencioso.
- **Segredos nunca versionados.** A chave vive em `.secrets/` (gitignored),
  injetada individualmente pelo `run.sh`. Nunca `source .env`, nunca em argv.
- **Nada de interno da Blue3 aqui.** Sem o gateway interno, sem o CLI interno,
  sem hostnames/URLs privados, sem chaves. Repo público e reprodutível por terceiros.
- **Proxy passivo:** não modifica o **corpo**; preserva os headers, **exceto o
  `Host`** (reescrita de transporte, obrigatória para nomear o upstream). Se
  modificasse o corpo, viraria variável do experimento (§4.4).
- **Auditoria é bloqueante:** se o bloco A falhar, o run aborta (`audit_passed:false`).
- **Métrica ausente = `null`, nunca `0`** (§10.3). Um harness sem contagem de
  subagente não tem "0 subagentes".
- **Esforço/raciocínio sempre explícitos** e idênticos por campanha (V17); nunca
  herdados, nunca omitidos.
- **Flags/campos do Claude Code são hipóteses a validar** a cada versão (§6.2, §15).
  O canário A5 (§11) e o proxy C3 (§4.4) são as duas verificações que não envelhecem.

---

## Stack & comandos

- `./preflight.sh` — checa python3/openssl/git/docker e o estado do repo.
- `runner/run.sh <cmd...>` — roda `<cmd>` no ambiente sanitizado.
- `runner/audit.sh` — bloco A mecânico (retorna ≠0 se algum check bloqueante falha).
- `runner/canary.sh --selftest` — prova o detector A5 offline (fixtures).
- `bash tests/run_all.sh` — **a suíte offline inteira** (sem rede/chave/docker).
  Rode isto, não os arquivos soltos: foi assim que um teste ficou vermelho sem
  ninguém notar.
- `python3 proxy/logging_proxy.py` — sobe o proxy passivo.

---

## Referências rápidas

- Versão: `version.md` · Spec: [docs/ambiente-isolado.md](docs/ambiente-isolado.md)
- Fonte de tarefas (LEB): `github.com/samirhvbr/AI-BENCHMARK` (clonar ao lado; override via `LEB_ROOT`)
- Log de progresso: [.continue/estado-atual.md](.continue/estado-atual.md)
