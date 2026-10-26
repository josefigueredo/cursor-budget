import json
import os
import tempfile
import time
import unittest
from pathlib import Path

# Days and cycles are local dates: the fixed clock below reads as a date in Buenos Aires, whatever the machine's zone.
os.environ["TZ"] = "America/Argentina/Buenos_Aires"
time.tzset()

from cbudget import budget, cli, limits, render  # noqa: E402

NOON = 1792850000.0  # 2026-10-24 10:53 -03, a fixed clock


def at(date: str, hour: int = 12) -> float:
    return time.mktime(time.strptime(f"{date} {hour}", "%Y-%m-%d %H"))


def stop(model="claude-sonnet-5-5-high", conv="c1", **tokens):
    return {"hook_event_name": "stop", "model": model, "conversation_id": conv, **tokens}


class TestCost(unittest.TestCase):
    table = budget.prices(Path("/nonexistent"))

    def test_opus_turn_follows_the_formula(self):
        # made-up turn: 5M uncached x $4 + 5M cache write x $4 x 1.25 + 90M cache read x $0.20 + 0.5M output x $20
        # + the $0.25 surcharge on all 100.5M tokens = 20 + 25 + 18 + 10 + 25.125
        event = {"model": "claude-opus-5-5-high", "input_tokens": 100_000_000, "cache_read_tokens": 90_000_000,
                 "cache_write_tokens": 5_000_000, "output_tokens": 500_000}
        self.assertAlmostEqual(budget.cost(event, self.table), 98.125, places=3)

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

    def test_a_cycle_day_the_month_lacks_starts_on_its_last_day(self):
        # Cursor bills a 31st cycle on Feb 28: that day belongs to the new cycle, not to both
        for date, cycle_day, start in (("2027-02-28", 31, "2027-02-28"), ("2027-03-01", 31, "2027-02-28"),
                                       ("2027-02-27", 31, "2027-01-31"), ("2027-03-31", 31, "2027-03-31"),
                                       ("2027-03-30", 31, "2027-02-28"), ("2026-04-30", 31, "2026-04-30"),
                                       ("2028-02-29", 30, "2028-02-29"), ("2028-02-28", 30, "2028-01-30"),
                                       ("2027-02-28", 29, "2027-02-28")):
            self.assertEqual(budget.cycle_start(at(date), cycle_day), start, f"{date} day {cycle_day}")

    def test_the_cycle_start_agrees_with_the_report_next_cycle(self):
        start = budget.cycle_start(at("2027-01-31"), 31)
        self.assertEqual(render.next_cycle(start, 31).isoformat(), budget.cycle_start(at("2027-02-28"), 31))

    def test_seeded_cycle_adds_billed_and_later_estimates_only(self):
        old = dict(self.ROW, Date="2026-09-30T12:00:00.000Z")  # previous cycle: ignored
        budget.seed([self.ROW, old], home=self.home, now=lambda: NOON, cycle_day=1)
        self.run_stop(stop(input_tokens=1_000_000, cache_read_tokens=1_000_000))  # $0.35 after the CSV
        total, seeded = budget.cycle_total(budget.read_ledger(self.home), self.home, NOON, 1)
        self.assertTrue(seeded)
        self.assertAlmostEqual(total, 100.35, places=2)

    def test_amount_seed_sets_the_cycle_without_a_csv(self):
        budget.seed_amount(84.30, home=self.home, now=lambda: NOON - 60, cycle_day=1)
        self.run_stop(stop(input_tokens=1_000_000, cache_read_tokens=1_000_000))
        total, seeded = budget.cycle_total(budget.read_ledger(self.home), self.home, NOON, 1)
        self.assertTrue(seeded)
        self.assertAlmostEqual(total, 84.65, places=2)
        self.assertIn("Lo que mostraba Cursor", render.report(7, home=self.home, now=NOON))

    def test_warns_when_the_cycle_crosses_a_share_of_the_limit(self):
        (self.home / "config.json").write_text(json.dumps({"cycle_limit": 100.2}), encoding="utf-8")
        budget.seed([self.ROW], home=self.home, now=lambda: NOON, cycle_day=1)
        self.run_stop(stop(input_tokens=1_000_000, cache_read_tokens=1_000_000))
        self.assertTrue(any("el ciclo ya va" in n and "100%" in n for n in self.notes))

    def test_each_turn_reports_the_cycle_unless_disabled(self):
        self.run_stop(stop(input_tokens=10_000))
        self.assertIn("ciclo", self.notes[-1])
        self.assertIn("cbudget seed --amount", self.notes[-1])
        (self.home / "config.json").write_text(json.dumps({"notify_each_turn": False}), encoding="utf-8")
        self.notes.clear()
        self.run_stop(stop(input_tokens=10_000))
        self.assertEqual(self.notes, [])

    NEXT_CYCLE = NOON + 10 * 86400  # 2026-11-03

    def test_a_seed_is_needed_once_the_next_cycle_counts_from_its_start(self):
        budget.seed_amount(84.30, home=self.home, now=lambda: NOON, cycle_day=1)
        self.run_stop(stop(input_tokens=10_000), t=self.NEXT_CYCLE)
        self.assertNotIn("cbudget seed", self.notes[-1])
        total, counted = budget.cycle_total(budget.read_ledger(self.home), self.home, self.NEXT_CYCLE, 1)
        self.assertTrue(counted)
        self.assertLess(total, 1)
        out = render.report(7, home=self.home, now=self.NEXT_CYCLE)
        self.assertNotIn("Solo lo visto desde la instalación", out)
        self.assertIn("desde que empezó el ciclo", out)

    def test_hooks_running_before_the_cycle_started_also_count_it_whole(self):
        self.run_stop(stop(input_tokens=10_000), t=NOON)  # no seed ever, but the hook was already counting
        self.notes.clear()
        self.run_stop(stop(input_tokens=10_000), t=self.NEXT_CYCLE)
        self.assertNotIn("cbudget seed", self.notes[-1])


