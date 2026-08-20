from permissions import check_permission


def can_run(tool_name: str) -> bool:
    return check_permission(tool_name)
