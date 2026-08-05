from __future__ import annotations

from web_app.runtime_profile import (
    empty_runtime_profile,
    format_runtime_note,
    record_runtime_sample,
)


def test_runtime_profile_keeps_only_last_five_samples() -> None:
    profile = empty_runtime_profile()
    for value in range(1, 8):
        profile = record_runtime_sample(
            profile,
            process_group="dashboard",
            elapsed_seconds=value,
        )

    assert profile["samples_seconds"]["dashboard"] == [3.0, 4.0, 5.0, 6.0, 7.0]
    assert profile["averages_seconds"]["dashboard"] == 5.0


def test_runtime_profile_does_not_mix_process_groups() -> None:
    profile = record_runtime_sample(
        empty_runtime_profile(), process_group="workbook", elapsed_seconds=90
    )
    profile = record_runtime_sample(profile, process_group="dashboard", elapsed_seconds=240)

    assert profile["averages_seconds"]["workbook"] == 90.0
    assert profile["averages_seconds"]["dashboard"] == 240.0
    assert profile["averages_seconds"]["full_run"] is None


def test_runtime_note_identifies_hugging_face_source() -> None:
    profile = record_runtime_sample(
        empty_runtime_profile(), process_group="full_run", elapsed_seconds=366
    )
    assert format_runtime_note(profile, process_group="full_run") == (
        "Average on Hugging Face: about 6 min 06 sec (last 5 successful runs)."
    )
