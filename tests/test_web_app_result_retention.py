"""Regression tests for temporary result cleanup and refresh restoration."""

import json
import os
import time
import zipfile
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
    assert "Dashboard archive (.zip)" in labels
    assert "The safest way to keep this run" not in text
    assert "website updates, clearing site data" in text
    assert "the oldest is replaced when a fourth is saved" in text


def test_results_css_hides_gradio_empty_file_placeholders() -> None:
    """A dashboard-only result must not show empty File-output document icons."""
    assert '#download-row > .block:has(.empty[aria-label="Empty value"])' in app.APP_CSS
    assert "#review-workbooks-download .empty" in app.APP_CSS


def test_complete_run_archive_name_identifies_run_and_creation_time() -> None:
    created_at = datetime(2026, 8, 12, 13, 4, 5, tzinfo=timezone.utc)

    name = app._complete_run_archive_name(
        "05_PRC", "Target", created_at=created_at
    )

    assert name == "05_PRC_Target_complete_run_archive_120826_220405.zip"


def test_dashboard_archive_name_is_recognisable_but_bounded() -> None:
    created_at = datetime(2026, 8, 12, 13, 4, 5, tzinfo=timezone.utc)

    name = app._dashboard_archive_name(
        "05_PRC_with_an_unnecessarily_long_economy_name",
        "Target scenario with a long label",
        created_at=created_at,
    )

    assert name == "05_PRC_with_an_u_Target_scena_dashboard_120826_220405.zip"
    assert len(name) <= 60


def test_dashboard_only_archive_does_not_require_diagnostics(tmp_path) -> None:
    """A dashboard-only run still produces a self-contained archive."""
    dashboard_directory = tmp_path / "dashboard"
    dashboard_directory.mkdir()
    (dashboard_directory / "index.html").write_text("dashboard", encoding="utf-8")
    log_directory = tmp_path / "logs"
    log_directory.mkdir()
    (log_directory / "run.log").write_text("complete", encoding="utf-8")
    bundle_path = tmp_path / "dashboard-only.zip"

    app._write_diagnostics_bundle(
        bundle_path=bundle_path,
        workbook_paths=[],
        diagnostics_directory=None,
        run_directory=tmp_path,
        dashboard_directory=dashboard_directory,
        log_directory=log_directory,
    )

    with zipfile.ZipFile(bundle_path) as bundle:
        assert set(bundle.namelist()) == {
            "d/0/p/index.html",
            "a/plotly.min.js",
            "l/run.log",
            "archive_manifest.json",
        }


def test_dashboard_archive_compacts_paths_and_rewrites_offline_links(
    monkeypatch, tmp_path
) -> None:
    """The review ZIP remains portable and self-describing."""
    rendered = tmp_path / "rendered"
    primary = rendered / "20USA"
    comparison = rendered / "20USA__esto_extended_leap"
    diagnostics = rendered / "diagnostics"
    for root in (primary, comparison):
        (root / "dashboards").mkdir(parents=True)
        (root / "chart_bundles").mkdir()
        (root / "supporting_files").mkdir()
        (root / "chart_bundles" / "power__charts.js").write_text(
            "window.charts = {};", encoding="utf-8"
        )
    (diagnostics / "dashboards").mkdir(parents=True)
    (diagnostics / "supporting_files").mkdir()
    (diagnostics / "supporting_files" / "source_to_common_esto_map.csv").write_text(
        "source,target\n", encoding="utf-8"
    )
    (diagnostics / "dashboards" / "mapping_diagnostics.html").write_text(
        '<a href="../supporting_files/source_to_common_esto_map.csv">mapping</a>',
        encoding="utf-8",
    )
    (rendered / "mapping_chain").mkdir()
    (rendered / "mapping_chain" / "raw_leap_results.csv").write_text(
        "not needed for dashboard review", encoding="utf-8"
    )
    (primary / "dashboards" / "index.html").write_text(
        '<meta http-equiv="refresh" content="0; url=power.html">', encoding="utf-8"
    )
    (primary / "dashboards" / "power.html").write_text(
        "\n".join(
            (
                f'<script src="{app.PLOTLY_CDN_URL}"></script>',
                '<script src="../chart_bundles/power__charts.js"></script>',
                '<a href="../../20USA__esto_extended_leap/dashboards/power.html">basis</a>',
                '<a href="../../diagnostics/dashboards/mapping_diagnostics.html">diagnostics</a>',
            )
        ),
        encoding="utf-8",
    )
    (comparison / "dashboards" / "index.html").write_text(
        '<a href="power.html">Power</a>', encoding="utf-8"
    )
    (comparison / "dashboards" / "power.html").write_text(
        '<script src="../chart_bundles/power__charts.js"></script>', encoding="utf-8"
    )
    (rendered / "OPEN THE DASHBOARD.html").write_text(
        '<meta http-equiv="refresh" content="0; url=20USA/dashboards/index.html">',
        encoding="utf-8",
    )
    plotly_bundle = tmp_path / "plotly.min.js"
    plotly_bundle.write_text("window.Plotly = {};", encoding="utf-8")
    monkeypatch.setattr(app, "_plotly_offline_bundle_path", lambda: plotly_bundle)
    upload = tmp_path / "long_source_workbook_name.xlsx"
    upload.write_bytes(b"workbook")
    bundle_path = tmp_path / "dashboard.zip"

    app._write_dashboard_bundle(
        bundle_path=bundle_path,
        dashboard_directory=primary / "dashboards",
        uploaded_export_paths=[upload],
    )

    with zipfile.ZipFile(bundle_path) as bundle:
        names = bundle.namelist()
        assert max(map(len, names)) <= 80
        assert "mapping_chain/raw_leap_results.csv" not in names
        assert "m/raw_leap_results.csv" not in names
        assert "in/source_01.xlsx" in names
        assert "a/plotly.min.js" in names
        assert "OPEN THE DASHBOARD.html" in names
        assert "d/0/p/index.html" in names
        assert "archive_manifest.json" in names
        page = bundle.read("d/0/p/power.html").decode("utf-8")
        assert 'src="../../../a/plotly.min.js"' in page
        assert 'src="../c/power__charts.js"' in page
        assert 'href="../../1/p/power.html"' in page
        assert 'href="../../../x/p/mapping_diagnostics.html"' in page
        assert app.PLOTLY_CDN_URL not in page
        diagnostics_page = bundle.read("x/p/mapping_diagnostics.html").decode("utf-8")
        assert 'href="../s/source_to_common_esto_map.csv"' in diagnostics_page
        shortcut = bundle.read("OPEN THE DASHBOARD.html").decode("utf-8")
        assert "url=d/0/p/index.html" in shortcut
        manifest = json.loads(bundle.read("archive_manifest.json"))
        assert manifest["archive_type"] == "dashboard_review"
        assert manifest["uploaded_balance_exports"] == [
            {
                "archive_path": "in/source_01.xlsx",
                "original_filename": upload.name,
            }
        ]
        assert manifest["dashboard"]["run_diagnostics_included"] is False

    complete_path = tmp_path / "complete.zip"
    app._write_diagnostics_bundle(
        bundle_path=complete_path,
        workbook_paths=[],
        diagnostics_directory=None,
        run_directory=tmp_path,
        dashboard_directory=primary / "dashboards",
    )
    with zipfile.ZipFile(complete_path) as bundle:
        assert "m/raw_leap_results.csv" in bundle.namelist()
        manifest = json.loads(bundle.read("archive_manifest.json"))
        assert manifest["dashboard"]["run_diagnostics_included"] is True


