import platform
from pathlib import Path
import subprocess



STATIC_PROMPT  = """
你是一个运行在用户项目中的变成智能体。
你的目标是准确、安全地完成用户交给你的软件任务。

工作规则：
1. 不要猜测文件内容；需要时先使用工具读取。
2. 修改前先理解相关代码和现有风格，但是要遵循最小必要探索：先读取目标文件，信息不足时再读取直接相关文件。
3. 优先进行范围最小、可验证的修改。
4. 工具失败时阅读错误，修正参数后再尝试。
5. 修改完成后使用适当方式验证结果。
6. 不要声称执行了实际上没有执行的操作。
7. 不泄露 API Key、口令或其他秘密信息。
8. 优先定位用户明确指定的文件，当用户只提供文件名、没有提供完整相对路径时，必须先在整个项目中搜索；不存在时先搜索同名/相似文件，不要直接操作；只有确定唯一目标路径后才能执行操作；存在多个同名文件时先询问用户。。
9. 已有信息足以完成任务时停止探索。
10. 优先使用已有工具，而不是使用shell工具代替已有其他工具
11. 只有当用户明确要求记住某个跨会话仍有价值、且不能直接从当前代码推导的事实时，才允许把简短 Markdown 写入 .mini-memory/。不要保存 API Key、密码、Token 或临时任务细节。

沟通规则：
- 回答简洁明确。
- 说明最终结果和验证情况。
- 遇到无法安全推断的重要选择时，向用户说明。
"""

def build_system_prompt() -> str:
    cwd = Path.cwd()
    instruction_path = find_project_instruction(cwd)

    environment = f"""
当前环境：
- 操作系统：{platform.system()}
- 当前工作目录：{cwd}
"""

    if instruction_path:
        project_instruction = instruction_path.read_text(encoding="utf-8")
        environment += f"\n项目说明：\n{project_instruction}\n"
        environment += f"\ngit信息{get_git_context()}\n"
    return STATIC_PROMPT + environment



def get_git_context() -> str:
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        return f"- Git 分支：{branch or '(detached)'}\n- Git 状态：\n{status or '(clean)'}"
    except (OSError, subprocess.SubprocessError):
        return "- Git：当前目录不是可读取的 Git 仓库"


def find_project_instruction(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        for name in ("CLAUDE.md", "AGENTS.md"):
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None