class TestExtras(Home):
    TURN = stop(input_tokens=1_000_000, cache_read_tokens=1_000_000)  # ~$0.35

    def grant(self, t=NOON):
        return budget.grant_extra(home=self.home, now=lambda: t, actor="terminal", notify=self.notes.append)

    def test_the_plan_takes_an_extra_and_then_an_exceptional_one_of_75_each(self):
        self.assertEqual(budget.cycle_cap(budget.config(self.home), self.home, NOON), 150.0)
        self.assertEqual(self.grant()["after"], 225.0)
        self.assertEqual(self.grant()["after"], 300.0)
        self.assertEqual(budget.cycle_cap(budget.config(self.home), self.home, NOON), 300.0)

    def test_a_third_extra_is_refused_and_changes_nothing(self):
        self.grant()
        self.grant()
        with self.assertRaises(ValueError) as cm:
            self.grant()
        self.assertIn("los 2 extras", str(cm.exception))
        self.assertEqual(budget.cycle_cap(budget.config(self.home), self.home, NOON), 300.0)

    def test_extras_end_with_their_cycle(self):
        self.grant()
        next_cycle = NOON + 10 * 86400  # 2026-11-03
        self.assertEqual(budget.cycle_cap(budget.config(self.home), self.home, next_cycle), 150.0)

    def test_each_extra_is_logged_and_notified(self):
        self.grant()
        [change] = [json.loads(line) for line in (self.home / "changes.jsonl").read_text().splitlines()]
        self.assertEqual((change["key"], change["before"], change["after"], change["actor"]),
                         ("cycle_extra", 150.0, 225.0, "terminal"))
        self.assertTrue(any("extra" in n and "$225" in n for n in self.notes))

    def test_reaching_the_plan_cap_suggests_asking_for_the_extra(self):
        budget.seed_amount(149.80, home=self.home, now=lambda: NOON - 60, cycle_day=1)
        self.run_stop(self.TURN)
        [warning] = [n for n in self.notes if "el ciclo ya va" in n]
        self.assertIn("100%", warning)
        self.assertIn("pedir el extra de $75", warning)
        self.assertIn("cbudget extra", warning)

    def test_with_the_extra_the_warnings_follow_the_new_cap(self):
        self.grant()
        budget.seed_amount(149.80, home=self.home, now=lambda: NOON - 60, cycle_day=1)
        self.run_stop(self.TURN)
        self.assertFalse(any("100%" in n for n in self.notes))
        budget.seed_amount(224.80, home=self.home, now=lambda: NOON + 60, cycle_day=1)
        self.run_stop(self.TURN, t=NOON + 120)
        [warning] = [n for n in self.notes if "el ciclo ya va" in n and "100%" in n]
        self.assertIn("de $225", warning)
        self.assertIn("extra excepcional de $75", warning)

    def test_with_both_extras_used_the_warning_says_there_is_no_more(self):
        self.grant()
        self.grant()
        budget.seed_amount(299.80, home=self.home, now=lambda: NOON - 60, cycle_day=1)
        self.run_stop(self.TURN)
        [warning] = [n for n in self.notes if "el ciclo ya va" in n and "100%" in n]
        self.assertIn("ya usaste los 2 extras", warning)

    def extra_cli(self):
        import contextlib, io
        from unittest import mock
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(budget, "HOME", self.home), mock.patch.object(cli, "notify", self.notes.append), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["extra", "--by", "agente"])
        return code, out.getvalue() + err.getvalue()

    def test_cli_registers_the_extras_in_order_and_refuses_a_third(self):
        self.assertEqual(self.extra_cli(), (0, "El extra de $75 quedó registrado: el tope del ciclo pasa de "
                                               "$150.00 a $225.00.\n"))
        code, out = self.extra_cli()
        self.assertEqual(code, 0)
        self.assertIn("El extra excepcional de $75", out)
        code, out = self.extra_cli()
        self.assertEqual(code, 1)
        self.assertIn("los 2 extras", out)
        self.assertEqual(json.loads((self.home / "changes.jsonl").read_text().splitlines()[0])["actor"], "agente")

    def test_seed_with_several_csvs_takes_the_newest_and_says_which(self):
        # a glob like team-usage-events*.csv matches a re-download "(1)" too
        import contextlib, io, os
        from unittest import mock
        head = "Date,Model,Cost\n"
        old, new = self.home / "team-usage-events.csv", self.home / "team-usage-events (1).csv"
        old.write_text(head + "2026-10-20T12:00:00.000Z,claude-sonnet-5-5-high,10.00\n", encoding="utf-8")
        new.write_text(head + "2026-10-20T12:00:00.000Z,claude-sonnet-5-5-high,30.00\n", encoding="utf-8")
        os.utime(old, (NOON - 600, NOON - 600))
        out = io.StringIO()
        with mock.patch.object(budget, "HOME", self.home), mock.patch.object(budget.time, "time", lambda: NOON), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["seed", str(old), str(new)]), 0)
        self.assertIn("team-usage-events (1).csv", out.getvalue())
        self.assertIn("$30.00", out.getvalue())

    def test_the_check_compares_model_budgets_with_the_cap_including_extras(self):
        (self.home / "config.json").write_text(json.dumps({"model_budgets": {"claude-*": 195}}), encoding="utf-8")
        self.assertIn("suman $195.00", limits.show(home=self.home, color=False, now=NOON))
        self.grant()
        text = limits.show(home=self.home, color=False, now=NOON)
        self.assertNotIn("suman $195.00", text)

    def test_the_limits_screen_lists_each_extra_and_whether_it_was_registered(self):
        self.grant()
        text = limits.show(home=self.home, color=False, now=NOON)
        self.assertIn("Extras del plan", text)
        self.assertRegex(text, r"El extra\s+\$75\.00\s+registrado")
        self.assertRegex(text, r"El extra excepcional\s+\$75\.00\s+sin registrar: `cbudget extra`")
        self.assertIn("Tope del ciclo: $225.00", text)

    def test_the_report_counts_the_extra_in_the_cap(self):
        self.grant()
        out = render.report(7, home=self.home, now=NOON)
        self.assertIn("/ $225.00", out)
        self.assertIn("$150.00 + extra $75.00", out)


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
        for heading in ("Uso de Cursor", "Uso del ciclo", "Presupuesto por modelo", "Uso por día", "Últimos turnos"):
            self.assertIn(heading, text)
        self.assertNotIn("\033[", text)
        self.assertIn("Quedan 8 días", text)
        self.assertIn("1.0M", text)

    def test_every_card_line_has_the_same_width(self):
        (self.home / "config.json").write_text(json.dumps({"model_budgets": {"claude-*": 10}}), encoding="utf-8")
        self.run_stop(stop(input_tokens=1_000_000))
        for columns in (80, 100, 140):
            text = render.report(7, home=self.home, color=True, now=NOON, columns=columns)
            boxed = [render.width(line) for line in text.splitlines() if "│" in line and line.count("│") == 2]
            self.assertEqual(len(set(boxed)), 1, columns)

    def test_color_report_paints_a_spent_budget_red(self):
        (self.home / "config.json").write_text(json.dumps({"model_budgets": {"claude-*": 1}}), encoding="utf-8")
        self.run_stop(stop(input_tokens=1_000_000))
        self.assertIn("38;2;232;96;80", render.report(7, home=self.home, color=True, now=NOON))

    def test_next_cycle_handles_short_months(self):
        self.assertEqual(render.next_cycle("2026-10-01", 1).isoformat(), "2026-11-01")
        self.assertEqual(render.next_cycle("2027-01-31", 31).isoformat(), "2027-02-28")


