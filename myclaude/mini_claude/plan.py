from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PlanState:
    session_id: str
    project_root: Path
    active: bool = False
    awaiting_review: bool = False

    @property
    def relative_path(self) -> str:
        return (
            f".mini-agent/plans/plan-{self.session_id}.md"
        )

    @property
    def absolute_path(self) -> Path:
        return self.project_root / self.relative_path

    def enter(self) -> str:
        self.absolute_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        if not self.absolute_path.exists():
            self.absolute_path.write_text(
                "# Implementation Plan\n",
                encoding="utf-8",
            )
        self.active = True
        self.awaiting_review = False
        return (
            "已进入 Plan Mode。唯一允许修改的文件："
            f"{self.relative_path}"
        )

    def exit_for_review(self) -> str:
        if not self.active:
            return "Error: 当前没有处于 Plan Mode"
        if not self.absolute_path.is_file():
            return "Error: Plan 文件不存在"
        content = self.absolute_path.read_text(
            encoding="utf-8"
        ).strip()
        if not content or content == "# Implementation Plan":
            return "Error: Plan 文件还是空的"
        self.active = False
        self.awaiting_review = True
        return "Plan 已提交，等待用户审批。"

    def read(self) -> str:
        try:
            return self.absolute_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "active": self.active,
            "awaiting_review": self.awaiting_review,
        }

    def restore(self, value: dict) -> None:
        self.active = bool(value.get("active", False))
        self.awaiting_review = bool(
            value.get("awaiting_review", False)
        )