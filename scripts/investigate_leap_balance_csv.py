"""Run a guarded LEAP Energy Balance CSV through the dashboard workflow."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from web_app.app import _build_context  # noqa: E402
from codebase.portable_release import developer_launcher  # noqa: E402


def run_csv_dashboard(
    *,
    csv_path: Path,
    hierarchy_template_path: Path,
    economy: str,
    output_root: Path,
    min_year: int,
    max_year: int,
) -> dict[str, object]:
    """Copy one CSV into an isolated input folder and run the real dashboard."""
    csv_path = csv_path.resolve()
    hierarchy_template_path = hierarchy_template_path.resolve()
    output_root = output_root.resolve()
    export_dir = output_root / "csv_export"
    export_dir.mkdir(parents=True, exist_ok=True)
    staged_csv = export_dir / csv_path.name
    shutil.copy2(csv_path, staged_csv)

    context = _build_context(output_root / "workflow")
    result = developer_launcher.run_dashboard_from_export(
        context=context,
        economy=economy,
        export_dir=export_dir,
        balance_csv_hierarchy_template_path=hierarchy_template_path,
        min_year=min_year,
        max_year=max_year,
        run_label="leap-csv-investigation",
    )
    summary = {
        "ok": bool(result.ok),
        "error": result.error,
        "outputs": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in result.outputs.items()
        },
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "dashboard_run_summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("hierarchy_template_path", type=Path)
    parser.add_argument("--economy", default="01_AUS")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--min-year", type=int, default=2022)
    parser.add_argument("--max-year", type=int, default=2022)
    arguments = parser.parse_args()
    summary = run_csv_dashboard(
        csv_path=arguments.csv_path,
        hierarchy_template_path=arguments.hierarchy_template_path,
        economy=arguments.economy,
        output_root=arguments.output_root,
        min_year=arguments.min_year,
        max_year=arguments.max_year,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
