#!/usr/bin/env python3
"""Select the named executables from a Bazel cquery --output=files result."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence


def select_binaries(paths: Sequence[str], expected_names: Sequence[str]) -> list[Path]:
    """Return exactly one existing executable for each requested basename."""
    if not expected_names or len(set(expected_names)) != len(expected_names):
        raise ValueError("expected executable names must be non-empty and unique")

    candidates = [Path(path) for path in paths if path]
    if len(set(candidates)) != len(candidates):
        raise ValueError("Bazel query returned duplicate output paths")
    selected: list[Path] = []
    for name in expected_names:
        matches = [path for path in candidates if path.name == name]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one Bazel output named {name!r}, found {len(matches)}"
            )
        path = matches[0]
        if not path.is_file():
            raise ValueError(f"Bazel executable output does not exist: {path}")
        if not os.access(path, os.X_OK):
            raise ValueError(f"Bazel output is not executable: {path}")
        selected.append(path)

    # Extra files (for example debug symbols) are expected.
    return selected


def main(argv: Sequence[str]) -> int:
    try:
        selected = select_binaries(sys.stdin.read().splitlines(), argv)
    except ValueError as error:
        print(f"SDK Bazel output selection failed: {error}", file=sys.stderr)
        return 1
    for path in selected:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
