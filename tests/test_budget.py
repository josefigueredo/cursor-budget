import json
import tempfile
import unittest
from pathlib import Path

from cbudget import budget, cli, render

NOON = 1792850000.0  # 2026-10-24 10:53 -03, a fixed clock


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
        self.assertEqual(len([n for n in self.notes if "este chat ya va" in n]), 1)

    def test_answers_an_empty_object(self):
        self.assertEqual(self.run_stop(stop(input_tokens=10)), {})


class TestCycle(Home):
    ROW = {"Model": "claude-sonnet-5-5-high", "Cost": "100.00", "Date": "2026-10-20T12:00:00.000Z"}

    def test_cycle_starts_on_the_configured_day(self):
        self.assertEqual(budget.cycle_start(NOON, 1), "2026-10-01")
        self.assertEqual(budget.cycle_start(NOON, 25), "2026-09-25")

    def test_seeded_cycle_adds_billed_and_later_estimates_only(self):
        old = dict(self.ROW, Date="2026-09-30T12:00:00.000Z")  # previous cycle: ignored
        budget.seed([self.ROW, old], home=self.home, now=lambda: NOON, cycle_day=1)
        self.run_stop(stop(input_tokens=1_000_000, cache_read_tokens=1_000_000))  # $0.35 after the CSV
        total, seeded = budget.cycle_total(budget.read_ledger(self.home), self.home, NOON, 1)
        self.assertTrue(seeded)
        self.assertAlmostEqual(total, 100.35, places=2)

    def test_warns_when_the_cycle_crosses_a_share_of_the_limit(self):
        (self.home / "config.json").write_text(json.dumps({"cycle_limit": 100.2}), encoding="utf-8")
        budget.seed([self.ROW], home=self.home, now=lambda: NOON, cycle_day=1)
        self.run_stop(stop(input_tokens=1_000_000, cache_read_tokens=1_000_000))
        self.assertTrue(any("el ciclo ya va" in n and "100%" in n for n in self.notes))

    def test_each_turn_reports_the_cycle_unless_disabled(self):
        self.run_stop(stop(input_tokens=10_000))
        self.assertIn("ciclo", self.notes[-1])
        self.assertIn("cbudget seed", self.notes[-1])
        (self.home / "config.json").write_text(json.dumps({"notify_each_turn": False}), encoding="utf-8")
        self.notes.clear()
        self.run_stop(stop(input_tokens=10_000))
        self.assertEqual(self.notes, [])


