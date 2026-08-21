from __future__ import annotations

import argparse
import json
from pathlib import Path


FIELDS = [
    "pass_rate",
    "prompt_build_ms",
    "schema_build_ms",
    "prepare_total_ms",
    "duration_seconds",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "estimated_cost_usd",
    "model_turns",
    "tool_calls",
]


def load_summary(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data["summary"])


def fmt(value: float | int) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()

    base = load_summary(args.baseline)
    cand = load_summary(args.candidate)

    print("| metric | baseline | candidate | delta |")
    print("|---|---:|---:|---:|")
    for field in FIELDS:
        before = base.get(field, 0)
        after = cand.get(field, 0)
        delta = after - before
        print(
            f"| {field} | {fmt(before)} | {fmt(after)} | {fmt(delta)} |"
        )


if __name__ == "__main__":
    main()