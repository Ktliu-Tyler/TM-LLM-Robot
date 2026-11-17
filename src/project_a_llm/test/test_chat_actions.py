import pytest
import sqlite3
from project_a_llm.chat_actions import finish_action
from project_a_llm.chat_core import run_turn, validate_tool
from project_a_llm.inventory import initialize, seed_objects, execute


def call(action, **fields):
    return {"function": {"name": "workbench_action", "arguments": {"action": action, **fields}}}


def test_pick_with_destination_becomes_placement():
    args = validate_tool(call("pick", item="筆", location="工作檯桌面"), "U_A")
    assert args["action"] == "place"
    assert args["location"] == "工作檯桌面"


@pytest.mark.parametrize("action", ["pick", "place", "restore", "tidy", "set_home", "move"])
def test_actions_require_actor(action):
    with pytest.raises(ValueError):
        validate_tool(call(action, item="筆", location="A1"), "")


def test_simulation_never_dispatches_arm():
    sent = []
    data = {"tasks": [{"object_id": "P01", "source_location": "A1", "target_location": "工作檯桌面"}], "simulated": True}
    result = finish_action({"action": "pick"}, "U_A", "turn", data, sent.append, lambda *a, **k: None)
    assert not sent
    assert result["execution"] == "simulated"
    assert result["physical_completion"] is False


def test_dispatch_is_stable_and_not_completion():
    sent = []
    data = {"tasks": [{"object_id": "P01", "source_location": "A1", "target_location": "工作檯桌面"}]}
    result = finish_action({"action": "pick"}, "U_A", "turn", data, sent.append, lambda *a, **k: None)
    assert sent[0]["api"] == "place_object"
    assert result["execution"] == "dispatched" and result["physical_completion"] is False
    again = finish_action({"action": "pick"}, "U_A", "turn", data, lambda _: None, lambda *a, **k: None)
    assert again["commands"][0]["task_id"] == sent[0]["task_id"]


@pytest.mark.parametrize("fails", [False, True])
def test_repeated_model_mutation_is_not_reexecuted(fails):
    executed = []
    class Model:
        count = 0
        def stream(self, *a):
            self.count += 1
            return {"role": "assistant", "content": "處理結果"} if self.count == 3 else {
                "role": "assistant", "content": "", "tool_calls": [call("pick", item="筆")]}
    def tool(*a):
        executed.append(a)
        if fails:
            raise TimeoutError("unknown outcome")
        return {"execution": "dispatched", "commands": [{"args": {"object_id": "P01"}}]}
    result = run_turn(Model(), [], "拿筆", "U_A", False, tool, lambda *a, **k: None)
    assert len(executed) == 1
    if not fails:
        assert "尚未收到手臂完成回報" in result[-1]["content"]


@pytest.mark.parametrize("person,expected", [("", {"P01", "R01"}), ("@me", {"P01"})])
def test_tidy_scope_excludes_delivery_area(person, expected):
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        initialize(db)
        db.executemany("INSERT INTO users VALUES (?,?)", [("U_A", "小安"), ("U_B", "小林")])
        seed_objects(db)
        db.execute("UPDATE objects SET location='工作檯桌面'")
        db.execute("UPDATE objects SET location='交付區' WHERE object_id='E01'")
        db.commit()
        data = execute(db, dict(action="tidy", item="", location="", person=person), "清理我的桌面", "U_A")
        assert {task["object_id"] for task in data["tasks"]} == expected
        assert all(task["source_location"] == "工作檯桌面" for task in data["tasks"])
