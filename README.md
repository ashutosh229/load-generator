# Custom Load Generator — Lab 6 Reporting/Experimentation

Your own load generator, separate from the TA's official one, for producing
the plots your report needs: response time behavior and system utilization
across all 4 allotted systems.

## What's included

```
load-gen/
├── agent/
│   └── sysmetrics_agent.py     stdlib-only CPU/mem/load exporter — run on all 4 systems
├── generator/
│   ├── loadgen.py               the load generator itself
│   ├── plot_results.py          turns a run's raw data into PNG plots + summary.csv
│   ├── fake_backend_for_testing.py   local mock server (dev/testing only, not for your report)
│   └── requirements.txt
└── results/                     output directory (created automatically)
```

## 1. Deploy the metrics agent on all 4 systems

On each of your 3 backend systems and your LB system:

```bash
python3 sysmetrics_agent.py --port 9100
```

(No installs needed — it only reads `/proc/stat`, `/proc/meminfo`, `/proc/loadavg`,
`/proc/net/dev`, all standard on any Linux container.)

Run it detached the same way you're already running the LB:

```bash
nohup python3 sysmetrics_agent.py --port 9100 >> agent.log 2>&1 &
disown
```

Note each system's globally reachable URL using your port-mapping formula
(`global_port = (app_port÷1000)×1000 + (ssh_port mod 1000)`), e.g. app port
`9100` on the system whose SSH port ends in `206` → globally `9206`.

## 2. Install generator dependencies (on your laptop)

```bash
cd generator
pip install -r requirements.txt
```

## 3. Run a load test

**Mirror the leaderboard's static board** (250 → 500 → 750 → 1000 users):

```bash
python loadgen.py \
  --lb-url http://10.1.75.79:6205 \
  --stages 250,500,750,1000 \
  --requests-per-stage 5000 \
  --msg-len-min 10 \
  --msg-len-max 300 \
  --interval-min 0.1 \
  --interval-max 1.5 \
  --content-type json \
  --metrics-agents 10.1.75.79:4206,10.1.75.79:4207,10.1.75.79:4208,10.1.75.79:4205 \
  --out-dir ../results
```

**Mirror the leaderboard's breakpoint board** (ramp until >20% errors — you
watch the printed per-stage error % and stop manually, or just let all
stages run and read the errors off the plots):

```bash
python loadgen.py \
  --lb-url http://10.1.75.79:5205 \
  --stages 200,350,500,750,1000,1500,2000,2500 \
  --requests-per-stage 5000 \
  --content-type json \
  --metrics-agents 10.1.75.79:4206,10.1.75.79:4207,10.1.75.79:4208,10.1.75.79:3205 \
  --out-dir ../results
```

Flags that matter for the assignment's requirements:

- `--stages` — variable number of concurrent users, comma-separated ladder.
- `--msg-len-min` / `--msg-len-max` — random message length per message.
- `--interval-min` / `--interval-max` — random think-time between messages
  per virtual user (uniform distribution).
- `--metrics-agents` — one `host:port` per system; this is what makes the
  utilization plots include **all 4 systems**, not just the LB.
- `--content-type form|json` — matches whatever format your backend/TA
  settles on.

Each run prints live per-stage stats and, at the end, verifies `/feed`
against every accepted message ID (persistence + duplicate check) — this is
your own sanity check of the same thing the leaderboard grades on.

## 4. Generate plots

```bash
python3 plot_results.py --last
# or: python3 plot_results.py ../results/run_20260912_153000
```

Produces in `<run_dir>/plots/`:

- `response_time_timeline.png` — latency of every request over the run, success vs error
- `response_time_boxplot.png` — latency distribution per stage
- `throughput_errors.png` — req/s and error % per stage
- `cpu_utilization.png` / `mem_utilization.png` / `load_average.png` — one line per system, across the whole run
- `summary.csv` — per-stage mean/p50/p95/p99 latency, error %, throughput, plus the feed verification result

These are the exact artifacts to drop into your report.

## 5. Using this to tune the LB threshold

Run it once, look at `cpu_utilization.png`/`response_time_boxplot.png`
together: find the stage (concurrency level) where a backend's CPU starts
pinning near 100% or its latency box jumps — that's the empirical knee
point referenced in the LB's `README.md` §7. Set `max_active_requests` /
`max_latency_ms` just below it, then re-run to confirm the LB actually
routes around that backend earlier next time (you'll see traffic shift in
`/stats` on the LB, and the boxplot's spread should tighten).

## Note on `fake_backend_for_testing.py`

This is only there so you (or I) can sanity-check `loadgen.py` locally
without needing your real deployment up. Don't use it for your actual
report numbers — point `--lb-url` at your real LB.
