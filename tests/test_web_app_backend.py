"""Focused regression tests for the web application's backend helpers."""

from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

from web_app.app import (
    WEB_ARTIFACT_MAX_AGE_SECONDS,
    _browser_dashboard_choices,
    _browser_dashboard_record,
    _cleanup_stale_web_artifacts,
    _compress_dashboard_html,
    _decompress_dashboard_html,
    _write_diagnostics_bundle,
)


def test_dashboard_snapshot_helpers_round_trip_and_filter_records() -> None:
    html = "<html><body><script>const value = 42;</script></body></html>"
    encoded = _compress_dashboard_html(html)

    assert _decompress_dashboard_html(encoded) == html

    records = [
        {
            "archive_id": "run-1",
            "economy": "20_USA",
            "scenario": "Target",
            "years": [2022],
            "created_at": "2026-08-05 00:00 UTC",
        },
        {"archive_id": "run-2", "economy": "01_AUS"},
        {"economy": "missing-id"},
        "invalid record",
    ]

    assert _browser_dashboard_choices(records) == [
        ("20_USA / Target / [2022] (2026-08-05 00:00 UTC)", "run-1"),
        ("01_AUS / unknown /  ()", "run-2"),
    ]
    assert _browser_dashboard_record("run-1", records) == records[0]
    assert _browser_dashboard_record("missing", records) is None


def test_cleanup_only_removes_expired_web_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("web_app.app.tempfile.gettempdir", lambda: str(tmp_path))
    stale_run = tmp_path / "leap_balance_review_web_stale"
    stale_download = tmp_path / "leap_balance_review_download_stale"
    recent_run = tmp_path / "leap_balance_review_web_recent"
    unrelated = tmp_path / "other_application_data"
    for directory in (stale_run, stale_download, recent_run, unrelated):
        directory.mkdir()

    old_time = time.time() - WEB_ARTIFACT_MAX_AGE_SECONDS - 10
    for directory in (stale_run, stale_download):
        os.utime(directory, (old_time, old_time))

    removed = _cleanup_stale_web_artifacts()

    assert stale_run in removed
    assert stale_download in removed
    assert not stale_run.exists()
    assert not stale_download.exists()
    assert recent_run.exists()
    assert unrelated.exists()


def test_diagnostics_bundle_contains_workbooks_diagnostics_dashboard_and_logs(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "review.xlsx"
    workbook.write_bytes(b"workbook")
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    (diagnostics / "leap_balance_source_review.csv").write_text("a,b\n1,2\n")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    (run_directory / "run_manifest.json").write_text("{}")
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "index.html").write_text("<html></html>")
    (dashboard / "page.html").write_text("<html>page</html>")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "run.log").write_text("completed")
    bundle = tmp_path / "output.zip"

    _write_diagnostics_bundle(
        bundle_path=bundle,
        workbook_paths=[workbook],
        diagnostics_directory=diagnostics,
        run_directory=run_directory,
        dashboard_directory=dashboard,
        log_directory=logs,
    )

    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {
            "workbooks/review.xlsx",
            "diagnostics/leap_balance_source_review.csv",
            "run_manifest.json",
            "dashboard/index.html",
            "dashboard/page.html",
            "logs/run.log",
        }
