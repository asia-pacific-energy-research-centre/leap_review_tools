"""Regression tests for temporary result cleanup and refresh restoration."""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from web_app import app


def test_browser_state_secret_is_stable_across_app_restarts() -> None:
    """All persisted records use the fixed secret required by BrowserState."""
    demo = app.create_app()
    browser_states = {
        component.storage_key: component
        for component in demo.blocks.values()
        if component.__class__.__name__ == "BrowserState"
    }

    assert set(browser_states) == {
        "leap_balance_review_dashboard_archives",
        "leap_balance_review_active_job",
        "leap_balance_review_last_run",
    }
    assert {
        component.secret for component in browser_states.values()
    } == {app.BROWSER_STATE_SECRET}


def test_results_copy_keeps_only_saved_dashboard_limits() -> None:
    """The archive is self-explanatory; only dashboard loss needs guidance."""
    config = app.create_app().get_config_file()
    text = "\n".join(
        str(component.get("props", {}).get("value", ""))
        for component in config["components"]
    )
    labels = {
        str(component.get("props", {}).get("label", ""))
        for component in config["components"]
    }

    assert "Complete run archive (.zip)" in labels
    assert "The safest way to keep this run" not in text
    assert "website updates, clearing site data" in text
    assert "the oldest is replaced when a fourth is saved" in text


def test_complete_run_archive_name_identifies_run_and_creation_time() -> None:
    created_at = datetime(2026, 8, 12, 13, 4, 5, tzinfo=timezone.utc)

    name = app._complete_run_archive_name(
        "05_PRC", "Target", created_at=created_at
    )

    assert name == "05_PRC_Target_complete_run_archive_120826_130405.zip"


def test_cleanup_removes_only_expired_app_directories(monkeypatch, tmp_path) -> None:
    """The 48-hour rule is an on-run cleanup threshold, not a file lifetime."""
    dashboard_root = tmp_path / "leap_balance_review_dashboards"
    dashboard_root.mkdir()
    old_run = tmp_path / "leap_balance_review_web_old"
    recent_run = tmp_path / "leap_balance_review_download_recent"
    unrelated = tmp_path / "another_app_old"
    old_dashboard = dashboard_root / "old-dashboard"
    for directory in (old_run, recent_run, unrelated, old_dashboard):
        directory.mkdir()

    now = time.time()
    expired_time = now - app.WEB_ARTIFACT_MAX_AGE_SECONDS - 60
    os.utime(old_run, (expired_time, expired_time))
    os.utime(old_dashboard, (expired_time, expired_time))
    os.utime(unrelated, (expired_time, expired_time))

    monkeypatch.setattr(app.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(app, "DASHBOARD_SERVE_ROOT", dashboard_root)

    removed = app._cleanup_stale_web_artifacts()

    assert set(removed) == {old_run, old_dashboard}
    assert not old_run.exists()
    assert not old_dashboard.exists()
    assert recent_run.is_dir()
    assert unrelated.is_dir()


def test_refresh_restores_downloads_that_still_exist(tmp_path) -> None:
    """Restoring browser state reads surviving files without expiring them."""
    workbook = tmp_path / "review.xlsx"
    archive = tmp_path / "complete-run.zip"
    workbook.write_bytes(b"workbook")
    archive.write_bytes(b"archive")
    record = {
        "summary": "Finished",
        "status": "Complete",
        "workbooks": [str(workbook)],
        "bundle": str(archive),
        "finished_at": "2026-08-12 10:00 UTC",
    }

    restored = app.restore_last_run(record, [])

    assert restored[2] == [str(workbook)]
    assert restored[3] == str(archive)
    assert "Restored from 2026-08-12 10:00 UTC" in restored[4]
    assert workbook.is_file()
    assert archive.is_file()


def test_refresh_reports_downloads_removed_with_the_server_instance(tmp_path) -> None:
    """Browser metadata survives even when its temporary server files do not."""
    missing_workbook = Path(tmp_path / "removed-review.xlsx")
    record = {
        "summary": "Finished",
        "status": "Complete",
        "workbooks": [str(missing_workbook)],
        "bundle": str(tmp_path / "removed-archive.zip"),
        "finished_at": "2026-08-12 10:00 UTC",
    }

    restored = app.restore_last_run(record, [])

    assert restored[2] == []
    assert restored[3] is None
    assert "cleared from the server" in restored[4]
