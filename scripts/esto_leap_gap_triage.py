"""Build a repeatable ESTO-versus-LEAP base-year issue queue from dashboards.

The workflow can either consume existing dashboard roots or first render LEAP
exports with the local web-app backend.  It then applies one threshold contract,
removes graph identities already represented by a baseline case set, assigns
stable case IDs, and writes standalone Plotly graphs plus a CSV case registry.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import shutil
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

DEFAULT_YEAR = 2022
DEFAULT_MIN_ABSOLUTE_PJ = 5.0
DEFAULT_MIN_PERCENT = 25.0
DEFAULT_FORCE_INCLUDE_ABSOLUTE_PJ = 20.0

EDITABLE_REGISTRY_FIELDS = (
    "status",
    "owner",
    "issue_type",
    "root_cause",
    "fix_reference",
    "reviewer_notes",
)


def decoded_values(value: object) -> list[object]:
    """Decode either normal Plotly arrays or its typed-array JSON encoding."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and "bdata" in value:
        dtype = np.dtype(str(value.get("dtype") or "f8"))
        return np.frombuffer(base64.b64decode(value["bdata"]), dtype=dtype).tolist()
    return []


def trace_value(trace: dict[str, object] | None, year: int) -> float | None:
    if trace is None:
        return None
    years = decoded_values(trace.get("x"))
    values = decoded_values(trace.get("y"))
    for index, raw_year in enumerate(years):
        try:
            matches = int(raw_year) == year
        except (TypeError, ValueError):
            matches = str(raw_year).strip() == str(year)
        if matches and index < len(values):
            try:
                return float(values[index])
            except (TypeError, ValueError):
                return None
    return None


def preferred_trace(
    traces: list[dict[str, object]], exact_names: tuple[str, ...], prefix: str
) -> dict[str, object] | None:
    by_name = {str(trace.get("name") or ""): trace for trace in traces}
    for name in exact_names:
        if name in by_name:
            return by_name[name]
    candidates = [
        trace for trace in traces if str(trace.get("name") or "").startswith(prefix)
    ]
    return candidates[0] if len(candidates) == 1 else None


def read_manifest(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["chart_key"]: row for row in csv.DictReader(handle)}


def graph_signature(row: dict[str, object]) -> str:
    """Economy-independent identity used to remove known baseline graphs."""
    return f"{row.get('page_key', '')}|{row.get('chart_key', '')}"


def case_key(row: dict[str, object]) -> str:
    """Economy-specific stable identity used by the issue registry."""
    return f"{row.get('economy', '')}|{graph_signature(row)}"


def stable_case_id(row: dict[str, object]) -> str:
    economy = "".join(character for character in str(row["economy"]) if character.isalnum())
    digest = hashlib.sha1(case_key(row).encode("utf-8")).hexdigest()[:8].upper()
    return f"ELG-{economy}-{digest}"


def priority_for(absolute_difference_pj: float) -> str:
    if absolute_difference_pj >= 100.0:
        return "P0"
    if absolute_difference_pj >= 20.0:
        return "P1"
    return "P2"


