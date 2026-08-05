#%%
"""Reject syntax the deployed Python cannot parse, before it reaches a Space.

The Hugging Face Space runs an older Python than a maintainer's checkout, and
one difference bites repeatedly: before 3.12, an f-string replacement field
cannot contain a backslash. Such a file imports cleanly here and raises
``SyntaxError`` in production, several minutes into a run.

``ast.parse(..., feature_version=(3, 10))`` does **not** catch this. Since 3.12
f-strings are tokenised by the new PEP 701 tokeniser and ``feature_version``
does not restore the old behaviour, so the check silently passes. This walks
the tree instead and reads the source text of every replacement field.

    python scripts/check_python_compat.py runtime web_app
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

BACKSLASH = chr(92)


def offending_fstrings(path: Path) -> list[tuple[int, str]]:
    """Return (line, expression) for f-string fields holding a backslash."""
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FormattedValue):
            continue
        segment = ast.get_source_segment(source, node.value)
        if segment and BACKSLASH in segment:
            found.append((node.lineno, " ".join(segment.split())[:90]))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", default=["runtime", "web_app"])
    arguments = parser.parse_args()

    failures = 0
    for root in arguments.roots:
        for path in sorted(Path(root).rglob("*.py")):
            for line, expression in offending_fstrings(path):
                failures += 1
                print(f"{path}:{line}: backslash in f-string field: {expression}")
    if failures:
        print(
            f"\n{failures} construct(s) will raise SyntaxError on Python < 3.12.",
            file=sys.stderr,
        )
        return 1
    print("No f-string backslash constructs found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#%%
