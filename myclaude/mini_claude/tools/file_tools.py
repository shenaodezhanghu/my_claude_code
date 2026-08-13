from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .base import Tool, ToolContext


def resolve_project_path(raw_path: str, context: ToolContext) -> Path:
    project_root = context.project_root.resolve()
    path = (project_root / raw_path).resolve()
    if not path.is_relative_to(project_root):
        raise PermissionError("只能访问当前项目目录中的文件")
    return path


def check_file_freshness(
    path: Path,
    context: ToolContext,
) -> str | None:
    if not path.exists():
        return None

    recorded_mtime = context.read_file_state.get(str(path))
    if recorded_mtime is None:
        return "Error: 修改已有文件前必须先使用 read_file 读取当前内容"

    if path.stat().st_mtime != recorded_mtime:
        return (
            "Warning: 文件在上次读取后已被外部修改。"
            "请重新调用 read_file，再根据最新内容修改"
        )
    return None


def normalize_quotes(text: str) -> str:
    return (
        text.replace("‘", "'").replace("’", "'").replace("′", "'")
        .replace("“", '"').replace("”", '"').replace("″", '"')
    )


def find_actual_string(file_content: str, search_string: str) -> str | None:
    if search_string in file_content:
        return search_string

    normalized_file = normalize_quotes(file_content)
    index = normalized_file.find(normalize_quotes(search_string))
    if index == -1:
        return None
    return file_content[index:index + len(search_string)]


def generate_diff(file_content: str, old_text: str, new_text: str) -> str:
    line_number = file_content[:file_content.index(old_text)].count("\n") + 1
    old_lines = old_text.splitlines() or [""]
    new_lines = new_text.splitlines() or [""]
    parts = [
        f"@@ -{line_number},{len(old_lines)} +{line_number},{len(new_lines)} @@"
    ]
    parts.extend(f"- {line}" for line in old_lines)
    parts.extend(f"+ {line}" for line in new_lines)
    return "\n".join(parts)


class ReadFileTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__(
            "read_file",
            "读取当前项目中的 UTF-8 文本文件，返回带行号的内容",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于项目根目录的路径",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        raw_path = args.get("path", "").strip()
        if not raw_path:
            return "Error: 没有提供文件路径"

        try:
            path = resolve_project_path(raw_path, context)
            content = path.read_text(encoding="utf-8")
            context.read_file_state[str(path)] = path.stat().st_mtime
            return "\n".join(
                f"{number:4d} | {line}"
                for number, line in enumerate(content.splitlines(), 1)
            )
        except FileNotFoundError:
            return f"Error: 文件不存在：{raw_path}"
        except IsADirectoryError:
            return f"Error: 目标是目录而不是文件：{raw_path}"
        except UnicodeDecodeError:
            return f"Error: 文件不是有效的 UTF-8 文本：{raw_path}"
        except PermissionError as exc:
            return f"Error: {exc}"
        except OSError as exc:
            return f"Error: 读取失败：{exc}"


class WriteFileTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            "write_file",
            "创建或覆盖文件；覆盖已有文件前必须先 read_file",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        path = resolve_project_path(args["path"], context)
        freshness_error = check_file_freshness(path, context)
        if freshness_error:
            return freshness_error

        content = args["content"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        context.read_file_state[str(path)] = path.stat().st_mtime

        lines = content.splitlines()
        preview = "\n".join(
            f"{number:4d} | {line}"
            for number, line in enumerate(lines[:30], 1)
        )
        omitted = f"\n... ({len(lines)} lines total)" if len(lines) > 30 else ""
        return (
            f"Successfully wrote {path.relative_to(context.project_root)} "
            f"({len(lines)} lines)\n\n{preview}{omitted}"
        )


class EditFileTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            "edit_file",
            "用 new_text 替换文件中唯一出现的 old_text；编辑前必须先 read_file",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        path = resolve_project_path(args["path"], context)
        freshness_error = check_file_freshness(path, context)
        if freshness_error:
            return freshness_error

        content = path.read_text(encoding="utf-8")
        requested_old_text = args["old_text"]
        actual_old_text = find_actual_string(content, requested_old_text)
        if actual_old_text is None:
            return f"Error: old_text not found in {args['path']}"

        count = content.count(actual_old_text)
        if count > 1:
            return (
                f"Error: old_text appears {count} times in {args['path']}; "
                "provide a longer unique match"
            )

        new_text = args["new_text"]
        updated = content.replace(actual_old_text, new_text, 1)
        path.write_text(updated, encoding="utf-8")
        context.read_file_state[str(path)] = path.stat().st_mtime

        quote_note = (
            " (matched via quote normalization)"
            if actual_old_text != requested_old_text
            else ""
        )
        return (
            f"Successfully edited {path.relative_to(context.project_root)}"
            f"{quote_note}\n\n{generate_diff(content, actual_old_text, new_text)}"
        )


class ListFilesTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__("list_files", "列出匹配 glob 模式的文件")

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "例如 **/*.py"},
                "path": {"type": "string", "description": "搜索起点，默认当前目录"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        base = resolve_project_path(args.get("path") or ".", context)
        files = []
        omitted = 0
        for path in base.glob(args["pattern"]):
            if not path.is_file():
                continue
            if ".git" in path.parts or "node_modules" in path.parts:
                continue
            if len(files) < 200:
                files.append(str(path.relative_to(context.project_root)))
            else:
                omitted += 1
        files.sort()
        if not files:
            return "No files found matching the pattern."
        result = "\n".join(files)
        if omitted:
            result += f"\n... and {omitted} more"
        return result


class GrepSearchTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__(
            "grep_search",
            "使用正则表达式搜索文本，返回文件路径、行号和匹配行",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "文件或目录，默认当前目录"},
                "include": {"type": "string", "description": "可选文件模式，例如 *.py"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        base = resolve_project_path(args.get("path") or ".", context)
        include = args.get("include")
        try:
            regex = re.compile(args["pattern"])
        except re.error as exc:
            return f"Error: invalid regex pattern: {exc}"

        candidates = [base] if base.is_file() else base.rglob("*")
        matches = []
        omitted = 0
        for path in candidates:
            if not path.is_file():
                continue
            if ".git" in path.parts or "node_modules" in path.parts:
                continue
            if include and not fnmatch.fnmatch(path.name, include):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if regex.search(line):
                    if len(matches) < 100:
                        matches.append(
                            f"{path.relative_to(context.project_root)}:{number}: {line}"
                        )
                    else:
                        omitted += 1

        if not matches:
            return "No matches found."
        result = "\n".join(matches)
        if omitted:
            result += f"\n... and {omitted} more matches"
        return result
