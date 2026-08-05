#%%
"""Pull the runtime closure of the three source repositories into this one.

This repository is the home of the LEAP review tools web app. It deliberately
does not vendor the analysis code: that lives in ``leap_initialisation``,
``leap_mappings`` and ``leap_dashboard``, which remain the single source of
truth. Running this script copies only the files those repositories declare as
runtime assets into ``runtime/``, and records the commit each one came from.

Run it whenever the source repositories move on, and before publishing the
Hugging Face Space, so the deployed app matches a known set of commits.

    python scripts/refresh_runtime.py --dry-run
    python scripts/refresh_runtime.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PARENT = REPO_ROOT.parent
DEFAULT_RUNTIME_ROOT = REPO_ROOT / "runtime"

REQUIRED_REPOSITORIES = (
    "leap_initialisation",
    "leap_mappings",
    "leap_dashboard",
)

# The web app uses developer_launcher even though the portable executable
# manifest deliberately excludes maintainer-only launcher modules.
EXTRA_RUNTIME_PATHS = {
    "leap_initialisation": (
        "codebase/portable_release/developer_launcher.py",
        "codebase/portable_release/manifest.py",
        "codebase/portable_release/settings.py",
        "config/portable_release_manifest.toml",
    )
}


def _normalise_path(path: object) -> Path:
    return Path(str(path).replace("\\", "/"))


def _git_metadata(repository_root: Path) -> dict[str, object]:
    """Return the source commit and dirty state for one repository."""
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        capture_output=True,
        check=True,
        text=True,
    )
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repository_root,
        capture_output=True,
        check=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repository_root,
        capture_output=True,
        check=True,
        text=True,
    )
    return {
        "commit": commit.stdout.strip(),
        "branch": branch.stdout.strip(),
        "dirty": bool(status.stdout.strip()),
    }


def refresh_runtime(
    *,
    source_parent: Path | str = DEFAULT_SOURCE_PARENT,
    runtime_root: Path | str = DEFAULT_RUNTIME_ROOT,
    dry_run: bool = False,
    allow_dirty_sources: bool = False,
) -> dict[str, Any]:
    """Copy the declared runtime closure of the source repositories here.

    Every source path is validated before the previous ``runtime/`` is removed,
    so a failed refresh leaves the working copy intact rather than half
    replaced.
    """
    source_parent_path = _normalise_path(source_parent).resolve()
    runtime_root_path = _normalise_path(runtime_root).resolve()
    manifest_path = (
        source_parent_path
        / "leap_initialisation"
        / "config"
        / "portable_release_manifest.toml"
    )
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Release manifest is missing: {manifest_path}")
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    repositories = manifest.get("repositories") or {}
    config_assets = manifest.get("config_assets") or []
    data_assets = manifest.get("data_assets") or []

    source_roots = {name: source_parent_path / name for name in REQUIRED_REPOSITORIES}
    missing = [name for name, path in source_roots.items() if not (path / ".git").exists()]
    if missing:
        raise FileNotFoundError("Missing source repositories: " + ", ".join(missing))

    source_metadata = {name: _git_metadata(path) for name, path in source_roots.items()}
    dirty = [name for name, meta in source_metadata.items() if meta["dirty"]]
    if dirty and not allow_dirty_sources:
        raise RuntimeError(
            "Source repositories have uncommitted changes: "
            + ", ".join(dirty)
            + ". Commit them first, or pass --allow-dirty for a local experiment."
        )

    copy_plan: list[tuple[str, str]] = []
    for repository_name, specification in repositories.items():
        source_name = specification.get("source_key", repository_name)
        if source_name not in source_roots:
            continue
        for relative_path in specification.get("paths") or []:
            copy_plan.append((source_name, str(relative_path)))
    for asset in [*config_assets, *data_assets]:
        copy_plan.append((str(asset["repository"]), str(asset["path"])))
    for source_name, paths in EXTRA_RUNTIME_PATHS.items():
        copy_plan.extend((source_name, path) for path in paths)

    plan = list(dict.fromkeys(copy_plan))
    for source_name, relative_path in plan:
        source = source_roots[source_name] / _normalise_path(relative_path)
        if not source.is_file():
            raise FileNotFoundError(f"Required runtime source is missing: {source}")

    copied: dict[str, list[str]] = {name: [] for name in REQUIRED_REPOSITORIES}
    if not dry_run:
        if runtime_root_path.exists():
            shutil.rmtree(runtime_root_path)
        runtime_root_path.mkdir(parents=True, exist_ok=True)

    for source_name, relative_path in plan:
        relative = _normalise_path(relative_path)
        copied[source_name].append(relative.as_posix())
        if dry_run:
            continue
        destination = runtime_root_path / source_name / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_roots[source_name] / relative, destination)

    summary = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "source_repositories": source_metadata,
        "file_counts": {name: len(paths) for name, paths in copied.items()},
        "dry_run": dry_run,
    }
    if not dry_run:
        (runtime_root_path / "source_manifest.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
    return {"runtime_root": runtime_root_path, "manifest": summary, "copied_files": copied}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be copied without touching runtime/.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Permit uncommitted changes in the source repositories.",
    )
    parser.add_argument(
        "--source-parent",
        default=str(DEFAULT_SOURCE_PARENT),
        help="Directory holding the three source repositories.",
    )
    arguments = parser.parse_args()
    result = refresh_runtime(
        source_parent=arguments.source_parent,
        dry_run=arguments.dry_run,
        allow_dirty_sources=arguments.allow_dirty,
    )
    print(json.dumps(result["manifest"], indent=2))


if __name__ == "__main__":
    main()

#%%
