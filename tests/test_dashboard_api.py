from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tools.generate_dashboard_via_api import (
    available_target,
    short_output_name,
    validate_dashboard_archive,
)
from web_app import app


def _successful_result(archive: Path) -> tuple[object, ...]:
    return (
        json.dumps({"dashboard_status": "succeeded", "economy": "01_AUS"}),
        "<p>Built the dashboard.</p>",
        [],
        None,
        "",
        None,
        [],
        str(archive),
    )


def test_dashboard_api_returns_archive_and_machine_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "dashboard.zip"
    archive.write_bytes(b"zip")
    captured: dict[str, object] = {}

    def fake_run(**kwargs: object) -> tuple[object, ...]:
        captured.update(kwargs)
        return _successful_result(archive)

    monkeypatch.setattr(app, "_run_build_serialized", fake_run)

    output, summary = app.generate_dashboard_archive_api(["input.xlsx"], "2024")

    assert output == str(archive)
    assert summary["dashboard_status"] == "succeeded"
    assert summary["api_contract"] == "generate_dashboard_archive/v1"
    assert captured["want_workbook"] is False
    assert captured["want_dashboard"] is True
    assert captured["balance_export_workbook"] == ["input.xlsx"]
    assert captured["esto_vintage_choice"] == "2024"


def test_dashboard_api_surfaces_plain_language_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app,
        "_run_build_serialized",
        lambda **_kwargs: (
            "",
            "<div><strong>Build failed:</strong> invalid export</div>",
            [],
            None,
            "",
            None,
            [],
            None,
        ),
    )

    with pytest.raises(RuntimeError, match="Build failed: invalid export"):
        app.generate_dashboard_archive_api(["bad.xlsx"], "2024")


def test_start_dashboard_api_returns_background_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_start(*_args: object, **kwargs: object) -> tuple[str, object]:
        captured.update(kwargs)
        return "job-123", object()

    monkeypatch.setattr(app, "start_run", fake_start)

    started = app.start_dashboard_archive_api(["input.xlsx"], "2024")

    assert started == {
        "api_contract": "dashboard_archive_job/v1",
        "job_id": "job-123",
        "state": "running",
    }
    assert captured["durable_api_job"] is True


def test_dashboard_status_returns_short_poll_then_completed_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started = 1_000.0
    monkeypatch.setattr(app.time, "time", lambda: 1_030.0)
    monkeypatch.setattr(
        app,
        "_job_snapshot",
        lambda _job_id: {
            "state": "running",
            "started": started,
            "message": "Step 3 of 5: Converting LEAP results.",
        },
    )

    status, archive = app.dashboard_archive_status_api("job-123")

    assert status["state"] == "running"
    assert status["elapsed_seconds"] == 30.0
    assert archive is None

    output = tmp_path / "dashboard.zip"
    output.write_bytes(b"zip")
    result = _successful_result(output)
    monkeypatch.setattr(
        app,
        "_job_snapshot",
        lambda _job_id: {
            "state": "done",
            "started": started,
            "message": "",
            "result": result,
        },
    )

    status, archive = app.dashboard_archive_status_api("job-123")

    assert status["state"] == "done"
    assert status["summary"]["dashboard_status"] == "succeeded"
    assert archive == str(output)


def test_dashboard_status_survives_worker_local_memory_loss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = app.time.time()
    job_store = tmp_path / "jobs"
    monkeypatch.setattr(app, "JOB_STATE_ROOT", job_store)
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()

    app._set_job(
        "job-cross-worker",
        durable=True,
        state="running",
        started=now,
        finished=None,
        message="Building dashboard.",
    )
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()

    running = app._job_snapshot("job-cross-worker")
    assert running is not None
    assert running["state"] == "running"

    source_archive = tmp_path / "dashboard.zip"
    source_archive.write_bytes(b"zip")
    published_result = app._publish_job_archive(
        "job-cross-worker", _successful_result(source_archive)
    )
    app._set_job(
        "job-cross-worker",
        state="done",
        finished=now + 30.0,
        message="",
        result=published_result,
    )
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()

    status, archive = app.dashboard_archive_status_api("job-cross-worker")

    assert status["state"] == "done"
    assert status["summary"]["economy"] == "01_AUS"
    assert archive == str(job_store / "job-cross-worker.zip")
    assert Path(archive).read_bytes() == b"zip"


def test_dashboard_status_treats_corrupt_persisted_record_as_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_store = tmp_path / "jobs"
    job_store.mkdir()
    (job_store / "job-corrupt.json").write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(app, "JOB_STATE_ROOT", job_store)
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()

    status, archive = app.dashboard_archive_status_api("job-corrupt")

    assert status["state"] == "unknown"
    assert archive is None


def test_dashboard_status_treats_invalid_utf8_record_as_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_store = tmp_path / "jobs"
    job_store.mkdir()
    (job_store / "job-invalid-encoding.json").write_bytes(b"\xff")
    monkeypatch.setattr(app, "JOB_STATE_ROOT", job_store)
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()

    status, archive = app.dashboard_archive_status_api("job-invalid-encoding")

    assert status["state"] == "unknown"
    assert archive is None


