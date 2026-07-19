#!/usr/bin/env python3
"""dummy_upstream.py — fake Anthropic-style SSE endpoint for OFFLINE proxy tests.

Streams a minimal `message_start → deltas → message_delta → message_stop`
sequence (chunked, with small sleeps so TTFT is measurable), carrying a `usage`
block and a `request-id` header. Lets us prove logging_proxy.py end-to-end
WITHOUT the network, TLS, or an API key. Stdlib only.
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EVENTS = [
    ("message_start", {"type": "message_start", "message": {
        "id": "msg_dummy", "model": "dummy-model-1",
        "usage": {"input_tokens": 123, "output_tokens": 0}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "OK"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 7}}),
    ("message_stop", {"type": "message_stop"}),
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
        self.send_header("request-id", "req_dummy_123")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        first = True
        for ev, data in EVENTS:
            time.sleep(0.08 if first else 0.01)
            first = False
            payload = ("event: %s\ndata: %s\n\n" % (ev, json.dumps(data))).encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(payload), payload))
        self.wfile.write(b"0\r\n\r\n")


if __name__ == "__main__":
    host = os.environ.get("DUMMY_HOST", "127.0.0.1")
    port = int(os.environ.get("DUMMY_PORT", "9911"))
    ThreadingHTTPServer((host, port), H).serve_forever()
