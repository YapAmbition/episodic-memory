#!/usr/bin/env python3
"""Frozen recall regression benchmark; does not touch a running server or user database."""
import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from .schemas import ContextInput, EmbeddingInput, EventInput, SpaceInput
from .service import MemoryService


def summarize(rows, k):
    positives = [r for r in rows if r['expected']]
    negatives = [r for r in rows if not r['expected']]
    latency = sorted(r['elapsed_ms'] for r in rows)
    return {
        'cases': len(rows), 'positive_cases': len(positives), 'negative_cases': len(negatives),
        f'hit@{k}': sum(bool(r['hits']) for r in positives) / len(positives) if positives else None,
        f'recall@{k}': statistics.mean(len(r['hits']) / len(r['expected']) for r in positives) if positives else None,
        f'mrr@{k}': statistics.mean(r['reciprocal_rank'] for r in positives) if positives else None,
        'negative_false_positive_rate': sum(bool(r['selected']) for r in negatives) / len(negatives) if negatives else None,
        'forbidden_cases': sum(bool(r['forbidden_hits']) for r in rows),
        'degraded_cases': sum(r['degraded'] for r in rows),
        'latency_mean_ms': statistics.mean(latency) if latency else None,
        'latency_p95_ms': latency[max(0, math.ceil(len(latency)*.95)-1)] if latency else None,
    }


def dataset_directory():
    for folder in (ROOT / 'evals/recall', Path(__file__).parent / 'benchmark_data', Path(sys.prefix) / 'share/memory-slice/recall'):
        if (folder / 'corpus.json').exists():
            return folder
    raise FileNotFoundError('benchmark dataset is not installed')


