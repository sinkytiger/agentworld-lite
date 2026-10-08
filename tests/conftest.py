import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TASK_DIR = ROOT / "tasks" / "main"


def usage(i=100, o=20):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def tool_use(name, args):
    return SimpleNamespace(type="tool_use", id="toolu_1", name=name, input=args)


def text(t):
    return SimpleNamespace(type="text", text=t)


class FakeEndpoint:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.responder(kw)


class FakeSDK:
    """Stands in for anthropic.Anthropic(): records requests, returns canned responses."""

    def __init__(self, responder):
        self.messages = FakeEndpoint(responder)
        self.beta = SimpleNamespace(messages=FakeEndpoint(responder))

    @property
    def all_calls(self):
        return self.messages.calls + self.beta.messages.calls


def json_response(obj):
    return SimpleNamespace(content=[text(json.dumps(obj))], stop_reason="end_turn", usage=usage())


@pytest.fixture
def task_dir():
    return TASK_DIR
