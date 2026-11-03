"""Fake Gemini endpoint. Modes: 503, 400, ok, empty. Logs one line per request."""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, MODE, LOG = int(sys.argv[1]), sys.argv[2], sys.argv[3]
DELAY = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
_DEC = json.JSONDecoder()


def _batch(body: bytes) -> dict:
    text = json.loads(body)["contents"][0]["parts"][0]["text"]
    tail = text.split("Strings to translate (JSON):\n", 1)[1]
    return _DEC.raw_decode(tail)[0]


class H(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        with open(LOG, "a") as f:
            f.write(f"{time.monotonic():.3f}\n")
        time.sleep(DELAY)
        if MODE in ("503", "400"):
            out, code = b'{"error":"stub"}', int(MODE)
        else:
            batch = _batch(body)
            answer = {k: ("" if MODE == "empty" else f"DE {v}") for k, v in batch.items()}
            env = {"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}
            out, code = json.dumps(env).encode(), 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
