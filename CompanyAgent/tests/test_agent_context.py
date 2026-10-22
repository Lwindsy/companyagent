"""Context compression must preserve user constraints and native protocol pairing."""
import json

from agent_runtime.context import project_messages


def test_projection_keeps_negative_constraint_and_call_pairing():
    messages = [{"role": "user", "content": "Do not refund or cancel my subscription."},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "old", "name": "read", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "old", "content": "x" * 20000}]},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "new", "name": "read", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "new", "content": "fresh"}]}]
    projected, stats = project_messages(messages)
    assert projected[0] == messages[0] and projected[-2:] == messages[-2:]
    assert projected[2]["content"][0]["tool_use_id"] == "old"
    assert stats["after_chars"] < stats["before_chars"]
    assert len(json.dumps(messages)) > stats["after_chars"]  # Original is unmodified.