class TestLimits(Home):
    def test_check_flags_a_warning_above_its_block(self):
        cfg = {**budget.DEFAULTS, "warn_day": 20}
        self.assertEqual(budget.check_limits(cfg, [])[0][0], "error")

    def test_check_flags_budgets_above_the_cycle_and_shadowed_patterns(self):
        cfg = {**budget.DEFAULTS, "cycle_limit": 100, "model_budgets": {"claude-*": 80, "claude-opus-*": 40}}
        messages = " ".join(m for _, m in budget.check_limits(cfg, ["claude-opus-5-5-high"]))
        self.assertIn("suman $120.00", messages)
        self.assertIn("tapado", messages)

    def test_the_plan_cap_defaults_to_150(self):
        self.assertEqual(budget.config(self.home)["cycle_limit"], 150.0)

    def test_defaults_pass_the_check(self):
        self.assertEqual(budget.check_limits(dict(budget.DEFAULTS), []), [])

    def test_set_parses_money_and_percent_and_logs_the_change(self):
        limits.set_scalar("block_day", "$12,5", home=self.home, actor="agente", notify=self.notes.append)
        limits.set_scalar("model_warn", "70%", home=self.home)
        cfg = budget.config(self.home)
        self.assertEqual((cfg["block_day"], cfg["model_warn"]), (12.5, 0.7))
        change = json.loads((self.home / "changes.jsonl").read_text().splitlines()[0])
        self.assertEqual((change["actor"], change["before"], change["after"]), ("agente", 15.0, 12.5))
        self.assertIn("agente", self.notes[0])

    def test_set_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            limits.set_scalar("model_warn", "150%", home=self.home)
        with self.assertRaises(ValueError):
            limits.set_scalar("cycle_day", "40", home=self.home)

    def test_model_budget_and_block_round_trip(self):
        limits.set_model("composer-*", "20", home=self.home)
        limits.set_blocked("claude-opus-*", True, home=self.home)
        self.assertEqual(budget.config(self.home)["model_budgets"], {"composer-*": 20.0})
        self.assertIn("claude-opus-*", budget.config(self.home)["blocked_models"])
        limits.set_model("composer-*", None, home=self.home)
        limits.set_blocked("claude-opus-*", False, home=self.home)
        self.assertEqual(budget.config(self.home)["model_budgets"], {})

    def test_interactive_edit_saves_only_after_confirming(self):
        answers = iter(["", "", "", "10", "", "", "", "", "composer-*", "15", "", "", "s"])
        out = []
        self.assertTrue(limits.edit(home=self.home, ask=lambda _: next(answers), say=out.append))
        cfg = budget.config(self.home)
        self.assertEqual(cfg["block_day"], 10.0)
        self.assertEqual(cfg["model_budgets"], {"composer-*": 15.0})
        self.assertIn("terminal", (self.home / "changes.jsonl").read_text())
        self.assertIn("  composer-*: sin presupuesto -> $15.00", out)

    def test_a_value_that_is_not_a_number_says_so_in_spanish(self):
        with self.assertRaisesRegex(ValueError, "block_day: esperaba un número, llegó 'doce'"):
            budget.parse_value("block_day", "doce")
        with self.assertRaisesRegex(ValueError, "cycle_day: esperaba un día"):
            budget.parse_value("cycle_day", "lunes")

    def test_interactive_edit_discards_without_confirmation(self):
        answers = iter(["3"] + [""] * 9 + ["n"])
        self.assertFalse(limits.edit(home=self.home, ask=lambda _: next(answers), say=lambda _: None))
        self.assertFalse((self.home / "config.json").exists())

    def test_show_lists_limits_and_the_check(self):
        text = limits.show(home=self.home, color=False, now=NOON)
        for heading in ("Límites de cursor-budget", "Generales", "Presupuesto por modelo", "Chequeo"):
            self.assertIn(heading, text)


