#!/usr/bin/env python3
"""logging_proxy.py — passive logging proxy (spec §4.4). Stdlib only.

    CLI  ──►  127.0.0.1:8787 (this)  ──►  api.anthropic.com
                    │
                    └──► proxy.jsonl  (one line per request)

Sits between the harness/CLI and the model API. For every request it records
the ground-truth measurements the harness's own aggregate cannot give:

  - TTFT (time to first content byte from upstream) and end-to-end timing,
  - the full `usage` block straight from the API,
  - destination host + a check against the allowlist,
  - request model, response stop_reason, request-id / provider headers.

Scope of the destination check (be honest): this proxy only sees traffic that
the CLI routes through it via ANTHROPIC_BASE_URL. It therefore audits where the
MODEL calls go — not arbitrary egress. Telemetry to a *different* host would
bypass the proxy entirely and would NOT appear here; catching that needs a real
network allowlist (Fase 4 — spec §13), not this passive logger. So `host_allowed`
proves the model endpoint, not that "nothing left the machine".

PASSIVE by contract: the body is forwarded byte-for-byte and headers are
preserved. The one unavoidable transport rewrite is the `Host` header (it must
name the upstream, not the proxy) — that is transport, not payload. If the proxy
ever modified the body, it would become a variable of the experiment.

Config (CLI flag overrides env; env overrides default):
  --listen HOST:PORT     PROXY_HOST/PROXY_PORT    default 127.0.0.1:8787
  --upstream URL         PROXY_UPSTREAM           default https://api.anthropic.com
  --log FILE             SHVIA_PROXY_LOG          default ./proxy.jsonl
  --allow "h1 h2"        PROXY_ALLOWED_HOSTS      default api.anthropic.com
"""
import argparse
import http.client
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# Headers we must NOT copy verbatim when forwarding (hop-by-hop / transport).
HOP_BY_HOP = {"host", "connection", "proxy-connection", "keep-alive", "te",
              "trailer", "transfer-encoding", "upgrade", "content-length"}

_log_lock = threading.Lock()


class Cfg:
    def __init__(self, upstream, log_path, allowed):
        s = urlsplit(upstream)
        self.scheme = s.scheme or "https"
        self.host = s.hostname
        self.port = s.port or (443 if self.scheme == "https" else 80)
        self.log_path = log_path
        self.allowed = set(allowed)


