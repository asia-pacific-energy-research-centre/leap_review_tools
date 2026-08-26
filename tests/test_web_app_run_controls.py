"""Regression tests for upload readiness and background-run cancellation."""

import threading
import time
from pathlib import Path

import pytest

from web_app import app
from web_app.runtime_profile import (
    empty_runtime_profile,
    load_runtime_profile,
    record_runtime_sample,
)


def test_browser_does_not_derive_readiness_from_replaced_file_input() -> None:
    """Gradio clears the browser FileList after preserving the server upload."""
    assert "fileInput.files" not in app.APP_JS
    assert "Run readiness is server-owned" in app.APP_JS


def test_browser_merges_file_actions_and_places_cancel_with_progress() -> None:
    """The visible controls belong to the rows and progress strip they affect."""
    assert "const mergeUploadControls" in app.APP_JS
    assert "parsed.appendChild(actions)" in app.APP_JS
    assert "holder.classList.toggle('is-merged-preview', hasMergedRows)" in app.APP_JS
    assert "holder.classList.add('is-merged-preview')" not in app.APP_JS
    assert "const placeCancelRun" in app.APP_JS
    assert "animation.appendChild(cancel)" in app.APP_JS


def test_runtime_update_enables_run_from_server_upload_state(monkeypatch) -> None:
    """A parsed upload and review year enable Run without browser FileList data."""
    monkeypatch.setattr(app, "_uploaded_paths", lambda value: [Path("export.xlsx")])
    monkeypatch.setattr(app, "read_uploads", lambda value: [])
    monkeypatch.setattr(app, "group_by_economy", lambda uploads: {})

    _, _, _, run_button = app.update_runtime_notes(
        "2022", False, True, ["export.xlsx"]
    )

    assert run_button.interactive is True


def test_runtime_update_uses_version_comparison_copy(monkeypatch) -> None:
    profile = record_runtime_sample(
        empty_runtime_profile(),
        process_group="dashboard",
        elapsed_seconds=600,
        version_comparison=True,
    )
    monkeypatch.setattr(app, "_hosted_runtime_profile", lambda: profile)
    monkeypatch.setattr(app, "_uploaded_paths", lambda value: [Path("export.xlsx")])
    monkeypatch.setattr(app, "read_uploads", lambda value: [])
    monkeypatch.setattr(app, "group_by_economy", lambda uploads: {})

    _, dashboard_note, calculator, _ = app.update_runtime_notes(
        "2022", False, True, ["export.xlsx"], True
    )

    assert "Version 1 / Version 2 comparison" in dashboard_note
    assert "complete two-version dashboard comparison" in dashboard_note
    assert 'data-expected="600"' in calculator


def test_app_runtime_writer_preserves_exact_run_shape(monkeypatch, tmp_path) -> None:
    profile_path = tmp_path / "runtime-profile.json"
    monkeypatch.setenv("LEAP_RUNTIME_PROFILE_PATH", str(profile_path))

    app._save_runtime_sample(
        "dashboard",
        840,
        economies=2,
        version_comparison=True,
    )

    profile = load_runtime_profile(profile_path)
    assert profile["samples_seconds"]["dashboard"] == [840.0]
    assert profile["samples_economies"]["dashboard"] == [2]
    assert profile["samples_run_kinds"]["dashboard"] == ["version_comparison"]


def test_prepare_run_resets_upload_lost_during_space_restart(tmp_path) -> None:
    """A stale parsed row becomes a clear re-upload prompt, not a build error."""
    stale_upload = tmp_path / "export.xlsx"
    stale_upload.write_bytes(b"present before restart")
    stale_upload.unlink()

    (
        upload_is_live,
        run_button,
        result_links,
        upload_update,
        readout,
        _economy_update,
        clear_button,
        add_export,
        status,
    ) = app.prepare_run([str(stale_upload)], "previous results")

    assert upload_is_live is False
    assert run_button.interactive is False
    assert result_links == "previous results"
    assert upload_update is None
    assert "Please upload this export again" in readout
    assert "app was updated" in status
    assert clear_button.visible is False
    assert add_export.visible is False


def test_prepare_run_clears_status_from_previous_failed_run(tmp_path) -> None:
    """Starting valid work removes an obsolete failure while progress is shown."""
    upload = tmp_path / "export.xlsx"
    upload.write_bytes(b"live upload")

    updates = app.prepare_run([str(upload)], "previous results")

    assert updates[0] is True
    assert str(updates[1].value).startswith("Running")
    assert updates[-1] == ""


def test_start_run_does_not_create_job_for_stale_upload() -> None:
    """The invalid branch must not start a worker that later says no file exists."""
    jobs_before = set(app.RUN_JOBS)

    job_id, cancel_button = app.start_run(
        True,
        True,
        "2022",
        "",
        ["missing.xlsx"],
        [],
        False,
    )

    assert job_id == ""
    assert cancel_button.visible is False
    assert set(app.RUN_JOBS) == jobs_before


def test_cancel_run_sets_signal_and_cancel_requested_state() -> None:
    """The Cancel action is idempotent and visible to the background worker."""
    job_id = "cancel-control-test"
    signal = threading.Event()
    app._set_job(job_id, state="running", cancel_signal=signal, message="Working")
    try:
        cancel_button, _ = app.cancel_run(job_id)
        snapshot = app._job_snapshot(job_id)

        assert signal.is_set()
        assert snapshot is not None
        assert snapshot["state"] == "cancel_requested"
        assert cancel_button.value == "Cancelling…"
        assert cancel_button.interactive is False
    finally:
        with app.RUN_JOBS_LOCK:
            app.RUN_JOBS.pop(job_id, None)


def test_build_honours_cancellation_before_starting_work() -> None:
    """Cancellation escapes the normal build-failure conversion path."""
    with pytest.raises(app.RunCancelled):
        app.build_review_from_export(
            False,
            True,
            "2022",
            "",
            [],
            cancellation_check=lambda: True,
        )


def test_background_worker_finishes_as_cancelled(monkeypatch, tmp_path) -> None:
    """A cancellation request propagates through the worker to its final state."""
    upload = tmp_path / "export.xlsx"
    upload.write_bytes(b"test")
    worker_started = threading.Event()

    def fake_build(*values, cancellation_check=None, **options):
        worker_started.set()
        for _ in range(100):
            app._raise_if_cancelled(cancellation_check)
            time.sleep(0.01)
        raise AssertionError("The worker did not observe cancellation")

    monkeypatch.setattr(app, "build_review_from_export", fake_build)
    job_id, _ = app.start_run(False, True, "2022", "", [str(upload)], [])
    try:
        assert worker_started.wait(timeout=1)
        app.cancel_run(job_id)
        deadline = time.time() + 2
        while time.time() < deadline:
            snapshot = app._job_snapshot(job_id)
            if snapshot and snapshot.get("state") == "cancelled":
                break
            time.sleep(0.02)

        assert snapshot is not None
        assert snapshot["state"] == "cancelled"
        assert snapshot["result"] is None
    finally:
        with app.RUN_JOBS_LOCK:
            app.RUN_JOBS.pop(job_id, None)
