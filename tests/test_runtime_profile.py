from __future__ import annotations

import json

from web_app.runtime_profile import (
    OUTLIER_MIN_SAMPLES,
    SAMPLE_LIMIT,
    empty_runtime_profile,
    estimate_runtime,
    format_runtime_note,
    load_runtime_profile,
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


def test_runtime_profile_records_parallel_run_shape_metadata() -> None:
    profile = record_runtime_sample(
        empty_runtime_profile(),
        process_group="dashboard",
        elapsed_seconds=720,
        economies=2,
        version_comparison=True,
    )

    assert profile["samples_economies"]["dashboard"] == [2]
    assert profile["samples_run_kinds"]["dashboard"] == ["version_comparison"]
    assert profile["samples_years"]["dashboard"] == [None]


def test_schema_two_dashboard_history_is_not_assumed_to_be_standard(tmp_path) -> None:
    profile_path = tmp_path / "runtime.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "samples_seconds": {"dashboard": [300, 600]},
                "samples_years": {"dashboard": [None, None]},
            }
        ),
        encoding="utf-8",
    )

    profile = load_runtime_profile(profile_path)

    assert profile["samples_run_kinds"]["dashboard"] == [
        "legacy_unknown",
        "legacy_unknown",
    ]
    assert estimate_runtime(profile, process_group="dashboard")[0] is None


def test_version_comparison_runtime_uses_complete_matching_run() -> None:
    profile = record_runtime_sample(
        empty_runtime_profile(),
        process_group="dashboard",
        elapsed_seconds=600,
        version_comparison=True,
    )

    note = format_runtime_note(
        profile,
        process_group="dashboard",
        version_comparison=True,
    )

    assert "Version 1 / Version 2 comparison" in note
    assert "10 min 00 sec" in note
    assert "complete two-version dashboard comparison" in note
    assert "Different economy counts are measured separately" in note


def test_dashboard_estimates_match_economy_count_and_run_kind() -> None:
    profile = empty_runtime_profile()
    for seconds, economies, comparison in (
        (300, 1, False),
        (520, 2, False),
        (640, 1, True),
    ):
        profile = record_runtime_sample(
            profile,
            process_group="dashboard",
            elapsed_seconds=seconds,
            economies=economies,
            version_comparison=comparison,
        )

    assert estimate_runtime(
        profile, process_group="dashboard", economies=1
    )[0] == 300.0
    assert estimate_runtime(
        profile, process_group="dashboard", economies=2
    )[0] == 520.0
    assert estimate_runtime(
        profile,
        process_group="dashboard",
        economies=1,
        version_comparison=True,
    )[0] == 640.0
    assert estimate_runtime(
        profile,
        process_group="dashboard",
        economies=2,
        version_comparison=True,
    )[0] is None


def test_outlier_filter_activates_only_after_enough_comparable_runs() -> None:
    profile = empty_runtime_profile()
    normal_values = [98, 99, 100, 101, 102, 100, 99]
    for value in normal_values + [1000]:
        profile = record_runtime_sample(
            profile,
            process_group="dashboard",
            elapsed_seconds=value,
        )

    assert OUTLIER_MIN_SAMPLES == 8
    estimate, _ = estimate_runtime(profile, process_group="dashboard")
    assert estimate == sum(normal_values) / len(normal_values)

    small_profile = empty_runtime_profile()
    for value in [98, 99, 100, 101, 102, 1000, 99]:
        small_profile = record_runtime_sample(
            small_profile,
            process_group="dashboard",
            elapsed_seconds=value,
        )
    estimate, _ = estimate_runtime(small_profile, process_group="dashboard")
    assert estimate == sum([98, 99, 100, 101, 102, 1000, 99]) / 7


def test_outlier_filter_handles_zero_median_deviation() -> None:
    profile = empty_runtime_profile()
    for value in [100] * 7 + [1000]:
        profile = record_runtime_sample(
            profile,
            process_group="dashboard",
            elapsed_seconds=value,
        )

    assert estimate_runtime(profile, process_group="dashboard")[0] == 100.0


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

    profile = empty_runtime_profile()
    profile["samples_seconds"].update({
        "workbook": [200.0, 210.0, 230.0, 240.0],
        "dashboard": [260.0, 275.0],
        "full_run": [430.0],
    })
    profile["samples_years"].update({
        "workbook": [1, 1, 2, 3],
        "dashboard": [1, 1],
        "full_run": [1],
    })
    profile["samples_economies"].update({
        "workbook": [1, 1, 1, 1],
        "dashboard": [1, 1],
        "full_run": [1],
    })
    profile["samples_run_kinds"].update({
        "workbook": ["standard"] * 4,
        "dashboard": ["standard"] * 2,
        "full_run": ["standard"],
    })

    assert samples_behind_estimate(profile, process_group="workbook") == 4
    assert samples_behind_estimate(profile, process_group="dashboard") == 2
    # Composed from both, so only as well measured as the scarcer one.
    assert samples_behind_estimate(profile, process_group="full_run") == 2


def test_samples_behind_a_directly_fitted_full_run_estimate():
    """With two year counts of its own, a full run is fitted from its samples."""
    from web_app.runtime_profile import samples_behind_estimate

    profile = empty_runtime_profile()
    profile["samples_seconds"]["full_run"] = [430.0, 470.0]
    profile["samples_years"]["full_run"] = [1, 2]
    profile["samples_economies"]["full_run"] = [1, 1]
    profile["samples_run_kinds"]["full_run"] = ["standard", "standard"]

    assert samples_behind_estimate(profile, process_group="full_run") == 2


def test_no_measurements_means_no_claim():
    from web_app.runtime_profile import samples_behind_estimate

    empty = {"samples_seconds": {}, "samples_years": {}}

    assert samples_behind_estimate(empty, process_group="full_run") == 0
