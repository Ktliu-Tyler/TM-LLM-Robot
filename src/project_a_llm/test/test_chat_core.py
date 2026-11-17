"""Deterministic tool boundary and multi-turn protocol checks."""
import time
import pytest
from project_a_llm.chat_core import run_turn, validate_tool


def call(**args):
    return {"function": {"name": "inventory", "arguments": args}}


@pytest.mark.parametrize("args", [
    {"action": "move"}, {"action": "pick"}, {"action": "objects", "sql": "DELETE"},
    {"action": "objects", "item": 123}, {"action": "objects", "person": "@me"}])
def test_invalid_tools_never_execute(args):
    with pytest.raises(ValueError):
        validate_tool(call(**args), "")


def test_followup_includes_history_and_tool_result():
    seen = []
    class Model:
        def stream(self, messages, think, emit, deadline):
            assert deadline > time.monotonic()
            seen.append(list(messages))
            if len(seen) == 1:
                return {"role": "assistant", "content": "", "tool_calls": [call(action="objects", item="P01")]}
            assert messages[-1]["role"] == "tool"
            assert "A1" in messages[-1]["content"]
            return {"role": "assistant", "content": "它在 A1。"}
    history = [{"role": "user", "content": "我的筆是 P01"}, {"role": "assistant", "content": "了解"}]
    events = []
    result = run_turn(Model(), history, "它在哪", "U_A", False,
                      lambda args, actor: {"location": "A1"},
                      lambda kind, **fields: events.append(kind))
    assert seen[0][1:3] == history
    assert result[0]["content"] == "它在哪"
    assert result[-1]["content"] == "它在 A1。"
    assert events.index("tool_start") < events.index("tool_end") < events.index("model_end", events.index("tool_end"))


def test_failed_tool_is_returned_to_model():
    calls = []
    class Model:
        def stream(self, messages, think, emit, deadline):
            if messages[-1]["role"] == "tool":
                assert "禁止" in messages[-1]["content"]
                return {"role": "assistant", "content": "不能修改"}
            return {"role": "assistant", "content": "", "tool_calls": [call(action="move")]}
    run_turn(Model(), [], "移動", "U_A", False, lambda *a: calls.append(a), lambda *a, **k: None)
    assert not calls


def test_tool_loop_is_bounded():
    class Model:
        def stream(self, *args):
            return {"role": "assistant", "content": "", "tool_calls": [call(action="status")]}
    with pytest.raises(RuntimeError, match="6 次"):
        run_turn(Model(), [], "查詢", "", False, lambda *a: {}, lambda *a, **k: None)


def test_my_query_keeps_actor_scope_when_model_omits_it():
    seen = []
    class Model:
        def stream(self, messages, *args):
            if messages[-1]["role"] == "tool":
                return {"role": "assistant", "content": "已查詢"}
            return {"role": "assistant", "content": "", "tool_calls": [call(action="objects", item="筆")]}
    def query(args, actor):
        seen.append((args, actor))
        return {}
    run_turn(Model(), [], "我的筆在哪", "U_A", False, query, lambda *a, **k: None)
    assert seen[0][0]["person"] == "@me"
    assert seen[0][1] == "U_A"


def test_service_failure_is_visible_and_recoverable():
    events = []
    class Model:
        def stream(self, messages, *args):
            if messages[-1]["role"] == "tool":
                assert "offline" in messages[-1]["content"]
                return {"role": "assistant", "content": "目前無法查詢"}
            return {"role": "assistant", "content": "", "tool_calls": [call(action="status")]}
    def query(*args):
        raise TimeoutError("offline")
    result = run_turn(Model(), [], "查詢", "", False, query,
                      lambda kind, **fields: events.append(kind))
    assert "tool_error" in events and "tool_end" not in events
    assert result[-1]["content"] == "目前無法查詢"