class TestCli(unittest.TestCase):
    def test_a_broken_payload_fails_open(self):
        self.assertEqual(cli.hook("not json"), {})

    def test_install_moves_an_older_entry_to_the_new_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hooks.json"
            cli.install_hook("/usr/bin/python3 /repo/cbudget.py hook", path)
            self.assertTrue(cli.install_hook("/usr/bin/python3 /app/cbudget.py hook", path))
            hooks = json.loads(path.read_text())["hooks"]
            self.assertEqual([h["command"] for h in hooks["stop"]], ["/usr/bin/python3 /app/cbudget.py hook"])

    def test_notify_calls_osascript_by_its_full_path_so_the_hook_path_does_not_matter(self):
        import subprocess
        from unittest import mock
        with mock.patch.dict("os.environ", {"PATH": "/nonexistent"}), \
                mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            cli.notify("hola")
        self.assertEqual(run.call_args[0][0][0], "/usr/bin/osascript")

    def test_notify_passes_the_message_as_is_so_accents_do_not_break_applescript(self):
        # json.dumps turned "·" into ·, which AppleScript rejects with a syntax error (-2741)
        import subprocess
        from unittest import mock
        message = 'turno ~$0.03 · ciclo "así" de $150'
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            cli.notify(message)
        argv = run.call_args[0][0]
        self.assertEqual(argv[-1], message)
        self.assertFalse(any("\\u" in a for a in argv))

    def test_a_failed_notification_is_logged_instead_of_lost(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(budget, "HOME", Path(tmp)), \
                mock.patch.object(cli, "OSASCRIPT", "/nonexistent/osascript"):
            cli.notify("hola")
            [line] = (Path(tmp) / "notify-errors.log").read_text(encoding="utf-8").splitlines()
        entry = json.loads(line)
        self.assertEqual(entry["message"], "hola")
        self.assertIn("nonexistent", entry["error"])

    def test_notify_test_sends_one_notification_and_says_how_to_allow_them(self):
        import contextlib, io
        sent, out = [], io.StringIO()
        original, cli.notify = cli.notify, sent.append
        try:
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["notify-test"]), 0)
        finally:
            cli.notify = original
        self.assertEqual(len(sent), 1)
        self.assertIn("Script Editor", out.getvalue())

    def test_alias_is_added_once_and_replaced_when_the_folder_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = Path(tmp) / ".zshrc"
            rc.write_text("export A=1\n", encoding="utf-8")
            self.assertTrue(cli.install_alias(Path("/repo/cbudget.py"), rc))
            self.assertFalse(cli.install_alias(Path("/repo/cbudget.py"), rc))
            self.assertTrue(cli.install_alias(Path("/app/cbudget.py"), rc))
            lines = rc.read_text().splitlines()
            self.assertEqual(lines[0], "export A=1")
            self.assertEqual(len([ln for ln in lines if "cursor-budget" in ln]), 1)
            self.assertIn("/app/cbudget.py", lines[-1])

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


