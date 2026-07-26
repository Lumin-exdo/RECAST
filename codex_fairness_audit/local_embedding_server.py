#!/usr/bin/env python3
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sentence_transformers import SentenceTransformer

MODEL_PATH = "/mnt/laq/RECAST/models/all-MiniLM-L6-v2"
MODEL_NAME = "all-MiniLM-L6-v2"
model = SentenceTransformer(MODEL_PATH)


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/v1/health"):
            self._json(200, {"status": "ok", "model": MODEL_NAME, "dimension": 384})
        elif self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/embeddings":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            texts = request.get("input", [])
            if isinstance(texts, str):
                texts = [texts]
            vectors = model.encode(texts, normalize_embeddings=True).tolist()
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"object": "embedding", "index": i, "embedding": vector}
                        for i, vector in enumerate(vectors)
                    ],
                    "model": request.get("model", MODEL_NAME),
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                    "id": f"embd-{uuid.uuid4()}",
                    "created": int(time.time()),
                },
            )
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8290), Handler).serve_forever()