class TestModelBudget(Home):
    def setUp(self):
        super().setUp()
        (self.home / "config.json").write_text(json.dumps(
            {"blocked_models": [], "block_day": 1000, "model_budgets": {"claude-opus-*": 80, "auto*": 50}}), encoding="utf-8")
        rows = [{"Model": "claude-opus-5-5-high", "Cost": "60.00", "Date": "2026-10-21T12:00:00.000Z"},
                {"Model": "grok-4.7-high (Auto Balanced)", "Cost": "49.00", "Date": "2026-10-22T12:00:00.000Z"}]
        budget.seed(rows, home=self.home, now=lambda: NOON, cycle_day=1)

    def opus_turn(self):  # ~$10.6 of Opus after the CSV
        self.run_stop(stop(model="claude-opus-5-5-medium", conv="x", input_tokens=2_500_000))

    def test_a_model_under_its_budget_passes(self):
        self.assertEqual(self.run_submit({"model": "claude-opus-5-5-medium", "conversation_id": "n"}), {})

    def test_warns_when_a_model_crosses_its_warn_share(self):
        self.opus_turn()
        self.assertTrue(any("claude-opus-*" in n and "80" in n for n in self.notes))

    def test_blocks_a_model_once_its_budget_is_spent(self):
        self.opus_turn()
        self.opus_turn()  # 60 + 21.2 > 80
        answer = self.run_submit({"model": "claude-opus-5-5-low", "conversation_id": "n"})
        self.assertIn("agotó su presupuesto", answer["user_message"])

    def test_auto_routed_csv_rows_count_against_auto(self):
        self.run_stop(stop(model="auto-smart", conv="y", input_tokens=1_000_000))  # ~$2.25
        answer = self.run_submit({"model": "auto-smart", "conversation_id": "n"})
        self.assertIn("auto*", answer["user_message"])

    def test_other_models_are_not_limited(self):
        self.opus_turn()
        self.opus_turn()
        self.assertEqual(self.run_submit({"model": "claude-sonnet-5-5-high", "conversation_id": "n"}), {})


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

    def test_a_block_leaves_evidence(self):
        self.run_submit({"model": "claude-opus-5-5-high", "conversation_id": "c9"})
        [line] = (self.home / "blocks.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(line)["model"], "claude-opus-5-5-high")

    def test_allow_lifts_the_blocks(self):
        budget.allow(30, home=self.home, now=lambda: NOON)
        self.assertEqual(self.run_submit({"model": "claude-opus-5-5-high", "conversation_id": "c9"}), {})
        self.assertIs(self.run_submit({"model": "claude-opus-5-5-high"}, t=NOON + 31 * 60)["continue"], False)

    def test_config_overrides_the_limits(self):
        (self.home / "config.json").write_text(json.dumps({"blocked_models": []}), encoding="utf-8")
        self.assertEqual(self.run_submit({"model": "claude-opus-5-5-high", "conversation_id": "c9"}), {})


class TestCalibrate(unittest.TestCase):
    HEADER = ("Date,User,PR Author,Repo,PR Number,Cloud Agent ID,Automation ID,Kind,Model,Max Mode,"
              "Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost\n")

    def write(self, tmp, *rows):
        path = Path(tmp) / "usage.csv"
        path.write_text(self.HEADER + "".join(rows), encoding="utf-8")
        return path

    def test_groups_by_model_and_prices_auto_routed_rows_by_the_routed_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp,
                              '"d","u","","","","","","Included","grok-4.7-high (Auto Balanced)","No","0","1000000","0","0","1000000","2.00"\n',
                              '"d","u","","","","","","Included","kimi-k3 (Auto Balanced)","No","0","0","0","0","0","Free"\n',
                              '"d","u","","","","","","","usage-correction","","0","0","0","0","0","1.06"\n')
            import csv
            with path.open() as fh:
                rows = budget.calibrate(csv.DictReader(fh), budget.prices(Path("/nonexistent")))
        self.assertEqual(rows[0], ("grok-4.7-high (Auto Balanced)", 1, 2.0, 2.0, "grok"))
        self.assertEqual([r[0] for r in rows], ["grok-4.7-high (Auto Balanced)", "kimi-k3 (Auto Balanced)"])

    def test_report_flags_a_model_off_by_more_than_ten_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, '"d","u","","","","","","Included","cursor-grok-4.6-high","No","0","1000000","0","0","1000000","1.00"\n')
            self.assertIn("<- ajustar", cli.calibrate_report(path))


class TestReport(Home):
    def test_plain_report_has_sections_and_no_escape_codes(self):
        (self.home / "config.json").write_text(json.dumps({"model_budgets": {"claude-*": 10}}), encoding="utf-8")
        self.run_stop(stop(input_tokens=1_000_000))
        text = render.report(7, home=self.home, color=False, now=NOON)
        for heading in ("CURSOR BUDGET", "CICLO", "PRESUPUESTO POR MODELO", "ÚLTIMOS 7 DÍAS", "Chats más caros"):
            self.assertIn(heading, text)
        self.assertNotIn("\033[", text)
        self.assertIn("quedan 8 días", text)

    def test_color_report_paints_a_spent_budget_red(self):
        (self.home / "config.json").write_text(json.dumps({"model_budgets": {"claude-*": 1}}), encoding="utf-8")
        self.run_stop(stop(input_tokens=1_000_000))
        self.assertIn("\033[31m", render.report(7, home=self.home, color=True, now=NOON))

    def test_next_cycle_handles_short_months(self):
        self.assertEqual(render.next_cycle("2026-10-01", 1).isoformat(), "2026-11-01")
        self.assertEqual(render.next_cycle("2027-01-31", 31).isoformat(), "2027-02-28")


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
