#%%
"""Check whether sibling LEAP repositories are ready for a colleague run.

This is read-only. It checks the shared Python environment, cloned repositories,
portable-bundle inputs, LEAP exports, and the mapping output needed by the
dashboard. Warnings describe optional or not-yet-generated capabilities.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


# --- Stable setup contract ---

REPO_ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES = (
    "leap_initialisation",
    "leap_mappings",
    "leap_dashboard",
    "leap_review_tools",
)
REQUIRED_IMPORTS = ("pandas", "openpyxl", "pyarrow", "plotly", "gradio")
SHARED_ENVIRONMENT_FILE = "environment.yml"
INITIALISATION_INPUTS = (
    "data/00APEC_2024_low_with_subtotals.csv",
    "data/00APEC_2025_low_with_subtotals.csv",
    "data/merged_file_energy_ALL_20251106.csv",
    "data/9th merged_file_energy_00_APEC_20251106.csv",
)
MAPPING_INPUTS = (
    "data/00APEC_2024_low_with_subtotals.csv",
    "data/00APEC_2025_low_with_subtotals.csv",
    "data/merged_file_energy_ALL_20251106.csv",
    "data/esto_extended.csv",
    "data/temp/new leap rows.xlsx",
)


def _result(level: str, check: str, detail: str) -> dict[str, str]:
    return {"level": level, "check": check, "detail": detail}


def _missing_paths(root: Path, relative_paths: tuple[str, ...]) -> list[str]:
    return [relative for relative in relative_paths if not (root / relative).is_file()]


def check_repositories(source_parent: Path) -> list[dict[str, str]]:
    results = []
    for name in REPOSITORIES:
        root = source_parent / name
        if not root.is_dir() or not (root / ".git").exists():
            results.append(_result("FAIL", name, f"missing Git clone at {root}"))
            continue
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        level = "WARN" if dirty else "PASS"
        detail = f"commit {commit or 'unknown'}" + ("; uncommitted changes present" if dirty else "; clean")
        results.append(_result(level, name, detail))
    return results


def check_python_environment() -> list[dict[str, str]]:
    results = [_result("PASS", "Python", f"{sys.version.split()[0]} at {sys.executable}")]
    for module_name in REQUIRED_IMPORTS:
        try:
            module = importlib.import_module(module_name)
            version = str(getattr(module, "__version__", "installed"))
            results.append(_result("PASS", module_name, version))
        except Exception as error:
            results.append(_result("FAIL", module_name, f"import failed: {error}"))
    try:
        importlib.import_module("win32com.client")
        results.append(_result("PASS", "LEAP COM support", "pywin32 is installed"))
    except Exception:
        results.append(
            _result("WARN", "LEAP COM support", "pywin32 unavailable; file-only workflows still work")
        )
    return results


def check_shared_environment_files(source_parent: Path) -> list[dict[str, str]]:
    """Confirm every sibling repository carries the same environment contract."""
    paths = {
        name: source_parent / name / SHARED_ENVIRONMENT_FILE
        for name in REPOSITORIES
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        return [
            _result(
                "FAIL",
                "shared environment",
                "missing environment.yml in: " + ", ".join(missing),
            )
        ]

    canonical = paths["leap_review_tools"].read_bytes()
    different = [
        name for name, path in paths.items() if path.read_bytes() != canonical
    ]
    if different:
        return [
            _result(
                "FAIL",
                "shared environment",
                "environment.yml differs in: " + ", ".join(different),
            )
        ]
    return [
        _result(
            "PASS",
            "shared environment",
            "environment.yml is identical in all four repositories",
        )
    ]


def check_data_inputs(source_parent: Path) -> list[dict[str, str]]:
    results = []
    initialisation = source_parent / "leap_initialisation"
    mappings = source_parent / "leap_mappings"
    for name, root, required in (
        ("initialisation bundle", initialisation, INITIALISATION_INPUTS),
        ("mapping bundle", mappings, MAPPING_INPUTS),
    ):
        missing = _missing_paths(root, required)
        if missing:
            results.append(_result("FAIL", name, "missing: " + ", ".join(missing)))
        else:
            results.append(_result("PASS", name, f"all {len(required)} required files present"))

    templates = list((initialisation / "data/leap_export_templates").glob("*.xlsx"))
    exports = list((initialisation / "data/leap balances exports/20_USA").glob("*.xlsx"))
    export_names = [path.name.upper() for path in exports]
    has_ref = any("REF" in name or "REFERENCE" in name for name in export_names)
    has_tgt = any("TGT" in name or "TARGET" in name for name in export_names)
    if templates:
        results.append(_result("PASS", "LEAP templates", f"{len(templates)} workbook(s)"))
    else:
        results.append(_result("FAIL", "LEAP templates", "no .xlsx templates installed"))
    if has_ref and has_tgt:
        results.append(_result("PASS", "20_USA balances", "REF and TGT exports present"))
    else:
        results.append(_result("FAIL", "20_USA balances", "both REF and TGT exports are required"))

    contract = mappings / "results/common_esto/common_esto_output_contract.json"
    if contract.is_file():
        results.append(_result("PASS", "dashboard mapping contract", str(contract)))
    else:
        results.append(
            _result("WARN", "dashboard mapping contract", "not generated yet; run the mapping pipeline first")
        )
    return results


def check_disk_space(source_parent: Path) -> list[dict[str, str]]:
    free_gb = shutil.disk_usage(source_parent).free / (1024 ** 3)
    level = "PASS" if free_gb >= 25 else "WARN"
    return [_result(level, "free disk", f"{free_gb:.1f} GB; keep at least 25 GB for the bounded workflow")]


def run_colleague_setup_checks(source_parent: Path) -> list[dict[str, str]]:
    source_parent = Path(source_parent).resolve()
    return [
        *check_python_environment(),
        *check_shared_environment_files(source_parent),
        *check_repositories(source_parent),
        *check_data_inputs(source_parent),
        *check_disk_space(source_parent),
    ]


def print_report(results: list[dict[str, str]]) -> None:
    for result in results:
        print(f"[{result['level']}] {result['check']}: {result['detail']}")
    failures = sum(result["level"] == "FAIL" for result in results)
    warnings = sum(result["level"] == "WARN" for result in results)
    print(f"\nSummary: {failures} failure(s), {warnings} warning(s).")
    if failures == 0:
        print("Setup is ready for the documented colleague workflow.")


#%%
# --- Frequently changed run settings ---

SOURCE_PARENT = Path(os.environ.get("LEAP_SOURCE_PARENT", REPO_ROOT.parent))
RUN_CHECKS = True

if __name__ == "__main__" and RUN_CHECKS:
    CHECK_RESULTS = run_colleague_setup_checks(SOURCE_PARENT)
    print_report(CHECK_RESULTS)
    raise SystemExit(1 if any(item["level"] == "FAIL" for item in CHECK_RESULTS) else 0)

#%%