async def run(args, *, embedding_config=None, progress=None, quiet=False):
    folder = dataset_directory()
    corpus = json.loads((folder/'corpus.json').read_text())
    suites = [json.loads(p.read_text()) for p in sorted(folder.glob('*.json')) if p.name != 'corpus.json']
    fingerprint = hashlib.sha256(json.dumps([corpus, suites], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    ids = {e['id'] for e in corpus['events']}
    assert len(ids) == len(corpus['events']), 'duplicate event IDs'
    cases = [dict(c, suite=s['name']) for s in suites for c in s['cases']]
    assert len({c['id'] for c in cases}) == len(cases), 'duplicate case IDs'
    for c in cases:
        assert set(c['expected'] + c.get('forbidden', [])) <= ids
        assert not set(c['expected']) & set(c.get('forbidden', []))
    modes = ['local', 'hybrid'] if args.mode == 'both' else [args.mode]
    with tempfile.TemporaryDirectory(prefix='memory-recall-eval-') as directory:
        core = MemoryService(directory)
        try:
            sid = core.add_space(SpaceInput(name='Isolated recall benchmark'))['id']
            # Frozen, validated events with stable IDs. This tests indexing/recall, not extraction.
            for raw in corpus['events']:
                value = EventInput.model_validate({k:v for k,v in raw.items() if k != 'id'}).model_dump(mode='json', exclude={'expected_version'})
                core.store.put('event', dict(value, id=raw['id'], space_id=sid,
                    reinforcements=[], updated_at=corpus['at']), sid)
            config = core.config_for(sid)
            embedding = None
            if 'hybrid' in modes:
                supplied = embedding_config or {}
                url, model = supplied.get('base_url') or os.environ.get('EMBEDDING_BASE_URL'), supplied.get('model') or os.environ.get('EMBEDDING_MODEL')
                if not url or not model:
                    raise ValueError('hybrid requires EMBEDDING_BASE_URL and EMBEDDING_MODEL; optional EMBEDDING_API_KEY')
                core.embeddings.configure(sid, EmbeddingInput(**(dict(enabled=True, base_url=url, model=model, api_key=os.environ.get('EMBEDDING_API_KEY'), timeout_seconds=args.timeout) | supplied)))
                if progress:
                    progress({'phase': 'indexing', 'completed': 0, 'total': len(cases)*len(modes)})
                for _ in range(100):
                    status = core.embeddings.status(sid)
                    if not status['pending_chunks']:
                        break
                    await core.embeddings.index_once(sid)
                else:
                    raise RuntimeError('embedding indexing did not finish')
                embedding = {k:core.embeddings.status(sid).get(k) for k in ('model','dimensions','indexed_chunks','pending_chunks','min_similarity','timeout_seconds')}
                embedding['provider_sha256'] = hashlib.sha256(url.encode()).hexdigest()
            rows = []
            for mode in modes:
                for case in cases:
                    started = time.perf_counter()
                    result = await core.recall(sid, ContextInput(query=case['query'], at=corpus['at'],
                        recent_messages=case.get('recent_messages', []), retrieval_mode=mode))
                    all_selected = [v['event_id'] for v in result['selected']]
                    selected = all_selected[:args.k]
                    expected = case['expected']
                    hits = [eid for eid in selected if eid in expected]
                    row = dict(case, mode=mode, selected=all_selected, top_k=selected, hits=hits,
                        missing=[eid for eid in expected if eid not in selected],
                        reciprocal_rank=next((1/(i+1) for i,eid in enumerate(selected) if eid in expected),0),
                        forbidden_hits=sorted(set(all_selected) & set(case.get('forbidden', []))),
                        elapsed_ms=round((time.perf_counter()-started)*1000,3),
                        degraded=result['degraded'], effective_mode=result['effective_mode'])
                    rows.append(row)
                    if not quiet: print(f"{mode:6} {case['id']:14} hits={len(hits)}/{len(expected)} selected={','.join(selected) or '-'}", flush=True)
                    if progress:
                        progress({'phase': 'recalling', 'completed': len(rows), 'total': len(cases)*len(modes)})
                    await asyncio.sleep(0)
            core_sha = hashlib.sha256(b''.join(f.name.encode()+f.read_bytes() for f in sorted((ROOT/'backend/memory_slice').glob('*.py')))).hexdigest()
            report = dict(events={e["id"]:{"title":e["title"],"body":e["body"]} for e in corpus["events"]}, core_sha256=core_sha, dataset_sha256=fingerprint, k=args.k, at=corpus['at'], config=config, embedding=embedding,
                results=rows, summaries={mode:{'all':summarize([r for r in rows if r['mode']==mode],args.k),
                    **{s['name']:summarize([r for r in rows if r['mode']==mode and r['suite']==s['name']],args.k) for s in suites}} for mode in modes})
            regressions = []
            if args.compare:
                old = json.loads(Path(args.compare).read_text())
                old['config'].setdefault('maintenance_pending_token_limit', 4000)
                if old['dataset_sha256'] != fingerprint or old['k'] != args.k or old['config'] != config or old.get('embedding') != embedding:
                    raise ValueError('baseline dataset/k/config/embedding differs; comparisons would be misleading')
                previous = {(r['mode'],r['id']):r for r in old['results']}
                for row in rows:
                    before = previous.get((row['mode'],row['id']))
                    if before is None:
                        raise ValueError('baseline lacks requested mode/case')
                    if (set(before['hits']) - set(row['hits']) or row['reciprocal_rank'] < before['reciprocal_rank']
                        or (not row['expected'] and not before['selected'] and row['selected'])
                        or set(row['forbidden_hits']) - set(before['forbidden_hits'])
                        or (row['degraded'] and not before['degraded'])):
                        regressions.append(f"{row['mode']}:{row['id']}")
            report['regressions'] = regressions
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            lines = ['# Recall benchmark', '', f'Dataset: `{fingerprint}`; k={args.k}', '',
                '| Mode / Suite | Hit@k | Recall@k | MRR@k | Negative FPR | Mean ms | Degraded |',
                '|---|---:|---:|---:|---:|---:|---:|']
            for mode, summaries in report['summaries'].items():
                for name, summary in summaries.items():
                    values = [summary[f'hit@{args.k}'], summary[f'recall@{args.k}'], summary[f'mrr@{args.k}'], summary['negative_false_positive_rate'], summary['latency_mean_ms']]
                    lines.append(f'| {mode}/{name} | '+' | '.join('-' if v is None else f'{v:.3f}' for v in values)+f" | {summary['degraded_cases']} |")
            lines += ['', 'Regressions: '+(', '.join(regressions) or 'none / no baseline supplied'), '',
                'Fixed small corpus, not population accuracy. Event retrieval only; not extraction or final-answer correctness. See JSON for every hit/miss and all returned events.']
            output.with_suffix('.md').write_text('\n'.join(lines)+'\n')
            if not quiet:
                print(json.dumps(report['summaries'], ensure_ascii=False, indent=2))
                print('Report:', output)
            return 1 if regressions else 0
        finally:
            await core.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['local','hybrid','both'], default='local')
    parser.add_argument('--k', type=int, default=5)
    parser.add_argument('--timeout', type=float, default=1.5)
    parser.add_argument('--output', default=str(ROOT/'eval-results/recall.json'))
    parser.add_argument('--compare', help='prior JSON; exit 1 on per-case regression')
    args = parser.parse_args()
    if args.k < 1:
        parser.error('k must be positive')
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        # Provider exceptions can include request details; never print credentials or response bodies.
        print(f'Evaluation failed ({type(exc).__name__}); check input/config and embedding availability.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
