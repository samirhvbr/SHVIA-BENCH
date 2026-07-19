#!/usr/bin/env python3
"""Offline test do adapter LEB — sem docker, sem chave.

Valida a PREPARAÇÃO da tarefa (prompt = enunciado §2 + manifesto; golden = code/;
private/ NUNCA vaza) e a construção do comando de verify (dry-run). O `verify`
real roda docker (mysql8+php8.4) — é um passo AO VIVO, fora deste teste.
Pula com SKIP se o LEB não estiver ao lado. Stdlib only.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))
import leb  # noqa: E402

LEB_ROOT = os.environ.get("LEB_ROOT", os.path.expanduser("~/x/AI-BENCHMARK"))
INSTANCE = "LEB-100-A"


def main():
    if not os.path.isdir(os.path.join(LEB_ROOT, "instances", INSTANCE)):
        print(f"SKIP: LEB não encontrado em {LEB_ROOT} (teste precisa do AI-BENCHMARK ao lado)")
        return 0

    r = leb.prepare(LEB_ROOT, INSTANCE)
    prompt, golden = r["prompt"], r["golden_dir"]
    print(f"golden: {golden}")
    print(f"prompt: {len(prompt)} chars, head: {prompt[:120]!r}")

    dry = leb.verify(LEB_ROOT, INSTANCE, "/tmp/sub", dry_run=True)
    print(f"verify cmd: {' '.join(os.path.basename(c) for c in dry['cmd'])}")

    # o que o modelo NÃO pode ver (conteúdo do gabarito)
    matrix = ""
    mp = os.path.join(LEB_ROOT, "instances", INSTANCE, "private", "matrix.md")
    if os.path.exists(mp):
        matrix = open(mp, encoding="utf-8").read()

    checks = {
        "prompt tem o enunciado §2 (neutro)": "sistema legado" in prompt.lower()
            and "não reescreva" in prompt.lower(),
        "prompt tem o manifesto (func pública 'autenticar')": "autenticar" in prompt,
        "golden aponta pro code/": golden.rstrip("/").endswith("code")
            and os.path.isdir(golden),
        "golden NÃO contém private/matrix/probe": not any(
            ("private" in f.lower() or "matrix" in f.lower() or "probe" in f.lower())
            for f in leb._walk(golden)),
        "prompt NÃO vaza a matriz-gabarito": (not matrix)
            or (matrix[:200].strip() and matrix[:200].strip() not in prompt),
        "verify chama leb_harness.py com --instance/--submission": (
            any("leb_harness.py" in c for c in dry["cmd"])
            and "--instance" in dry["cmd"] and "--submission" in dry["cmd"]),
        "verify roda no cwd do LEB": dry["cwd"] == LEB_ROOT,
    }
    print()
    allok = True
    for k, v in checks.items():
        print(f"  [{'pass' if v else 'FAIL'}] {k}")
        allok = allok and v
    print("\nLEB OFFLINE TEST:", "ALL PASS" if allok else "FAILURES ABOVE")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
