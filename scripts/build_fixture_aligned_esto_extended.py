"""Build a test ESTO Extended table aligned to detailed LEAP fixture values.

This is a validation-only overlay. It preserves the selected ESTO Extended
vintage and replaces one economy/year's detailed-demand flow/product values
with the mapped values observed in a detailed LEAP dashboard run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--esto-extended", required=True, type=Path)
    parser.add_argument("--converted-leap", required=True, type=Path)
    parser.add_argument("--mapping-workbook", required=True, type=Path)
    parser.add_argument("--replacement-manifest", required=True, type=Path)
    parser.add_argument("--output-parquet", required=True, type=Path)
    parser.add_argument("--audit-csv", required=True, type=Path)
    parser.add_argument("--economy", default="01_AUS")
    parser.add_argument("--year", type=int, default=2022)
    args = parser.parse_args()

    extended = pd.read_parquet(args.esto_extended)
    year_column = str(args.year)
    extended[year_column] = pd.to_numeric(extended[year_column], errors="coerce")
    converted = pd.read_csv(args.converted_leap)
    mappings = active_mapping_rows(args.mapping_workbook)
    manifest = json.loads(args.replacement_manifest.read_text(encoding="utf-8"))

    detailed_leap_flows = {
        str(flow) for component in manifest for flow in component["detailed_flows"]
    }
    detailed_pairs = (
        mappings[mappings["leap_sector_name_full_path"].isin(detailed_leap_flows)][
            ["esto_flow", "esto_product"]
        ]
        .drop_duplicates()
        .rename(columns={"esto_flow": "flows", "esto_product": "products"})
    )

    evidence = converted[
        converted["economy"].eq(args.economy)
        & converted["year"].eq(args.year)
        & converted["scenario"].eq("Target")
    ].rename(
        columns={
            "target_flow": "flows",
            "target_product": "products",
            "value": "detailed_leap_value_pj",
        }
    )
    evidence = evidence[["flows", "products", "detailed_leap_value_pj"]]
    evidence = detailed_pairs.merge(evidence, on=["flows", "products"], how="left")
    evidence["detailed_leap_value_pj"] = pd.to_numeric(
        evidence["detailed_leap_value_pj"], errors="coerce"
    ).fillna(0.0)
    if evidence.duplicated(["flows", "products"]).any():
        raise ValueError("Detailed LEAP evidence contains duplicate flow/product pairs")

    economy_key = args.economy.replace("_", "")
    target = extended[extended["economy"].astype(str).eq(economy_key)].merge(
        evidence,
        on=["flows", "products"],
        how="inner",
    )
    if target.empty:
        raise ValueError("No detailed ESTO Extended rows matched the LEAP evidence")
    target["old_esto_extended_value_pj"] = pd.to_numeric(
        target[year_column], errors="coerce"
    ).fillna(0.0)
    target["difference_pj"] = (
        target["detailed_leap_value_pj"] - target["old_esto_extended_value_pj"]
    )

    value_lookup = evidence.set_index(["flows", "products"])["detailed_leap_value_pj"]
    update_mask = extended["economy"].astype(str).eq(
        economy_key
    ) & pd.MultiIndex.from_frame(extended[["flows", "products"]]).isin(
        value_lookup.index
    )
    extended.loc[update_mask, year_column] = [
        value_lookup[(flow, product)]
        for flow, product in zip(
            extended.loc[update_mask, "flows"],
            extended.loc[update_mask, "products"],
            strict=True,
        )
    ]

    args.output_parquet.parent.mkdir(parents=True, exist_ok=True)
    args.audit_csv.parent.mkdir(parents=True, exist_ok=True)
    extended.to_parquet(args.output_parquet, index=False)
    target[
        [
            "economy",
            "flows",
            "products",
            "old_esto_extended_value_pj",
            "detailed_leap_value_pj",
            "difference_pj",
        ]
    ].to_csv(args.audit_csv, index=False)
    print(
        json.dumps(
            {
                "output": str(args.output_parquet.resolve()),
                "updated_rows": int(update_mask.sum()),
                "explicit_zero_rows": int(
                    (evidence["detailed_leap_value_pj"].abs() <= 1e-12).sum()
                ),
                "maximum_absolute_change_pj": float(
                    target["difference_pj"].abs().max()
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
