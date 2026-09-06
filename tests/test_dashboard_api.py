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
    monkeypatch.setattr(app, "start_run", lambda *_args, **_kwargs: ("job-123", object()))

    started = app.start_dashboard_archive_api(["input.xlsx"], "2024")

    assert started == {
        "api_contract": "dashboard_archive_job/v1",
        "job_id": "job-123",
        "state": "running",
    }


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