class TestSafeInstall(unittest.TestCase):
    """The installer edits files other tools own (hooks.json, cli-config.json): it must never lose their content."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_hooks_file_that_is_not_strict_json_is_left_alone(self):
        path = self.dir / "hooks.json"
        text = '{\n  "version": 1,\n  // formatter\n  "hooks": {"stop": [{"command": "other hook"}]},\n}\n'
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            cli.install_hook("/usr/bin/python3 /x/cbudget.py hook", path)
        self.assertIn("línea 3", str(ctx.exception))
        self.assertEqual(path.read_text(encoding="utf-8"), text)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["hooks.json"])

    def test_a_hooks_file_with_an_unexpected_shape_is_left_alone(self):
        path = self.dir / "hooks.json"
        for data in ([], {"hooks": []}, {"hooks": {"stop": {}}}, {"hooks": {"stop": ["other hook"]}}):
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError, msg=data):
                cli.install_hook("/usr/bin/python3 /x/cbudget.py hook", path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), data)

    def test_install_hook_command_says_why_it_left_the_file_alone(self):
        import contextlib, io
        from unittest import mock
        path = self.dir / "hooks.json"
        path.write_text("{,}", encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(cli, "HOOKS", path), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["install-hook"]), 1)
        self.assertIn("no lo toqué", err.getvalue())

    def test_the_rewrite_leaves_no_temporary_file_and_keeps_a_backup(self):
        path = self.dir / "hooks.json"
        path.write_text(json.dumps({"version": 1, "hooks": {}}), encoding="utf-8")
        cli.install_hook("/usr/bin/python3 /x/cbudget.py hook", path)
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names[0], "hooks.json")
        self.assertEqual(len(names), 2)
        self.assertTrue(names[1].startswith("hooks.json.bak-"))

    def test_hook_and_alias_use_the_system_python_not_the_one_that_ran_the_installer(self):
        # a Homebrew python that ran install.sh disappears with `brew upgrade`; macOS keeps /usr/bin/python3
        entry = Path("/Users/a b/.cursor/budget/app/cbudget.py")
        self.assertEqual(cli.hook_command(entry), "/usr/bin/python3 '/Users/a b/.cursor/budget/app/cbudget.py' hook")
        rc = self.dir / ".zshrc"
        cli.install_alias(entry, rc)
        [line] = rc.read_text(encoding="utf-8").splitlines()
        self.assertIn("/usr/bin/python3", line)
        import shlex
        # the alias body is word-split when it expands: each part must survive a path with spaces
        body = shlex.split(line.split("=", 1)[1].split("  #")[0])[0]
        self.assertEqual(shlex.split(body), ["/usr/bin/python3", str(entry)])


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.app = self.home / "app"
        self.app.mkdir()
        (self.app / "VERSION").write_text("abc1234def\n", encoding="utf-8")
        self.notes = []

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, answer, t=NOON):
        from cbudget import update
        return update.check(self.home, self.app, now=lambda: t, compare=lambda sha: answer, notify=self.notes.append)

    def test_reads_the_installed_commit(self):
        from cbudget import update
        self.assertEqual(update.installed(self.app), "abc1234def")
        (self.app / "VERSION").unlink()
        self.assertIsNone(update.installed(self.app))

    def test_a_newer_main_is_notified_once_per_new_commit(self):
        self.check(("behind", "fff9999"))
        self.check(("behind", "fff9999"), t=NOON + 2 * 86400)
        self.assertEqual(len(self.notes), 1)
        self.assertIn("versión nueva de cursor-budget", self.notes[0])
        self.assertIn("cbudget update", self.notes[0])
        self.check(("behind", "eee8888"), t=NOON + 4 * 86400)
        self.assertEqual(len(self.notes), 2)

    def test_up_to_date_ahead_or_unknown_say_nothing(self):
        for answer in (("identical", "abc1234def"), ("ahead", "abc1234def"), ("diverged", "x"), None):
            self.check(answer)
        self.assertEqual(self.notes, [])

    def test_without_a_recorded_commit_it_does_not_check(self):
        (self.app / "VERSION").unlink()
        from cbudget import update
        asked = []
        update.check(self.home, self.app, now=lambda: NOON, compare=lambda sha: asked.append(sha),
                     notify=self.notes.append)
        self.assertEqual(asked, [])

    def test_github_compare_reads_as_behind_when_main_has_newer_commits(self):
        from cbudget import update
        # compare/<installed>...main: "ahead" means main is ahead of the install
        self.assertEqual(update.parse_compare({"status": "ahead", "commits": [{"sha": "a1"}, {"sha": "b2"}]}),
                         ("behind", "b2"))
        self.assertEqual(update.parse_compare({"status": "identical", "commits": []}), ("identical", None))
        self.assertEqual(update.parse_compare({"status": "behind", "commits": []}), ("ahead", None))
        self.assertIsNone(update.parse_compare({"message": "Not Found"}))

    def test_the_check_runs_at_most_once_a_day_and_claims_the_slot_first(self):
        from cbudget import update
        self.assertTrue(update.claim(self.home, NOON))
        self.assertFalse(update.claim(self.home, NOON + 3600))
        self.assertTrue(update.claim(self.home, NOON + 86400 + 1))

    def test_a_stop_hook_starts_the_check_in_the_background_without_waiting(self):
        from unittest import mock
        started = []
        with mock.patch.object(budget, "HOME", self.home), mock.patch.object(cli, "APP", self.app), \
                mock.patch.object(cli, "notify", self.notes.append), \
                mock.patch.object(cli, "spawn_update_check", lambda: started.append(1)), \
                mock.patch.object(budget.time, "time", lambda: NOON):
            cli.hook(json.dumps(stop(input_tokens=10)))
            cli.hook(json.dumps(stop(input_tokens=10)))
        self.assertEqual(started, [1])

    def test_the_background_check_detaches_from_the_hook_output(self):
        import subprocess
        from unittest import mock
        with mock.patch.object(subprocess, "Popen") as popen:
            cli.spawn_update_check()
        kwargs = popen.call_args.kwargs
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(kwargs[stream], subprocess.DEVNULL)
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(popen.call_args.args[0][-1], "update-check")


class TestVersionCommand(unittest.TestCase):
    """`cbudget version` asks GitHub at that moment: a person runs it, so a second of waiting is fine."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.app = self.home / "app"
        self.app.mkdir()
        (self.app / "VERSION").write_text("abc1234def\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_version(self, answer):
        import contextlib, io
        from unittest import mock
        from cbudget import update
        asked, out = [], io.StringIO()
        def compare(sha):
            asked.append(sha)
            return answer
        with mock.patch.object(budget, "HOME", self.home), mock.patch.object(cli, "APP", self.app), \
                mock.patch.object(update, "compare_with_main", compare), mock.patch.object(cli, "notify", lambda m: None), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["version"]), 0)
        return asked, out.getvalue()

    def test_it_checks_github_now_even_right_after_an_old_check_said_up_to_date(self):
        from cbudget import update
        budget.write_json_atomic(self.home / "update.json", {"checked": round(time.time()), "status": "identical"})
        asked, out = self.run_version(("behind", "fff9999"))
        self.assertEqual(asked, ["abc1234def"])
        self.assertIn("instalado: abc1234", out)
        self.assertIn("hay una versión nueva", out)
        self.assertNotIn("al día", out)

    def test_up_to_date_says_so(self):
        _, out = self.run_version(("identical", None))
        self.assertIn("al día con GitHub", out)

    def test_without_github_it_says_it_could_not_check_and_when_it_last_did(self):
        budget.write_json_atomic(self.home / "update.json", {"checked": 1792850000, "status": "identical"})
        _, out = self.run_version(None)
        self.assertIn("no pude consultar GitHub", out)
        self.assertIn("24/10", out)


class TestHelp(unittest.TestCase):
    def test_help_points_to_the_workshop_episode(self):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.assertIn("https://probable-dollop-lk5vvym.pages.github.io/serie-cursor.html#cursor-budget/", out.getvalue())


