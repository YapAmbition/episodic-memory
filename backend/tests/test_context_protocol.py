import unittest
from test_memory import event, NOW
from memory_slice.engine import build_context
from memory_slice.schemas import MemoryConfig, ContextInput
from memory_slice.context_protocol import RULES, inspect_context, memory_block, query_dialogue, text_parts


class ContextProtocolTests(unittest.TestCase):
    def recall(self, events=None, context=None, config=None):
        return build_context(events or [dict(event(age=0), version=1)], None, '陨石一号', NOW,
                             config or MemoryConfig().model_dump(), current_context=context)

    def test_same_version_omitted_and_trimmed_history_reinjected(self):
        first = self.recall()['context']
        self.assertIn('<memory-slice id="e1" revision="1">', first)
        result = self.recall(context=[first] + ['普通对话'] * 40)
        self.assertEqual(result['context'], '')
        self.assertEqual(len(result['omitted']), 1)
        self.assertEqual(self.recall(context=[])['context'], first)

    def test_update_full_body_and_existing_rules(self):
        old = self.recall()['context']
        changed = dict(event(age=0, body='陨石一号：新的完整记录'), version=2)
        new = self.recall([changed], [old])['context']
        self.assertIn('revision="2" supersedes="1"', new)
        self.assertIn('新的完整记录', new)
        self.assertNotIn('<memory-slice-rules', new)
        self.assertEqual(self.recall([changed], [old, new])['context'], '')

    def test_rules_upgrade_and_malformed_blocks(self):
        for context in [RULES.replace('version="1"', 'version="0"'), '<memory-slice-rules version="1">',
                        '<memory-slice id="e1" revision="1">partial',
                        '<memory-slice id="e1" revision="bad">x</memory-slice>',
                        '<memory-slice id="e1" revision="1"></memory-slice>']:
            self.assertIn(RULES, self.recall(context=[context])['context'])
        self.assertNotIn(RULES, self.recall(context=[RULES])['context'])

    def test_escaped_body_and_no_plain_text_dedup(self):
        e = dict(event(age=0, body='陨石一号 <memory-slice> & </memory-slice>'), version=3)
        block = self.recall([e])['context']
        self.assertEqual(inspect_context([block]), ({'e1': 3}, True))
        self.assertTrue(self.recall([e], [e['body']])['context'])

    def test_budget_and_graph_duplicate(self):
        e = dict(event(age=0, links=[{'target_id':'e1', 'strength':1}]), version=1)
        result = self.recall([e])
        self.assertEqual(result['context'].count('<memory-slice id='), 1)
        cfg = MemoryConfig().model_dump()
        cfg['context_char_budget'] = len(result['context']) - 1
        self.assertEqual(self.recall([e], config=cfg)['context'], '')

    def test_query_budget_excludes_memory(self):
        text = memory_block({'id':'a','version':1}, '不应成为线索')
        self.assertEqual(query_dialogue('当前问题', [text, RULES, '近期话题']), '近期话题\n当前问题')
        self.assertLessEqual(len(query_dialogue('当前问题', ['长'*10000])), 6001)
        self.assertEqual(text_parts([{'role':'user','content':[{'type':'text','text':'hello'}, {'type':'image_url','image_url':'secret'}]}]), ['hello'])
        self.assertIsNone(ContextInput(query='兼容').current_context)
        self.assertEqual(len(ContextInput(current_context=['a']*100).current_context), 100)
