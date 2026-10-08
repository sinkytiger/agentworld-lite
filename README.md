# AgentWorld-Lite

English · [한국어](README.ko.md)

A lightweight, pip-installable testbed for **long-horizon multi-agent LLM collaboration**, built from two September 2026 papers:

- **[AgentWorld](https://arxiv.org/abs/2609.31590)** (Shu et al.) — how to *measure* collaboration: agents with asymmetric roles cooperate through chat only, and the **Causal Collaboration Effectiveness (CCE)** metric counts how much of a team's effort causally led to success.
- **[AutoGym](https://arxiv.org/abs/2609.22592)** (Noronha et al.) — how to *generate* verifiable tasks: fix the solution blueprint first, then build the environment and verifiers around it.

> **Unofficial.** Not affiliated with the authors of either paper. The original benchmark runs on the Kaetram MMORPG; this project uses its own small grid simulator that follows the same protocol. Numbers are **not comparable** with the papers; use it for relative comparisons between models and for failure analysis.

## What's inside

- **Simulator**: 64×48 tile world (forest, mines, lake, village, dungeon), ~40 items, recipes with crafting stations, combat, NPC shops, buildable structures, aggressive mobs.
- **13 high-level tools** (`move_to`, `attack_entity`, `harvest_resource`, `craft_item`, `transfer_items`, `send_chat`, …). Each call wraps a multi-step mechanic, as in the paper.
- **Protocol**: turn-based round-robin, one tool call per agent per round, black-box agents with chat as the only shared channel. Ablations: `no_comm`, `no_docs`, `whitebox` (oracle-communication proxy), `random_spawn`.
- **9 hand-built tasks** covering all 8 AgentWorld categories (combat, crafting, gathering, trading, exploration, survival, construction, coordination), 3–6 agents each. Every task ships a reference solution that the test suite replays.
- **Metrics**: success rate, partial success rate, **CCE / per-agent contribution (PAC)** with two judges (the paper's backward-tracing LLM judge, plus a deterministic rule-based judge for free runs and judge-agreement checks), and the paper's 6-way **communication failure taxonomy**.
- **Blueprint-first task generator** (AutoGym-style, adapted to multi-agent play): sample generation parameters, build the solution DAG (who gathers, crafts and hands what to whom), materialize a task, certify it by running the reference solution, repair failures, set the round budget. No LLM calls; **~93% yield**.
- **Model backends**: Claude (Anthropic API) and **Ollama** (free local models, or Ollama cloud models such as `gemma4:cloud`).
- **87 tests**, none of which call an API.

## Quick start (no API key)

```bash
pip install -e ".[dev]"
```

```bash
python -m agentworld_lite validate
```

```bash
python -m agentworld_lite run --agent scripted --out runs/oracle
```

```bash
python -m agentworld_lite run --agent random --settings full no_comm --seeds 1 2 3 --out runs/random
```

```bash
python -m agentworld_lite evaluate --run runs/oracle --judge rule
```

```bash
python -m agentworld_lite report --runs runs/oracle runs/random --out reports/baseline.md --graphs 3
```

## Run with a free model (Ollama)

Install [Ollama](https://ollama.com), then either pull a local model that supports tool calling (`ollama pull <model>`) or sign in (`ollama signin`) and use a cloud model whose name ends in `:cloud`. Cloud models are still called through the local Ollama server.

```bash
python -m agentworld_lite run --agent llm --provider ollama --model gemma4:cloud --only t01_magic_staff --out runs/gemma_smoke --verbose
```

```bash
python -m agentworld_lite evaluate --run runs/gemma_smoke --judge both --judge-provider ollama --judge-model gemma4:cloud
```

Notes: the context window defaults to 8192 tokens (`--num-ctx`), since a turn is roughly 3k tokens of prompt plus 1.5k of tool schemas. Episodes run one at a time by default with Ollama. Cloud models do not support structured outputs, so the judges fall back to a schema-in-prompt request and extract the JSON from the reply. Small models that print a tool call as JSON text instead of a structured call are still executed and tagged `note: text_tool_call`.

## Run with Claude

```bash
pip install -e ".[claude]"
```

Set `ANTHROPIC_API_KEY` (or log in with `ant auth login`). The default model is `claude-opus-5-5`; `--model claude-haiku-4-5` matches one of the models evaluated in the AgentWorld paper.

```bash
python -m agentworld_lite run --agent llm --model claude-haiku-4-5 --settings full no_comm --out runs/haiku
```

```bash
python -m agentworld_lite evaluate --run runs/haiku --judge both --failures
```

A full pass over the 9 tasks is at most 892 agent calls (less when tasks finish early): roughly $4 with Haiku 4.5 and $20–27 with Opus 5.5. Cost estimates from response usage appear in the run log and the report.

## Generate tasks (blueprint-first)

```bash
python -m agentworld_lite gen --n 30 --seed 7 --out tasks/gym
```

Generation parameters: recipe depth of the target (1–3), team structure (`chain` = one agent per crafting skill, `hub` = one central workshop), **distributed obfuscation** (0–1: how much of the plan moves from the shared document into individual agents' private briefs or out of the docs entirely), quantity, and extra gatherers. Each kept task records its parameters, ground-truth length, repairs, and capability-axis predicates (hand-offs, travel distance, information and permission asymmetry, station constraints). Sampling weights can be overridden with `--weights weights.json`. See [docs/autogym_integration.md](docs/autogym_integration.md) (Korean) for the full design, including the planned distractors, four-signal verifier, difficulty bands, and failure-driven curriculum.

## Baselines

| System | Setting | Episodes | Success | Partial success | CCE (rule judge) | Avg. rounds |
|---|---|---|---|---|---|---|
| Reference solutions (oracle) | full | 9 | 100% | 100% | 0.642 | 7.4 |
| Random actions | full | 27 | 0% | 5.6% | 0 | 26.6 |
| Random actions | no_comm | 27 | 0% | 3.7% | 0 | 26.6 |

LLM baselines are not published yet. Contributions of run results are welcome.

## Tasks

| ID | Category | Agents | Rounds | What forces collaboration |
|---|---|---|---|---|
| t01_magic_staff | crafting | 3 | 25 | logs → sticks → staff; axe, fletching and bead are held by different agents |
| t02_supply_run | gathering | 3 | 30 | forest and mine gatherers deliver to a quartermaster who cannot gather |
| t03_ogre_hunt | combat | 4 | 25 | a regenerating boss needs concentrated damage while a cleric hands out food |
| t04_market_day | trading | 3 | 30 | only the merchant may trade; coins and ore must be pooled to buy a sword |
| t05_lost_shrine | exploration | 3 | 25 | only the tracker can see the shrine and must share its coordinates |
| t06_wolf_siege | survival | 3 | 14 | survive 10 rounds surrounded by wolves: campfire, cooking, tanking |
| t07_outpost_walls | construction | 4 | 40 | logs and ore → planks and nails → palisade wall |
| t08_crystal_plates | coordination | 4 | 20 | each agent knows a different plate location; all four must be held at once |
| t09_harvest_feast | coordination | 6 | 30 | two fishers and two foragers feed a cook who serves the host |

## Metrics

| Metric | Definition |
|---|---|
| SR | Fraction of episodes the verifier marks successful |
| PSR | Mean checkpoint completion (counts give partial credit, e.g. `3/5`) |
| CCE | \|contributing actions\| / \|all actions\|. Failed episodes score 0 because they have no success action |
| CCE \| success | Mean CCE over successful episodes only (efficiency without the success-rate confound) |
| PAC_i | Share of agent *i*'s actions that contributed; the minimum PAC exposes free riders |
| Judge agreement | Raw agreement and Cohen's κ between the rule-based and LLM judges |
| Failure modes | Stale/redundant, misidentification, factual error, premature completion, non-material, logical inconsistency |

Each trajectory JSON stores every tool call, result, inventory and position deltas, checkpoint progress, and the model's own reasoning text (never shared with teammates). Read one with `python -m agentworld_lite show <trajectory.json> --reasoning`.

## Layout

```
agentworld_lite/
  content.py      items, recipes, resources, mobs, shops, structures
  gamemap.py      map, regions, BFS pathfinding, seeded default population
  engine.py       world state, the 13 tools, end-of-round tick (aggro, regen, respawn)
  observation.py  per-agent structured text observation
  task.py         task loading, validation, prompt context, static collaboration check
  verifier.py     declarative checkpoints -> success / PSR
  agents.py       Claude agent, scripted (reference) agent, random agent
  llm.py          Anthropic SDK wrapper (per-model request shaping, JSON calls, usage/cost)
  local_llm.py    Ollama backend (local and cloud models, standard library only)
  runner.py       episode protocol, ablation settings, parallel suites
  cce.py          CCE/PAC: rule judge, LLM judge (paper procedure), agreement, Mermaid graphs
  failures.py     communication failure taxonomy
  blueprint.py    blueprint-first generation: parameters -> solution DAG -> task YAML
  gym.py          ground-truth certification, repair, round budget, yield
  augment.py      LLM-written task variants, kept only if their reference solution succeeds
  report.py       Markdown report
tasks/main/       9 hand-built tasks
tasks/gym/        28 generated tasks (example output of `gen`)
docs/             paper analysis and AutoGym integration design (Korean)
```

## Differences from the papers

- Much smaller world (64×48 tiles and ~40 items instead of 1056×768 and 380+), no leveling.
- The paper does not list all 13 tools; this tool set is a reconstruction that covers every task category.
- 9 hand-built tasks instead of 100 + 100; more can be generated with `gen` or `augment`.
- The oracle-communication ablation is approximated by `whitebox`; single-agent and shared-plan ablations are not implemented.
- The CCE LLM judge runs on Claude or an Ollama model instead of GPT-4.1, and a rule-based judge is added.
- The task generator builds blueprints programmatically from the known recipe graph instead of with an LLM, so solvability holds by construction. Distractors, the four-signal verifier, difficulty bands and the curriculum loop are designed but not yet implemented.

## Citing

If you use this repository, please cite the original papers:

```bibtex
@article{shu2026agentworld,
  title   = {AgentWorld: Benchmarking Long-Horizon Collaboration of Multi-agent LLMs},
  author  = {Shu, Raphael and Zhang, Yusen and Cho, Young Min and Yang, Jin Mo and Yuan, Yuan and Zheng, Wenliang and Guntuku, Sharath Chandra and Ungar, Lyle and Yu, Zhou and Zhang, Rui},
  journal = {arXiv preprint arXiv:2609.31590},
  year    = {2026}
}

@article{noronha2026autogym,
  title   = {AutoGym: Blueprint-First Generation of Verifiable Agent Gyms},
  author  = {Noronha, Aarati Andrea and Ravikumar, Kavya and Lin, Carly Xiaoyu},
  journal = {arXiv preprint arXiv:2609.22592},
  year    = {2026}
}
```

The CCE judge prompts follow the backward-tracing procedure and wording described in the AgentWorld paper.

## License

MIT. See [LICENSE](LICENSE).
