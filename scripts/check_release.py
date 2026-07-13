#!/usr/bin/env python3
"""Verify release metadata consistency before publishing.

Checks that the version declared in pyproject.toml, limitlens/__init__.py,
and CHANGELOG.md all agree, and (when running in CI) that the pushed git tag
matches the package version.

Exits 0 with a summary line on success, exits 1 with a clear error naming
the mismatched file otherwise.

Usage:
    python scripts/check_release.py
    GITHUB_REF_NAME=v0.8.0 python scripts/check_release.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_FILE = REPO_ROOT / "limitlens" / "__init__.py"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

VERSION_RE = re.compile(r"^version\s*=\s*\"([^\"]+)\"", re.MULTILINE)
INIT_VERSION_RE = re.compile(r"^__version__\s*=\s*\"([^\"]+)\"", re.MULTILINE)


def read_pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    if not match:
        print(f"ERROR: could not find version in {PYPROJECT.relative_to(REPO_ROOT)}", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def read_init_version() -> str:
    text = INIT_FILE.read_text(encoding="utf-8")
    match = INIT_VERSION_RE.search(text)
    if not match:
        print(f"ERROR: could not find __version__ in {INIT_FILE.relative_to(REPO_ROOT)}", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def changelog_has_version(version: str) -> bool:
    text = CHANGELOG.read_text(encoding="utf-8")
    pattern = re.compile(rf"^##\s+\[{re.escape(version)}\](\s|$)")
    return any(pattern.match(line) for line in text.splitlines())


def main() -> int:
    pyproject_version = read_pyproject_version()
    init_version = read_init_version()

    if pyproject_version != init_version:
        print(
            f"ERROR: version mismatch — pyproject.toml has '{pyproject_version}' "
            f"but limitlens/__init__.py has '{init_version}'",
            file=sys.stderr,
        )
        return 1

    version = pyproject_version

    if not changelog_has_version(version):
        print(
            f"ERROR: CHANGELOG.md has no heading '## [{version}]'",
            file=sys.stderr,
        )
        return 1

    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    if ref_name.startswith("v"):
        expected_tag = f"v{version}"
        if ref_name != expected_tag:
            print(
                f"ERROR: git tag '{ref_name}' does not match package version "
                f"'{expected_tag}' (pyproject.toml)",
                file=sys.stderr,
            )
            return 1

    print(f"OK: release version {version} is consistent across pyproject.toml, "
          f"limitlens/__init__.py, and CHANGELOG.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
