#!/usr/bin/env python3
"""
loadgen.py — your own load generator for Lab 6 reporting/experimentation.

Independent of the TA's official load generator; this is for your own
plots (response time, system utilization across all 4 systems) so you can
tune the LB's thresholds with real data instead of guessing.

Supports:
  - a variable number of concurrent virtual users, in stages (ramp or fixed)
  - random/variable message length per message
  - random/variable think-time interval between messages per virtual user
  - concurrent polling of system-utilization agents on all 4 systems
  - a post-run /feed verification pass (persistence + duplicate check)

Usage examples are in the accompanying README.md.
"""
import argparse
import asyncio
import json
import os
import random
import string
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

import aiohttp


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Stage:
    users: int                 # concurrent virtual users during this stage
    requests: int               # request budget for this stage (shared across its users)
    label: str = ''

@dataclass
class RunConfig:
    lb_url: str
    stages: List[Stage]
    msg_len_min: int = 10
    msg_len_max: int = 200
    interval_min: float = 0.2
    interval_max: float = 2.0
    metrics_agents: List[str] = field(default_factory=list)  # "host:port" list, one per system
    metrics_poll_interval: float = 1.0
    request_timeout: float = 10.0
    content_type: str = 'form'   # 'form' or 'json'
    out_dir: str = 'results'
    stage_gap_sec: float = 2.0    # pause between stages so utilization plots show clear boundaries
    verify_feed: bool = True


_WORDS = (
    "chat message lab load balancer backend system network request response "
    "latency throughput concurrency stress test dynamic threshold health "
    "check persistent database queue worker client server socket packet "
    "cluster node metric cpu memory disk async event loop retry timeout"
).split()


def random_message(min_len: int, max_len: int) -> str:
    target_len = random.randint(min_len, max_len)
    words = []
    total = 0
    while total < target_len:
        w = random.choice(_WORDS)
        words.append(w)
        total += len(w) + 1
    text = ' '.join(words)
    return text[:max(target_len, 1)]


def random_interval(lo: float, hi: float) -> float:
    return random.uniform(lo, hi)


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------

@dataclass
class RequestRecord:
    stage_index: int
    stage_label: str
    t_start: float          # seconds since run start
    latency_ms: float
    status: int             # HTTP status, or -1 on exception
    ok: bool
    msg_id: str
    client_name: str
    error: Optional[str] = None


@dataclass
class MetricSample:
    agent: str               # "host:port" identifying which of the 4 systems
    t: float                 # seconds since run start
    cpu_pct: Optional[float]
    mem_pct: Optional[float]
    load1: Optional[float]
    ok: bool


# ---------------------------------------------------------------------------
# Virtual user / stage execution
# ---------------------------------------------------------------------------

class StageRunner:
    def __init__(self, cfg: RunConfig, session: aiohttp.ClientSession,
                 run_start: float, results: List[RequestRecord]):
        self.cfg = cfg
        self.session = session
        self.run_start = run_start
        self.results = results

    async def _post_message(self, client_name: str, text: str, msg_id: str):
        url = self.cfg.lb_url.rstrip('/') + '/message'
        payload = {'client-name': client_name, 'msg': text, 'id': msg_id}
        t0 = time.monotonic()
        status = -1
        err = None
        try:
            timeout = aiohttp.ClientTimeout(total=self.cfg.request_timeout)
            if self.cfg.content_type == 'json':
                async with self.session.post(url, json=payload, timeout=timeout) as resp:
                    status = resp.status
                    await resp.read()
            else:
                async with self.session.post(url, data=payload, timeout=timeout) as resp:
                    status = resp.status
                    await resp.read()
        except Exception as e:
            err = str(e)
        latency_ms = (time.monotonic() - t0) * 1000.0
        return status, latency_ms, err

    async def _worker(self, worker_id: int, stage_index: int, stage: Stage,
                       budget: 'SharedBudget', stop_event: asyncio.Event):
        client_name = f'lgen_s{stage_index}_u{worker_id}'
        while not stop_event.is_set():
            if not budget.take():
                break
            text = random_message(self.cfg.msg_len_min, self.cfg.msg_len_max)
            msg_id = str(uuid.uuid4())
            status, latency_ms, err = await self._post_message(client_name, text, msg_id)
            self.results.append(RequestRecord(
                stage_index=stage_index,
                stage_label=stage.label,
                t_start=time.monotonic() - self.run_start,
                latency_ms=latency_ms,
                status=status,
                ok=(200 <= status < 300),
                msg_id=msg_id,
                client_name=client_name,
                error=err,
            ))
            await asyncio.sleep(random_interval(self.cfg.interval_min, self.cfg.interval_max))

    async def run_stage(self, stage_index: int, stage: Stage):
        budget = SharedBudget(stage.requests)
        stop_event = asyncio.Event()
        tasks = [
            asyncio.create_task(self._worker(i, stage_index, stage, budget, stop_event))
            for i in range(stage.users)
        ]
        await asyncio.gather(*tasks)


