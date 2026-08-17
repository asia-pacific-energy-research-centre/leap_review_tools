from pathlib import Path

from scripts.check_colleague_setup import (
    INITIALISATION_INPUTS,
    MAPPING_INPUTS,
    check_data_inputs,
)


def _touch(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_data_check_passes_bundle_inputs_and_warns_before_mapping_run(tmp_path: Path) -> None:
    initialisation = tmp_path / "leap_initialisation"
    mappings = tmp_path / "leap_mappings"
    for relative in INITIALISATION_INPUTS:
        _touch(initialisation, relative)
    for relative in MAPPING_INPUTS:
        _touch(mappings, relative)
    _touch(initialisation, "data/leap_export_templates/USA template.xlsx")
    _touch(initialisation, "data/leap balances exports/20_USA/0805 REF.xlsx")
    _touch(initialisation, "data/leap balances exports/20_USA/0805 TGT.xlsx")

    results = check_data_inputs(tmp_path)

    assert not [result for result in results if result["level"] == "FAIL"]
    assert any(
        result["level"] == "WARN" and result["check"] == "dashboard mapping contract"
        for result in results
    )


def test_data_check_reports_missing_extended_mapping_inputs(tmp_path: Path) -> None:
    results = check_data_inputs(tmp_path)

    mapping_result = next(result for result in results if result["check"] == "mapping bundle")
    assert mapping_result["level"] == "FAIL"
    assert "data/esto_extended.csv" in mapping_result["detail"]
    assert "data/temp/new leap rows.xlsx" in mapping_result["detail"]
