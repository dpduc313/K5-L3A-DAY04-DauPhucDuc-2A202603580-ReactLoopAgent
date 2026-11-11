"""Kiểm tra offline phần agent cho bonus: retry có giới hạn, Retry-After, phân trang, context."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
import agent
from agent import run_agent
from bonus.tools import BonusTools
from scenarios import ScenarioTools
from status_tools import SCHEMAS
from test_lab import FakeProvider, tool_call, final


def observations(fake, history_index):
    """Các tool message trong history của lần gọi model thứ history_index."""
    return [json.loads(m['content']) for m in fake.histories[history_index] if m.get('role') == 'tool']


class RecordingProvider(FakeProvider):
    """FakeProvider đã lưu histories; thêm schemas để kiểm tra bộ tools được dùng."""
    def __init__(self, responses):
        super().__init__(responses)
        self.schemas = []

    def complete(self, messages, schemas):
        self.schemas.append(schemas)
        return super().complete(messages, schemas)


class BonusAgentLoopTests(unittest.TestCase):
    def test_core_tools_keep_core_prompt_and_schemas(self):
        fake = RecordingProvider([final()])
        run_agent('x', fake, ScenarioTools('normal'))
        self.assertEqual(fake.histories[0][0]['content'], agent.SYSTEM_PROMPT)
        self.assertIs(fake.schemas[0], SCHEMAS)

    def test_persistent_429_stops_after_three_attempts(self):
        tools = BonusTools(scenario='rate_limit_always')
        calls = [tool_call(str(i), 'get_vercel_status', '{}') for i in range(4)]
        fake = RecordingProvider(calls + [final('Vercel: không xác định được do 429.')])
        result = run_agent('x', fake, tools, max_steps=6)
        self.assertEqual(result['status'], 'completed')
        executed = [t for t in result['trace'] if t['event'] == 'tool']
        self.assertEqual(len(executed), 3)
        self.assertEqual(tools.calls[('get_vercel_status', 'vercel')], 3)
        skipped = [t for t in result['trace'] if t['event'] == 'tool_skipped']
        self.assertEqual(skipped[0]['reason'], 'RETRY_BUDGET_EXHAUSTED')
        self.assertIn('hết lượt thử', observations(fake, 3)[-1]['agent_note'])
        self.assertEqual(observations(fake, 4)[-1]['error'], 'RETRY_BUDGET_EXHAUSTED')

    def test_429_is_labelled_with_provider_and_recovery_is_noted(self):
        tools = BonusTools(scenario='rate_limit_once')
        fake = RecordingProvider([tool_call('a', 'get_vercel_status', '{}'), tool_call('b', 'get_vercel_status', '{}'), final()])
        run_agent('x', fake, tools, max_steps=4)
        failed = observations(fake, 1)[-1]
        self.assertEqual((failed['tool'], failed['provider']), ('get_vercel_status', 'vercel'))
        recovered = observations(fake, 2)[-1]
        self.assertEqual(recovered['provider'], 'vercel')
        self.assertIn('thành công sau 1 lần', recovered['agent_note'])

    def test_429_budget_is_per_provider(self):
        tools = BonusTools(scenario='rate_limit_always')
        calls = [tool_call(str(i), 'get_vercel_status', '{}') for i in range(3)]
        fake = RecordingProvider(calls + [tool_call('n', 'get_npm_status', '{}'), final()])
        result = run_agent('x', fake, tools, max_steps=6)
        self.assertEqual(observations(fake, 4)[-1]['provider'], 'npm')
        self.assertFalse(any(t['event'] == 'tool_skipped' for t in result['trace']))

    def test_retry_after_is_respected_before_next_call(self):
        class LimitedOnce(BonusTools):
            def execute(self, name, args):
                self.n = getattr(self, 'n', 0) + 1
                if self.n == 1:
                    return {'ok': False, 'error': 'RATE_LIMITED', 'http_status': 429, 'retry_after': '3', 'retriable': True}
                return super().execute(name, args)
        waits = []
        fake = RecordingProvider([tool_call('a', 'get_npm_status', '{}'), tool_call('b', 'get_npm_status', '{}'), final()])
        with patch.object(agent, '_sleep', side_effect=waits.append):
            result = run_agent('x', fake, LimitedOnce(), max_steps=4)
        self.assertEqual(len(waits), 1)
        self.assertGreater(waits[0], 2.5)
        self.assertTrue(any(t['event'] == 'retry_wait' for t in result['trace']))
        self.assertEqual(observations(fake, 2)[-1]['provider'], 'npm')

    def test_long_retry_after_is_not_waited(self):
        class LongLimit(BonusTools):
            def execute(self, name, args):
                return {'ok': False, 'error': 'RATE_LIMITED', 'http_status': 429, 'retry_after': '120', 'retriable': True}
        fake = RecordingProvider([tool_call('a', 'get_npm_status', '{}'), tool_call('b', 'get_npm_status', '{}'), final()])
        with patch.object(agent, '_sleep') as sleep:
            result = run_agent('x', fake, LongLimit(), max_steps=4)
        sleep.assert_not_called()
        self.assertEqual(sum(t['event'] == 'tool' for t in result['trace']), 1)
        self.assertEqual(observations(fake, 2)[-1]['error'], 'RETRY_BUDGET_EXHAUSTED')

    def test_pagination_note_and_format_hint(self):
        tools = BonusTools(scenario='format_once')
        args = '{"provider":"vercel","incident_id":"sps6n39gm4sg","offset":0,"limit":2}'
        fake = RecordingProvider([tool_call('a', 'get_incident_details', args), tool_call('b', 'get_incident_details', args),
                                  final('https://www.vercel-status.com/incidents/sps6n39gm4sg')])
        run_agent('x', fake, tools, max_steps=4)
        bad = observations(fake, 1)[-1]
        self.assertEqual(bad['error'], 'INVALID_ARGUMENTS')
        self.assertEqual(bad['expected_parameters']['offset']['type'], 'integer')
        good = observations(fake, 2)[-1]
        self.assertEqual(good['next_offset'], 2)
        self.assertIn('offset=2', good['pagination_note'])

    def test_noise_is_compacted_in_context_but_kept_in_trace(self):
        fake = RecordingProvider([tool_call('a', 'get_vercel_status', '{}'), final()])
        result = run_agent('x', fake, BonusTools(scenario='noisy'), max_steps=3)
        sent = observations(fake, 1)[-1]
        self.assertEqual(len(sent['distractors']), agent.LONG_LIST_KEEP)
        self.assertIn('distractors: bỏ 158 mục', sent['agent_context_note'])
        self.assertTrue(sent['incidents'])
        self.assertEqual(len(result['trace'][1]['result']['distractors']), 160)

    def test_core_noisy_status_is_not_compacted(self):
        fake = RecordingProvider([tool_call('a', 'get_status', '{"provider":"github","component":"Actions"}'), final()])
        run_agent('x', fake, ScenarioTools('noisy_status'), max_steps=3)
        self.assertEqual(len(observations(fake, 1)[-1]['distractors']), 40)


if __name__ == '__main__':
    unittest.main()
