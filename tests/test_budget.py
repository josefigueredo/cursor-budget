import json
import tempfile
import unittest
from pathlib import Path

from cbudget import budget, cli

NOON = 1761300000.0  # 2026-10-24, a fixed clock


def stop(model="claude-sonnet-5-5-high", conv="c1", **tokens):
    return {"hook_event_name": "stop", "model": model, "conversation_id": conv, **tokens}


class TestCost(unittest.TestCase):
    table = budget.prices(Path("/nonexistent"))

    def test_reproduces_the_oct_21_opus_bill_within_five_percent(self):
        # Cursor CSV, Oct 21: 29 claude-opus-5-5-high events billed $70.26
        event = {"model": "claude-opus-5-5-high", "input_tokens": 91_471_000, "cache_read_tokens": 87_470_000,
                 "cache_write_tokens": 4_000_000, "output_tokens": 611_000}
        self.assertAlmostEqual(budget.cost(event, self.table), 70.26, delta=70.26 * 0.05)

    def test_cached_input_is_not_billed_twice(self):
        event = {"model": "gpt-5.6-terra-medium", "input_tokens": 1_000_000, "cache_read_tokens": 1_000_000}
        # 1M cache read at $0.20 plus the $0.25 surcharge, no uncached input
        self.assertAlmostEqual(budget.cost(event, self.table), 0.45, places=4)

    def test_cursor_models_skip_the_surcharge(self):
        event = {"model": "cursor-grok-4.6-high", "input_tokens": 1_000_000}
        self.assertAlmostEqual(budget.cost(event, self.table), 2.0, places=4)

    def test_auto_is_priced_like_a_mid_tier_model_not_as_unknown(self):
        # the stop payload names the Auto mode (auto-smart), never the model it routed to
        event = {"model": "auto-smart", "input_tokens": 1_000_000}
        self.assertAlmostEqual(budget.cost(event, self.table), 2.25, places=4)

    def test_unknown_model_is_priced_high(self):
        event = {"model": "brand-new-model", "output_tokens": 1_000_000}
        self.assertAlmostEqual(budget.cost(event, self.table), 20.25, places=4)


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.notes = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_stop(self, payload, t=NOON):
        return budget.on_stop(payload, home=self.home, now=lambda: t, notify=self.notes.append)

    def run_submit(self, payload, t=NOON):
        return budget.on_submit(payload, home=self.home, now=lambda: t, notify=self.notes.append)


class TestStop(Home):
    def test_records_each_turn_in_the_ledger(self):
        self.run_stop(stop(input_tokens=100_000, cache_read_tokens=90_000, output_tokens=1_000))
        [entry] = budget.read_ledger(self.home)
        self.assertEqual(entry["conversation_id"], "c1")
        self.assertGreater(entry["usd"], 0)

    def test_warns_once_when_the_conversation_crosses_its_limit(self):
        turn = stop(input_tokens=1_000_000, cache_read_tokens=900_000, output_tokens=50_000)  # ~$1.1
        for _ in range(3):
            self.run_stop(turn)
        self.assertEqual(len([n for n in self.notes if "este chat" in n]), 1)

    def test_answers_an_empty_object(self):
        self.assertEqual(self.run_stop(stop(input_tokens=10)), {})


class TestSubmit(Home):
    def test_lets_a_cheap_conversation_through(self):
        self.run_stop(stop(input_tokens=10_000))
        self.assertEqual(self.run_submit({"model": "claude-sonnet-5-5-high", "conversation_id": "c1"}), {})

    def test_blocks_a_blocked_model(self):
        answer = self.run_submit({"model": "claude-opus-5-5-high", "conversation_id": "c9"})
        self.assertIs(answer["continue"], False)
        self.assertIn("claude-opus-5-5-high", answer["user_message"])

    def test_blocks_an_expensive_conversation(self):
        self.run_stop(stop(model="claude-opus-5-5-medium", input_tokens=1_500_000))  # ~$6.4
        answer = self.run_submit({"model": "claude-sonnet-5-5-high", "conversation_id": "c1"})
        self.assertIn("chat nuevo", answer["user_message"])

    def test_a_new_conversation_passes_under_the_daily_limit(self):
        self.run_stop(stop(model="claude-opus-5-5-medium", input_tokens=1_500_000))  # ~$6.4
        self.assertEqual(self.run_submit({"model": "claude-sonnet-5-5-high", "conversation_id": "c2"}), {})

    def test_blocks_when_the_day_is_spent(self):
        for conv in ("a", "b", "c", "d"):
            self.run_stop(stop(model="claude-opus-5-5-medium", conv=conv, input_tokens=1_500_000))
        answer = self.run_submit({"model": "claude-sonnet-5-5-high", "conversation_id": "e"})
        self.assertIn("hoy", answer["user_message"])

    def test_allow_lifts_the_blocks(self):
        budget.allow(30, home=self.home, now=lambda: NOON)
        self.assertEqual(self.run_submit({"model": "claude-opus-5-5-high", "conversation_id": "c9"}), {})
        self.assertIs(self.run_submit({"model": "claude-opus-5-5-high"}, t=NOON + 31 * 60)["continue"], False)

    def test_config_overrides_the_limits(self):
        (self.home / "config.json").write_text(json.dumps({"blocked_models": []}), encoding="utf-8")
        self.assertEqual(self.run_submit({"model": "claude-opus-5-5-high", "conversation_id": "c9"}), {})


class TestCli(unittest.TestCase):
    def test_a_broken_payload_fails_open(self):
        self.assertEqual(cli.hook("not json"), {})

    def test_install_keeps_other_hooks_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hooks.json"
            path.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "other hook"}]}}))
            cmd = "/usr/bin/python3 /x/cbudget.py hook"
            self.assertTrue(cli.install_hook(cmd, path))
            self.assertFalse(cli.install_hook(cmd, path))
            hooks = json.loads(path.read_text())["hooks"]
            self.assertEqual([h["command"] for h in hooks["stop"]], ["other hook", cmd])
            self.assertEqual([h["command"] for h in hooks["beforeSubmitPrompt"]], [cmd])


if __name__ == "__main__":
    unittest.main()
