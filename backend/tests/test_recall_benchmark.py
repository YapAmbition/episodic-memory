"""Validate the scorer separately from retrieval quality (quality failures are baseline deltas)."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('recall_benchmark', Path(__file__).resolve().parents[2]/'scripts/evaluate_recall.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkMetricsTests(unittest.TestCase):
    def test_partial_recall_is_not_hit_rate_and_negatives_are_separate(self):
        common = dict(forbidden_hits=[], degraded=False, elapsed_ms=10)
        rows = [dict(common, expected=['a','b'], hits=['a'], selected=['x','a'], reciprocal_rank=.5),
                dict(common, expected=['c'], hits=[], selected=[], reciprocal_rank=0),
                dict(common, expected=[], hits=[], selected=['x'], reciprocal_rank=0),
                dict(common, expected=[], hits=[], selected=[], reciprocal_rank=0)]
        result = benchmark.summarize(rows, 5)
        self.assertEqual(result['hit@5'], .5)
        self.assertEqual(result['recall@5'], .25)
        self.assertEqual(result['mrr@5'], .25)
        self.assertEqual(result['negative_false_positive_rate'], .5)

    def test_empty_group_is_not_perfect_accuracy(self):
        result = benchmark.summarize([], 5)
        self.assertIsNone(result['hit@5'])
        self.assertIsNone(result['negative_false_positive_rate'])


import time
from unittest.mock import patch
import test_memory as fixtures


class BenchmarkAPITests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown

    def test_runs_isolated_and_reports_baseline_without_mutating_events(self):
        before = self.client.get(self.prefix+'/events').json()
        response = self.client.post(self.prefix+'/benchmarks?mode=local', json={})
        self.assertEqual(response.status_code, 200, response.text)
        for _ in range(200):
            job = self.client.get('/api/v1/benchmarks').json()[-1]
            if job['status'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(job['status'], 'completed', job.get('error'))
        self.assertEqual(len(job['report']['results']), 28)
        self.assertIsNotNone(job['report']['baseline'])
        self.assertFalse(any(r['regressed'] for r in job['report']['results']))
        self.assertEqual(self.client.get(self.prefix+'/events').json(), before)
        self.assertEqual(len(self.client.get('/api/v1/spaces').json()), 1)

    def test_validates_mode_configuration_and_space(self):
        self.assertEqual(self.client.post(self.prefix+'/benchmarks?mode=llm').status_code, 422)
        self.assertEqual(self.client.post(self.prefix+'/benchmarks?mode=both').status_code, 422)
        self.assertEqual(self.client.post('/api/v1/spaces/missing/benchmarks').status_code, 404)

    def test_rejects_concurrent_runs(self):
        import asyncio
        async def waiting(*args, **kwargs):
            await asyncio.sleep(60)
        with patch('memory_slice.benchmark.run', waiting):
            self.assertEqual(self.client.post(self.prefix+'/benchmarks').status_code, 200)
            self.assertEqual(self.client.post(self.prefix+'/benchmarks').status_code, 409)
        # App lifespan cancels the owned test task during tearDown.