def file_fingerprint(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def chart_rows(
    economy: str,
    dashboard_root: Path,
    *,
    year: int,
    min_absolute_pj: float,
    min_percent: float,
    force_include_absolute_pj: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    manifest = read_manifest(dashboard_root / "supporting_files" / "chart_manifest.csv")
    rows: list[dict[str, object]] = []
    coverage_gaps: list[dict[str, object]] = []
    for bundle_path in sorted((dashboard_root / "chart_bundles").glob("*__charts.json")):
        charts = json.loads(bundle_path.read_text(encoding="utf-8"))["charts"]
        for chart_key, figure in charts.items():
            metadata = manifest.get(chart_key, {})
            if str(metadata.get("suppressed", "")).casefold() == "true":
                continue
            traces = figure.get("data", [])
            esto_trace = preferred_trace(
                traces, ("ESTO Historical total", "ESTO Historical"), "ESTO Historical"
            )
            leap_trace = preferred_trace(
                traces, ("LEAP Target total", "LEAP Target"), "LEAP Target"
            )
            if esto_trace is None and leap_trace is None:
                continue
            esto = trace_value(esto_trace, year)
            leap = trace_value(leap_trace, year)
            common = {
                "economy": economy,
                "chart_key": chart_key,
                "page_key": metadata.get("page_key", bundle_path.stem.split("__")[0]),
                "page_label": metadata.get("page_label", ""),
                "section_label": metadata.get("section_label", ""),
                "common_flow_label": metadata.get("common_flow_label", ""),
                "common_product_label": metadata.get("common_product_label", ""),
                "chart_type": metadata.get("chart_type", ""),
                f"esto_{year}_pj": esto,
                f"leap_{year}_pj": leap,
            }
            if esto is None or leap is None:
                coverage_gaps.append(
                    common
                    | {
                        "gap_reason": (
                            f"missing ESTO {year} value"
                            if esto is None
                            else f"missing LEAP {year} value"
                        )
                    }
                )
                continue
            difference = leap - esto
            absolute_difference = abs(difference)
            percent_difference = (
                absolute_difference / abs(esto) * 100.0 if abs(esto) > 1e-9 else None
            )
            is_large = absolute_difference >= force_include_absolute_pj or (
                absolute_difference >= min_absolute_pj
                and (percent_difference is None or percent_difference >= min_percent)
            )
            if not is_large:
                continue
            chart_type = str(metadata.get("chart_type") or "")
            category = (
                "aggregate"
                if chart_type == "stacked_area" or "__area__" in chart_key
                else "detailed"
            )
            rows.append(
                common
                | {
                    "category": category,
                    "difference_pj": difference,
                    "absolute_difference_pj": absolute_difference,
                    "percent_difference_vs_esto": percent_difference,
                    "figure": figure,
                }
            )
    rows.sort(key=lambda row: float(row["absolute_difference_pj"]), reverse=True)
    return rows, coverage_gaps


def deduplicate_aggregate_rows(rows: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Remove repeated aggregate presentations while retaining every line chart."""
    selected: list[dict[str, object]] = []
    aggregate_signatures: set[tuple[object, ...]] = set()
    for row in rows:
        if row["category"] != "aggregate":
            selected.append(row)
            continue
        value_columns = sorted(key for key in row if key.startswith(("esto_", "leap_")))
        signature = (
            row["economy"],
            row["page_key"],
            *(round(float(row[key]), 6) for key in value_columns if row[key] is not None),
        )
        if signature in aggregate_signatures:
            continue
        aggregate_signatures.add(signature)
        selected.append(row)
    return sorted(
        selected, key=lambda row: float(row["absolute_difference_pj"]), reverse=True
    )


def read_baseline_signatures(path: Path) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {graph_signature(row) for row in csv.DictReader(handle)}


def read_registry(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["case_key"]: row for row in csv.DictReader(handle)}


def update_registry(
    cases: list[dict[str, object]],
    previous: dict[str, dict[str, str]],
    *,
    run_id: str,
) -> list[dict[str, object]]:
    updated: list[dict[str, object]] = []
    active_keys: set[str] = set()
    for row in cases:
        key = case_key(row)
        active_keys.add(key)
        old = previous.get(key, {})
        registry_row = row.copy()
        registry_row.pop("figure", None)
        registry_row.update(
            {
                "case_id": old.get("case_id") or stable_case_id(row),
                "case_key": key,
                "priority": priority_for(float(row["absolute_difference_pj"])),
                "current_run_state": "active",
                "first_seen_run": old.get("first_seen_run") or run_id,
                "last_seen_run": run_id,
            }
        )
        for field in EDITABLE_REGISTRY_FIELDS:
            registry_row[field] = old.get(field, "open" if field == "status" else "")
        updated.append(registry_row)
    for key, old in previous.items():
        if key in active_keys:
            continue
        carried = dict(old)
        carried["current_run_state"] = "not_reproduced"
        updated.append(carried)
    return sorted(
        updated,
        key=lambda row: (
            row.get("current_run_state") != "active",
            {"P0": 0, "P1": 1, "P2": 2}.get(str(row.get("priority")), 3),
            -float(row.get("absolute_difference_pj") or 0),
            str(row.get("case_id") or ""),
        ),
    )


def safe_token(value: object, limit: int = 90) -> str:
    text = "".join(character if character.isalnum() else "_" for character in str(value))
    return "_".join(part for part in text.split("_") if part)[:limit] or "chart"


def write_graph(path: Path, row: dict[str, object], case_id: str, year: int) -> None:
    figure = json.loads(json.dumps(row["figure"]))
    original_title = figure.get("layout", {}).get("title", {}).get("text", "")
    esto = row[f"esto_{year}_pj"]
    leap = row[f"leap_{year}_pj"]
    gap = float(row["difference_pj"])
    figure.setdefault("layout", {}).setdefault("title", {})["text"] = (
        f"{html.escape(case_id)} — {original_title}<br><sup>{year}: ESTO {esto:.2f} PJ; "
        f"LEAP {leap:.2f} PJ; LEAP−ESTO {gap:+.2f} PJ</sup>"
    )
    figure["layout"]["autosize"] = True
    payload = json.dumps(figure, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    title = html.escape(f"{case_id} {row['page_label']} — {row['section_label']}")
    path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<script src='../assets/plotly.min.js'></script>"
        "<style>html,body,#chart{width:100%;height:100%;margin:0}body{font-family:Arial,sans-serif}"
        ".back{position:fixed;z-index:10;top:10px;left:10px;padding:7px 10px;background:#fff;"
        "border:1px solid #94a3b8;border-radius:5px;color:#0f172a;text-decoration:none}</style>"
        "</head><body><a class='back' href='../index.html'>← Case index</a><div id='chart'></div>"
        f"<script>const figure={payload};Plotly.newPlot('chart',figure.data,figure.layout,"
        "{responsive:true,displaylogo:false});</script></body></html>",
        encoding="utf-8",
    )


def write_rows_csv(path: Path, rows: list[dict[str, object]]) -> None:
    serializable = [{key: value for key, value in row.items() if key != "figure"} for row in rows]
    fields: list[str] = []
    for row in serializable:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["case_id"])
        writer.writeheader()
        writer.writerows(serializable)


def write_index(
    output: Path,
    active_cases: list[dict[str, object]],
    *,
    year: int,
    excluded_count: int,
    coverage_count: int,
) -> None:
    table_rows = []
    for row in active_cases:
        percent = row["percent_difference_vs_esto"]
        percent_text = "n/a" if percent is None else f"{float(percent):.1f}%"
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row['case_id']))}</td><td>{row['priority']}</td>"
            f"<td>{html.escape(str(row['economy']))}</td><td>{html.escape(str(row['category']))}</td>"
            f"<td>{html.escape(str(row['page_label']))}</td><td>{html.escape(str(row['section_label']))}</td>"
            f"<td>{html.escape(str(row['common_flow_label']))}</td>"
            f"<td>{html.escape(str(row['common_product_label']))}</td>"
            f"<td>{float(row[f'esto_{year}_pj']):.2f}</td>"
            f"<td>{float(row[f'leap_{year}_pj']):.2f}</td>"
            f"<td class='num'>{float(row['difference_pj']):+.2f}</td><td>{percent_text}</td>"
            f"<td><a href='{html.escape(str(row['graph_file']))}'>Open graph</a></td></tr>"
        )
    output.joinpath("index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>ESTO–LEAP issue queue</title>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>body{font:14px Arial,sans-serif;margin:28px;color:#172033}h1{margin-bottom:6px}"
        ".note{max-width:1100px;color:#475569;margin-bottom:18px}table{border-collapse:collapse;width:100%}"
        "th,td{padding:8px;border-bottom:1px solid #dbe2ea;text-align:left;vertical-align:top}"
        "th{position:sticky;top:0;background:#eef3f8}.num{font-weight:700}tr:hover{background:#f8fafc}"
        "a{color:#075ea8}</style></head><body><h1>ESTO versus LEAP — active issue queue</h1>"
        f"<p class='note'>{len(active_cases)} active {year} cases remain after removing {excluded_count} "
        "graphs already represented by the detailed-sector dummy baseline. "
        f"{coverage_count} missing-source coverage cases are listed separately. Case IDs remain stable "
        "across reruns when economy and graph identity are unchanged.</p><table><thead><tr>"
        "<th>Case</th><th>Priority</th><th>Economy</th><th>Type</th><th>Page</th><th>Section</th>"
        f"<th>Flow</th><th>Product</th><th>ESTO {year}</th><th>LEAP {year}</th>"
        "<th>LEAP−ESTO PJ</th><th>% of ESTO</th><th>Graph</th></tr></thead><tbody>"
        + "".join(table_rows)
        + "</tbody></table></body></html>",
        encoding="utf-8",
    )


def parse_assignments(values: list[str], label: str) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} must use ECONOMY=PATH: {value}")
        economy, raw_path = value.split("=", 1)
        parsed[economy.strip()] = Path(raw_path.strip()).resolve()
    return parsed


def render_exports(
    exports: dict[str, Path],
    *,
    web_app_root: Path,
    run_root: Path,
    esto_vintage: str,
    min_year: int,
    max_year: int,
) -> dict[str, Path]:
    """Render exports with the same backend and runtime used by the local web app."""
    sys.path.insert(0, str(web_app_root))
    from web_app import app as web_app  # type: ignore[import-not-found]

    esto_table = web_app._esto_table_for_vintage(esto_vintage)
    roots: dict[str, Path] = {}
    for economy, workbook in exports.items():
        economy_root = run_root / economy
        export_dir = economy_root / "selected_export"
        export_dir.mkdir(parents=True, exist_ok=True)
        local_workbook = export_dir / workbook.name
        shutil.copy2(workbook, local_workbook)
        context = web_app._build_context(economy_root)
        result = web_app.developer_launcher.run_dashboard_from_export(
            context=context,
            economy=economy,
            export_dir=export_dir,
            esto_table_path=esto_table,
            min_year=min_year,
            max_year=max_year,
            run_label="esto-leap-gap-triage",
        )
        if not result.ok:
            raise RuntimeError(f"{economy} dashboard failed: {result.error}")
        roots[economy] = Path(result.outputs["chart_bundle_directory"]).parent
    return roots


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", action="append", default=[], metavar="ECONOMY=PATH")
    parser.add_argument("--export", action="append", default=[], metavar="ECONOMY=WORKBOOK")
    parser.add_argument("--web-app-root", type=Path)
    parser.add_argument("--baseline-cases", required=True, type=Path)
    parser.add_argument("--previous-registry", type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--plotly-bundle", required=True, type=Path)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--min-absolute-pj", type=float, default=DEFAULT_MIN_ABSOLUTE_PJ)
    parser.add_argument("--min-percent", type=float, default=DEFAULT_MIN_PERCENT)
    parser.add_argument(
        "--force-include-absolute-pj",
        type=float,
        default=DEFAULT_FORCE_INCLUDE_ABSOLUTE_PJ,
    )
    parser.add_argument("--esto-vintage", default="2026")
    parser.add_argument("--min-year", type=int, default=2010)
    parser.add_argument("--max-year", type=int, default=2060)
    parser.add_argument("--run-id")
    args = parser.parse_args()

    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    dashboards = parse_assignments(args.dashboard, "--dashboard")
    exports = parse_assignments(args.export, "--export")
    if exports:
        if args.web_app_root is None:
            parser.error("--web-app-root is required when --export is used")
        dashboards.update(
            render_exports(
                exports,
                web_app_root=args.web_app_root.resolve(),
                run_root=output / "dashboard_runs",
                esto_vintage=args.esto_vintage,
                min_year=args.min_year,
                max_year=args.max_year,
            )
        )
    if not dashboards:
        parser.error("provide at least one --dashboard or --export")

    baseline_signatures = read_baseline_signatures(args.baseline_cases)
    all_large: list[dict[str, object]] = []
    coverage_gaps: list[dict[str, object]] = []
    for economy, dashboard_root in dashboards.items():
        rows, gaps = chart_rows(
            economy,
            dashboard_root,
            year=args.year,
            min_absolute_pj=args.min_absolute_pj,
            min_percent=args.min_percent,
            force_include_absolute_pj=args.force_include_absolute_pj,
        )
        all_large.extend(rows)
        coverage_gaps.extend(gaps)
    selected = deduplicate_aggregate_rows(all_large)
    excluded = [row for row in selected if graph_signature(row) in baseline_signatures]
    active = [row for row in selected if graph_signature(row) not in baseline_signatures]
    active.sort(key=lambda row: float(row["absolute_difference_pj"]), reverse=True)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    registry = update_registry(active, read_registry(args.previous_registry), run_id=run_id)
    registry_by_key = {str(row["case_key"]): row for row in registry}

    graph_directory = output / "graphs"
    asset_directory = output / "assets"
    graph_directory.mkdir(parents=True, exist_ok=True)
    asset_directory.mkdir(parents=True, exist_ok=True)
    for stale_graph in graph_directory.glob("*.html"):
        stale_graph.unlink()
    shutil.copy2(args.plotly_bundle, asset_directory / "plotly.min.js")
    for row in active:
        registry_row = registry_by_key[case_key(row)]
        case_id = str(registry_row["case_id"])
        filename = f"{case_id}_{safe_token(row['page_key'])}_{safe_token(row['chart_key'])}.html"
        row["case_id"] = case_id
        row["case_key"] = case_key(row)
        row["priority"] = registry_row["priority"]
        row["graph_file"] = f"graphs/{filename}"
        registry_row["graph_file"] = row["graph_file"]
        write_graph(graph_directory / filename, row, case_id, args.year)

    write_rows_csv(output / "active_cases.csv", active)
    write_rows_csv(output / "case_registry.csv", registry)
    write_rows_csv(output / "excluded_baseline_cases.csv", excluded)
    write_rows_csv(output / "coverage_gaps.csv", coverage_gaps)
    write_rows_csv(output / "all_large_candidates.csv", all_large)
    write_index(
        output,
        active,
        year=args.year,
        excluded_count=len(excluded),
        coverage_count=len(coverage_gaps),
    )
    summary = {
        "run_id": run_id,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "year": args.year,
        "source_exports": {
            economy: file_fingerprint(path) for economy, path in exports.items()
        },
        "dashboards": {economy: str(path) for economy, path in dashboards.items()},
        "baseline_cases": file_fingerprint(args.baseline_cases.resolve()),
        "previous_registry": (
            file_fingerprint(args.previous_registry.resolve())
            if args.previous_registry and args.previous_registry.is_file()
            else None
        ),
        "esto_vintage": args.esto_vintage,
        "large_candidates_before_deduplication": len(all_large),
        "selected_after_aggregate_deduplication": len(selected),
        "excluded_as_known_baseline_graphs": len(excluded),
        "active_cases": len(active),
        "coverage_gaps": len(coverage_gaps),
        "thresholds": {
            "minimum_absolute_pj": args.min_absolute_pj,
            "minimum_percent": args.min_percent,
            "force_include_absolute_pj": args.force_include_absolute_pj,
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