def test_dashboard_api_cancel_request_crosses_worker_memory_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app, "JOB_STATE_ROOT", tmp_path / "jobs")
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()
    app._set_job(
        "job-cancel",
        durable=True,
        state="running",
        started=1_000.0,
        finished=None,
        message="Building dashboard.",
    )
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()

    response = app.cancel_dashboard_archive_api("job-cancel")

    assert response["state"] == "cancel_requested"
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()
    assert app._job_cancel_requested("job-cancel") is True


def test_progress_update_cannot_overwrite_cross_worker_cancellation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app, "JOB_STATE_ROOT", tmp_path / "jobs")
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()
    app._set_job(
        "job-race",
        durable=True,
        state="running",
        started=1_000.0,
        finished=None,
        message="Building dashboard.",
    )
    with app.RUN_JOBS_LOCK:
        stale_worker_state = dict(app.RUN_JOBS["job-race"])
        app.RUN_JOBS.clear()
    app.cancel_dashboard_archive_api("job-race")
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS["job-race"] = stale_worker_state

    app._set_job("job-race", message="Later progress update.")

    persisted = app._persisted_job_snapshot("job-race")
    assert persisted is not None
    assert persisted["state"] == "cancel_requested"


def test_completion_cannot_overtake_cross_worker_cancellation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app, "JOB_STATE_ROOT", tmp_path / "jobs")
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()
    app._set_job(
        "job-finish-race",
        durable=True,
        state="running",
        started=app.time.time(),
        finished=None,
        message="Building dashboard.",
    )
    with app.RUN_JOBS_LOCK:
        stale_worker_state = dict(app.RUN_JOBS["job-finish-race"])
        app.RUN_JOBS.clear()
    app.cancel_dashboard_archive_api("job-finish-race")
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS["job-finish-race"] = stale_worker_state

    app._set_job(
        "job-finish-race",
        state="done",
        finished=app.time.time(),
        message="",
        result=_successful_result(tmp_path / "unused.zip"),
    )

    persisted = app._persisted_job_snapshot("job-finish-race")
    assert persisted is not None
    assert persisted["state"] == "cancelled"
    assert persisted["archive_path"] is None


def test_stale_active_record_becomes_failed_instead_of_running_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_store = tmp_path / "jobs"
    job_store.mkdir()
    monkeypatch.setattr(app, "JOB_STATE_ROOT", job_store)
    monkeypatch.setattr(app.time, "time", lambda: app.JOB_RETENTION_SECONDS + 100.0)
    (job_store / "job-stale.json").write_text(
        json.dumps(
            {
                "durable": True,
                "state": "running",
                "started": 1.0,
                "updated": 1.0,
                "finished": None,
                "message": "Working.",
            }
        ),
        encoding="utf-8",
    )
    with app.RUN_JOBS_LOCK:
        app.RUN_JOBS.clear()

    app._forget_stale_jobs()

    status, archive = app.dashboard_archive_status_api("job-stale")
    assert status["state"] == "failed"
    assert "worker stopped" in status["message"]
    assert archive is None


def test_legacy_stale_record_without_durable_flag_becomes_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_store = tmp_path / "jobs"
    job_store.mkdir()
    monkeypatch.setattr(app, "JOB_STATE_ROOT", job_store)
    monkeypatch.setattr(app.time, "time", lambda: app.JOB_RETENTION_SECONDS + 100.0)
    (job_store / "job-legacy.json").write_text(
        json.dumps(
            {
                "state": "running",
                "started": 1.0,
                "finished": None,
                "message": "Working.",
            }
        ),
        encoding="utf-8",
    )

    app._forget_stale_jobs()

    persisted = app._persisted_job_snapshot("job-legacy")
    assert persisted is not None
    assert persisted["state"] == "failed"
    assert persisted["durable"] is True


def test_batch_client_uses_short_non_overwriting_names(tmp_path: Path) -> None:
    workbook = Path("AUS_TGT_2022_2060_transport_corrected.xlsx")
    assert short_output_name(workbook) == "AUS_NEW_DASHBOARD.zip"
    preferred = tmp_path / "AUS_NEW_DASHBOARD.zip"
    assert available_target(tmp_path, preferred.name) == preferred
    preferred.write_bytes(b"existing")
    assert available_target(tmp_path, preferred.name) != preferred


def test_batch_client_validates_manifest_and_dashboard_index(tmp_path: Path) -> None:
    archive_path = tmp_path / "AUS_NEW_DASHBOARD.zip"
    manifest = {
        "archive_type": "dashboard_review",
        "uploaded_balance_exports": [{"original_filename": "AUS_input.xlsx"}],
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("archive_manifest.json", json.dumps(manifest))
        archive.writestr("d/0/p/index.html", "<html></html>")

    assert validate_dashboard_archive(archive_path) == manifest
