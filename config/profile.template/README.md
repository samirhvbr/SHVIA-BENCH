# profile.template — HOME sandbox versionado

Este diretório é o **template** do `HOME` sandbox. A cada run, o `run.sh` faz
`rm -rf ./profile && cp -R config/profile.template ./profile` e aponta
`HOME` e `CLAUDE_CONFIG_DIR` para a cópia viva `./profile` (gitignored). Assim a
sua instalação real do Claude Code (`~/.claude`) **nunca** é tocada.

## Por que o `settings.json` é mínimo

O isolamento **não** vem daqui. Vem de três lugares, nesta ordem de robustez:

1. `env -i` + `HOME` sandbox + `CLAUDE_CONFIG_DIR` (a fronteira real, §4.0).
2. Variáveis de ambiente explícitas no `run.sh` (telemetria off, autoupdate off,
   autocompact off, esforço/raciocínio pinados) — §4.2.
3. `--mcp-config config/mcp.empty.json --strict-mcp-config` na invocação da
   Trilha B, **mais o canário A5** que valida empiricamente que nenhum MCP
   vazou (§11) — porque as flags de MCP têm bugs conhecidos e não se confia
   cegamente nelas (§6.2).

O `settings.json` só fixa o que precisa ser determinístico e local:

- `includeCoAuthoredBy: false` — sem trailer de co-autoria, saída determinística.
- `cleanupPeriodDays: 3650` — nada é apagado no meio da campanha; as transcrições
  em `$CLAUDE_CONFIG_DIR/projects/` são a fonte de métricas C2 (§10.1) e o sandbox
  inteiro é descartado por run de qualquer forma.
- `permissions` explícito e vazio — o modelo de permissão da Trilha B (auto
  restrito ao workspace, §6.3) é fixado na Fase 3, depois de validar o surface
  de flags/settings contra a versão instalada do Claude Code.

**Não** adicione hooks, statusLine, MCP, subagentes ou `env` aqui — qualquer um
deles é um vetor de contaminação (V1–V7). A ausência é intencional.
