#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import re
from pathlib import Path


FORBIDDEN_SUFFIXES = {".pptx", ".docx", ".pdf", ".doc", ".ppt"}
FORBIDDEN_PARTS = {"runs", "profiles", ".deslop-ai", "private"}


def candidates(root: Path) -> list[Path]:
    git = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    if git.returncode == 0:
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True, text=True, check=True)
        return [root / line for line in listed.stdout.splitlines() if line]
    return [path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = []
    for path in candidates(root):
        if path.resolve() == Path(__file__).resolve():
            continue
        relative = path.relative_to(root)
        parts = {part.casefold() for part in relative.parts}
        is_synthetic = relative.parts[:2] == ("tests", "fixtures")
        if path.suffix.casefold() in FORBIDDEN_SUFFIXES and not is_synthetic:
            violations.append(f"binary document outside synthetic fixtures: {relative}")
        if parts & FORBIDDEN_PARTS:
            violations.append(f"private/run path: {relative}")
        if path.name.casefold() in {"source-map.json", "revised-source-map.json", "profile.json"}:
            violations.append(f"generated or private data: {relative}")
        if path.is_file() and path.stat().st_size < 4_000_000 and path.suffix.casefold() in {".py", ".md", ".json", ".yaml", ".yml", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="ignore").casefold()
            if re.search(r"c:[\\/]+users[\\/]+[^\\/]+[\\/]+(?:onedrive|downloads|documents|desktop)[\\/]", text, re.I):
                violations.append(f"absolute private source path embedded: {relative}")
    if violations:
        print("Privacy audit failed:")
        print("\n".join(f"- {item}" for item in sorted(set(violations))))
        return 2
    print(f"Privacy audit passed: {len(candidates(root))} candidate files checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
