import json
from pathlib import Path

from web_app.version_comparison import apply_version_comparison
from web_app.version_comparison import _total_index


def test_total_index_accepts_current_renderer_scenario_tags() -> None:
    figure = {
        "data": [
            {"name": "Transport"},
            {"name": "LEAP Target total (Domestic TFC)"},
        ],
        "layout": {
            "meta": {
                "trace_meta": [
                    {
                        "source_system": "LEAP",
                        "tag": "scenario:target",
                        "scenario": "Target",
                    },
                    {
                        "source_system": "LEAP",
                        "tag": "scenario:target",
                        "scenario": "Target",
                    },
                ]
            }
        },
    }

    assert _total_index(figure, "Target") == 1


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


def test_version_comparison_adds_paired_lines_without_coloured_borders(
    tmp_path: Path,
) -> None:
    original, new = tmp_path / "original", tmp_path / "new"
    _dashboard(original, 100)
    _dashboard(new, 103)
    original_bundle_before = (original / "chart_bundles" / "supply.json").read_bytes()
    dashboard_page = new / "dashboards" / "supply.html"
    dashboard_page_before = dashboard_page.read_bytes()

    counts = apply_version_comparison(
        original, new, scenario="Target", green_percent=1, yellow_percent=5
    )

    figure = json.loads(
        (new / "chart_bundles" / "supply.json").read_text()
    )["charts"]["chart-a"]
    assert counts == {"green": 0, "yellow": 0, "red": 0}
    assert [trace["name"] for trace in figure["data"]] == [
        "LEAP Target Total — Version 1 (original)",
        "LEAP Target Total — Version 2 (new)",
    ]
    assert [trace["x"] for trace in figure["data"]] == [
        [2022, 2023],
        [2022, 2023],
    ]
    assert [trace["y"] for trace in figure["data"]] == [
        [100, 100],
        [100, 103],
    ]
    assert (original / "chart_bundles" / "supply.json").read_bytes() == original_bundle_before
    assert dashboard_page.read_bytes() == dashboard_page_before
    assert "version-green" not in dashboard_page.read_text()
    assert "version-yellow" not in dashboard_page.read_text()
    assert "version-red" not in dashboard_page.read_text()


def test_version_comparison_refuses_a_silent_no_op(tmp_path: Path) -> None:
    original, new = tmp_path / "original", tmp_path / "new"
    (original / "chart_bundles").mkdir(parents=True)
    (new / "chart_bundles").mkdir(parents=True)

    import pytest

    with pytest.raises(ValueError, match="No comparable Version 1 / Version 2"):
        apply_version_comparison(
            original, new, scenario="Target", green_percent=1, yellow_percent=5
        )


def test_version_comparison_pairs_aggregate_labels_without_the_word_total(
    tmp_path: Path,
) -> None:
    original, new = tmp_path / "original", tmp_path / "new"
    for root, value in ((original, 100), (new, 103)):
        (root / "chart_bundles").mkdir(parents=True)
        figures = {
            "area": {
                "data": [
                    {"name": "Transport", "x": [2022, 2023], "y": [40, 41]},
                    {"name": "Industry", "x": [2022, 2023], "y": [60, 62]},
                    {
                        "name": "LEAP Target (Domestic TFC)",
                        "x": [2022, 2023],
                        "y": [100, value],
                    },
                ],
                "layout": {
                    "meta": {
                        "trace_meta": [
                            {"source_system": "LEAP", "tag": "tgt"},
                            {"source_system": "LEAP", "tag": "tgt"},
                            {"source_system": "LEAP", "tag": "tgt"},
                        ]
                    }
                },
            },
            "line": {
                "data": [
                    {"name": "LEAP Target", "x": [2022, 2023], "y": [100, value]}
                ],
                "layout": {
                    "meta": {
                        "trace_meta": [{"source_system": "LEAP", "tag": "tgt"}]
                    }
                },
            },
            "categorical": {
                "data": [
                    {
                        "name": "LEAP fuel comparison",
                        "x": ["Coal", "Gas"],
                        "y": [100, value],
                    }
                ],
                "layout": {
                    "meta": {
                        "trace_meta": [{"source_system": "LEAP", "tag": "tgt"}]
                    }
                },
            },
            "components-only": {
                "data": [
                    {"name": "Natural gas", "x": [2022, 2023], "y": [30, 31]},
                    {"name": "Coal", "x": [2022, 2023], "y": [20, 19]},
                ],
                "layout": {
                    "meta": {
                        "trace_meta": [
                            {"source_system": "LEAP", "tag": "tgt"},
                            {"source_system": "LEAP", "tag": "tgt"},
                        ]
                    }
                },
            },
        }
        (root / "chart_bundles" / "all.json").write_text(
            json.dumps({"charts": figures})
        )

    apply_version_comparison(
        original, new, scenario="Target", green_percent=1, yellow_percent=5
    )

    charts = json.loads((new / "chart_bundles" / "all.json").read_text())["charts"]
    for chart_key in ("area", "line"):
        names = [trace["name"] for trace in charts[chart_key]["data"]]
        assert "LEAP Target Total — Version 1 (original)" in names
        assert "LEAP Target Total — Version 2 (new)" in names
    assert [trace["name"] for trace in charts["categorical"]["data"]] == [
        "LEAP fuel comparison — Version 1 (original)",
        "LEAP fuel comparison — Version 2 (new)",
    ]
    assert [trace["x"] for trace in charts["categorical"]["data"]] == [
        ["Coal", "Gas"],
        ["Coal", "Gas"],
    ]
    assert [trace["name"] for trace in charts["components-only"]["data"]] == [
        "Natural gas",
        "Coal",
    ]
