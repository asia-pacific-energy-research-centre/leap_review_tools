import json
from pathlib import Path

from web_app.version_comparison import apply_version_comparison


def _figure(value: float) -> dict:
    return {
        "data": [{"name": "LEAP Target Total", "x": [2022, 2023], "y": [100, value]}],
        "layout": {"meta": {"trace_meta": [{"source_system": "LEAP", "tag": "tgt"}]}},
    }


def _dashboard(root: Path, value: float) -> None:
    (root / "chart_bundles").mkdir(parents=True)
    (root / "dashboards").mkdir()
    (root / "chart_bundles" / "supply.json").write_text(
        json.dumps({"charts": {"chart-a": _figure(value)}})
    )
    (root / "dashboards" / "supply.html").write_text(
        '<html><head></head><body><figure class="chart-card"><div data-chart-key="chart-a"></div></figure></body></html>'
    )


def test_version_comparison_adds_paired_lines_and_colour(tmp_path: Path) -> None:
    original, new = tmp_path / "original", tmp_path / "new"
    _dashboard(original, 100)
    _dashboard(new, 103)

    counts = apply_version_comparison(
        original, new, scenario="Target", green_percent=1, yellow_percent=5
    )

    figure = json.loads(
        (new / "chart_bundles" / "supply.json").read_text()
    )["charts"]["chart-a"]
    assert counts == {"green": 0, "yellow": 1, "red": 0}
    assert [trace["name"] for trace in figure["data"]] == [
        "LEAP Target Total original",
        "LEAP Target Total new",
    ]
    assert 'version-yellow' in (new / "dashboards" / "supply.html").read_text()
