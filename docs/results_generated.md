# AgentWorld-Lite results: generated tasks

17 tasks from `tasks/gym` (blueprint-first generator, seed 7), sampled across obfuscation levels, run with `gemma4:cloud` on 2026-10-08 (seed 7, rule-based CCE).

| Obfuscation | Tasks | Success with chat | Success without chat | Gap |
|---|---|---|---|---|
| low | 5 | 100% | 100% | 0 pp |
| mid | 5 | 100% | 80% | 20 pp |
| high | 5 | 100% | 80% | 20 pp |
| max | 2 | 50% | 0% | 50 pp |
| all | 17 | 94.1% | 76.5% | 17.6 pp |

By recipe depth (with / without chat): depth 1: 100% / 100% (7 tasks), depth 2: 100% / 62% (8 tasks), depth 3: 50% / 50% (2 tasks).
In low-obfuscation tasks the model sent no messages at all. Samples per level are small, so read the trend rather than the exact numbers.

Generated with `python -m agentworld_lite report --runs runs/gemma4_cloud_gym --out docs/results_generated.md`.

Episodes: 34 | systems x settings: 2

## Main results

| System | Setting | N | SR% | PSR% | CCE | CCE \| success | min PAC \| success | Avg rounds | Avg chats | Repeated chats % | Failed-action % | Deaths/ep | Cost $ | CCE judge |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ollama:gemma4:cloud | full | 17 | 94.1 | 94.1 | 0.377 | 0.400 | 0.06 | 13.9 | 2.4 | 0.0 | 0.5 | 0.00 | 0.00 | rule |
| ollama:gemma4:cloud | no_comm | 17 | 76.5 | 76.5 | 0.299 | 0.391 | 0.05 | 16.8 | 0.0 | - | 3.4 | 0.00 | 0.00 | rule |

CCE counts failed episodes as 0 (no success action, so the contributing set is empty); 'CCE | success' averages successful episodes only. min PAC = least-contributing agent's share of useful actions. Repeated chats = messages whose text was already sent earlier in the episode (judge-free proxy for stale/redundant messages).

## Success rate by category (%)

| Category | ollama:gemma4:cloud / full | ollama:gemma4:cloud / no_comm |
|---|---|---|
| crafting | 100.0 | 72.7 |
| construction | 66.7 | 66.7 |
| coordination | 100.0 | 100.0 |

## Per-task outcomes

| Task | System / Setting | Success | PSR | Rounds | Chats | CCE | Verifier |
|---|---|---|---|---|---|---|---|
| g07000 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 7/22 | 0 | 0.37 | bronze_bar: 1/1 |
| g07000 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 7/22 | 0 | 0.37 | bronze_bar: 1/1 |
| g07002 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 8/20 | 1 | 0.41 | campfire: 1/1 |
| g07002 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 11/20 | 0 | 0.35 | campfire: 1/1 |
| g07003 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 6/20 | 0 | 0.38 | cooked_shrimp: 1/1 |
| g07003 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 4/20 | 0 | 0.40 | cooked_shrimp: 1/1 |
| g07005 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 15/23 | 2 | 0.24 | leatherarmor equipped: True |
| g07005 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 18/23 | 0 | 0.41 | leatherarmor equipped: True |
| g07006 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 21/31 | 1 | 0.36 | nails: 20/20 |
| g07006 | ollama:gemma4:cloud / no_comm (s7) | ✗ | 0.00 | 31/31 | 0 | 0.00 | nails: 0/20 |
| g07011 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 19/23 | 2 | 0.49 | nails: 20/20 |
| g07011 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 20/23 | 0 | 0.48 | nails: 20/20 |
| g07012 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 8/20 | 0 | 0.41 | iron_sword equipped: True |
| g07012 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 8/20 | 0 | 0.41 | iron_sword equipped: True |
| g07013 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 17/28 | 3 | 0.41 | nails: 10/10 |
| g07013 | ollama:gemma4:cloud / no_comm (s7) | ✗ | 0.00 | 28/28 | 0 | 0.00 | nails: 0/10 |
| g07014 | ollama:gemma4:cloud / full (s7) | ✗ | 0.00 | 45/45 | 9 | 0.00 | palisade_wall: 0/1 |
| g07014 | ollama:gemma4:cloud / no_comm (s7) | ✗ | 0.00 | 45/45 | 0 | 0.00 | palisade_wall: 0/1 |
| g07016 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 7/20 | 1 | 0.42 | string: 1/1 |
| g07016 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 9/20 | 0 | 0.36 | string: 1/1 |
| g07018 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 20/39 | 6 | 0.50 | palisade_wall: 1/1 |
| g07018 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 32/39 | 0 | 0.41 | palisade_wall: 1/1 |
| g07020 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 7/20 | 0 | 0.37 | herbal_tonic: 1/1 |
| g07020 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 7/20 | 0 | 0.37 | herbal_tonic: 1/1 |
| g07022 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 18/24 | 9 | 0.45 | fish_stew: 1/1 |
| g07022 | ollama:gemma4:cloud / no_comm (s7) | ✗ | 0.00 | 24/24 | 0 | 0.00 | fish_stew: 0/1 |
| g07023 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 13/27 | 5 | 0.35 | bow equipped: True |
| g07023 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 15/27 | 0 | 0.32 | bow equipped: True |
| g07027 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 12/26 | 2 | 0.42 | bronze_bar: 3/3 |
| g07027 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 14/26 | 0 | 0.40 | bronze_bar: 3/3 |
| g07028 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 8/22 | 0 | 0.44 | bronze_sword equipped: True |
| g07028 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 8/22 | 0 | 0.41 | bronze_sword equipped: True |
| g07029 | ollama:gemma4:cloud / full (s7) | ✓ | 1.00 | 5/20 | 0 | 0.38 | cooked_shrimp: 1/1 |
| g07029 | ollama:gemma4:cloud / no_comm (s7) | ✓ | 1.00 | 4/20 | 0 | 0.40 | cooked_shrimp: 1/1 |