def test_saved_dashboard_labels_use_details_then_time_to_disambiguate() -> None:
    records = [
        {
            "archive_id": "20260812T045500Z_first",
            "economy": "16_RUS",
            "scenarios": ["Target"],
            "years": "2022, 2023",
        },
        {
            "archive_id": "20260812T051015Z_second",
            "economy": "16_RUS",
            "scenarios": ["Target"],
            "years": "2022, 2023",
        },
        {
            "archive_id": "20260812T060000Z_third",
            "economy": "05_PRC",
            "scenarios": ["Reference", "Target"],
            "years": "2022",
        },
    ]

    labels = app._saved_dashboard_button_labels(records)

    assert labels == [
        "16_RUS · Target · 2022, 2023 · 12 Aug 2026 13:55:00 JST dashboard",
        "16_RUS · Target · 2022, 2023 · 12 Aug 2026 14:10:15 JST dashboard",
        "05_PRC · Reference + Target · 2022 dashboard",
    ]


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
    dashboard_archive = tmp_path / "dashboard.zip"
    workbook.write_bytes(b"workbook")
    archive.write_bytes(b"archive")
    dashboard_archive.write_bytes(b"dashboard archive")
    record = {
        "summary": "Finished",
        "status": "Complete",
        "workbooks": [str(workbook)],
        "bundle": str(archive),
        "dashboard_bundle": str(dashboard_archive),
        "finished_at": "2026-08-12 10:00 UTC",
    }

    restored = app.restore_last_run(record, [])

    assert restored[2] == [str(workbook)]
    assert restored[3] == str(archive)
    assert "Restored from 12 Aug 2026 19:00:00 JST" in restored[4]
    assert restored[5] == str(dashboard_archive)
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
        "dashboard_bundle": str(tmp_path / "removed-dashboard.zip"),
        "finished_at": "2026-08-12 10:00 UTC",
    }

    restored = app.restore_last_run(record, [])

    assert restored[2] == []
    assert restored[3] is None
    assert "cleared from the server" in restored[4]
    assert restored[5] is None


def test_refresh_restores_dashboard_only_archive_without_a_workbook(
    tmp_path,
) -> None:
    dashboard_archive = tmp_path / "dashboard.zip"
    dashboard_archive.write_bytes(b"dashboard archive")
    record = {
        "summary": "Finished",
        "status": "Complete",
        "workbooks": [],
        "bundle": "",
        "dashboard_bundle": str(dashboard_archive),
        "finished_at": "2026-08-12 10:00 UTC",
    }

    restored = app.restore_last_run(record, [])

    assert restored[2] == []
    assert restored[3] is None
    assert restored[5] == str(dashboard_archive)
