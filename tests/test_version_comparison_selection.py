from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from web_app import app as app_module
from web_app.app import (
    ExportUpload,
    _matching_version_pair,
    _run_version_comparison_pair,
    _uploads_table,
    _esto_vintage_choices,
    _default_esto_vintage,
    adjust_review_year_for_vintage,
    confirm_version_comparison,
    selected_version_uploads,
    version_comparison_selection_update,
    version_comparison_control_updates,
)


def test_selected_versions_must_share_identity() -> None:
    original = ExportUpload(Path("original.xlsx"), "01_AUS", "Target", (2023, 2024))
    new = ExportUpload(Path("new.xlsx"), "01_AUS", "Target", (2023, 2024))
    assert selected_version_uploads([original, new], "original.xlsx", "new.xlsx") == (
        original,
        new,
    )
    other = ExportUpload(Path("other.xlsx"), "01_AUS", "Target", (2022, 2024))
    with pytest.raises(ValueError, match="same economy"):
        selected_version_uploads([original, other], "original.xlsx", "other.xlsx")


def test_version_prompt_confirm_requires_two_distinct_files() -> None:
    assert confirm_version_comparison("old.xlsx", "new.xlsx")[0] is True
    assert confirm_version_comparison("same.xlsx", "same.xlsx")[0] is False
    assert "Choose different files" in version_comparison_selection_update(
        "same.xlsx", "same.xlsx"
    )[1]


def test_version_roles_are_shown_in_the_existing_upload_rows() -> None:
    original = ExportUpload(Path("original.xlsx"), "01_AUS", "Target", (2022, 2060))
    new = ExportUpload(Path("new.xlsx"), "01_AUS", "Target", (2022, 2060))

    readout = _uploads_table(
        [original, new],
        version_roles={"original.xlsx": "Version 1", "new.xlsx": "Version 2"},
    )

    assert "upload-file-name" in readout
    assert "upload-version'>Version 1" in readout
    assert "upload-version'>Version 2" in readout
    assert "01_AUS" in readout
    assert "2022–2060" in readout


def test_only_one_matching_two_file_upload_opens_the_version_prompt() -> None:
    first = ExportUpload(Path("first.xlsx"), "01_AUS", "Target", (2022, 2060))
    second = ExportUpload(Path("second.xlsx"), "01_AUS", "Target", (2022, 2060))
    other = ExportUpload(Path("other.xlsx"), "01_AUS", "Reference", (2022, 2060))

    assert _matching_version_pair([first, second]) == [first, second]
    assert _matching_version_pair([first, other]) == []
    assert _matching_version_pair([first, second, other]) == []


def test_matching_upload_selection_activates_comparison_prompt(monkeypatch) -> None:
    first = ExportUpload(Path("first.xlsx"), "01_AUS", "Target", (2022, 2060))
    second = ExportUpload(Path("second.xlsx"), "01_AUS", "Target", (2022, 2060))
    monkeypatch.setattr(app_module, "read_uploads", lambda uploads: [first, second])

    controls, original, new, compare_versions, note = (
        version_comparison_control_updates(["ignored"])
    )

    assert controls.visible is True
    assert original.value == "first.xlsx"
    assert new.value == "second.xlsx"
    assert compare_versions is False
    assert note == ""


@dataclass
class _FakeContext:
    output_root: Path
    log_root: Path


def test_version_pair_uses_isolated_roots_and_preserves_version_1(
    tmp_path: Path, monkeypatch
) -> None:
    original_file = tmp_path / "original.xlsx"
    new_file = tmp_path / "new.xlsx"
    original_file.write_bytes(b"original")
    new_file.write_bytes(b"new")
    original = ExportUpload(original_file, "01_AUS", "Target", (2022, 2060))
    new = ExportUpload(new_file, "01_AUS", "Target", (2022, 2060))
    calls = []
    original_sentinel: Path | None = None

    def fake_render(*, context, trace_only, **kwargs):
        nonlocal original_sentinel
        calls.append((context.output_root, trace_only))
        rendered_root = context.output_root / "rendered"
        (rendered_root / "chart_bundles").mkdir(parents=True)
        if trace_only:
            original_sentinel = rendered_root / "chart_bundles" / "sentinel.json"
            original_sentinel.write_text("version-1", encoding="utf-8")
            outputs = {"comparison_trace_root": str(rendered_root)}
        else:
            assert original_sentinel is not None and original_sentinel.read_text() == "version-1"
            (rendered_root / "dashboards").mkdir()
            index = rendered_root / "dashboards" / "index.html"
            index.write_text("dashboard", encoding="utf-8")
            outputs = {
                "output_root": str(context.output_root),
                "dashboard_index": str(index),
                "chart_bundle_directory": str(rendered_root / "chart_bundles"),
            }
        return SimpleNamespace(ok=True, error=None, outputs=outputs)

    seen_roots = []

    def fake_overlay(original_root, new_root, **kwargs):
        seen_roots.extend([original_root, new_root])
        assert original_sentinel is not None and original_sentinel.read_text() == "version-1"
        return {"green": 1, "yellow": 0, "red": 0}

    monkeypatch.setattr(app_module.developer_launcher, "run_dashboard_from_export", fake_render)
    monkeypatch.setattr(app_module, "apply_version_comparison", fake_overlay)

    outcome, counts, elapsed = _run_version_comparison_pair(
        context=_FakeContext(tmp_path / "output", tmp_path / "logs"),
        run_root=tmp_path / "run",
        economy="01_AUS",
        scenario="Target",
        original_upload=original,
        new_upload=new,
        esto_table_path=None,
        min_year=2022,
        max_year=2060,
        green_percent=1,
        yellow_percent=5,
    )

    assert outcome.ok
    assert counts == {"green": 1, "yellow": 0, "red": 0}
    assert [trace_only for _, trace_only in calls] == [True, False]
    assert calls[0][0] != calls[1][0]
    assert seen_roots[0] != seen_roots[1]
    assert original_sentinel is not None and original_sentinel.read_text() == "version-1"
    assert set(elapsed) == {"original", "new"}


def test_esto_vintage_picker_uses_available_maintained_releases() -> None:
    choices = _esto_vintage_choices()

    assert [value for _, value in choices] == ["2024", "2025", "2026"]
    assert _default_esto_vintage(choices) == "2024"
    assert adjust_review_year_for_vintage("2025", "2022, 2030") == "2023, 2030"
