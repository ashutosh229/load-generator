#!/usr/bin/env python3
"""
plot_results.py — turns a loadgen.py run directory into the plots you need
for the report: response time behavior and system utilization across all
4 systems.

Usage:
    python3 plot_results.py results/run_20260912_153000
    python3 plot_results.py --last   # uses results/LAST_RUN.txt
"""
import argparse
import json
import os
import statistics as stats
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')  # headless: no display needed, just save PNGs
import matplotlib.pyplot as plt


def load_run(run_dir: str):
    with open(os.path.join(run_dir, 'requests.json')) as f:
        requests = json.load(f)
    with open(os.path.join(run_dir, 'metrics.json')) as f:
        metrics = json.load(f)
    with open(os.path.join(run_dir, 'feed_report.json')) as f:
        feed_report = json.load(f)
    with open(os.path.join(run_dir, 'config.json')) as f:
        config = json.load(f)
    return requests, metrics, feed_report, config


def plot_response_time_timeline(requests, out_path):
    ok = [r for r in requests if r['ok']]
    bad = [r for r in requests if not r['ok']]
    plt.figure(figsize=(11, 5))
    if ok:
        plt.scatter([r['t_start'] for r in ok], [r['latency_ms'] for r in ok],
                    s=4, alpha=0.35, label='success', color='#2b7a3f')
    if bad:
        plt.scatter([r['t_start'] for r in bad], [r['latency_ms'] for r in bad],
                    s=10, alpha=0.6, label='error', color='#c0392b')
    plt.xlabel('Elapsed time (s)')
    plt.ylabel('Response time (ms)')
    plt.title('Response time over the run')
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_latency_boxplot_per_stage(requests, out_path):
    by_stage = defaultdict(list)
    labels = {}
    for r in requests:
        if r['ok']:
            by_stage[r['stage_index']].append(r['latency_ms'])
            labels[r['stage_index']] = r['stage_label']
    indices = sorted(by_stage.keys())
    data = [by_stage[i] for i in indices]
    tick_labels = [labels[i] for i in indices]

    plt.figure(figsize=(9, 5))
    plt.boxplot(data, tick_labels=tick_labels, showfliers=False)
    plt.ylabel('Response time (ms), successes only')
    plt.title('Response time distribution per stage')
    plt.xticks(rotation=30, ha='right')
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_throughput_and_errors_per_stage(requests, out_path):
    by_stage = defaultdict(list)
    labels = {}
    for r in requests:
        by_stage[r['stage_index']].append(r)
        labels[r['stage_index']] = r['stage_label']
    indices = sorted(by_stage.keys())

    throughputs, err_pcts, tick_labels = [], [], []
    for i in indices:
        recs = by_stage[i]
        span = max(r['t_start'] for r in recs) - min(r['t_start'] for r in recs)
        span = max(span, 0.001)
        throughputs.append(len(recs) / span)
        errs = sum(1 for r in recs if not r['ok'])
        err_pcts.append(100.0 * errs / len(recs))
        tick_labels.append(labels[i])

    fig, ax1 = plt.subplots(figsize=(9, 5))
    x = range(len(indices))
    ax1.bar(x, throughputs, color='#2b6cb0', alpha=0.8, label='throughput (req/s)')
    ax1.set_ylabel('Throughput (req/s)', color='#2b6cb0')
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(tick_labels, rotation=30, ha='right')

    ax2 = ax1.twinx()
    ax2.plot(x, err_pcts, color='#c0392b', marker='o', label='error %')
    ax2.set_ylabel('Error %', color='#c0392b')

    plt.title('Throughput and error rate per stage')
    fig.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_utilization(metrics, field_key, ylabel, title, out_path):
    by_agent = defaultdict(list)
    for m in metrics:
        if m.get(field_key) is not None:
            by_agent[m['agent']].append((m['t'], m[field_key]))

    plt.figure(figsize=(11, 5))
    for agent, points in sorted(by_agent.items()):
        points.sort()
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        plt.plot(xs, ys, label=agent, linewidth=1.3)
    plt.xlabel('Elapsed time (s)')
    plt.ylabel(ylabel)
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def write_summary_csv(requests, feed_report, out_path):
    by_stage = defaultdict(list)
    labels = {}
    for r in requests:
        by_stage[r['stage_index']].append(r)
        labels[r['stage_index']] = r['stage_label']
    indices = sorted(by_stage.keys())

    lines = ['stage,users_label,requests,errors,error_pct,mean_ms,p50_ms,p95_ms,p99_ms,throughput_rps']
    for i in indices:
        recs = by_stage[i]
        ok_lat = sorted(r['latency_ms'] for r in recs if r['ok'])
        errs = sum(1 for r in recs if not r['ok'])
        span = max(r['t_start'] for r in recs) - min(r['t_start'] for r in recs)
        span = max(span, 0.001)
        def pct(p):
            if not ok_lat:
                return 0.0
            k = min(len(ok_lat) - 1, int(len(ok_lat) * p))
            return ok_lat[k]
        mean_ms = stats.mean(ok_lat) if ok_lat else 0.0
        lines.append(f"{i},{labels[i]},{len(recs)},{errs},{100.0*errs/len(recs):.2f},"
                      f"{mean_ms:.1f},{pct(0.50):.1f},{pct(0.95):.1f},{pct(0.99):.1f},"
                      f"{len(recs)/span:.1f}")

    lines.append('')
    lines.append(f"feed_verification,{json.dumps(feed_report)}")

    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run_dir', nargs='?')
    ap.add_argument('--last', action='store_true')
    ap.add_argument('--out-dir', default=None, help='defaults to <run_dir>/plots')
    args = ap.parse_args()

    run_dir = args.run_dir
    if args.last or not run_dir:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join('results', 'LAST_RUN.txt'),                 # cwd-relative
            os.path.join(script_dir, 'results', 'LAST_RUN.txt'),      # generator/results
            os.path.join(script_dir, '..', 'results', 'LAST_RUN.txt'),  # project-root/results
        ]
        last_file = next((c for c in candidates if os.path.exists(c)), candidates[0])
        with open(last_file) as f:
            run_dir = f.read().strip()

    requests, metrics, feed_report, config = load_run(run_dir)
    out_dir = args.out_dir or os.path.join(run_dir, 'plots')
    os.makedirs(out_dir, exist_ok=True)

    plot_response_time_timeline(requests, os.path.join(out_dir, 'response_time_timeline.png'))
    plot_latency_boxplot_per_stage(requests, os.path.join(out_dir, 'response_time_boxplot.png'))
    plot_throughput_and_errors_per_stage(requests, os.path.join(out_dir, 'throughput_errors.png'))

    if metrics:
        plot_utilization(metrics, 'cpu_pct', 'CPU %', 'CPU utilization across all 4 systems',
                          os.path.join(out_dir, 'cpu_utilization.png'))
        plot_utilization(metrics, 'mem_pct', 'Memory %', 'Memory utilization across all 4 systems',
                          os.path.join(out_dir, 'mem_utilization.png'))
        plot_utilization(metrics, 'load1', 'Load average (1 min)', 'Load average across all 4 systems',
                          os.path.join(out_dir, 'load_average.png'))
    else:
        print('No metrics samples found — did you pass --metrics-agents to loadgen.py?')

    write_summary_csv(requests, feed_report, os.path.join(out_dir, 'summary.csv'))

    print(f'Plots and summary.csv written to: {out_dir}')


if __name__ == '__main__':
    main()
