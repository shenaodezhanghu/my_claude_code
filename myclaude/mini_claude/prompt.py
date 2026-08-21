import platform
from pathlib import Path
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
import time
from mini_claude.prompt_cache import PromptBuildCache, file_mtime
from mini_claude.model import ModelCapabilities


@dataclass(frozen=True)
class PromptParts:
    static: str
    dynamic: str


STATIC_PROMPT  = """
你是一个运行在用户项目中的变成智能体。
你的目标是准确、安全地完成用户交给你的软件任务。

工作规则：
1. 不要猜测文件内容；需要时先使用工具读取。
2. 修改前先理解相关代码和现有风格，但是要遵循最小必要探索：先读取目标文件，信息不足时再读取直接相关文件。
3. 优先进行范围最小、可验证的修改，不要多做无用的步骤。
4. 工具失败时最多进行一次相邻修正；仍失败则说明原因并决定下一步怎么做，不要盲目枚举方法。
5. 执行完任务后必须找方法测试或验证任务是否完成，比如修改代码后必须运行项目已有测试或用户指定验证命令。
6. 不要声称执行了实际上没有执行的操作。
7. 不泄露用户的API Key、口令或其他秘密信息。
8. 已有工具能够完成任务时，不要用 shell 代替write_file/edit_file等工具完成任务。
9. 已经拿到足够信息时停止工具调用，直接总结。

记忆规则：
1. 需要项目长期记忆、用户偏好或过去踩坑经验时，调用 memory_search。
2. 当前目标、计划、待办或阻塞点变化时，调用 working_memory_update。
3. 发现可能跨会话复用的信息时，先加入 working memory 候选。
4. 候选稳定且重要时，调用 memory_add；长期记忆过时或错误时，调用 memory_forget。
5. 不要每轮机械地读写记忆，也不要把全部历史会话当作长期记忆。
6. 只有当用户明确要求记住某个跨会话仍有价值、且不能直接从当前代码推导的事实时，才允许把简短 Markdown 写入 .mini-memory/。不要保存 API Key、密码、Token 或临时任务细节。

沟通规则：
- 回答简洁明确，用户的要求为第一标准，比如如果用户要求只输出数字、列表或短语，不要附加解释。
- 说明最终结果和验证情况。
- 遇到无法安全推断的重要选择时，向用户说明。
- 推导思考时需要详略分明，考虑周全，完成必要推理，但不要展开完整长篇过程，不需要为了“验证”再写一大段过程。
"""

def build_prompt_parts(
    project_root: Path,
    mode_prompt: str = "",
    memory_prompt: str = "",
    deferred_names: Iterable[str] = (),
    cache: PromptBuildCache | None = None,
) -> PromptParts:
    instruction = read_project_instruction_cached(project_root, cache)
    sections = [
        "当前环境：",
        f"- 操作系统：{platform.system()}",
        f"- 当前工作目录：{project_root}",
        "",
        "Git 信息：",
        get_git_context_cached(cache),
    ]

    if instruction:
            sections.extend(["", "项目说明：", instruction])

    if mode_prompt.strip():
        sections.extend(["", mode_prompt.strip()])
    if memory_prompt.strip():
        sections.extend(["", memory_prompt.strip()])

    names = sorted(set(deferred_names))
    if names:
        sections.extend(
            [
                "",
                "可按需加载的工具：" + ", ".join(names),
                "需要时先调用 tool_search。",
            ]
        )

    return PromptParts(
        static=STATIC_PROMPT.strip(),
        dynamic="\n".join(sections).strip(),
    )


def build_system_message(
    parts: PromptParts,
    capabilities: ModelCapabilities,
) -> dict:
    if not capabilities.explicit_cache:
        return {
            "role": "system",
            "content": f"{parts.static}\n\n{parts.dynamic}",
        }
    return {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": parts.static,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": parts.dynamic},
        ],
    }



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


def get_git_context_cached(
    cache: PromptBuildCache | None,
) -> str:
    if cache is None:
        return get_git_context()

    if not cache.git_expired():
        return cache.git_context.value

    value = get_git_context()
    cache.git_context.value = value
    cache.git_context.updated_at = time.monotonic()
    cache.git_dirty = False
    return value


def find_project_instruction(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        for name in ("CLAUDE.md", "AGENTS.md"):
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


def read_project_instruction_cached(
    project_root: Path,
    cache: PromptBuildCache | None,
) -> str:
    instruction_path = find_project_instruction(project_root)
    if instruction_path is None:
        return ""

    current_mtime = file_mtime(instruction_path)
    if (
        cache is not None
        and cache.project_instruction.mtime == current_mtime
    ):
        return cache.project_instruction.value

    try:
        value = instruction_path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""

    if cache is not None:
        cache.project_instruction.value = value
        cache.project_instruction.mtime = current_mtime
        cache.project_instruction.updated_at = time.monotonic()

    return value

