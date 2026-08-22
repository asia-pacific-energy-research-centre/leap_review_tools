from pathlib import Path

import pytest

from web_app.app import (
    ExportUpload,
    _esto_vintage_choices,
    adjust_review_year_for_vintage,
    confirm_version_comparison,
    dismiss_version_comparison,
    selected_version_uploads,
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


def test_version_prompt_confirm_and_dismiss_choose_the_expected_mode() -> None:
    assert confirm_version_comparison()[0] is True
    assert dismiss_version_comparison()[0] is False


def test_esto_vintage_picker_uses_available_maintained_releases() -> None:
    choices = _esto_vintage_choices()

    assert [value for _, value in choices] == ["2024", "2025", "2026"]
    assert adjust_review_year_for_vintage("2025", "2022, 2030") == "2023, 2030"
