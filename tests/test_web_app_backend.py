"""Focused regression tests for the web application's backend helpers."""

from __future__ import annotations

import os
import subprocess
import sys
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
    _locked_dashboard_html,
    _write_diagnostics_bundle,
)


def test_documented_direct_file_launch_resolves_package_imports() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy; runpy.run_path('web_app/app.py')",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


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
    dashboards = dashboard / "dashboards"
    dashboards.mkdir()
    (dashboards / "index.html").write_text("<html></html>")
    (dashboards / "page.html").write_text("<html>page</html>")
    chart_bundles = dashboard / "chart_bundles"
    chart_bundles.mkdir()
    (chart_bundles / "page.js").write_text("console.log('chart');")
    (dashboard / "OPEN THE DASHBOARD.html").write_text(
        '<meta http-equiv="refresh" content="0; url=dashboards/index.html">'
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "run.log").write_text("completed")
    bundle = tmp_path / "output.zip"

    _write_diagnostics_bundle(
        bundle_path=bundle,
        workbook_paths=[workbook],
        diagnostics_directory=diagnostics,
        run_directory=run_directory,
        dashboard_directory=dashboards,
        log_directory=logs,
    )

    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {
            "workbooks/review.xlsx",
            "diagnostics/leap_balance_source_review.csv",
            "run_manifest.json",
            "dashboard/dashboards/index.html",
            "dashboard/dashboards/page.html",
            "dashboard/chart_bundles/page.js",
            "dashboard/OPEN THE DASHBOARD.html",
            "logs/run.log",
        }


def test_single_scenario_dashboard_is_pinned_and_loses_its_toggle():
    """One scenario means the other view has no data behind it."""
    page = _locked_dashboard_html("<html><body></body></html>", "Reference")

    assert ".scenario-toggle { display:none" in page
    assert 'var mode = "ref"' in page


def test_two_scenario_dashboard_keeps_its_toggle_unpinned():
    """Both scenarios uploaded: the user switches between them in place."""
    page = _locked_dashboard_html(
        "<html><body></body></html>", "Reference", allow_switching=True
    )

    assert ".scenario-toggle" not in page.split("</style>")[0]
    assert 'var mode = ""' in page
    # The renderer's own dashboard switcher points at folders this app does
    # not serve, so it stays hidden either way.
    assert ".dashboard-switcher { display:none" in page


def test_same_export_under_two_names_is_used_once(tmp_path):
    """Two names for one export are one export, not two."""
    from web_app.app import ExportUpload, duplicate_uploads, without_duplicates

    first = tmp_path / "prc.xlsx"
    second = tmp_path / "prc 1.xlsx"
    for path in (first, second):
        path.write_bytes(b"x")
    # The copy added last is the one set aside.
    os.utime(first, (1_000, 1_000))
    os.utime(second, (2_000, 2_000))
    uploads = [
        ExportUpload(path=first, economy="05_PRC", scenario="Target", years=(2022, 2060)),
        ExportUpload(path=second, economy="05_PRC", scenario="Target", years=(2022, 2060)),
    ]

    assert duplicate_uploads(uploads) == {"prc 1.xlsx": "prc.xlsx"}
    assert [upload.path.name for upload in without_duplicates(uploads)] == ["prc.xlsx"]


def test_a_second_scenario_is_not_a_duplicate(tmp_path):
    """Same economy and years, different scenario: both are wanted."""
    from web_app.app import ExportUpload, duplicate_uploads

    reference = tmp_path / "ref.xlsx"
    target = tmp_path / "tgt.xlsx"
    for path in (reference, target):
        path.write_bytes(b"x")
    uploads = [
        ExportUpload(path=reference, economy="05_PRC", scenario="Reference", years=(2022,)),
        ExportUpload(path=target, economy="05_PRC", scenario="Target", years=(2022,)),
    ]

    assert duplicate_uploads(uploads) == {}


def test_previous_results_are_disowned_when_a_run_starts():
    """A finished run's links must not read as this run's."""
    from web_app.app import lock_run_button

    finished = "<div class='result-links'><a href='/x'>Open the dashboard</a></div>"
    button, marked = lock_run_button(finished)

    assert button.interactive is False
    assert "results-superseded" in marked
    assert "previous run" in marked
    # The links stay usable; they are labelled, not withdrawn.
    assert "Open the dashboard" in marked


def test_an_empty_results_panel_is_left_alone():
    """There is nothing to disown before the first run."""
    from web_app.app import RESULTS_EMPTY_HTML, lock_run_button

    assert lock_run_button(RESULTS_EMPTY_HTML)[1] == RESULTS_EMPTY_HTML
    assert lock_run_button("")[1] == ""


def test_the_notice_is_not_stacked_by_a_second_run():
    """Two runs in a row must not leave two notices."""
    from web_app.app import lock_run_button

    once = lock_run_button("<div class='result-links'>links</div>")[1]
    twice = lock_run_button(once)[1]

    assert twice.count("superseded-note") == 1


def test_clearing_the_export_returns_one_value_per_wired_output():
    """The click handler is wired to five outputs and must return five.

    Returning more made Gradio raise, so the button that clears the export was
    the one control on the page that could not be pressed.
    """
    from web_app.app import clear_uploaded_export

    values = clear_uploaded_export()

    assert len(values) == 5
    # The file field is emptied and the merged export strip disappears until
    # another file is selected.
    assert values[0] is None
    assert values[1] == ""
