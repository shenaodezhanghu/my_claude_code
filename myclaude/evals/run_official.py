from __future__ import annotations

import argparse
from pathlib import Path
import time

from evals.official.gaia_adapter import run_gaia_subset
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
REPORTS = Path(__file__).resolve().parent / "reports" / "official"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run official benchmark adapters."
    )
    parser.add_argument(
        "--benchmark",
        choices=["gaia"],
        required=True,
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)

    parser.add_argument("--gaia-split", default="test")
    parser.add_argument("--gaia-level-config", default="2023_level1")
    parser.add_argument("--offset", type=int, default=0)
    return parser.parse_args()


def default_output(benchmark: str) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return REPORTS / f"{stamp}-{benchmark}.jsonl"


def main() -> None:
    args = parse_args()
    output = args.output or default_output(args.benchmark)

    run_gaia_subset(
        output_path=output,
        split=args.gaia_split,
        level_config=args.gaia_level_config,
        model=args.model,
        limit=args.limit or 3,
        offset=args.offset,
    )


if __name__ == "__main__":
    main()