class TestStatusline(Home):
    PAYLOAD = {"model": {"id": "claude-sonnet-5-5-high", "display_name": "Sonnet 5.5"},
               "context_window": {"used_percentage": 34.4}}

    def line(self, payload=None, t=NOON):
        from cbudget import statusline
        return statusline.line(self.PAYLOAD if payload is None else payload, home=self.home, now=lambda: t)

    def test_shows_model_context_day_and_cycle_against_their_limits(self):
        self.run_stop(stop(input_tokens=1_000_000))  # $2.25 at the mid tier
        text = self.line()
        for part in ("Sonnet 5.5", "contexto 34%", "hoy $", "de $15", "ciclo $", "de $150"):
            self.assertIn(part, text)
        self.assertNotIn("\n", text)

    def test_a_broken_state_still_prints_the_model_never_an_empty_line(self):
        from unittest import mock
        with mock.patch.object(budget, "read_ledger", side_effect=OSError("disk")):
            self.assertEqual(self.line(), "Sonnet 5.5")
            self.assertEqual(self.line(payload={}), "cursor-budget")

    def test_install_keeps_every_other_key_and_the_file_mode(self):
        from cbudget import statusline
        import os, stat
        path = self.home / "cli-config.json"
        cfg = {"version": 1, "authInfo": {"email": "x@y"}, "permissions": {"allow": ["Shell(ls)"]}}
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)
        self.assertTrue(statusline.install("/usr/bin/python3 /app/cbudget.py statusline", path))
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual({k: data[k] for k in cfg}, cfg)
        self.assertEqual(data["statusLine"]["type"], "command")
        self.assertEqual(data["statusLine"]["command"], "/usr/bin/python3 /app/cbudget.py statusline")
        self.assertGreaterEqual(data["statusLine"]["updateIntervalMs"], 1000)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.home.iterdir() if p.name.startswith("cli-config")], ["cli-config.json"])
        self.assertFalse(statusline.install("/usr/bin/python3 /app/cbudget.py statusline", path))

    def test_install_never_replaces_someone_elses_status_line(self):
        from cbudget import statusline
        path = self.home / "cli-config.json"
        cfg = {"version": 1, "statusLine": {"type": "command", "command": "~/bin/my-line.sh"}}
        path.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaises(ValueError):
            statusline.install("/usr/bin/python3 /app/cbudget.py statusline", path)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), cfg)

    def test_install_refuses_a_config_it_cannot_read_or_that_does_not_exist(self):
        from cbudget import statusline
        path = self.home / "cli-config.json"
        with self.assertRaises(ValueError):
            statusline.install("/usr/bin/python3 /app/cbudget.py statusline", path)
        path.write_text('{"version": 1,', encoding="utf-8")
        with self.assertRaises(ValueError):
            statusline.install("/usr/bin/python3 /app/cbudget.py statusline", path)
        self.assertEqual(path.read_text(encoding="utf-8"), '{"version": 1,')

    def test_remove_takes_out_only_its_own_status_line(self):
        from cbudget import statusline
        path = self.home / "cli-config.json"
        path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        statusline.install("/usr/bin/python3 /app/cbudget.py statusline", path)
        self.assertTrue(statusline.remove(path))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"version": 1})
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": "mine"}}), encoding="utf-8")
        self.assertFalse(statusline.remove(path))


# Each process waits for a "go" file, so they all reach the ledger at the same moment, like turns ending together.
GO = "import os,sys,time\nwhile not os.path.exists(os.path.join(sys.argv[1],'go')): time.sleep(0.001)\n"


class TestConcurrency(Home):
    """Several Cursor windows and agents end turns at the same time; each hook is its own process."""
    ROOT = Path(__file__).resolve().parent.parent

    def test_turns_ending_together_still_warn_once_when_the_day_crosses_its_limit(self):
        import subprocess, sys
        (self.home / "config.json").write_text(json.dumps({"warn_day": 1.0, "block_day": 100.0,
                                                           "notify_each_turn": False}), encoding="utf-8")
        notes = self.home / "notes.txt"
        script = (GO + "import json,sys,pathlib;from cbudget import budget;h=pathlib.Path(sys.argv[1]);n=h/'notes.txt';"
                  "budget.on_stop({'hook_event_name':'stop','model':'claude-sonnet-5-5-high','conversation_id':"
                  "sys.argv[2],'input_tokens':75000},home=h,now=lambda:float(sys.argv[3]),"
                  "notify=lambda m:n.open('a').write(m+'\\n'))")
        procs = [subprocess.Popen([sys.executable, "-c", script, str(self.home), f"c{i}", str(NOON)], cwd=self.ROOT)
                 for i in range(8)]
        (self.home / "go").touch()
        for p in procs:
            self.assertEqual(p.wait(timeout=30), 0)
        self.assertEqual(len(budget.read_ledger(self.home)), 8)
        text = notes.read_text(encoding="utf-8") if notes.exists() else ""
        warnings = [ln for ln in text.splitlines() if "warn_day" in ln]
        self.assertEqual(len(warnings), 1, warnings)

    def test_two_extras_registered_together_both_count(self):
        import subprocess, sys
        script = (GO + "import sys,pathlib;from cbudget import budget;"
                  "budget.grant_extra(home=pathlib.Path(sys.argv[1]),now=lambda:float(sys.argv[2]))")
        procs = [subprocess.Popen([sys.executable, "-c", script, str(self.home), str(NOON)], cwd=self.ROOT)
                 for _ in range(2)]
        (self.home / "go").touch()
        for p in procs:
            p.wait(timeout=30)
        self.assertEqual(budget.extras_granted(self.home, NOON, 1), [75.0, 75.0])


