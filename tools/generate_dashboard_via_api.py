"""Generate dashboard archives sequentially through the deployed Gradio API."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
import zipfile
from concurrent.futures import CancelledError
from datetime import datetime
from pathlib import Path

DEFAULT_SPACE_URL = "https://finbarmaunsell-leap-review-web-app.hf.space/"
START_API_NAME = "/start_dashboard_archive"
STATUS_API_NAME = "/dashboard_archive_status"
POLL_SECONDS = 15


def short_output_name(workbook: Path) -> str:
    """Return a bounded, recognisable archive name from an export filename."""
    first_token = workbook.stem.split("_", 1)[0]
    token = re.sub(r"[^A-Za-z0-9]+", "", first_token).upper()[:12] or "LEAP"
    return f"{token}_NEW_DASHBOARD.zip"


def available_target(output_dir: Path, preferred_name: str) -> Path:
    """Avoid overwriting an earlier review archive."""
    preferred = output_dir / preferred_name
    if not preferred.exists():
        return preferred
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{preferred.stem}_{stamp}{preferred.suffix}"


def returned_file_path(value: object) -> Path:
    """Normalise Gradio client file outputs across supported client versions."""
    if isinstance(value, dict):
        value = value.get("path") or value.get("name")
    path = Path(str(value or ""))
    if not path.is_file():
        raise FileNotFoundError(f"The API returned no downloadable archive: {value!r}")
    return path


def validate_dashboard_archive(path: Path) -> dict[str, object]:
    """Validate the compact offline archive and return its manifest."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        required = {"archive_manifest.json", "d/0/p/index.html"}
        missing = sorted(required - names)
        if missing:
            raise ValueError(
                f"Dashboard archive {path.name} is missing: {', '.join(missing)}"
            )
        manifest = json.loads(archive.read("archive_manifest.json"))
    return manifest


def generate_archives(
    *,
    workbooks: list[Path],
    output_dir: Path,
    space_url: str = DEFAULT_SPACE_URL,
    esto_vintage: str = "2024",
) -> list[Path]:
    """Submit each workbook only after the preceding archive is validated."""
    try:
        from gradio_client import Client, handle_file
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Install gradio_client to submit dashboards through the hosted API."
        ) from error
    output_dir.mkdir(parents=True, exist_ok=True)
    client = Client(space_url)
    outputs: list[Path] = []
    for workbook in workbooks:
        if not workbook.is_file():
            raise FileNotFoundError(workbook)
        print(f"Submitting {workbook.name} to {START_API_NAME} ...", flush=True)
        start = client.predict(
            [handle_file(str(workbook))], esto_vintage, api_name=START_API_NAME
        )
        job_id = str(start.get("job_id") or "") if isinstance(start, dict) else ""
        if not job_id:
            raise RuntimeError(f"The API returned no job id: {start!r}")
        last_message = ""
        transient_failures = 0
        while True:
            try:
                status, archive_value = client.predict(job_id, api_name=STATUS_API_NAME)
                transient_failures = 0
            except (CancelledError, OSError, TimeoutError) as error:
                transient_failures += 1
                if transient_failures > 12:
                    raise RuntimeError(
                        f"Lost contact with dashboard job {job_id}: {error}"
                    ) from error
                print(
                    f"Status connection interrupted; retrying job {job_id} "
                    f"({transient_failures}/12).",
                    flush=True,
                )
                client = Client(space_url)
                time.sleep(POLL_SECONDS)
                continue
            state = str(status.get("state") or "unknown")
            message = str(status.get("message") or "").strip()
            if message and message != last_message:
                elapsed = status.get("elapsed_seconds")
                print(f"{workbook.name}: {message} ({elapsed}s)", flush=True)
                last_message = message
            if state == "done" and archive_value:
                summary = status.get("summary", {})
                break
            if state in {"failed", "cancelled", "unknown"}:
                raise RuntimeError(
                    f"Dashboard job {job_id} ended as {state}: {message}"
                )
            time.sleep(POLL_SECONDS)
        downloaded = returned_file_path(archive_value)
        target = available_target(output_dir, short_output_name(workbook))
        shutil.copy2(downloaded, target)
        manifest = validate_dashboard_archive(target)
        uploaded = [
            item.get("original_filename")
            for item in manifest.get("uploaded_balance_exports", [])
        ]
        print(
            f"Saved {target} | inputs={uploaded} | "
            f"status={summary.get('dashboard_status') if isinstance(summary, dict) else 'unknown'}",
            flush=True,
        )
        outputs.append(target)
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbooks", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--space-url", default=DEFAULT_SPACE_URL)
    parser.add_argument("--esto-vintage", default="2024")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generate_archives(
        workbooks=args.workbooks,
        output_dir=args.output_dir,
        space_url=args.space_url,
        esto_vintage=args.esto_vintage,
    )


if __name__ == "__main__":
    main()
