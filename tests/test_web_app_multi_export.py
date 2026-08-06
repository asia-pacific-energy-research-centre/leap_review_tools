"""What happens when more than one export is uploaded at once.

A multi-export upload is the shape the app is least able to check by eye: it
withdraws the workbook, renders one dashboard per economy, and decides per
economy whether a scenario toggle means anything. The fast tests below pin
that reasoning without touching a file; the integration test at the end runs
the whole chain, because the reasoning being right is not the same as the
chain still working.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from web_app.app import (
    ExportUpload,
    _publish_dashboard_pages,
    duplicate_uploads,
    group_by_economy,
    scenarios_for,
    without_duplicates,
)


LEAP_EXPORTS = Path(
    r"C:/Users/Work/github/leap_initialisation/data/leap balances exports"
)


def _upload(tmp_path: Path, name: str, economy: str, scenario: str) -> ExportUpload:
    path = tmp_path / name
    path.write_bytes(b"x")
    return ExportUpload(
        path=path, economy=economy, scenario=scenario, years=(2022, 2060)
    )


def test_two_economies_are_grouped_one_dashboard_each(tmp_path):
    uploads = [
        _upload(tmp_path, "aus.xlsx", "01_AUS", "Reference"),
        _upload(tmp_path, "prc.xlsx", "05_PRC", "Reference"),
    ]

    grouped = group_by_economy(uploads)

    assert list(grouped) == ["01_AUS", "05_PRC"]
    assert [len(v) for v in grouped.values()] == [1, 1]


def test_two_scenarios_for_one_economy_stay_together(tmp_path):
    """Both belong to one dashboard, which is why they are not two renders."""
    uploads = [
        _upload(tmp_path, "ref.xlsx", "01_AUS", "Reference"),
        _upload(tmp_path, "tgt.xlsx", "01_AUS", "Target"),
    ]

    grouped = group_by_economy(uploads)

    assert list(grouped) == ["01_AUS"]
    assert scenarios_for(uploads, "01_AUS") == ["Reference", "Target"]
    assert duplicate_uploads(uploads) == {}
    assert len(without_duplicates(uploads)) == 2


def _publish(tmp_path, monkeypatch, *, scenario, scenarios):
    """Publish one stub page and return its markup and index line."""
    from web_app import app

    monkeypatch.setattr(app, "DASHBOARD_SERVE_ROOT", tmp_path / "served")
    page = app._compress_dashboard_html("<html><body></body></html>")
    url = _publish_dashboard_pages(
        {"overview.html": page},
        economy="01_AUS",
        scenario=scenario,
        years="2022",
        scenarios=scenarios,
    )
    served = Path(url.split("file=")[1]).parent
    index = (served / "index.html").read_text(encoding="utf-8")
    meta = re.search(r"class='meta'>(.*?)</p>", index).group(1)
    return (served / "overview.html").read_text(encoding="utf-8"), meta


def test_an_economy_with_both_scenarios_keeps_its_toggle(tmp_path, monkeypatch):
    page, meta = _publish(
        tmp_path, monkeypatch, scenario="Reference", scenarios=["Reference", "Target"]
    )

    assert ".scenario-toggle" not in page.split("</style>")[0]
    assert 'var mode = ""' in page
    assert "Reference, Target" in meta


def test_an_economy_with_one_scenario_is_pinned_to_it(tmp_path, monkeypatch):
    """The other view would show comparators with no LEAP series behind them."""
    page, meta = _publish(tmp_path, monkeypatch, scenario="Target", scenarios=["Target"])

    assert ".scenario-toggle { display:none" in page
    assert 'var mode = "tgt"' in page
    assert "Target" in meta


def test_the_index_never_names_a_year(tmp_path, monkeypatch):
    """A dashboard draws its own range; the workbook's review year is not it."""
    _, meta = _publish(tmp_path, monkeypatch, scenario="Target", scenarios=["Target"])

    assert "2022" not in meta


@pytest.mark.integration
def test_two_exports_build_one_dashboard_each():
    """Drive the real chain with two economies at once.

    This is the path a single-export run cannot cover: the per-economy loop,
    the per-economy scenario decision, and a results panel that has to offer
    more than one link. It takes about ten minutes.
    """
    from web_app.app import build_review_from_export

    exports = [
        LEAP_EXPORTS / "01_AUS" / "3007 REF.xlsx",
        LEAP_EXPORTS / "05_PRC" / "REF 3007.xlsx",
    ]
    missing = [str(path) for path in exports if not path.is_file()]
    if missing:
        pytest.skip(f"LEAP export fixtures not on this machine: {missing}")
    if not os.environ.get("LEAP_WEB_APP_INTEGRATION"):
        pytest.skip("set LEAP_WEB_APP_INTEGRATION=1 to run the real chain")

    summary_json, _, _, _, links_html, _, archives = build_review_from_export(
        False, True, "", "", [str(path) for path in exports]
    )

    import json

    summary = json.loads(summary_json)
    assert summary["status"] == "succeeded", summary.get("error")
    # One dashboard per economy, each with its own link.
    economies = {str(item.get("economy")) for item in archives or []}
    assert economies == {"01_AUS", "05_PRC"}, economies
    assert links_html.count("result-link is-primary") == 2 or "dashboard" in links_html
