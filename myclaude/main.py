import argparse
from dotenv import load_dotenv
from mini_claude.agent import MINI_CLUE_AGENT
from mini_claude.session import load_session, save_session
from uuid import uuid4
from pathlib import Path
ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="一个从零实现的 Coding Agent")
    parser.add_argument("prompt", nargs="*", help="要交给 Agent 的任务")
    parser.add_argument("--resume", metavar="SESSION_ID", help="恢复上一次会话")
    parser.add_argument("--new", action="store_true", help="创建新会话")
    parser.add_argument("--model", "-m",help="目前还没实现，修改模型", default=None)
    parser.add_argument("--permission-mode", choices=["default", "accept_edits", "dont_ask"], default="default",
    )

    return parser.parse_args()


def run_one_shot(agent: MINI_CLUE_AGENT, prompt: str, session_id: str) -> None:
    answer = agent.chat(prompt)
    print(answer)
    save_session(session_id, agent.history())


def show_history(agent: MINI_CLUE_AGENT) -> None:
    for message in agent.history():
        role = message.get("role", "unknown")
        content = str(message.get("content") or "")
        print(f"{role}: {content}")



def run_repl(agent: MINI_CLUE_AGENT, session_id: str) -> None:
    print("mini-agent：输入任务，/clear 清空历史，exit 退出。")
    while True:
        try:
            line = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not line:
            continue
        if line in {"exit", "quit"}:
            break
        if line == "/clear":
            agent.clear_history()
            save_session(session_id, agent.history())
            print("历史已清空。")
            continue

        agent.chat(line)
        save_session(session_id, agent.history())


def main() -> None:
    args = parse_args()
    agent = MINI_CLUE_AGENT(permission_mode=args.permission_mode)
    session_id = args.resume or uuid4().hex

    if args.resume:
        history = load_session(args.resume)
        agent.load_history(history)
        print(f"已恢复 {len(history)} 条消息。")
        show_history(agent)
    elif args.new:
        save_session(session_id, [])

    prompt = " ".join(args.prompt).strip()
    if prompt:
        run_one_shot(agent, prompt, session_id)
    else:
        run_repl(agent, session_id)


if __name__ == "__main__":
    main()