"""Translate validated Inventory plans into the existing arm command contract."""
from uuid import NAMESPACE_URL, uuid5


def finish_action(args, actor, turn_id, data, publish, emit):
    action = args["action"]
    if "clarification" in data:
        return {**data, "execution": "clarify"}
    if action in ("move", "set_home"):
        receipt = {**data, "execution": "updated", "physical_completion": False}
    elif data.get("simulated"):
        receipt = {**data, "execution": "simulated", "physical_completion": False}
    else:
        commands = []
        for task in data["tasks"]:
            task_id = str(uuid5(NAMESPACE_URL,
                f"project_a/{action}/{turn_id}/{task['object_id']}/{task['target_location']}"))
            command = dict(utterance_id=turn_id, task_id=task_id, user_id=actor,
                api="place_object" if action in ("pick", "place") and task["target_location"]
                    in ("工作檯桌面", "交付區") else "return_object", args=task)
            publish(command)
            emit("arm_dispatch", api="/project_a/arm/command", command=command)
            commands.append(command)
        receipt = {**data, "execution": "dispatched" if commands else "noop",
                   "commands": commands, "physical_completion": False}
    emit("action_result", action=action, result=receipt)
    return receipt
