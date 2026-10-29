#!/usr/bin/env python3
"""Check a compiled project's scene manifest and assets against its
monolithic GLB (#314); see server/core/scene_check.py.

    check_scene_manifest.py <project dir with output.glb and tertius-scene/>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))

from core.scene_check import check_project  # noqa: E402


def main(project: Path) -> int:
    nodes, problems, sizes = check_project(project)
    print(
        f"{nodes} nodes compared, {sizes.get('assets', 0)} assets "
        f"({sizes.get('asset_bytes', 0) / 1e6:.1f} MB), monolithic {sizes.get('monolithic_bytes', 0) / 1e6:.1f} MB, "
        f"manifest {sizes.get('manifest_bytes', 0) / 1e6:.2f} MB: "
        f"{'MATCH' if not problems else str(len(problems)) + ' problems'}"
    )
    for problem in problems[:15]:
        print("  ", problem)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
