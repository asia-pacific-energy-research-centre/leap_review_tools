"""Regression tests for guarded LEAP Energy Balance CSV parsing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MAPPINGS_ROOT = REPO_ROOT / "runtime" / "leap_mappings"


def _run_mapping_parser(script: str, *arguments: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(MAPPINGS_ROOT)
    return subprocess.run(
        [sys.executable, "-c", script, *(str(path) for path in arguments)],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_balance(path: Path, *, indented: bool, mismatch: bool = False) -> None:
    child = "   Child plant" if indented else "Child plant"
    demand = "  Road" if indented else "Road"
    electricity = "    Electricity" if indented else "Electricity"
    if mismatch:
        demand = f"{demand} changed"
    path.write_text(
        "\n".join(
            [
                '"Energy Balance for Area AUS test model""""",,,',
                '"Scenario: Target, Year: 2022, Units: Petajoule",,,',
                ",Electricity,Natural gas,Total",
                "Production,-,10,10",
                f"{child},2,-,2",
                "Parent plant,2,-,2",
                "Total Transformation,2,-,2",
                "Demand,3,4,7",
                f"{demand},3,4,7",
                f"{electricity},3,-,3",
                "Total Final Energy Demand,3,4,7",
                "Unmet Requirements,-,-,-",
            ]
        ),
        encoding="utf-8",
    )


def test_csv_without_hierarchy_fails_closed(tmp_path: Path) -> None:
    csv_path = tmp_path / "balance.csv"
    _write_balance(csv_path, indented=False)

    result = _run_mapping_parser(
        "from pathlib import Path; import sys; "
        "from codebase.mapping_tools.parse_leap_balance_export import parse_leap_balance_csv; "
        "parse_leap_balance_csv(Path(sys.argv[1]), economy_override='01_AUS')",
        csv_path,
    )

    assert result.returncode != 0
    assert "no leading-space hierarchy" in result.stderr
    assert "cannot be reconstructed unambiguously" in result.stderr


def test_all_years_fuel_aggregate_is_rejected_clearly(tmp_path: Path) -> None:
    csv_path = tmp_path / "all_years.csv"
    csv_path.write_text(
        '"Energy Balance for Area AUS test model"\n'
        '"Fuels: All, Scenario: Target, Units: Thousand Petajoule"\n'
        '"",2022,2023,2024\n'
        "Production,18.18,18.28,18.27\n",
        encoding="utf-8",
    )

    result = _run_mapping_parser(
        "from pathlib import Path; import sys; "
        "from codebase.mapping_tools.parse_leap_balance_export import parse_leap_balance_csv; "
        "parse_leap_balance_csv(Path(sys.argv[1]), economy_override='01_AUS')",
        csv_path,
    )

    assert result.returncode != 0
    assert "individual fuel/product axis" in result.stderr
    assert "2022-2024" in result.stderr


def test_csv_template_restores_dashboard_long_contract(tmp_path: Path) -> None:
    csv_path = tmp_path / "balance.csv"
    template_path = tmp_path / "hierarchy.csv"
    _write_balance(csv_path, indented=False)
    _write_balance(template_path, indented=True)

    result = _run_mapping_parser(
        "from pathlib import Path; import json,sys; "
        "from codebase.mapping_tools.parse_leap_balance_export import parse_leap_balance_csv; "
        "d=parse_leap_balance_csv(Path(sys.argv[1]), economy_override='01_AUS', "
        "hierarchy_template_path=Path(sys.argv[2])); "
        "print(json.dumps({'rows':len(d),'flows':sorted(d.leap_flow.unique().tolist()),"
        "'years':sorted(d.year.unique().tolist()),'scenarios':sorted(d.scenario.unique().tolist()),"
        "'production_gas':float(d[(d.leap_flow=='Production') & "
        "(d.leap_product=='Natural gas')].value.iloc[0])}))",
        csv_path,
        template_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["rows"] == 18
    assert "Parent plant/Child plant" in payload["flows"]
    assert "Demand/Road/Electricity" in payload["flows"]
    assert payload["years"] == [2022]
    assert payload["scenarios"] == ["Target"]
    assert payload["production_gas"] == 10.0


def test_csv_template_rejects_changed_model_structure(tmp_path: Path) -> None:
    csv_path = tmp_path / "balance.csv"
    template_path = tmp_path / "hierarchy.csv"
    _write_balance(csv_path, indented=False)
    _write_balance(template_path, indented=True, mismatch=True)

    result = _run_mapping_parser(
        "from pathlib import Path; import sys; "
        "from codebase.mapping_tools.parse_leap_balance_export import parse_leap_balance_csv; "
        "parse_leap_balance_csv(Path(sys.argv[1]), economy_override='01_AUS', "
        "hierarchy_template_path=Path(sys.argv[2]))",
        csv_path,
        template_path,
    )

    assert result.returncode != 0
    assert "different balance-row labels" in result.stderr
