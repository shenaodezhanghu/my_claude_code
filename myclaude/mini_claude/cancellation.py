import threading


class AgentCancelled(RuntimeError):
    pass


def raise_if_cancelled(cancelled: threading.Event) -> None:
    if cancelled.is_set():
        raise AgentCancelled("当前任务已取消")