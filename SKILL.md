---
name: cursor-budget
description: Use when the user asks about their Cursor spend or wants to see or change its limits from a chat ("cuánto llevo gastado en Cursor", "subí el presupuesto de Sonnet a 70", "bloqueá Opus", "desbloqueá gpt", "bajá el tope diario a 10", "chequeá mis límites", "levantá el bloqueo media hora"). Runs the cbudget CLI that the Cursor hooks already use. Not for Cursor's own plan limits, which only a team admin changes in Cursor's dashboard.
compatibility: Requires cursor-budget installed with its install.sh (copy in ~/.cursor/budget/app, hooks in ~/.cursor/hooks.json) and python3 3.9+. Changes the user's own limits in ~/.cursor/budget/config.json; every change is logged in changes.jsonl and shown to the user as a macOS notification.
---

# cursor-budget

The user's Cursor spend guard. The hooks estimate each turn's cost, warn and block; this skill reads the spend and
changes the limits through the same CLI. In a terminal the user can run `cbudget limits edit` themselves; from a chat,
you run the non-interactive subcommands below.

Run it by its full path: the `cbudget` alias may not exist in your shell, and a variable such as `$CB` does not split
into words in zsh. Below, `cb` stands for:

```bash
python3 ~/.cursor/budget/app/cbudget.py
```

## Read

| The user asks | Run |
|---|---|
| How much they spent, per day, model or chat | `cb report --color never` |
| Their limits and whether they make sense | `cb limits --color never` |
| Only the problems | `cb limits check` (exit 1 when there is an error) |

Answer with the numbers that matter, not the whole screen. The cycle total is the seeded amount plus the hooks'
estimate since; say so when it matters.

If the report says it only counts since installation (nothing seeded this cycle), the total is short by whatever was
spent before. Ask the user for the number on Cursor's Usage page (cursor.com/dashboard, "Your monthly usage", the
first amount of "$84.30 / $200") and run `cb seed --amount <that number>`. That is the no-CSV path, meant for
people who do not export files. When they also give the second amount (their plan's cap) and it differs from
`cycle_limit`, set it too with `cb limits set cycle_limit <cap>`: they asked for it by giving it. A CSV (`cb seed <csv>`) adds the split per model and per day; use it when they have one.

## Change

Change a limit **only when the user asked for that change in this conversation**, with the value they gave. Never
raise a limit, unblock a model or run `allow` on your own initiative, even to finish a task the hooks blocked: that is
the user's call, and they see every change in a notification and in `~/.cursor/budget/changes.jsonl`.

| The user asks | Run |
|---|---|
| A general limit (`warn_conversation`, `block_conversation`, `warn_day`, `block_day`, `cycle_limit`, `cycle_day`, `model_warn`, `notify_each_turn`) | `cb limits set <key> <value>` (money as `12.5`, shares as `80%`, yes/no as `si`/`no`) |
| A model's budget for the cycle | `cb limits model '<pattern>' <usd>`, e.g. `'claude-sonnet-*' 70` |
| Remove a model's budget | `cb limits model '<pattern>' --remove` |
| Block or unblock a model | `cb limits block '<pattern>'` / `cb limits unblock '<pattern>'` |
| Lift every block for a while | `cb allow --minutes <n>` |
| Load what Cursor shows as spent this cycle | `cb seed --amount <usd>` (or `cb seed <csv>` with an exported usage CSV) |
| A test notification, or they get no warnings | `cb notify-test`, then relay its hint about System Settings |
| Update cursor-budget | `gh api repos/josefigueredo/cursor-budget/contents/bootstrap.sh -H "Accept: application/vnd.github.raw" \| bash` (keeps their limits, ledger and seed) |

Patterns use shell globs against Cursor's model ids (`claude-opus-5-5-high`, `auto-smart`, `composer-2.5`,
`gpt-5.6-terra-medium`). For model budgets the first matching pattern wins, in the order of config.json.

After any change, run `cb limits check` and tell the user the new value and any problem it reports. Never edit
`config.json` by hand: the CLI validates the value and logs the change.

`cb limits edit` is the interactive editor for a terminal; it refuses without one, so do not run it.
