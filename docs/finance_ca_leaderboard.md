# Canadian desk: model comparison

Written by `python -m agentworld_lite.finance leaderboard --market ca` from saved runs, with no model call. Every trajectory is replayed on a fresh desk, and its answers and intermediate calculations are matched against the trap catalog (`agentworld_lite/finance/traps_ca.py`).

## Summary

| Model | Episodes | Solved | Answers right | Seeds | Rounds used (of budget) | Failed tool calls / episode | Turns without a tool call | Rule CCE | Cost | Cost / episode | Cost / solved |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | 40 | 40 (100%) | 100% | 7, 8, 9 | 45% | 0.00 | 0% | 0.58 | $18.96 | $0.474 | $0.474 |
| claude-sonnet-5-5 | 16 | 16 (100%) | 100% | 7 | 47% | 0.06 | 0% | 0.49 | $3.65 | $0.228 | $0.228 |
| claude-haiku-5-5 | 48 | 48 (100%) | 100% | 7, 8, 9 | 55% | 1.12 | 0% | 0.41 | $0.71 | $0.015 | $0.015 |

## Solved by task

| Model | c01 RBC trailing P/E | c02 RBC and TD Q3 efficiency | c03 Big 5 Q3 earnings and capital | c04 Big 5 ROE vs objective |
|---|---|---|---|---|
| claude-opus-5-5 | 12/12 | 12/12 | 12/12 | 4/4 |
| claude-sonnet-5-5 | 4/4 | 4/4 | 4/4 | 4/4 |
| claude-haiku-5-5 | 12/12 | 12/12 | 12/12 | 12/12 |

## Where the method sits, and chat

Shared: every method sentence is in the document all agents see. Private: they are only in the analyst's brief. no_comm blocks chat.

| Model | Shared, chat | Shared, no chat | Private, chat | Private, no chat |
|---|---|---|---|---|
| claude-opus-5-5 | 10/10 | 10/10 | 10/10 | 10/10 |
| claude-sonnet-5-5 | 4/4 | 4/4 | 4/4 | 4/4 |
| claude-haiku-5-5 | 12/12 | 12/12 | 12/12 | 12/12 |

## Failures: wrong or missing answers

No answer was wrong or missing.

## Trap values in intermediate calculations

Calculations whose result equals a listed mistake's value, submitted or not (with whether the final answer was right).

No intermediate calculation hit a listed trap.
