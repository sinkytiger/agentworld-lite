from __future__ import annotations

import json
from copy import deepcopy

import pytest
import yaml

from agentworld_lite.agents import BaseAgent, Decision
from agentworld_lite.report import system_label
from agentworld_lite.runner import run_episode, run_suite
from agentworld_lite.task import Task


class WaitAgent(BaseAgent):
    def act(self, turn):
        return Decision("wait", {"reason": "test"})


@pytest.fixture
def saved_run(tmp_path, monkeypatch):
    import agentworld_lite.runner as runner

    monkeypatch.setattr(runner, "make_factory", lambda *a, **kw: lambda task, name: WaitAgent(name))
    task = {
        "id": "resume_task", "name": "Resume test", "category": "gathering", "max_rounds": 5,
        "objectives": {"primary": "Hold logs"},
        "agents": [{"username": "worker", "spawn": [32, 20], "inventory": {"logs": 1}}],
        "verifier": {"checkpoints": [{"name": "logs", "type": "has_item", "agent": "worker", "item": "logs", "count": 1}]},
    }
    task_path = tmp_path / "task.yaml"
    task_path.write_text(yaml.safe_dump(task), encoding="utf-8")
    out = tmp_path / "run"
    args = dict(task_paths=[task_path], out_dir=str(out), agent="llm", provider="ollama",
                model="example:cloud", workers=1)
    run_suite(**args)
    return args, task_path, out


def test_identical_resume_skips_completed_episodes_and_extends_manifest(saved_run):
    args, _, out = saved_run
    assert run_suite(**args) == []
    added = run_suite(**args, settings=("no_comm",), seeds=(8,))
    assert len(added) == 1
    config = json.loads((out / "run_config.json").read_text())
    assert config["settings"] == ["full", "no_comm"]
    assert config["seeds"] == [7, 8]
    assert config["task_hashes"]["resume_task"] == added[0]["meta"]["task_hash"]


@pytest.mark.parametrize("change", [
    {"model": "different:cloud"}, {"think": True}, {"num_ctx": 4096},
    {"host": "http://other:11434"}, {"coordination": True},
])
def test_resume_rejects_changed_execution_config_without_rewriting_manifest(saved_run, change):
    args, _, out = saved_run
    original = (out / "run_config.json").read_bytes()
    with pytest.raises(ValueError, match="different run configuration"):
        run_suite(**(args | change))
    assert (out / "run_config.json").read_bytes() == original
    assert len(list((out / "trajectories").glob("*.json"))) == 1


def test_resume_rejects_edited_private_brief_even_when_task_id_is_unchanged(saved_run):
    args, task_path, out = saved_run
    task = yaml.safe_load(task_path.read_text())
    task["agents"][0]["role_brief"] = "New private instruction"
    task_path.write_text(yaml.safe_dump(task), encoding="utf-8")
    before = (out / "run_config.json").read_bytes()
    with pytest.raises(ValueError, match="task resume_task changed"):
        run_suite(**args)
    assert (out / "run_config.json").read_bytes() == before


def test_corrupt_saved_episode_is_not_silently_skipped(saved_run):
    args, _, out = saved_run
    path = next((out / "trajectories").glob("*.json"))
    path.write_text('{"task_id":', encoding="utf-8")
    with pytest.raises(ValueError, match="is invalid"):
        run_suite(**args)


def test_saved_episode_metadata_is_checked_even_with_valid_manifest(saved_run):
    args, _, out = saved_run
    path = next((out / "trajectories").glob("*.json"))
    data = json.loads(path.read_text())
    data["meta"]["model"] = "ollama:other:cloud"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="different run configuration"):
        run_suite(**args)


def test_legacy_baseline_resume_requires_explicit_override_and_keeps_evidence(saved_run):
    args, _, out = saved_run
    path = next((out / "trajectories").glob("*.json"))
    episode = json.loads(path.read_text())
    legacy_keys = {"agent", "model", "provider", "effort", "think", "coordination"}
    episode["meta"] = {k: v for k, v in episode["meta"].items() if k in legacy_keys}
    path.write_text(json.dumps(episode), encoding="utf-8")
    config_path = out / "run_config.json"
    config = json.loads(config_path.read_text())
    config = {k: v for k, v in config.items() if k in legacy_keys | {"settings", "seeds", "tasks"}}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    original_episode = path.read_bytes()
    with pytest.raises(ValueError, match="--allow-legacy-resume"):
        run_suite(**args)
    assert run_suite(**args, allow_legacy_resume=True) == []
    resumed = json.loads(config_path.read_text())
    assert resumed["legacy_resume_unverified"] is True
    assert resumed["legacy_episode_files"] == [path.name]
    assert path.read_bytes() == original_episode


def test_legacy_coordination_requires_fresh_output_directory(saved_run):
    args, _, out = saved_run
    config_path = out / "run_config.json"
    config = json.loads(config_path.read_text())
    config["coordination"] = True
    config.pop("coordination_version", None)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="different run configuration"):
        run_suite(**args, coordination=True, allow_legacy_resume=True)


def test_atomic_json_failure_preserves_existing_file(tmp_path, monkeypatch):
    from pathlib import Path
    from agentworld_lite.runner import _atomic_json

    path = tmp_path / "result.json"
    path.write_text('{"old": true}', encoding="utf-8")
    before = path.read_bytes()

    def fail_replace(*args, **kwargs):
        raise OSError("interrupted before replace")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        _atomic_json(path, {"new": True})
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_report_keeps_baseline_controller_versions_and_configs_separate():
    base = {"meta": {"agent": "llm", "model": "same-model"}}
    coord = deepcopy(base)
    coord["meta"].update(coordination=True, coordination_version="2", coordination_config={"max_replans": 8})
    other_config = deepcopy(coord)
    other_config["meta"]["coordination_config"]["max_replans"] = 0
    other_version = deepcopy(coord)
    other_version["meta"]["coordination_version"] = "3"
    legacy = deepcopy(base)
    legacy["meta"]["coordination"] = True
    assert len({system_label(t) for t in [base, coord, other_config, other_version, legacy]}) == 5


def test_episode_keeps_decision_usage_and_controller_diagnostics(saved_run):
    _, task_path, _ = saved_run

    class TracedAgent(BaseAgent):
        def act(self, turn):
            assert turn.visible_state is not None
            return Decision("wait", {"reason": "suppressed"}, usage={"calls": 2, "input_tokens": 12},
                            decision_trace={"extra_decisions": 1, "suppressed": True})

    traj = run_episode(Task.load(task_path), lambda task, name: TracedAgent(name))
    assert traj["actions"][0]["usage"] == {"calls": 2, "input_tokens": 12}
    assert traj["usage"]["calls"] == 2
    assert traj["result"]["controller_replans"] == 1
    assert traj["result"]["controller_suppressed"] == 1
    assert traj["result"]["n_wait_actions"] == 1
