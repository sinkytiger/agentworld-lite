from copy import deepcopy

from agentworld_lite.diagnostics import diagnose_run


def action(tool, *, agent="a", ok=True, args=None, trace=None, calls=None, message="", action_id="r1.1"):
    record = {"id": action_id, "agent": agent, "tool": tool, "args": args or {}, "ok": ok, "message": message}
    if trace is not None:
        record["decision_trace"] = trace
    if calls is not None:
        record["usage"] = {"calls": calls}
    return record


def trajectory(actions, **kwargs):
    return {"task_id": "example", "setting": "full", "seed": 7, "agents": [{"username": "a"}, {"username": "b"}],
            "actions": actions, "result": {"success": False, "rounds_used": 3}, "usage": {"calls": 9}, **kwargs}


def row(report, name):
    return next(line for line in report.splitlines() if line.startswith(f"| {name} |"))


def test_duplicate_chats_require_same_sender_and_recipient_and_success():
    actions = [action("send_chat", args={"to": "b", "message": "Bring wood"}),
               action("send_chat", args={"to": "B", "message": " bring  WOOD "}),
               action("send_chat", args={"to": "all", "message": "Bring wood"}),
               action("send_chat", agent="b", args={"to": "b", "message": "Bring wood"}),
               action("send_chat", ok=False, args={"to": "b", "message": "Bring wood"})]
    report = diagnose_run([trajectory(actions)])
    assert row(report, "Episode") == "| Episode | no | 5 | 1 | 0 | 4 | 1 | 9 | N/A | N/A | 0/5 |"
    assert "sent 2 times (1 duplicates)" in report
    assert "not a semantic judgment" in report


def test_replans_and_suppressions_count_attempts_and_keep_actual_failures_separate():
    accepted = {"outcome": "accepted", "extra_decisions": 0, "proposals": []}
    suppressed = {"outcome": "suppressed", "extra_decisions": 1, "proposals": []}
    exhausted = {"outcome": "suppressed", "extra_decisions": 0, "proposals": []}
    actions = [action("send_chat", args={"to": "b", "message": "help"}, trace=accepted, calls=1),
               action("wait", trace=suppressed, calls=2), action("wait", trace=exhausted, calls=1)]
    report = diagnose_run([trajectory(actions, usage={"calls": 4})])
    assert row(report, "Episode") == "| Episode | no | 3 | 0 | 2 | 1 | 0 | 4 | 1 | 2 | 3/3 |"
    assert row(report, "a") == "| a | - | 3 | 0 | 2 | 1 | 0 | 4 | 1 | 2 | 3/3 |"
    assert "Fewer failures alone do not demonstrate progress" in report


def test_legacy_notes_do_not_become_zero_trace_or_model_call_counts():
    records = [action("wait"), action("move_to", args={"x": 1, "y": 1})]
    records[0]["note"] = "duplicate_chat_suppressed"
    records[1]["note"] = "ineffective_action_replanned"
    report = diagnose_run([trajectory(records)])
    assert "| a | - | 2 | 0 | 1 | 0 | 0 | N/A | N/A | N/A | 0/2 |" in report
    assert "Legacy controller notes: duplicate_chat_suppressed x1, ineffective_action_replanned x1" in report


def test_partial_traces_are_not_reported_as_complete_totals():
    report = diagnose_run([trajectory([action("wait", calls=1), action("wait", calls=2,
        trace={"outcome": "suppressed", "extra_decisions": 1})])])
    assert "| a | - | 2 | 0 | 2 | 0 | 0 | 3 | N/A | N/A | 1/2 |" in report


def test_failure_and_wait_examples_and_markdown_escaping_do_not_mutate_input():
    actions = [action("craft_item", ok=False, args={"item": "sword"}, message="Need ore | fuel", action_id="r1.1"),
               action("craft_item", ok=False, args={"item": "sword"}, message="Need ore | fuel", action_id="r2.1")]
    actions.extend(action("wait", args={"reason": "await delivery"}, action_id=f"r{r}.1") for r in range(3, 6))
    traj = trajectory(actions)
    original = deepcopy(traj)
    report = diagnose_run([traj])
    assert "Need ore \\| fuel" in report
    assert "Wait streak: 3 own turns, r3.1 to r5.1" in report
    assert "last reason: await delivery" in report
    assert traj == original


def test_empty_runs_and_agents_with_no_actions_are_supported():
    assert "No trajectories found" in diagnose_run([])
    report = diagnose_run([trajectory([], usage={"calls": 0})])
    assert row(report, "Episode") == "| Episode | no | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 |"
    assert row(report, "a") == "| a | - | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 |"
    assert "No executed actions" in report


def test_executed_chat_effects_take_precedence_over_proposed_arguments():
    first = action("send_chat", args={"to": "bad", "message": "ignored"})
    first["effects"] = {"chat": {"to": "b", "text": "Actual message"}}
    second = action("send_chat", args={"to": "b", "message": "actual message"})
    report = diagnose_run([trajectory([first, second])])
    assert "sent 2 times (1 duplicates)" in report


def test_controller_example_includes_recorded_objection_without_inventing_failure():
    record = action("move_to", calls=2, trace={"outcome": "replanned", "extra_decisions": 1,
        "proposals": [{"tool": "wait", "args": {}, "objection": {"code": "duplicate_chat", "reason": "Already sent"}}]})
    report = diagnose_run([trajectory([record])])
    assert "Controller replanned at r1.1: Already sent" in report
    assert "| a | - | 1 | 0 | 0 | 0 | 0 | 2 | 1 | 0 | 1/1 |" in report
