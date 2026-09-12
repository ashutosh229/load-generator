#!/usr/bin/env python3
"""
sysmetrics_agent.py — zero-dependency system metrics exporter.

Runs on each of your 4 allotted systems (3 backends + 1 LB) and exposes
a single GET /metrics endpoint returning CPU%, memory%, load average, and
network I/O as JSON. Deliberately stdlib-only (no psutil, no pip install)
so it drops onto any of the lab containers with nothing but `python3`.

Usage:
    python3 sysmetrics_agent.py --port 9100

Then poll it from your load generator (run from your laptop) at:
    http://10.1.75.79:<global-port-for-9100-on-that-system>/metrics
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _read_proc_stat():
    """Returns cumulative CPU jiffies: (idle, total)."""
    with open('/proc/stat') as f:
        line = f.readline()
    parts = [int(x) for x in line.split()[1:]]
    # user, nice, system, idle, iowait, irq, softirq, steal, guest, guest_nice
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    return idle, total


def _read_meminfo():
    info = {}
    with open('/proc/meminfo') as f:
        for line in f:
            key, val = line.split(':', 1)
            info[key.strip()] = int(val.strip().split()[0])  # kB
    total = info.get('MemTotal', 1)
    available = info.get('MemAvailable', info.get('MemFree', 0))
    used_pct = round((1 - available / total) * 100, 2) if total else 0.0
    return {
        'total_kb': total,
        'available_kb': available,
        'used_pct': used_pct,
    }


def _read_loadavg():
    with open('/proc/loadavg') as f:
        parts = f.read().split()
    return {'load1': float(parts[0]), 'load5': float(parts[1]), 'load15': float(parts[2])}


def _read_net():
    """Sum rx/tx bytes across all interfaces except loopback."""
    rx_total, tx_total = 0, 0
    try:
        with open('/proc/net/dev') as f:
            lines = f.readlines()[2:]
        for line in lines:
            iface, rest = line.split(':', 1)
            iface = iface.strip()
            if iface == 'lo':
                continue
            fields = rest.split()
            rx_total += int(fields[0])
            tx_total += int(fields[8])
    except Exception:
        pass
    return {'rx_bytes': rx_total, 'tx_bytes': tx_total}


class _CPUSampler:
    """CPU% needs two /proc/stat samples over an interval; keep last sample
    cached so each HTTP request only needs one fresh read plus a delta."""

    def __init__(self):
        self._last_idle, self._last_total = _read_proc_stat()
        self._last_ts = time.time()

    def sample(self):
        idle, total = _read_proc_stat()
        d_idle = idle - self._last_idle
        d_total = total - self._last_total
        self._last_idle, self._last_total = idle, total
        self._last_ts = time.time()
        if d_total <= 0:
            return 0.0
        return round((1 - d_idle / d_total) * 100, 2)


_cpu_sampler = _CPUSampler()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default access logging; we don't need it here

    def do_GET(self):
        if self.path.rstrip('/') != '/metrics':
            self.send_response(404)
            self.end_headers()
            return

        payload = {
            'ts': time.time(),
            'cpu_pct': _cpu_sampler.sample(),
            'mem': _read_meminfo(),
            'load': _read_loadavg(),
            'net': _read_net(),
        }
        body = json.dumps(payload).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=9100)
    ap.add_argument('--host', default='0.0.0.0')
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'sysmetrics_agent listening on {args.host}:{args.port} (GET /metrics)')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
