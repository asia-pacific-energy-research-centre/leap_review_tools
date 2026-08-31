from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "esto_leap_gap_triage.py"
SPEC = importlib.util.spec_from_file_location("esto_leap_gap_triage", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _row(economy: str, chart_key: str, difference: float = 25.0) -> dict[str, object]:
    return {
        "economy": economy,
        "page_key": "supply",
        "chart_key": chart_key,
        "category": "detailed",
        "absolute_difference_pj": abs(difference),
        "difference_pj": difference,
    }


def test_baseline_signature_is_economy_independent_but_case_id_is_not() -> None:
    aus = _row("01_AUS", "chart__line__02_imports__07_09_lpg")
    usa = _row("20_USA", "chart__line__02_imports__07_09_lpg")

    assert MODULE.graph_signature(aus) == MODULE.graph_signature(usa)
    assert MODULE.stable_case_id(aus) != MODULE.stable_case_id(usa)
    assert MODULE.stable_case_id(aus) == MODULE.stable_case_id(dict(aus))


def test_registry_preserves_review_fields_and_marks_absent_cases() -> None:
    old_row = _row("01_AUS", "chart__line__old")
    old_key = MODULE.case_key(old_row)
    previous = {
        old_key: {
            "case_id": MODULE.stable_case_id(old_row),
            "case_key": old_key,
            "status": "confirmed_issue",
            "reviewer_notes": "Known mapping defect",
            "first_seen_run": "run-1",
            "absolute_difference_pj": "30",
            "priority": "P1",
        }
    }

    registry = MODULE.update_registry([], previous, run_id="run-2")

    assert registry[0]["current_run_state"] == "not_reproduced"
    assert registry[0]["status"] == "confirmed_issue"
    assert registry[0]["reviewer_notes"] == "Known mapping defect"


def test_registry_carries_notes_when_case_reappears() -> None:
    row = _row("20_USA", "chart__line__new", 120.0)
    key = MODULE.case_key(row)
    previous = {
        key: {
            "case_id": MODULE.stable_case_id(row),
            "case_key": key,
            "status": "investigating",
            "owner": "Analyst",
            "reviewer_notes": "Check sign",
            "first_seen_run": "run-1",
        }
    }

    registry = MODULE.update_registry([row], previous, run_id="run-2")

    assert registry[0]["current_run_state"] == "active"
    assert registry[0]["priority"] == "P0"
    assert registry[0]["status"] == "investigating"
    assert registry[0]["owner"] == "Analyst"
    assert registry[0]["first_seen_run"] == "run-1"
    assert registry[0]["last_seen_run"] == "run-2"


def test_aggregate_deduplication_is_scoped_to_economy() -> None:
    aus_a = _row("01_AUS", "chart__area__a", 30.0) | {
        "category": "aggregate",
        "esto_2022_pj": 100.0,
        "leap_2022_pj": 130.0,
    }
    aus_b = dict(aus_a) | {"chart_key": "chart__area__b"}
    usa = dict(aus_a) | {"economy": "20_USA", "chart_key": "chart__area__c"}

    selected = MODULE.deduplicate_aggregate_rows([aus_a, aus_b, usa])

    assert len(selected) == 2
    assert {row["economy"] for row in selected} == {"01_AUS", "20_USA"}


def test_extended_only_demand_pairs_excludes_base_and_transformation_rows(
    tmp_path: Path,
) -> None:
    dashboard_root = tmp_path / "dashboard" / "20USA"
    comparison_path = dashboard_root.parent / "mapping_chain" / "common_esto_comparison_data.parquet"
    comparison_path.parent.mkdir(parents=True)
    rows = [
        {
            "source_system": "ESTO",
            "year": 2022,
            "common_row_id": "ordinary-production",
            "common_flow_code": "01",
            "common_flow_label": "01 Production",
            "common_product_code": "08.01",
            "common_product_label": "08.01 Natural gas",
            "value": 10.0,
        },
        {
            "source_system": "ESTO_EXTENDED",
            "year": 2022,
            "common_row_id": "ordinary-production",
            "common_flow_code": "01",
            "common_flow_label": "01 Production",
            "common_product_code": "08.01",
            "common_product_label": "08.01 Natural gas",
            "value": 10.0,
        },
        {
            "source_system": "ESTO_EXTENDED",
            "year": 2022,
            "common_row_id": "extended-steel",
            "common_flow_code": "14.03.01.01",
            "common_flow_label": "14.03.01.01 BF-BOF",
            "common_product_code": "01.02",
            "common_product_label": "01.02 Coal",
            "value": 5.0,
        },
        {
            "source_system": "LEAP",
            "year": 2022,
            "common_row_id": "extended-steel",
            "common_flow_code": "14.03.01.01",
            "common_flow_label": "14.03.01.01 BF-BOF",
            "common_product_code": "01.02",
            "common_product_label": "01.02 Coal",
            "value": 4.0,
        },
        {
            "source_system": "ESTO_EXTENDED",
            "year": 2022,
            "common_row_id": "extended-chp",
            "common_flow_code": "09.01.02.01",
            "common_flow_label": "Coal CHP",
            "common_product_code": "01.02",
            "common_product_label": "01.02 Coal",
            "value": 7.0,
        },
    ]
    pd.DataFrame(rows).to_parquet(comparison_path, index=False)

    pairs, audit = MODULE.extended_only_demand_pairs(dashboard_root, 2022)

    assert pairs == {("14.03.01.01 BF-BOF", "01.02 Coal")}
    assert [row["common_row_id"] for row in audit] == ["extended-steel"]
    assert audit[0]["leap_value_pj"] == 4.0
    assert audit[0]["coverage_status"] == "comparable_nonzero_leap"


def test_supply_and_parent_guardrail_classification() -> None:
    assert MODULE.is_no_fix_supply(
        {"common_flow_label": "02 Imports", "category": "detailed"}
    )
    assert not MODULE.is_no_fix_supply(
        {"common_flow_label": "15.02 Road", "category": "aggregate"}
    )
    assert MODULE.is_replacement_parent_guardrail(
        {"common_flow_label": "15.02 Road", "category": "aggregate"}
    )
    assert not MODULE.is_replacement_parent_guardrail(
        {"common_flow_label": "15.02.01.01 BEV", "category": "detailed"}
    )
