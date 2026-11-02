import unittest

from memory_slice.engine import build_context
from memory_slice.schemas import MemoryConfig
from memory_slice.context_protocol import RULES
from test_memory import NOW, event


class RecallBudgetTests(unittest.TestCase):
    def recall(self, events, mode='associative', current_context=None, **overrides):
        config = MemoryConfig(max_seeds=12, context_char_budget=100000).model_dump()
        config.update(overrides)
        return build_context(events, None, '陨石一号', NOW, config, mode=mode, current_context=current_context)

    def test_remove_lowest_first_in_both_modes(self):
        events = [event('high', salience=1, age=0), event('middle', salience=.7, age=0),
                  event('low', salience=.3, age=0)]
        for mode in ('associative', 'deliberate'):
            full = self.recall(events, mode, context_char_budget=100000)
            two = self.recall(events, mode, max_events=2, context_char_budget=100000)
            budget = two['context_chars']
            result = self.recall(events, mode, context_char_budget=budget)
            self.assertEqual([x['event']['id'] for x in result['selected']], ['high', 'middle'])
            self.assertEqual(result['context_chars'], budget)
            self.assertLess(budget, full['context_chars'])
            self.assertEqual(result['omitted'][0]['event_id'], 'low')
            self.assertEqual(result['omitted'][0]['code'], 'char_budget')

    def test_huge_best_event_does_not_backfill_lower_ranked(self):
        result = self.recall([event('huge', salience=1, age=0, body='陨石一号' * 1000),
                              event('small', salience=.3, age=0)], context_char_budget=1024)
        self.assertEqual(result['context'], '')
        self.assertEqual(result['selected'], [])
        self.assertEqual(result['context_chars'], 0)
        self.assertEqual({x['event_id'] for x in result['omitted']}, {'small', 'huge'})

    def test_count_includes_rules_markup_escaped_content_and_source_ids(self):
        e = event(age=0, body='陨石一号 中文 english 😀 <&>')
        e['source_ids'] = ['source' * 20]
        full = self.recall([e])
        self.assertEqual(full['context_chars'], len(full['context']))
        self.assertGreater(full['context_chars'], len(e['body']))
        exact = full['context_chars']
        self.assertTrue(self.recall([e], context_char_budget=exact)['selected'])
        self.assertFalse(self.recall([e], context_char_budget=exact - 1)['selected'])
        without_rules = self.recall([e], current_context=[RULES])
        self.assertLess(without_rules['context_chars'], exact)
        dedup = self.recall([e], current_context=[full['context']])
        self.assertEqual(dedup['context_chars'], 0)

    def test_graph_events_count_toward_same_budget(self):
        e = event('entry', age=0, links=[{'target_id': 'linked', 'strength': 1}])
        linked = event('linked', age=0)
        config = MemoryConfig(max_events=1).model_dump()
        result = build_context([e, linked], None, '陨石一号', NOW, config, entry_ids={'entry'})
        self.assertEqual([x['event']['id'] for x in result['selected']], ['entry'])
        self.assertEqual(result['omitted'][0]['event_id'], 'linked')
        self.assertEqual(result['omitted'][0]['code'], 'event_limit')

    def test_legacy_config_default_and_validation(self):
        config = MemoryConfig().model_dump()
        result = build_context([event(age=0)], None, '陨石一号', NOW, config)
        self.assertEqual(config['context_char_budget'], 18000)
        self.assertLessEqual(result['context_chars'], 18000)
        for key in ('context_char_budget', 'max_events'):
            with self.assertRaises(ValueError):
                MemoryConfig(**{key: 0})
