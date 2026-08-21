from pathlib import Path

import pytest

from web_app.app import ExportUpload, selected_version_uploads


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
