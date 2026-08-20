from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from evals.official.common import write_jsonl


def load_gaia_rows(
    *,
    split: str,
    level_config: str,
    limit: int | None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError(
            "运行 GAIA 需要先安装：pip install datasets"
        ) from exc

    dataset = load_dataset(
        "gaia-benchmark/GAIA",
        level_config,
        split=split,
    )
    rows = [dict(item) for item in dataset]
    rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return rows


def copy_attachment(
    example: dict[str, Any],
    workspace: Path,
) -> str | None:
    file_path = example.get("file_path")
    if not file_path:
        return None
    source = Path(str(file_path))
    if not source.exists():
        return None
    target = workspace / source.name
    shutil.copy2(source, target)
    return target.name


def run_gaia_subset(
    *,
    output_path: Path,
    split: str,
    level_config: str,
    model: str | None,
    limit: int | None,
    offset: int = 0,
) -> Path:
    from mini_claude.agent import MINI_CLUE_AGENT

    rows = load_gaia_rows(
        split=split,
        level_config=level_config,
        limit=limit,
        offset=offset,
    )
    predictions: list[dict[str, Any]] = []

    for row in rows:
        task_id = str(row["task_id"])
        workspace = Path(".mini-agent") / "gaia-workspaces" / task_id
        workspace.mkdir(parents=True, exist_ok=True)
        attachment_name = copy_attachment(row, workspace)
        prompt = str(row["Question"])
        if attachment_name:
            prompt += f"\n\n附件已经放在当前工作区：{attachment_name}"

        print(f"GAIA running {task_id}")
        agent = MINI_CLUE_AGENT(
            session_id=f"gaia-{task_id}-{uuid4().hex}",
            permission_mode="dont_ask",
            model=model,
            project_root=workspace,
        )
        try:
            answer = agent.chat(prompt)
        finally:
            agent.close()

        predictions.append(
            {
                "task_id": task_id,
                "level": row.get("Level"),
                "question": row.get("Question"),
                "final_answer": row.get("Final answer"),
                "prediction": answer,
                "exact_match": str(answer).strip().lower()
                == str(row.get("Final answer", "")).strip().lower(),
            }
        )

    write_jsonl(output_path, predictions)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Mini Claude on a small GAIA subset."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", default="test")
    parser.add_argument("--level-config", default="2023_level1")
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--offset", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = run_gaia_subset(
        output_path=args.output,
        split=args.split,
        level_config=args.level_config,
        model=args.model,
        limit=args.limit,
        offset=args.offset,
    )
    print(f"GAIA predictions written to {output}")


if __name__ == "__main__":
    main()
