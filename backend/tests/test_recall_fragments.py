import unittest

from memory_slice.engine import build_context
from memory_slice.schemas import MemoryConfig
from memory_slice.recall_fragments import split_body
from memory_slice.context_protocol import inspect_context, inspect_fragments, memory_block, RULES
from test_memory import NOW, event


class RecallFragmentTests(unittest.TestCase):
    def recall(self, e, query='陨石一号', history=None, **kw):
        config = MemoryConfig(fragment_target_chars=200, fragment_max_chars=400,
                              context_char_budget=1800).model_dump()
        config.update(kw)
        return build_context([e], None, query, NOW, config, current_context=history)

    def long_event(self):
        return dict(event(age=0, body='共同背景。\n\n' + ''.join(
            f'### 阶段{i}\n' + (('面包烤焦。' if i == 3 else '天气晴朗。') * 65) + '\n\n'
            for i in range(10))), version=1)

    def test_lossless_hard_bound_unicode_and_append_stability(self):
        body = '😀<&>英文\n' * 50 + '\n### 后续\n' + '无标点' * 1000
        parts = split_body(body, 200, 400)
        self.assertEqual(''.join(p['text'] for p in parts), body)
        self.assertTrue(all(0 < len(p['text']) <= 400 for p in parts))
        self.assertEqual(parts, split_body(body, 200, 400))
        later = split_body(body + '\n### 新阶段\n后续事实', 200, 400)
        # The complete earlier structural section is unchanged.
        self.assertEqual(parts[0]['id'], later[0]['id'])
        self.assertEqual(parts[0]['text'], body[parts[0]['start']:parts[0]['end']])

    def test_long_event_returns_relevant_partial_within_budget(self):
        e = self.long_event()
        result = self.recall(e, '陨石一号 面包烤焦')
        self.assertTrue(result['selected'])
        selected = result['selected'][0]
        self.assertTrue(selected['partial'])
        self.assertIn('面包烤焦', result['context'])
        self.assertIn('共同背景', result['context'])
        self.assertLessEqual(len(result['context']), 1800)
        self.assertEqual(selected['event']['body'], e['body'])
        self.assertNotIn(e['id'], inspect_context([result['context']])[0])
        self.assertTrue(inspect_fragments([result['context']])[0])

    def test_same_revision_can_supply_more_parts_without_repeating(self):
        e = self.long_event()
        first = self.recall(e)
        second = self.recall(e, history=[first['context']])
        self.assertTrue(second['selected'])
        a = {p['id'] for p in first['selected'][0]['fragments']}
        b = {p['id'] for p in second['selected'][0]['fragments']}
        self.assertFalse(a & b)
        self.assertNotIn('<memory-slice-rules', second['context'])
        new = self.recall(dict(e, version=2), history=[first['context']])
        self.assertIn('supersedes="1"', new['context'])
        self.assertIn('revision="2"', new['context'])

    def test_full_history_and_newer_partial_suppress_older_event(self):
        e = self.long_event()
        full = memory_block(e, e['body'])
        self.assertFalse(self.recall(e, history=[full])['selected'])
        newer = self.recall(dict(e, version=2))['context']
        self.assertFalse(self.recall(e, history=[newer])['selected'])

    def test_correction_is_kept_or_whole_dependency_group_omitted(self):
        e = self.long_event()
        e['body'] += '\n### 更正\n面包其实没有烤焦，黑色的是可可粉。'
        result = self.recall(e, '陨石一号 面包烤焦')
        self.assertTrue(result['selected'])
        self.assertIn('可可粉', result['context'])
        self.assertIn('共同背景', result['context'])
        tiny = self.recall(e, '陨石一号 面包烤焦', context_char_budget=100)
        self.assertEqual(tiny['context'], '')

    def test_event_larger_than_default_total_budget(self):
        e = event(age=0, body='背景：面包实验。\n' + ''.join(
            f'### 第{i}次记录\n' + ('陨石一号烤箱温度。' * 180) + '\n\n' for i in range(20)))
        self.assertGreater(len(e['body']), 18000)
        result = build_context([e], None, '陨石一号烤箱温度', NOW, MemoryConfig().model_dump())
        self.assertTrue(result['selected'])
        self.assertTrue(result['selected'][0]['partial'])
        self.assertLessEqual(result['context_chars'], 18000)
        self.assertTrue(all(len(p['text']) <= 3000 for p in result['selected'][0]['fragments']))

    def test_invalid_partial_does_not_suppress_and_rules_upgrade(self):
        e = self.long_event()
        malformed = '<memory-slice id="e1" revision="1" partial="true" parts="bad">text</memory-slice>'
        result = self.recall(e, history=[malformed, RULES.replace('version="2"', 'version="1"')])
        self.assertTrue(result['selected'])
        self.assertIn('<memory-slice-rules version="2">', result['context'])
        with self.assertRaises(ValueError):
            MemoryConfig(fragment_target_chars=1000, fragment_max_chars=500)
