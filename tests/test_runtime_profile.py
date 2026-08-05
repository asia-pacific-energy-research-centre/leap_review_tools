from __future__ import annotations

from web_app.runtime_profile import (
    estimate_runtime,
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


def test_runtime_note_identifies_hugging_face_source(monkeypatch) -> None:
    """A sample taken on the Space is labelled as a hosted average."""
    monkeypatch.setenv("SPACE_ID", "owner/space")
    profile = record_runtime_sample(
        empty_runtime_profile(), process_group="full_run", elapsed_seconds=366
    )
    assert profile["source"] == "huggingface_space"
    assert format_runtime_note(profile, process_group="full_run") == (
        "Average on Hugging Face: about 6 min 06 sec."
    )


def test_local_sample_is_not_labelled_as_hosted(monkeypatch) -> None:
    """A local run must not claim its timing came from the hosted hardware."""
    monkeypatch.delenv("SPACE_ID", raising=False)
    profile = record_runtime_sample(
        empty_runtime_profile(), process_group="full_run", elapsed_seconds=366
    )
    assert profile["source"] == "local_seed"
    assert format_runtime_note(profile, process_group="full_run").startswith(
        "Typical run:"
    )


def test_workbook_estimate_separates_fixed_and_per_year_cost() -> None:
    """Two different year counts give a real marginal cost, not a guess."""
    profile = empty_runtime_profile()
    profile["samples_seconds"]["workbook"] = [200.0, 260.0]
    profile["samples_years"]["workbook"] = [1, 3]
    estimate, per_year = estimate_runtime(profile, process_group="workbook", years=5)
    assert per_year == 30.0
    assert estimate == 320.0


def test_single_year_count_offers_no_marginal_cost() -> None:
    """With one year count the split is unknowable, so none is claimed."""
    profile = empty_runtime_profile()
    profile["samples_seconds"]["workbook"] = [200.0]
    profile["samples_years"]["workbook"] = [1]
    estimate, per_year = estimate_runtime(profile, process_group="workbook", years=4)
    assert per_year is None
    assert estimate == 200.0
