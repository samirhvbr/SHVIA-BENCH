#!/usr/bin/env python3
"""status.py — taxonomia de `status` de uma execução. Fonte única da verdade.

Três eixos ORTOGONAIS, e a razão de este módulo existir é não deixar que se
misturem (foi exatamente o que aconteceu no incidente de 19/07/2026):

  (i)   a infraestrutura entregou uma MEDIÇÃO válida?   → harness_outcome
  (ii)  o agente parou por um governador que NÓS impusemos? → harness_outcome
  (iii) o artefato entregue passou no VERIFICADOR da tarefa? → verification

`failed_verification` pertence exclusivamente ao eixo (iii): é um JUÍZO SOBRE O
TRABALHO DO MODELO e só o verificador pode emiti-lo. Erro de infra/API é outra
categoria e não pode penalizar o modelo — num benchmark publicado seria um falso
negativo. A spec já dizia isso em prosa (§10.4: "Nunca colapse `infra_error` em
`failed_verification`"); aqui isso vira ESTRUTURA:

  - `classify_c1` só devolve desfecho de HARNESS — não tem acesso a veredito e é
    incapaz de escrever `failed_verification`;
  - a string "failed_verification" aparece UMA vez em todo o runner, dentro de
    `resolve_status`, atrás de `verification.passed is False`.

Um fallback distraído não consegue mais alcançar um veredito, porque não existe
caminho de código do harness até ele.

Stdlib only.
"""

# Desfecho do HARNESS — o que aconteceu com a EXECUÇÃO. Não é veredito.
HARNESS_OUTCOMES = ("ok", "timeout", "infra_error", "budget_exceeded", "max_turns")

# `status` do registro (results.schema.json). Espelha o enum do schema.
STATUS_ENUM = ("completed", "failed_verification", "pending_verification", "timeout",
               "max_turns", "budget_exceeded", "infra_error", "refused", "invalid_isolation")

# subtype do C1 → desfecho de harness.
# ATENÇÃO (§15 / config/harness-matrix.md): só `success` foi observado AO VIVO no
# Claude Code 2.1.207. `error_max_budget_usd` / `error_max_turns` são **hipótese
# não validada**. É por isso que o default de subtype desconhecido é `infra_error`
# (conservador: "não sei medir isto") e nunca um veredito (punitivo).
SUBTYPE_OUTCOME = {
    "success": "ok",
    "error_max_budget_usd": "budget_exceeded",   # hipótese a validar
    "error_max_turns": "max_turns",              # hipótese a validar
}


def classify_c1(c1):
    """C1 (result object) → (harness_outcome, anomaly|None). NUNCA devolve veredito.

    Achado empírico do incidente (2.1.207): `is_error` e `subtype` são eixos
    INDEPENDENTES. O registro pago do Opus tinha `subtype:"success"` **com**
    `is_error:true` e `terminal_reason:"api_error"` — erro de API é sinalizado por
    `is_error`+`terminal_reason`, não por `subtype`. O código antigo indexava um
    único dicionário por `subtype` e tratava os dois eixos como um só; a colisão
    era estrutural, e o `.get(sub, "failed_verification")` transformava erro de
    API em juízo sobre a entrega do modelo.
    """
    if not c1:
        return "infra_error", {"reason": "sem result JSON (C1)"}

    sub = c1.get("subtype")
    is_err = bool(c1.get("is_error"))
    mapped = SUBTYPE_OUTCOME.get(sub)

    # limite que NÓS impusemos: categoria própria, independente de is_error.
    if mapped is not None and mapped != "ok":
        return mapped, None
    # caminho feliz: subtype conhecido-bom E sem sinal de erro no outro eixo.
    if mapped == "ok" and not is_err:
        return "ok", None

    # Sobra: is_error sem outro sinal, ou subtype desconhecido. Não sabemos
    # classificar ⇒ `infra_error` + a anomalia CRUA anexada, para o operador
    # decidir e para a harness-matrix absorver. Nunca um fallback silencioso.
    return "infra_error", {
        "is_error": is_err,
        "subtype": sub,
        "terminal_reason": c1.get("terminal_reason"),
        "stop_reason": c1.get("stop_reason"),
        "api_error_status": c1.get("api_error_status"),
    }


def resolve_status(harness_outcome, verification):
    """(harness_outcome, verification) → `status`. Função PURA e ÚNICA arbitragem.

    O mesmo combinador roda no track_b (durante) e no leb.patch_results (pós-run),
    então PROMOVER e REBAIXAR são o mesmo mecanismo — não há caminho que só saiba
    rebaixar (o 2º bug do incidente: um verify que passou não restaurava o status).
    """
    if harness_outcome not in HARNESS_OUTCOMES:
        raise ValueError(f"harness_outcome inválido: {harness_outcome!r}")

    # Desfecho de harness ruim MANDA no status: a medição (tempo/custo/turnos)
    # está truncada, mesmo que o trabalho entregue esteja certo. O veredito segue
    # anexado como EVIDÊNCIA no campo `verification` — só não arbitra o status.
    if harness_outcome != "ok":
        return harness_outcome

    v = verification or {}
    passed = v.get("passed")
    if passed is True:
        return "completed"
    if passed is False:
        return "failed_verification"   # ÚNICA ocorrência desta string no runner
    # passed is None: a tarefa não tem verificador (nada a medir) vs. o verificador
    # ainda não rodou (não medido ≠ aprovado). §10.3, "ausente = null, nunca juízo".
    if v.get("applicable") is False:
        return "completed"
    return "pending_verification"


def harness_outcome_of_record(rec):
    """Desfecho de harness de um registro JÁ GRAVADO (entrada do patch pós-run)."""
    ho = rec.get("harness_outcome")
    if ho in HARNESS_OUTCOMES:
        return ho
    st = rec.get("status")
    if st in HARNESS_OUTCOMES:
        return st
    # Registro LEGADO (< 0.7.0): o `status` podia conter um veredito emitido pelo
    # HARNESS — precisamente o bug desta versão —, então não dá para confiar nele.
    # Reclassifica pelo que sobrou do C1 dentro do próprio registro, passando pelo
    # MESMO `classify_c1` — uma só política de default em todo o módulo. Ter aqui
    # um default próprio (permissivo, `ok`) já significou o oposto do que
    # `classify_c1` decide para o mesmo C1: subtype desconhecido viraria `ok` e, com
    # o verify passando, seria promovido a `completed` — o falso positivo que este
    # módulo existe para impedir.
    is_err = rec.get("is_error")
    if is_err is None:
        # não era persistido antes da 0.7.0: `terminal_reason`/`api_error_status`
        # são o único traço de erro de API que restou no registro.
        is_err = (rec.get("terminal_reason") == "api_error"
                  or bool(rec.get("api_error_status")))
    outcome, _ = classify_c1({
        "subtype": rec.get("subtype"),
        "is_error": is_err,
        "terminal_reason": rec.get("terminal_reason"),
        "stop_reason": rec.get("stop_reason"),
        "api_error_status": rec.get("api_error_status"),
    })
    return outcome
