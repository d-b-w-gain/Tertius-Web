#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = REPOSITORY_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.structural.review_pack_validation import (  # noqa: E402
    validate_structural_review_pack,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a Tertius Structural Workbench review pack.",
    )
    parser.add_argument("review_pack", type=Path)
    parser.add_argument(
        "--profile",
        choices=("technical", "abcb_claim"),
        default="technical",
        help="Use abcb_claim only for a release presented as independently appraised.",
    )
    parser.add_argument(
        "--output", type=Path, help="Write the JSON result to this file."
    )
    args = parser.parse_args()

    result = validate_structural_review_pack(
        args.review_pack.read_bytes(),
        profile=args.profile,
    )
    rendered = json.dumps(result.model_dump(), indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