class SharedBudget:
    """Thread-unsafe but coroutine-safe (single event loop) shared request counter."""
    def __init__(self, total: int):
        self.remaining = total

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


# ---------------------------------------------------------------------------
# System utilization polling (runs concurrently with the whole test)
# ---------------------------------------------------------------------------

async def poll_metrics(cfg: RunConfig, session: aiohttp.ClientSession, run_start: float,
                        samples: List[MetricSample], stop_event: asyncio.Event):
    if not cfg.metrics_agents:
        return
    while not stop_event.is_set():
        t = time.monotonic() - run_start
        coros = [_poll_one(session, agent, t, samples) for agent in cfg.metrics_agents]
        await asyncio.gather(*coros)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=cfg.metrics_poll_interval)
        except asyncio.TimeoutError:
            pass


async def _poll_one(session: aiohttp.ClientSession, agent: str, t: float,
                     samples: List[MetricSample]):
    url = f'http://{agent}/metrics'
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
            data = await resp.json(content_type=None)
        samples.append(MetricSample(
            agent=agent, t=t,
            cpu_pct=data.get('cpu_pct'),
            mem_pct=(data.get('mem') or {}).get('used_pct'),
            load1=(data.get('load') or {}).get('load1'),
            ok=True,
        ))
    except Exception:
        samples.append(MetricSample(agent=agent, t=t, cpu_pct=None, mem_pct=None, load1=None, ok=False))


# ---------------------------------------------------------------------------
# Post-run /feed verification
# ---------------------------------------------------------------------------

