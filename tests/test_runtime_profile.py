from __future__ import annotations

from web_app.runtime_profile import (
    SAMPLE_LIMIT,
    estimate_runtime,
    empty_runtime_profile,
    format_runtime_note,
    record_runtime_sample,
)


def test_runtime_profile_keeps_only_the_most_recent_samples() -> None:
    """Older measurements fall off the end once the window is full."""
    profile = empty_runtime_profile()
    for value in range(1, SAMPLE_LIMIT + 3):
        profile = record_runtime_sample(
            profile,
            process_group="dashboard",
            elapsed_seconds=value,
        )

    kept = profile["samples_seconds"]["dashboard"]
    assert len(kept) == SAMPLE_LIMIT
    # The two oldest were dropped, the newest is still there.
    assert kept[0] == 3.0
    assert kept[-1] == float(SAMPLE_LIMIT + 2)
    assert profile["averages_seconds"]["dashboard"] == round(sum(kept) / len(kept), 1)


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


def test_samples_behind_a_composed_full_run_estimate():
    """A whole run quoted as workbook plus dashboard rests on those samples.

    The one full-run measurement was too thin to fit, so saying the estimate
    rests on it would name a number that was never used.
    """
    from web_app.runtime_profile import samples_behind_estimate

    profile = {
        "samples_seconds": {
            "workbook": [200.0, 210.0, 230.0, 240.0],
            "dashboard": [260.0, 275.0],
            "full_run": [430.0],
        },
        "samples_years": {
            "workbook": [1, 1, 2, 3],
            "dashboard": [1, 1],
            "full_run": [1],
        },
    }

    assert samples_behind_estimate(profile, process_group="workbook") == 4
    assert samples_behind_estimate(profile, process_group="dashboard") == 2
    # Composed from both, so only as well measured as the scarcer one.
    assert samples_behind_estimate(profile, process_group="full_run") == 2


def test_samples_behind_a_directly_fitted_full_run_estimate():
    """With two year counts of its own, a full run is fitted from its samples."""
    from web_app.runtime_profile import samples_behind_estimate

    profile = {
        "samples_seconds": {"workbook": [], "dashboard": [], "full_run": [430.0, 470.0]},
        "samples_years": {"workbook": [], "dashboard": [], "full_run": [1, 2]},
    }

    assert samples_behind_estimate(profile, process_group="full_run") == 2


def test_no_measurements_means_no_claim():
    from web_app.runtime_profile import samples_behind_estimate

    empty = {"samples_seconds": {}, "samples_years": {}}

    assert samples_behind_estimate(empty, process_group="full_run") == 0
