from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkspacePolicy:
    workspace_root: Path
    read_roots: set[Path] = field(default_factory=set)
    write_roots: set[Path] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.workspace_root = self.workspace_root.resolve()
        self.read_roots = {
            path.resolve() for path in self.read_roots
        }
        self.write_roots = {
            path.resolve() for path in self.write_roots
        }
        self.read_roots.add(self.workspace_root)
        self.write_roots.add(self.workspace_root)

    def resolve_path(self, raw_path: str) -> Path:
        raw = Path(raw_path)
        if raw.is_absolute():
            return raw.resolve()
        return (self.workspace_root / raw).resolve()

    def is_allowed(self, path: Path, access: str) -> bool:
        roots = (
            self.write_roots
            if access == "write"
            else self.read_roots | self.write_roots
        )
        return any(path.is_relative_to(root) for root in roots)

    def grant(self, root: Path, access: str) -> None:
        resolved = root.resolve()
        drive_root = Path(resolved.anchor)
        home_root = Path.home().resolve()
        if resolved in {drive_root, home_root}:
            raise PermissionError(
                "不能一次授权整个磁盘或用户主目录"
            )
        if access == "write":
            self.write_roots.add(resolved)
            self.read_roots.add(resolved)
        else:
            self.read_roots.add(resolved)

    def to_dict(self) -> dict:
        return {
            "workspace_root": str(self.workspace_root),
            "read_roots": sorted(map(str, self.read_roots)),
            "write_roots": sorted(map(str, self.write_roots)),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "WorkspacePolicy":
        read_roots = {
            Path(item).resolve()
            for item in value.get("read_roots", [])
            if Path(item).is_dir()
        }
        write_roots = {
            Path(item).resolve()
            for item in value.get("write_roots", [])
            if Path(item).is_dir()
        }
        return cls(
            workspace_root=Path(value["workspace_root"]),
            read_roots=read_roots,
            write_roots=write_roots,
        )