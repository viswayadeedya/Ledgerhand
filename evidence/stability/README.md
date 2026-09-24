# Stability

20 replays of `member-savings-lookup` v4,
covering every injected fault twice, in random order, plus clean runs.

The schedule is a shuffled pool rather than an independent draw per run:
sampling with replacement looks more random and is worse evidence -- the
first seeded pass produced eight `app_error`s and never once fired
`popup`, `slow_load` or `duplicate_members`. "No fault" is an entry in
that pool like any other, so clean runs are guaranteed too and a
regression that only breaks the happy path can't hide behind a scoreboard
made entirely of faults. Order and member stay unpredictable; coverage is
guaranteed. Seed `1729` -- reproducible with:

```
python scripts/run_stability.py --runs 20 --seed 1729
```

**0 wrong answers.** Every value each run handed back was compared against the fake
app's own seed data for the member actually requested. This is the number
that matters: a tally of outcomes would happily record a run that returned
someone else's balance as a clean success.

`unexpected_outcomes` is **0** -- runs that
ended somewhere other than their injected fault predicts. That, not the
outcome histogram, is the flakiness signal: the variance below is injected
on purpose, so the histogram measures the fault mix rather than stability.

**Outcomes**

| | count |
| --- | --- |
| `business_outcome` | 4 |
| `hard_failure` | 4 |
| `needs_human` | 2 |
| `recovered` | 4 |
| `success` | 6 |

**Failure reasons**

| | count |
| --- | --- |
| `app_error` | 2 |
| `identity_mismatch` | 2 |

**Recovery performed**

| | count |
| --- | --- |
| `dialog_dismissed` | 2 |
| `session_reauthenticated` | 2 |

**Faults injected**

| | count |
| --- | --- |
| `app_error` | 2 |
| `duplicate_members` | 2 |
| `extra_row` | 2 |
| `member_not_found` | 2 |
| `none` | 2 |
| `permission_denied` | 2 |
| `popup` | 2 |
| `session_expired` | 2 |
| `slow_load` | 2 |
| `wrong_member` | 2 |

Per-run detail, including which fault produced which outcome, is in
[`scoreboard.json`](scoreboard.json).
