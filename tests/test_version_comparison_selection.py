from pathlib import Path

import pytest

from web_app.app import (
    ExportUpload,
    _matching_version_pair,
    _esto_vintage_choices,
    adjust_review_year_for_vintage,
    confirm_version_comparison,
    selected_version_uploads,
    version_comparison_selection_update,
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


def test_only_one_matching_two_file_upload_opens_the_version_prompt() -> None:
    first = ExportUpload(Path("first.xlsx"), "01_AUS", "Target", (2022, 2060))
    second = ExportUpload(Path("second.xlsx"), "01_AUS", "Target", (2022, 2060))
    other = ExportUpload(Path("other.xlsx"), "01_AUS", "Reference", (2022, 2060))

    assert _matching_version_pair([first, second]) == [first, second]
    assert _matching_version_pair([first, other]) == []
    assert _matching_version_pair([first, second, other]) == []


def test_esto_vintage_picker_uses_available_maintained_releases() -> None:
    choices = _esto_vintage_choices()

    assert [value for _, value in choices] == ["2024", "2025", "2026"]
    assert adjust_review_year_for_vintage("2025", "2022, 2030") == "2023, 2030"