class TestDamagedFiles(Home):
    def test_an_unreadable_config_is_reported_and_never_overwritten(self):
        path = self.home / "config.json"
        path.write_text('{"block_day": 12, "blocked_models": [', encoding="utf-8")
        problems = budget.check_limits(budget.config(self.home), [], None, home=self.home)
        self.assertTrue(any(lvl == "error" and "config.json" in msg for lvl, msg in problems), problems)
        with self.assertRaises(ValueError):
            budget.save_config({"warn_day": 9.0}, home=self.home)
        self.assertEqual(path.read_text(encoding="utf-8"), '{"block_day": 12, "blocked_models": [')

    def test_a_value_of_the_wrong_type_falls_back_to_its_default_and_is_reported(self):
        (self.home / "config.json").write_text(json.dumps({"block_day": "15", "warn_day": 3}), encoding="utf-8")
        cfg = budget.config(self.home)
        self.assertEqual((cfg["block_day"], cfg["warn_day"]), (15.0, 3))
        problems = budget.check_limits(cfg, [], None, home=self.home)
        self.assertTrue(any("block_day" in msg for _, msg in problems), problems)
        self.assertEqual(self.run_submit({"model": "claude-sonnet-5-5-high", "conversation_id": "c"}), {})

    def test_config_that_is_not_an_object_keeps_the_guard_working(self):
        (self.home / "config.json").write_text("[]", encoding="utf-8")
        self.assertEqual(budget.config(self.home)["block_day"], 15.0)
        self.assertEqual(self.run_stop(stop(input_tokens=10)), {})

    def test_ledger_lines_that_are_not_turns_are_skipped(self):
        (self.home / "ledger.jsonl").write_text('5\n"x"\n{"t": 1}\n' + json.dumps(
            {"t": NOON, "usd": 1.5, "conversation_id": "c"}) + "\n", encoding="utf-8")
        self.assertEqual(len(budget.read_ledger(self.home)), 1)
        self.assertEqual(self.run_stop(stop(input_tokens=10)), {})

    def test_limits_that_are_not_finite_numbers_are_refused(self):
        for raw in ("nan", "inf", "-inf"):
            with self.assertRaises(ValueError):
                budget.parse_value("block_day", raw)

    def test_state_files_are_written_whole(self):
        budget.save_config({"warn_day": 9.0}, home=self.home)
        budget.grant_extra(home=self.home, now=lambda: NOON)
        budget.allow(30, home=self.home, now=lambda: NOON)
        budget.seed_amount(10.0, home=self.home, now=lambda: NOON)
        leftovers = [p.name for p in self.home.iterdir() if p.name.startswith(".")
                     and p.name != ".lock"]
        self.assertEqual(leftovers, [])


class TestPanel(Home):
    """The data behind the extension's panel: `cbudget panel` prints it as JSON."""

    def data(self):
        from cbudget import panel
        return panel.data(home=self.home, now=lambda: NOON)

    def test_detail_has_the_cycle_today_models_days_and_chats(self):
        self.run_stop(stop(model="claude-opus-5-5-medium", conv="caro", input_tokens=1_000_000))
        self.run_stop(stop(model="auto-smart", conv="barato", input_tokens=100_000))
        d = self.data()
        self.assertEqual(d["cycle"]["cap"], 150.0)
        self.assertEqual(d["cycle"]["start"], "2026-10-01")
        self.assertEqual(d["cycle"]["reset"], "2026-11-01")
        self.assertEqual(d["cycle"]["days_left"], 8)
        self.assertAlmostEqual(d["today"]["usd"], d["cycle"]["total"], places=4)
        self.assertEqual((d["today"]["warn"], d["today"]["block"]), (8.0, 15.0))
        self.assertEqual([m["model"] for m in d["models"]], ["claude-opus-5-5-medium", "auto-smart"])
        self.assertEqual([x["date"] for x in d["days"]], ["2026-10-24"])
        self.assertEqual([c["conversation_id"] for c in d["chats"]], ["caro", "barato"])
        self.assertEqual(d["chats"][0]["turns"], 1)
        json.dumps(d)  # the extension reads it as JSON

    def test_chats_say_since_when_the_hooks_count_them(self):
        # Cursor's CSV has no chat id: before the first counted turn of the cycle there are no chats to rank.
        self.run_stop(stop(conv="hoy", input_tokens=100_000))
        self.assertEqual(self.data()["chats_since"], "2026-10-24")
        budget.seed_amount(10.0, home=self.home, now=lambda: NOON)
        self.assertEqual(self.data()["chats_since"], "2026-10-24")

    def test_chats_cover_the_cycle_when_the_hooks_counted_before_it(self):
        with (self.home / "ledger.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": NOON - 40 * 86400, "usd": 1.0, "conversation_id": "viejo"}) + "\n")
        self.run_stop(stop(conv="hoy", input_tokens=100_000))
        d = self.data()
        self.assertIsNone(d["chats_since"])
        self.assertEqual([c["conversation_id"] for c in d["chats"]], ["hoy"])

    def test_without_turns_there_is_no_since(self):
        self.assertIsNone(self.data()["chats_since"])

    def test_limits_carry_value_kind_help_and_whether_they_were_changed(self):
        budget.save_config({"block_day": 12.0}, home=self.home)
        limits_ = {x["key"]: x for x in self.data()["limits"]}
        self.assertEqual(set(limits_), set(budget.SCALARS))
        self.assertEqual(limits_["block_day"], {"key": "block_day", "value": 12.0, "kind": "usd",
                                                "help": budget.SCALARS["block_day"][1], "custom": True})
        self.assertFalse(limits_["warn_day"]["custom"])

    def test_budgets_blocks_extras_and_the_check_are_included(self):
        budget.save_config({"model_budgets": {"claude-opus-*": 40.0}, "warn_day": 20.0}, home=self.home)
        self.run_stop(stop(model="claude-opus-5-5-medium", input_tokens=1_000_000))
        d = self.data()
        self.assertEqual(d["budgets"][0]["pattern"], "claude-opus-*")
        self.assertGreater(d["budgets"][0]["spent"], 0)
        self.assertEqual(d["blocked_models"], budget.DEFAULTS["blocked_models"])
        self.assertEqual([x["usd"] for x in d["extras"]], [75.0, 75.0])
        self.assertEqual([x["granted"] for x in d["extras"]], [False, False])
        self.assertTrue(any(p["level"] == "error" and "warn_day" in p["message"] for p in d["problems"]))

    def test_the_cli_prints_the_panel_as_json(self):
        import contextlib, io
        from unittest import mock
        out = io.StringIO()
        with mock.patch.object(budget, "HOME", self.home), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["panel"]), 0)
        self.assertIn("limits", json.loads(out.getvalue()))


