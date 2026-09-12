#!/usr/bin/env python3
"""Minimal fake /message and /feed server, used only to sanity-test loadgen.py."""
import json
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

STORE = {}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        if self.path.rstrip('/') != '/message':
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length).decode('utf-8')
        ctype = self.headers.get('Content-Type', '')
        if 'application/json' in ctype:
            data = json.loads(raw) if raw else {}
        else:
            parsed = parse_qs(raw)
            data = {k: v[0] for k, v in parsed.items()}

        # simulate some load-dependent latency + occasional errors
        time.sleep(random.uniform(0.01, 0.08))
        if random.random() < 0.02:
            self.send_response(500); self.end_headers(); return

        msg_id = data.get('id') or str(random.random())
        STORE[msg_id] = data
        body = json.dumps({'status': 'ok', 'id': msg_id}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip('/').split('?')[0] != '/feed':
            self.send_response(404); self.end_headers(); return
        items = [{'id': k, **v} for k, v in STORE.items()]
        body = json.dumps(items).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