def _write_log(cfg, record):
    line = json.dumps(record, ensure_ascii=False)
    with _log_lock:
        with open(cfg.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _parse_usage_from_sse(raw_text):
    """Merge usage / model / stop_reason across all SSE `data:` JSON events."""
    usage, model, stop_reason = {}, None, None
    for line in raw_text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") or {}
        if isinstance(msg, dict):
            model = model or msg.get("model")
            if isinstance(msg.get("usage"), dict):
                usage.update(msg["usage"])
        if isinstance(obj.get("usage"), dict):
            usage.update(obj["usage"])
        delta = obj.get("delta") or {}
        if isinstance(delta, dict) and delta.get("stop_reason"):
            stop_reason = delta["stop_reason"]
    return usage, model, stop_reason


def _parse_usage_from_json(raw_bytes):
    try:
        obj = json.loads(raw_bytes.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}, None, None
    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    return usage, obj.get("model"), obj.get("stop_reason")


def make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # silence default stderr access log
            pass

        def _proxy(self):
            t0 = time.perf_counter()
            sent_utc = time.time()
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            # corpo chunked de REQUISIÇÃO (sem Content-Length): a Anthropic manda
            # Content-Length, mas se algum harness mandar chunked, NÃO silenciamos
            # — marcamos no log (de-chunking completo = TODO se aparecer na prática).
            req_chunked = "chunked" in self.headers.get("Transfer-Encoding", "").lower()

            req_model = None
            if body:
                try:
                    req_model = json.loads(body.decode("utf-8", "replace")).get("model")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            fwd_headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in HOP_BY_HOP}
            fwd_headers["Host"] = cfg.host  # only transport rewrite

            conn_cls = (http.client.HTTPSConnection if cfg.scheme == "https"
                        else http.client.HTTPConnection)
            record = {
                "sent_utc": round(sent_utc, 3),
                "method": self.command, "path": self.path,
                "dest_host": cfg.host, "dest_scheme": cfg.scheme,
                "host_allowed": cfg.host in cfg.allowed,
                "request_bytes": len(body), "request_model": req_model,
                "request_body_chunked": req_chunked,
            }
            try:
                # TODO(Fase 3): pool/keep-alive por thread. Hoje é uma conexão
                # nova por requisição — em sessões multi-turno (Trilha B) cada
                # chamada paga um handshake TLS que um cliente direto reusaria,
                # inflando o TTFT das chamadas 2..N. Irrelevante na Trilha A (1 call).
                conn = conn_cls(cfg.host, cfg.port, timeout=900)
                if length:
                    fwd_headers["Content-Length"] = str(length)
                conn.request(self.command, self.path, body=body, headers=fwd_headers)
                resp = conn.getresponse()

                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() in HOP_BY_HOP:
                        continue
                    self.send_header(k, v)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                ttft_ms = None
                buf = bytearray()
                while True:
                    # read1() retorna no PRIMEIRO chunk disponível — read(n) do
                    # http.client acumula até juntar n bytes ou EOF, o que cravaria
                    # o TTFT em "tempo até os primeiros 64KB", não o 1º byte.
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    if ttft_ms is None:
                        ttft_ms = round((time.perf_counter() - t0) * 1000, 1)
                    buf.extend(chunk)
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.write(b"0\r\n\r\n")

                total_ms = round((time.perf_counter() - t0) * 1000, 1)
                ctype = resp.getheader("Content-Type", "")
                if "text/event-stream" in ctype:
                    usage, model, stop = _parse_usage_from_sse(
                        buf.decode("utf-8", "replace"))
                else:
                    usage, model, stop = _parse_usage_from_json(bytes(buf))

                record.update({
                    "status": resp.status,
                    "content_type": ctype,
                    "ttft_ms": ttft_ms,
                    "e2e_ms": total_ms,
                    "generation_ms": (round(total_ms - ttft_ms, 1)
                                      if ttft_ms is not None else None),
                    "response_bytes": len(buf),
                    "response_model": model or req_model,
                    "stop_reason": stop,
                    "usage": usage,
                    "request_id": resp.getheader("request-id")
                                  or resp.getheader("x-request-id"),
                    "provider": resp.getheader("x-openrouter-provider"),
                })
                conn.close()
            except Exception as exc:  # noqa: BLE001 — proxy must never crash the run
                record.update({"status": "proxy_error", "error": repr(exc)})
                try:
                    self.send_error(502, "proxy upstream error")
                except Exception:
                    pass
            finally:
                _write_log(cfg, record)

        do_GET = _proxy
        do_POST = _proxy
        do_PUT = _proxy
        do_DELETE = _proxy
        do_PATCH = _proxy

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default=None)
    ap.add_argument("--upstream", default=os.environ.get(
        "PROXY_UPSTREAM", "https://api.anthropic.com"))
    ap.add_argument("--log", default=os.environ.get(
        "SHVIA_PROXY_LOG", "proxy.jsonl"))
    ap.add_argument("--allow", default=os.environ.get(
        "PROXY_ALLOWED_HOSTS", "api.anthropic.com"))
    args = ap.parse_args()

    if args.listen:
        lhost, _, lport = args.listen.partition(":")
    else:
        lhost = os.environ.get("PROXY_HOST", "127.0.0.1")
        lport = os.environ.get("PROXY_PORT", "8787")
    lport = int(lport or 8787)

    log_dir = os.path.dirname(os.path.abspath(args.log))
    os.makedirs(log_dir, exist_ok=True)

    cfg = Cfg(args.upstream, args.log, args.allow.split())
    httpd = ThreadingHTTPServer((lhost, lport), make_handler(cfg))
    print(f"logging_proxy: {lhost}:{lport} → {args.upstream} "
          f"| log={args.log} | allow={sorted(cfg.allowed)}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
