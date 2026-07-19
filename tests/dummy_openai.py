#!/usr/bin/env python3
"""dummy_openai.py — fake OpenAI-compatible /chat/completions SSE endpoint for
OFFLINE track_a tests (OpenAI, xAI, DeepSeek, Z.ai, Novita, OpenRouter, Kilo…).
Streams chunks + a final usage block; `x-openrouter-provider` header set so the
provider_effective path is exercised. Stdlib only.
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHUNKS = [
    {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
     "usage": {"prompt_tokens": 123, "completion_tokens": 7, "total_tokens": 130,
               "prompt_tokens_details": {"cached_tokens": 0}}},
]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        _ = self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("x-openrouter-provider", "dummy-provider")
        self.send_header("request-id", "req_oai_123")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        first = True
        for c in CHUNKS:
            time.sleep(0.08 if first else 0.01)
            first = False
            obj = {"id": "chatcmpl-dummy", "object": "chat.completion.chunk",
                   "model": "dummy-oai-1", **c}
            payload = ("data: " + json.dumps(obj) + "\n\n").encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(payload), payload))
        done = b"data: [DONE]\n\n"
        self.wfile.write(b"%x\r\n%s\r\n" % (len(done), done))
        self.wfile.write(b"0\r\n\r\n")


if __name__ == "__main__":
    host = os.environ.get("DUMMY_HOST", "127.0.0.1")
    port = int(os.environ.get("DUMMY_PORT", "9912"))
    ThreadingHTTPServer((host, port), H).serve_forever()