class TestCursorExtension(Home):
    """The status bar item of Cursor's app: a .vsix built with no dependencies, fed by `cbudget statusline --json`."""

    def node(self, script, *args):
        import shutil, subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        return subprocess.run([node, "-e", script, *args], capture_output=True, text=True, check=True).stdout

    def panel_html(self, data, tab="detalle", error=""):
        script = ("const p=require(process.argv[1]);"
                  "process.stdout.write(p.html(JSON.parse(process.argv[2]),{nonce:'N0NCE',csp:'vscode-resource:',"
                  "tab:process.argv[3],error:process.argv[4]}))")
        return self.node(script, str(self.ROOT / "vscode" / "panel.js"), json.dumps(data), tab, error)

    def sample(self):
        from cbudget import panel
        self.run_stop(stop(model="claude-opus-5-5-medium", conv="caro", input_tokens=1_000_000))
        return panel.data(home=self.home, now=lambda: NOON)

    def test_the_panel_shows_both_tabs_and_the_detail(self):
        page = self.panel_html(self.sample())
        for part in ('data-tab="detalle"', 'data-tab="limites"', "Uso del ciclo", "Por modelo", "Por día",
                     "Chats más caros", "claude-opus-5-5-medium", "Se reinicia"):
            self.assertIn(part, page)

    def test_the_chats_title_says_since_when_they_are_counted(self):
        data = self.sample()
        data["chats_since"] = "2026-10-24"
        page = self.panel_html(data)
        self.assertIn("Chats más caros desde el 24 oct", page)
        self.assertNotIn("Chats más caros del ciclo", page)
        self.assertIn("no dice de qué chat", page)
        data["chats_since"] = None
        page = self.panel_html(data)
        self.assertIn("Chats más caros del ciclo", page)
        self.assertNotIn("no dice de qué chat", page)

    def test_the_limits_tab_has_an_input_per_limit_and_the_blocked_models(self):
        page = self.panel_html(self.sample(), tab="limites")
        for key in budget.SCALARS:
            self.assertIn(f'data-key="{key}"', page)
        for pattern in budget.DEFAULTS["blocked_models"]:
            self.assertIn(f'data-unblock="{pattern}"', page)
        self.assertIn('id="block-new"', page)

    def test_the_panel_escapes_text_and_only_runs_its_own_script(self):
        data = self.sample()
        data["models"][0]["model"] = '<img src=x onerror="alert(1)">'
        page = self.panel_html(data, error='<script>alert(2)</script>')
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("<script>alert(2)", page)
        self.assertIn("&lt;img src=x", page)
        self.assertIn("default-src 'none'", page)
        self.assertIn("script-src 'nonce-N0NCE'", page)
        import re
        self.assertEqual(re.findall(r"<script(?: [^>]*)?>", page), ['<script nonce="N0NCE">'])

    def test_the_panel_says_when_a_change_was_refused(self):
        page = self.panel_html(self.sample(), tab="limites", error="block_day: esperaba un número, llegó 'doce'")
        self.assertIn("esperaba un número", page)
    ROOT = Path(__file__).resolve().parent.parent

    def test_summary_has_the_numbers_the_status_bar_paints(self):
        from cbudget import statusline
        self.run_stop(stop(input_tokens=1_000_000))
        s = statusline.summary(home=self.home, now=lambda: NOON)
        self.assertEqual(set(s), {"today", "warn_day", "block_day", "cycle", "cap", "cycle_warn"})
        self.assertAlmostEqual(s["today"], 2.25, places=2)
        self.assertEqual((s["warn_day"], s["block_day"], s["cap"], s["cycle_warn"]), (8.0, 15.0, 150.0, 0.75))

    def test_the_vsix_holds_a_manifest_that_matches_its_package(self):
        import sys, zipfile
        sys.path.insert(0, str(self.ROOT / "vscode"))
        try:
            import build_vsix
        finally:
            sys.path.pop(0)
        vsix = build_vsix.build(self.home)
        package = json.loads((self.ROOT / "vscode" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(vsix.name, f"cursor-budget-{package['version']}.vsix")
        with zipfile.ZipFile(vsix) as z:
            names = set(z.namelist())
            manifest = z.read("extension.vsixmanifest").decode("utf-8")
        for name in ("[Content_Types].xml", "extension.vsixmanifest", "extension/package.json",
                     "extension/extension.js", "extension/format.js", "extension/panel.js"):
            self.assertIn(name, names)
        self.assertIn(f'Version="{package["version"]}"', manifest)
        self.assertIn(f'Publisher="{package["publisher"]}"', manifest)
        self.assertEqual(package["extensionKind"], ["ui"])  # the budget lives on this Mac, also in a remote window

    def test_the_status_bar_label_turns_yellow_at_a_warning_and_red_at_a_block(self):
        import shutil, subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        base = {"today": 3.2, "warn_day": 8, "block_day": 15, "cycle": 84.1, "cap": 150, "cycle_warn": 0.75}
        cases = [base, {**base, "today": 9}, {**base, "today": 15}, {**base, "cycle": 120}, {**base, "cycle": 150}]
        script = ("const f=require(process.argv[1]);"
                  "console.log(JSON.stringify(JSON.parse(process.argv[2]).map(f.label)))")
        out = subprocess.run([node, "-e", script, str(self.ROOT / "vscode" / "format.js"), json.dumps(cases)],
                             capture_output=True, text=True, check=True).stdout
        labels = json.loads(out)
        self.assertEqual(labels[0]["text"], "$(pulse) hoy $3.20 de $15 · ciclo $84 de $150")
        self.assertEqual([lb["level"] for lb in labels], ["ok", "warning", "error", "warning", "error"])


if __name__ == "__main__":
    unittest.main()