async def verify_feed(cfg: RunConfig, session: aiohttp.ClientSession, sent_ids: set):
    url = cfg.lb_url.rstrip('/') + '/feed'
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            status = resp.status
            data = await resp.json(content_type=None)
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    if not isinstance(data, list):
        return {'ok': False, 'error': f'/feed did not return a list (status={status})'}

    feed_ids = [m.get('id') for m in data if isinstance(m, dict) and m.get('id')]
    feed_id_set = set(feed_ids)
    found = len(sent_ids & feed_id_set)
    duplicates = len(feed_ids) - len(feed_id_set)

    return {
        'ok': True,
        'feed_status': status,
        'feed_total_messages': len(data),
        'sent_accepted': len(sent_ids),
        'found_in_feed': found,
        'feed_pct': round(100.0 * found / len(sent_ids), 2) if sent_ids else None,
        'duplicate_ids_in_feed': duplicates,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def run(cfg: RunConfig):
    os.makedirs(cfg.out_dir, exist_ok=True)
    results: List[RequestRecord] = []
    metric_samples: List[MetricSample] = []
    run_start = time.monotonic()

    connector = aiohttp.TCPConnector(limit=0)  # no artificial cap; the LB/backends are the limit under test
    async with aiohttp.ClientSession(connector=connector) as session:
        stop_metrics = asyncio.Event()
        metrics_task = asyncio.create_task(poll_metrics(cfg, session, run_start, metric_samples, stop_metrics))

        runner = StageRunner(cfg, session, run_start, results)
        for idx, stage in enumerate(cfg.stages):
            label = stage.label or f'stage{idx}_{stage.users}u'
            print(f'[stage {idx}] {label}: {stage.users} users, budget {stage.requests} requests ...')
            t0 = time.monotonic()
            await runner.run_stage(idx, stage)
            elapsed = time.monotonic() - t0
            stage_results = [r for r in results if r.stage_index == idx]
            errs = sum(1 for r in stage_results if not r.ok)
            print(f'[stage {idx}] done in {elapsed:.1f}s — '
                  f'{len(stage_results)} reqs, {errs} errors '
                  f'({100.0*errs/max(1,len(stage_results)):.1f}%)')
            if cfg.stage_gap_sec > 0 and idx < len(cfg.stages) - 1:
                await asyncio.sleep(cfg.stage_gap_sec)

        stop_metrics.set()
        await metrics_task

        feed_report = {'ok': False, 'error': 'skipped'}
        if cfg.verify_feed:
            sent_ids = {r.msg_id for r in results if r.ok}
            print(f'Verifying /feed against {len(sent_ids)} accepted message IDs ...')
            feed_report = await verify_feed(cfg, session, sent_ids)
            print('Feed verification:', json.dumps(feed_report, indent=2))

    _save_results(cfg, results, metric_samples, feed_report)
    return results, metric_samples, feed_report


def _save_results(cfg: RunConfig, results: List[RequestRecord],
                   metric_samples: List[MetricSample], feed_report: dict):
    ts = time.strftime('%Y%m%d_%H%M%S')
    run_dir = os.path.join(cfg.out_dir, f'run_{ts}')
    os.makedirs(run_dir, exist_ok=True)

    with open(os.path.join(run_dir, 'requests.json'), 'w') as f:
        json.dump([r.__dict__ for r in results], f)
    with open(os.path.join(run_dir, 'metrics.json'), 'w') as f:
        json.dump([m.__dict__ for m in metric_samples], f)
    with open(os.path.join(run_dir, 'feed_report.json'), 'w') as f:
        json.dump(feed_report, f, indent=2)
    with open(os.path.join(run_dir, 'config.json'), 'w') as f:
        json.dump({
            'lb_url': cfg.lb_url,
            'stages': [s.__dict__ for s in cfg.stages],
            'msg_len_min': cfg.msg_len_min, 'msg_len_max': cfg.msg_len_max,
            'interval_min': cfg.interval_min, 'interval_max': cfg.interval_max,
            'metrics_agents': cfg.metrics_agents,
            'content_type': cfg.content_type,
        }, f, indent=2)

    print(f'\nSaved raw results to: {run_dir}')
    print('Run plot_results.py against this directory to generate charts.')
    with open(os.path.join(cfg.out_dir, 'LAST_RUN.txt'), 'w') as f:
        f.write(run_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_stages(spec: str, requests_per_stage: int) -> List[Stage]:
    """spec like '250,500,750,1000' -> stages with given concurrency and a
    fixed per-stage request budget (mirrors the leaderboard's static board)."""
    stages = []
    for part in spec.split(','):
        users = int(part.strip())
        stages.append(Stage(users=users, requests=requests_per_stage, label=f'{users}u'))
    return stages


def main():
    ap = argparse.ArgumentParser(description='Custom load generator for the group chat LB')
    ap.add_argument('--lb-url', required=True, help='e.g. http://10.1.75.79:4205')
    ap.add_argument('--stages', default='250,500,750,1000',
                     help='comma-separated concurrent-user ladder, e.g. "200,350,500,750,1000,1500,2000,2500" for a breakpoint-style ramp')
    ap.add_argument('--requests-per-stage', type=int, default=2000)
    ap.add_argument('--msg-len-min', type=int, default=10)
    ap.add_argument('--msg-len-max', type=int, default=200)
    ap.add_argument('--interval-min', type=float, default=0.2)
    ap.add_argument('--interval-max', type=float, default=2.0)
    ap.add_argument('--metrics-agents', default='',
                     help='comma-separated host:port list for the 4 systems\' /metrics agents')
    ap.add_argument('--metrics-poll-interval', type=float, default=1.0)
    ap.add_argument('--content-type', choices=['form', 'json'], default='form')
    ap.add_argument('--out-dir', default='results')
    ap.add_argument('--stage-gap-sec', type=float, default=2.0)
    ap.add_argument('--no-verify-feed', action='store_true')
    args = ap.parse_args()

    stages = parse_stages(args.stages, args.requests_per_stage)
    agents = [a.strip() for a in args.metrics_agents.split(',') if a.strip()]

    cfg = RunConfig(
        lb_url=args.lb_url,
        stages=stages,
        msg_len_min=args.msg_len_min,
        msg_len_max=args.msg_len_max,
        interval_min=args.interval_min,
        interval_max=args.interval_max,
        metrics_agents=agents,
        metrics_poll_interval=args.metrics_poll_interval,
        content_type=args.content_type,
        out_dir=args.out_dir,
        stage_gap_sec=args.stage_gap_sec,
        verify_feed=not args.no_verify_feed,
    )

    asyncio.run(run(cfg))


if __name__ == '__main__':
    main()
