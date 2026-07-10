#!/usr/bin/env python3
"""Promote the API and UI chart image tags to one immutable build tag."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


TAG_PATTERN = re.compile(r"master-[0-9]+-[0-9]+-[a-f0-9]{7}")
IMAGE_NAMES = ("tertius-api", "tertius-ui")


class PromotionError(Exception):
    """Raised when chart values cannot be promoted safely."""


def _marker(image_name: str) -> bytes:
    return f'# {{"$imagepromoter": "{image_name}"}}'.encode("ascii")


def _tag_line_pattern(image_name: str) -> re.Pattern[bytes]:
    marker = re.escape(_marker(image_name))
    return re.compile(
        rb"^(?P<prefix>[ \t]*tag:[ \t]*)"
        rb'(?P<scalar>"[^"\r\n]*"|\'[^\'\r\n]*\'|[^ \t#\r\n]+)'
        rb"(?P<suffix>[ \t]+" + marker + rb"[ \t]*\r?)$",
        re.MULTILINE,
    )


def _promoted_values(contents: bytes, tag: str) -> bytes:
    tag_bytes = tag.encode("ascii")
    replacements: list[tuple[re.Match[bytes], bytes]] = []

    if contents.count(b"$imagepromoter") != len(IMAGE_NAMES):
        raise PromotionError("expected exactly two image promoter markers")

    for image_name in IMAGE_NAMES:
        marker = _marker(image_name)
        if contents.count(marker) != 1:
            raise PromotionError(
                f"expected exactly one image promoter marker for {image_name}"
            )

        matches = list(_tag_line_pattern(image_name).finditer(contents))
        if len(matches) != 1:
            raise PromotionError(
                f"image promoter marker for {image_name} must be on one valid tag line"
            )

        match = matches[0]
        scalar = match.group("scalar")
        if scalar.startswith(b'"'):
            replacement = b'"' + tag_bytes + b'"'
        elif scalar.startswith(b"'"):
            replacement = b"'" + tag_bytes + b"'"
        else:
            replacement = tag_bytes
        replacements.append((match, replacement))

    promoted = contents
    ordered_replacements = sorted(
        replacements, key=lambda item: item[0].start(), reverse=True
    )
    for match, replacement in ordered_replacements:
        start, end = match.span("scalar")
        promoted = promoted[:start] + replacement + promoted[end:]
    return promoted


def promote_images(values_path: Path, tag: str) -> None:
    if TAG_PATTERN.fullmatch(tag) is None:
        raise PromotionError(
            "tag must match master-[0-9]+-[0-9]+-[a-f0-9]{7}"
        )

    if values_path.is_symlink():
        raise PromotionError("--values must not be a symlink")

    contents = values_path.read_bytes()
    promoted = _promoted_values(contents, tag)
    mode = stat.S_IMODE(values_path.stat().st_mode)
    temp_path: Path | None = None

    try:
        fd, temp_name = tempfile.mkstemp(
            dir=values_path.parent,
            prefix=f".{values_path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, "wb") as temp_file:
            temp_file.write(promoted)
            temp_file.flush()
            os.fchmod(temp_file.fileno(), mode)
            os.fsync(temp_file.fileno())
        os.replace(temp_path, values_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update marked Tertius chart image tags atomically."
    )
    parser.add_argument("--values", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        promote_images(args.values, args.tag)
    except (OSError, PromotionError) as error:
        print(f"promote_images.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
