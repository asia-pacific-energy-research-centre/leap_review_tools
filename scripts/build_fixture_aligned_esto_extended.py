"""Allocate historical ESTO totals to detailed flows using LEAP structure.

This is a validation-only overlay. For each configured detailed-demand
component and product it preserves every ordinary ESTO row, then distributes
only an ordinary parent's value across genuinely added ESTO Extended children
using the detailed LEAP shares observed in the reference year. It never copies
LEAP absolute values into ESTO or replaces an observed ESTO child row.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ZERO_TOLERANCE = 1e-12


def active_mapping_rows(path: Path) -> pd.DataFrame:
    mapping = pd.read_excel(path, sheet_name="leap_combined_esto").fillna("")
    duplicate = (
        mapping["duplicate_to_remove"].astype(str).str.casefold().isin({"true", "1"})
    )
    return mapping.loc[
        ~duplicate,
        [
            "leap_sector_name_full_path",
            "raw_leap_fuel_name",
            "esto_flow",
            "esto_product",
        ],
    ].drop_duplicates()


def target_flows_for_paths(mapping: pd.DataFrame, paths: list[str]) -> set[str]:
    return {
        str(value).strip()
        for value in mapping.loc[
            mapping["leap_sector_name_full_path"].isin(paths), "esto_flow"
        ]
        if str(value).strip()
    }


def build_component_specs(
    mapping: pd.DataFrame, manifest: list[dict[str, object]]
) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = []
    for component in manifest:
        placeholder_flows = target_flows_for_paths(
            mapping, [str(component["placeholder_branch"])]
        )
        leaf_flows = target_flows_for_paths(
            mapping, [str(value) for value in component["structural_leaves"]]
        )
        leaf_mapping_rows = mapping[
            mapping["leap_sector_name_full_path"].isin(
                [str(value) for value in component["structural_leaves"]]
            )
        ]
        leaf_pairs = {
            (str(row.esto_flow).strip(), str(row.esto_product).strip())
            for row in leaf_mapping_rows.itertuples()
            if str(row.esto_flow).strip() and str(row.esto_product).strip()
        }
        specs.append(
            {
                "component": str(component["component"]),
                "placeholder_flows": placeholder_flows,
                "leaf_flows": leaf_flows - placeholder_flows,
                "leaf_pairs": {
                    pair for pair in leaf_pairs if pair[0] not in placeholder_flows
                },
            }
        )
    return specs


def reference_leaf_values(
    converted: pd.DataFrame,
    *,
    economy: str,
    scenario: str,
    reference_year: int,
) -> pd.Series:
    selected = converted[
        converted["economy"].eq(economy)
        & converted["scenario"].eq(scenario)
        & converted["year"].eq(reference_year)
    ].copy()
    selected["value"] = pd.to_numeric(selected["value"], errors="coerce").fillna(0.0)
    return selected.groupby(["target_flow", "target_product"])["value"].sum()


def extended_only_target_pairs(path: Path) -> set[tuple[str, str]]:
    relationships = pd.read_csv(path, low_memory=False).fillna("")
    included = relationships["include_in_use_case"].astype(str).str.casefold().isin(
        {"true", "1"}
    )
    removed = relationships["remove_row"].astype(str).str.casefold().isin(
        {"true", "1"}
    )
    selected = relationships[
        relationships["source_system"].astype(str).eq("LEAP")
        & relationships["esto_dataset_scope"].astype(str).eq("ESTO_EXTENDED")
        & included
        & ~removed
    ]
    return {
        (str(row.target_flow).strip(), str(row.target_product).strip())
        for row in selected.itertuples(index=False)
        if str(row.target_flow).strip() and str(row.target_product).strip()
    }


def normalized_economy(value: object) -> str:
    return str(value).replace("_", "").strip()


def flow_code(flow: object) -> str:
    return str(flow).strip().split(" ", 1)[0]


def is_descendant_flow(flow: str, ancestor: str) -> bool:
    flow_value = flow_code(flow)
    ancestor_value = flow_code(ancestor)
    return flow_value.startswith(f"{ancestor_value}.")


def nearest_ordinary_ancestor(
    flow: str,
    product: str,
    ordinary_pairs: set[tuple[str, str]],
) -> str | None:
    candidates = [
        ancestor
        for ancestor, candidate_product in ordinary_pairs
        if candidate_product == product and is_descendant_flow(flow, ancestor)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda value: len(flow_code(value)))


def allocate_historical_detail(
    extended: pd.DataFrame,
    ordinary: pd.DataFrame,
    reference_values: pd.Series,
    component_specs: list[dict[str, object]],
    *,
    economy: str,
    years: list[str],
    reference_year: int,
    allocatable_pairs: set[tuple[str, str]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, object]]]:
    """Return an ESTO overlay, a row-level audit, and component summaries."""
    result = extended.copy()
    economy_key = normalized_economy(economy)
    ordinary_economy = ordinary[
        ordinary["economy"].map(normalized_economy).eq(economy_key)
    ].copy()
    ordinary_pairs = set(
        zip(ordinary_economy["flows"], ordinary_economy["products"], strict=True)
    )
    existing_pairs = set(
        zip(
            result.loc[
                result["economy"].map(normalized_economy).eq(economy_key), "flows"
            ],
            result.loc[
                result["economy"].map(normalized_economy).eq(economy_key), "products"
            ],
            strict=True,
        )
    )
    added_rows: list[dict[str, object]] = []
    for spec in component_specs:
        for flow, product in spec["leaf_pairs"]:
            if (flow, product) in existing_pairs:
                continue
            row: dict[str, object] = {column: "" for column in result.columns}
            row.update(
                {
                    "economy": economy,
                    "flows": flow,
                    "products": product,
                    "is_subtotal": "False",
                }
            )
            row.update({year: 0.0 for year in years})
            added_rows.append(row)
            existing_pairs.add((flow, product))
    if added_rows:
        result = pd.concat([result, pd.DataFrame(added_rows)], ignore_index=True)

    for spec in component_specs:
        leaf_pairs = set(spec["leaf_pairs"])
        leaf_pairs = (
            leaf_pairs & allocatable_pairs
            if allocatable_pairs is not None
            else leaf_pairs - ordinary_pairs
        )
        if not leaf_pairs:
            continue
        pair_index = pd.MultiIndex.from_frame(result[["flows", "products"]])
        result.loc[
            result["economy"].astype(str).eq(economy) & pair_index.isin(leaf_pairs),
            "is_subtotal",
        ] = "False"

    economy_mask = result["economy"].map(normalized_economy).eq(economy_key)
    result[years] = result[years].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    audits: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []

    for spec in component_specs:
        component = str(spec["component"])
        placeholder_flows = set(spec["placeholder_flows"])
        leaf_pairs = set(spec["leaf_pairs"])
        leaf_pairs = (
            leaf_pairs & allocatable_pairs
            if allocatable_pairs is not None
            else leaf_pairs - ordinary_pairs
        )
        if not leaf_pairs:
            summaries.append(
                {
                    "component": component,
                    "status": "ordinary_rows_preserved_no_added_children",
                    "allocated_products": 0,
                    "fallback_products": 0,
                    "maximum_conservation_difference_pj": 0.0,
                }
            )
            continue

        allocation_groups: dict[tuple[str, str], set[str]] = {}
        for detail_flow, product in sorted(leaf_pairs):
            parent_flow = nearest_ordinary_ancestor(
                detail_flow, product, ordinary_pairs
            )
            if parent_flow is None and len(placeholder_flows) == 1:
                candidate = next(iter(placeholder_flows))
                if (candidate, product) in existing_pairs:
                    parent_flow = candidate
            if parent_flow is None:
                raise ValueError(
                    f"No ordinary ESTO ancestor for {component}/{detail_flow}/{product}"
                )
            allocation_groups.setdefault((parent_flow, product), set()).add(detail_flow)

        fallback_products = 0
        allocated_products = 0
        maximum_difference = 0.0

        for (parent_flow, product), detail_flows in sorted(
            allocation_groups.items()
        ):
            parent_rows = result[
                economy_mask
                & result["flows"].eq(parent_flow)
                & result["products"].eq(product)
            ]
            if len(parent_rows) != 1:
                raise ValueError(
                    f"Expected one ESTO parent row: {component}/{parent_flow}/{product}; "
                    f"found {len(parent_rows)}"
                )
            parent_row = parent_rows.iloc[0]
            original_parent_rows = ordinary_economy[
                ordinary_economy["flows"].eq(parent_flow)
                & ordinary_economy["products"].eq(product)
            ]
            if len(original_parent_rows) != 1:
                raise ValueError(
                    f"Expected one ordinary ESTO row: {parent_flow}/{product}; "
                    f"found {len(original_parent_rows)}"
                )
            original_parent_row = original_parent_rows.iloc[0]
            for year in years:
                if float(parent_row[year]) != float(original_parent_row[year]):
                    raise ValueError(
                        f"ESTO Extended changed ordinary parent before allocation: "
                        f"{parent_flow}/{product}/{year}"
                    )

            product_row_indices = result.index[
                economy_mask
                & result["flows"].isin(detail_flows)
                & result["products"].eq(product)
            ]
            rows_by_flow = (
                result.loc[product_row_indices]
                .assign(_row_index=product_row_indices)
                .set_index("flows")
            )
            if rows_by_flow.index.duplicated().any():
                raise ValueError(f"Duplicate detailed ESTO row: {component}/{product}")

            leap_vector = pd.Series(
                {
                    flow: float(reference_values.get((flow, product), 0.0))
                    for flow in detail_flows
                },
                dtype=float,
            )
            leap_total = float(leap_vector.sum())
            absent_nonzero_flows = {
                flow
                for flow, value in leap_vector.items()
                if abs(value) > ZERO_TOLERANCE and flow not in rows_by_flow.index
            }
            if absent_nonzero_flows:
                raise ValueError(
                    f"{component}/{product} has nonzero LEAP evidence without an ESTO row: "
                    f"{sorted(absent_nonzero_flows)}"
                )
            leap_vector = leap_vector[leap_vector.index.isin(rows_by_flow.index)]
            use_leap = abs(leap_total) > ZERO_TOLERANCE
            fallback_weights: pd.Series | None = None
            if not use_leap:
                fallback_products += 1
                for fallback_year in reversed(years):
                    existing = pd.to_numeric(
                        rows_by_flow[fallback_year], errors="coerce"
                    ).fillna(0.0)
                    existing_total = float(existing.sum())
                    if abs(existing_total) > ZERO_TOLERANCE:
                        fallback_weights = existing / existing_total
                        break

            for year in years:
                parent_total = float(parent_row[year])
                if use_leap:
                    weights = leap_vector / leap_total
                    method = f"detailed_LEAP_{reference_year}_share"
                else:
                    if abs(parent_total) <= ZERO_TOLERANCE:
                        weights = pd.Series(0.0, index=rows_by_flow.index)
                        method = "zero_placeholder_no_allocation"
                    elif fallback_weights is not None:
                        weights = fallback_weights
                        method = "existing_historical_share_no_LEAP_evidence"
                    else:
                        raise ValueError(
                            f"Cannot allocate nonzero {component}/{product}/{year}: "
                            "LEAP and existing detailed ESTO both have zero evidence"
                        )

                allocated = weights * parent_total
                allocated.iloc[-1] += parent_total - float(allocated.sum())
                for flow, value in allocated.items():
                    row_index = int(rows_by_flow.loc[flow, "_row_index"])
                    old_value = float(result.at[row_index, year])
                    result.at[row_index, year] = float(value)
                    audits.append(
                        {
                            "component": component,
                            "reference_year": reference_year,
                            "year": int(year),
                            "parent_flow": parent_flow,
                            "detail_flow": flow,
                            "product": product,
                            "allocation_method": method,
                            "allocation_share": float(weights[flow]),
                            "parent_total_pj": parent_total,
                            "old_detail_value_pj": old_value,
                            "allocated_detail_value_pj": float(value),
                            "difference_pj": float(value) - old_value,
                        }
                    )

                difference = abs(float(allocated.sum()) - parent_total)
                maximum_difference = max(maximum_difference, difference)
            allocated_products += 1

        summaries.append(
            {
                "component": component,
                "status": "allocated",
                "allocated_products": allocated_products,
                "fallback_products": fallback_products,
                "maximum_conservation_difference_pj": maximum_difference,
            }
        )

    return result, pd.DataFrame(audits), summaries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--esto-extended", required=True, type=Path)
    parser.add_argument("--esto-base", required=True, type=Path)
    parser.add_argument("--converted-leap", required=True, type=Path)
    parser.add_argument("--mapping-workbook", required=True, type=Path)
    parser.add_argument("--relationships", required=True, type=Path)
    parser.add_argument("--replacement-manifest", required=True, type=Path)
    parser.add_argument("--output-parquet", required=True, type=Path)
    parser.add_argument("--audit-csv", required=True, type=Path)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--economy", default="01_AUS")
    parser.add_argument("--scenario", default="Target")
    parser.add_argument("--reference-year", type=int, default=2022)
    parser.add_argument("--historical-start-year", type=int, default=1990)
    args = parser.parse_args()

    extended = pd.read_parquet(args.esto_extended)
    ordinary = pd.read_csv(args.esto_base, low_memory=False)
    converted = pd.read_csv(args.converted_leap)
    mappings = active_mapping_rows(args.mapping_workbook)
    manifest = json.loads(args.replacement_manifest.read_text(encoding="utf-8"))
    specs = build_component_specs(mappings, manifest)
    allocatable_pairs = extended_only_target_pairs(args.relationships)
    years = [
        str(year)
        for year in range(args.historical_start_year, args.reference_year + 1)
        if str(year) in extended.columns
    ]
    if not years or years[-1] != str(args.reference_year):
        raise ValueError(
            "Requested historical range does not include the reference year"
        )

    reference = reference_leaf_values(
        converted,
        economy=args.economy,
        scenario=args.scenario,
        reference_year=args.reference_year,
    )
    economy_key = args.economy.replace("_", "")
    allocated, audit, summaries = allocate_historical_detail(
        extended,
        ordinary,
        reference,
        specs,
        economy=economy_key,
        years=years,
        reference_year=args.reference_year,
        allocatable_pairs=allocatable_pairs,
    )
    if audit.empty:
        raise ValueError("No detailed ESTO rows were allocated")

    args.output_parquet.parent.mkdir(parents=True, exist_ok=True)
    args.audit_csv.parent.mkdir(parents=True, exist_ok=True)
    allocated.to_parquet(args.output_parquet, index=False)
    audit.to_csv(args.audit_csv, index=False)
    summary = {
        "output": str(args.output_parquet.resolve()),
        "reference_year": args.reference_year,
        "historical_years": years,
        "allocated_cells": len(audit),
        "explicit_zero_cells": int(
            audit["allocated_detail_value_pj"].abs().le(ZERO_TOLERANCE).sum()
        ),
        "maximum_absolute_change_pj": float(audit["difference_pj"].abs().max()),
        "maximum_conservation_difference_pj": float(
            max(item["maximum_conservation_difference_pj"] for item in summaries)
        ),
        "components": summaries,
    }
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
