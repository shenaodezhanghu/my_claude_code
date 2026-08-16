from mini_claude.budget import BudgetLimits, BudgetState
from mini_claude.context import (
    SNIP_PLACEHOLDER,
    budget_tool_results,
    snip_stale_results,
)


budget = BudgetState(BudgetLimits(max_turns=1))
assert budget.stop_reason() is None
budget.record_usage(None)
assert budget.stop_reason() is not None

messages = [
    {
        "role": "tool",
        "tool_call_id": str(index),
        "content": "x" * 10_000,
    }
    for index in range(10)
]
budget_tool_results(messages)
snip_stale_results(messages)
assert all("tool_call_id" in message for message in messages)
assert any(
    message["content"] == SNIP_PLACEHOLDER
    for message in messages
)
print("Budget 与上下文验证通过")