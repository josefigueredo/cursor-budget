import json
import tempfile
import unittest
from pathlib import Path

from cbudget import budget, cli, limits, render

NOON = 1792850000.0  # 2026-10-24 10:53 -03, a fixed clock


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


if __name__ == "__main__":
    unittest.main()
