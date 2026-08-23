#%%
"""Build one dashboard per non-archived LEAP export folder using the web app backend."""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = Path(r"C:\Users\Work\github\leap_initialisation\data\leap balances exports")
PINNED_RUNTIME_ROOT = Path(r"C:\Users\Work\github\leap_review_web_app_deploy_20260821\runtime")
PINNED_SOURCE_PARENT = Path(r"C:\Users\Work\github\hf_runtime_pins_20260823")
OUTPUT_ROOT = Path(r"C:\Users\Work\github\leap_initialisation\outputs\local_export_dashboards_20260823")
ESTO_VINTAGE = "2026"


def export_folders() -> list[Path]:
    """Return economy folders with direct, non-archived export workbooks."""
    return [
        folder
        for folder in sorted(EXPORT_ROOT.iterdir())
        if folder.is_dir() and any(folder.glob("*.xlsx"))
    ]


def run_dashboards() -> None:
    """Run the same dashboard command used by the local Gradio app."""
    os.environ["LEAP_RUNTIME_ROOT"] = str(PINNED_RUNTIME_ROOT)
    os.environ["LEAP_SOURCE_PARENT"] = str(PINNED_SOURCE_PARENT)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from web_app import app as web_app

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    esto_table = web_app._esto_table_for_vintage(ESTO_VINTAGE)
    rows: list[dict[str, str]] = []
    for folder in export_folders():
        economy = folder.name
        run_root = OUTPUT_ROOT / economy
        context = web_app._build_context(run_root)
        result = web_app.developer_launcher.run_dashboard_from_export(
            context=context,
            economy=economy,
            export_dir=folder,
            esto_table_path=esto_table,
            min_year=2010,
            max_year=2060,
            run_label="all-local-exports",
        )
        rows.append(
            {
                "economy": economy,
                "status": "succeeded" if result.ok else "failed",
                "dashboard_index": str(result.outputs.get("dashboard_index", "")),
                "error": str(result.error or ""),
            }
        )
        print(f"{economy}: {rows[-1]['status']}", flush=True)

    report = OUTPUT_ROOT / "batch_status.csv"
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["economy"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Finished {len(rows)} folders at {datetime.now().isoformat(timespec='seconds')}")


#%%
if __name__ == "__main__":
    run_dashboards()

#%%
