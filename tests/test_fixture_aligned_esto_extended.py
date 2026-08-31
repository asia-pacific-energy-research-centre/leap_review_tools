from __future__ import annotations

import pandas as pd
import pytest

from scripts.build_fixture_aligned_esto_extended import allocate_historical_detail


def test_allocation_preserves_placeholder_totals_and_uses_leap_shares() -> None:
    extended = pd.DataFrame(
        [
            {
                "economy": "01AUS",
                "flows": "15.02 Road",
                "products": "diesel",
                "is_subtotal": "True",
                "2021": 100.0,
                "2022": 120.0,
            },
            {
                "economy": "01AUS",
                "flows": "road_a",
                "products": "diesel",
                "is_subtotal": "False",
                "2021": 60.0,
                "2022": 60.0,
            },
            {
                "economy": "01AUS",
                "flows": "road_b",
                "products": "diesel",
                "is_subtotal": "False",
                "2021": 40.0,
                "2022": 60.0,
            },
        ]
    )
    reference = pd.Series(
        {("road_a", "diesel"): 30.0, ("road_b", "diesel"): 10.0},
        dtype=float,
    )
    specs = [
        {
            "component": "Road",
            "placeholder_flows": {"15.02 Road"},
            "leaf_flows": {"road_a", "road_b", "road_zero"},
            "leaf_pairs": {
                ("road_a", "diesel"),
                ("road_b", "diesel"),
                ("road_zero", "diesel"),
            },
        }
    ]

    allocated, audit, summaries = allocate_historical_detail(
        extended,
        reference,
        specs,
        economy="01AUS",
        years=["2021", "2022"],
        reference_year=2022,
    )

    rows = allocated.set_index(["flows", "products"])
    assert rows.loc[("road_a", "diesel"), "2021"] == pytest.approx(75.0)
    assert rows.loc[("road_b", "diesel"), "2021"] == pytest.approx(25.0)
    assert rows.loc[("road_a", "diesel"), "2022"] == pytest.approx(90.0)
    assert rows.loc[("road_b", "diesel"), "2022"] == pytest.approx(30.0)
    assert rows.loc[("road_zero", "diesel"), "2022"] == pytest.approx(0.0)
    assert audit.groupby("year")["allocated_detail_value_pj"].sum().to_dict() == {
        2021: pytest.approx(100.0),
        2022: pytest.approx(120.0),
    }
    assert summaries[0]["maximum_conservation_difference_pj"] <= 1e-12


def test_allocation_uses_latest_existing_shape_when_leap_has_no_evidence() -> None:
    extended = pd.DataFrame(
        [
            {
                "economy": "01AUS",
                "flows": "parent",
                "products": "legacy",
                "is_subtotal": "True",
                "2021": 20.0,
                "2022": 0.0,
            },
            {
                "economy": "01AUS",
                "flows": "leaf_a",
                "products": "legacy",
                "is_subtotal": "False",
                "2021": 0.0,
                "2022": 2.0,
            },
            {
                "economy": "01AUS",
                "flows": "leaf_b",
                "products": "legacy",
                "is_subtotal": "False",
                "2021": 0.0,
                "2022": 8.0,
            },
        ]
    )
    specs = [
        {
            "component": "Legacy",
            "placeholder_flows": {"parent"},
            "leaf_flows": {"leaf_a", "leaf_b"},
            "leaf_pairs": {("leaf_a", "legacy"), ("leaf_b", "legacy")},
        }
    ]

    allocated, audit, _ = allocate_historical_detail(
        extended,
        pd.Series(dtype=float),
        specs,
        economy="01AUS",
        years=["2021", "2022"],
        reference_year=2022,
    )

    rows = allocated.set_index(["flows", "products"])
    assert rows.loc[("leaf_a", "legacy"), "2021"] == pytest.approx(4.0)
    assert rows.loc[("leaf_b", "legacy"), "2021"] == pytest.approx(16.0)
    assert rows.loc[("leaf_a", "legacy"), "2022"] == pytest.approx(0.0)
    assert set(audit["allocation_method"]) == {
        "existing_historical_share_no_LEAP_evidence",
        "zero_placeholder_no_allocation",
    }
