#%%
"""Catch edits made to ``runtime/`` instead of to the repository it came from.

``runtime/`` is a copy. ``refresh_runtime.py`` rebuilds it from
leap_initialisation, leap_mappings and leap_dashboard, so anything typed into
it directly survives only until the next refresh and then vanishes without a
trace -- the deployed app quietly loses a fix and nothing says why.

This has already happened once: a fix for hosted logging was committed to the
deployed copy of ``portable_release/runtime.py`` and did not exist in
leap_initialisation at all. It was noticed by chance, while diffing before a
deploy.

So: compare every file in ``runtime/`` against the same file in its source
repository, and name any that disagree.

    python scripts/check_runtime_is_generated.py
    python scripts/check_runtime_is_generated.py --runtime ../leap_review_web_app/runtime

A difference is not always an edit -- a source repo that has moved on since the
last refresh shows up the same way -- so the report says which side is which
and what to do about it. Run it before a deploy, and after pulling someone
else's work into any of the three source repositories.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Files the runtime carries that no source repository owns.
GENERATED_ONLY = {"source_manifest.json"}
# Scratch a run writes inside the runtime tree; not part of the closure.
IGNORED_PARTS = {"__pycache__", "outputs"}


def _source_root(repo_root: Path, repo: str) -> Path:
    return repo_root / repo


def _iter_runtime_files(runtime: Path):
    for path in sorted(runtime.rglob("*")):
        if not path.is_file():
            continue
        parts = set(path.relative_to(runtime).parts)
        if parts & IGNORED_PARTS:
            continue
        if path.name in GENERATED_ONLY:
            continue
        yield path


def compare(runtime: Path, repo_root: Path) -> tuple[list[str], list[str]]:
    """Return (differing, missing) paths, described for a reader."""
    manifest_path = runtime / "source_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"No source_manifest.json under {runtime}")
    repositories = json.loads(manifest_path.read_text(encoding="utf-8"))[
        "source_repositories"
    ]

    differing: list[str] = []
    missing: list[str] = []
    for path in _iter_runtime_files(runtime):
        relative = path.relative_to(runtime)
        repo = relative.parts[0]
        if repo not in repositories:
            continue
        source = _source_root(repo_root, repo).joinpath(*relative.parts[1:])
        if not source.is_file():
            missing.append(str(relative))
            continue
        if source.read_bytes() != path.read_bytes():
            differing.append(str(relative))
    return differing, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        default="runtime",
        help="the runtime tree to check (default: this repository's)",
    )
    parser.add_argument(
        "--source-parent",
        default=str(Path(__file__).resolve().parents[2]),
        help="the folder holding the three source repositories",
    )
    arguments = parser.parse_args()

    runtime = Path(arguments.runtime).resolve()
    repo_root = Path(arguments.source_parent).resolve()
    differing, missing = compare(runtime, repo_root)

    if not differing and not missing:
        print(f"{runtime} matches its source repositories.")
        return 0

    for relative in differing:
        print(f"differs from source: {relative}")
    for relative in missing:
        print(f"not present in any source repository: {relative}")
    print(
        "\nEach file above is either an edit made to the copy, which the next "
        "refresh_runtime.py will discard, or a source repository that has "
        "moved on since the last refresh.\n"
        "  - fix belongs in the copy only: it does not. Move it to the source "
        "repository, commit there, then re-run refresh_runtime.py.\n"
        "  - source has simply moved on: re-run refresh_runtime.py.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#%%
