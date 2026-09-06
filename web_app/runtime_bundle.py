"""Validate the generated mapping-chain bundle used by dashboard runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 on the hosted Space.
    import tomli as tomllib

MAPPING_WORKBOOK_ROLE = "outlook_mappings_master"
MAPPING_CHAIN_GENERATION_ROLE = "mapping_chain_generation_manifest"
MAPPING_CHAIN_DATA_ROLES = (
    "mapping_chain_relationships",
    "mapping_chain_esto_exact_rows",
    "mapping_chain_ninth_converted",
    "mapping_chain_common_esto_rows",
    "mapping_chain_source_to_common_map",
    "mapping_chain_esto_to_common_map",
    "mapping_chain_esto_extended_vintage_registry",
    "mapping_chain_esto_extended_2024",
    "mapping_chain_esto_extended_2025",
    "mapping_chain_esto_extended_2026",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _indexed_assets(
    assets: object,
    *,
    section: str,
) -> dict[str, dict[str, object]]:
    if not isinstance(assets, list):
        raise TypeError(f"{section} must be a list in the release manifest.")
    indexed: dict[str, dict[str, object]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise TypeError(f"{section} contains a non-table entry.")
        role = str(asset.get("role") or "").strip()
        if not role:
            continue
        if role in indexed:
            raise RuntimeError(f"{section} declares role {role!r} more than once.")
        indexed[role] = asset
    return indexed


def _resolve_asset(
    asset: Mapping[str, object],
    repository_roots: Mapping[str, Path],
    *,
    role: str,
) -> Path:
    repository = str(asset.get("repository") or "").strip()
    relative_path = str(asset.get("path") or "").strip().replace("\\", "/")
    if repository not in repository_roots:
        raise RuntimeError(
            f"Mapping bundle role {role!r} names unknown repository {repository!r}."
        )
    if not relative_path:
        raise RuntimeError(f"Mapping bundle role {role!r} has no source path.")
    path = Path(repository_roots[repository]) / Path(relative_path)
    if not path.is_file():
        raise RuntimeError(f"Mapping bundle role {role!r} is missing: {path}")
    return path


def validate_mapping_chain_bundle(
    manifest_path: Path | str,
    repository_roots: Mapping[str, Path | str],
) -> dict[str, object]:
    """Fail unless the workbook and every mapping-chain artifact are coherent.

    The release manifest pins each generated artifact. The Stage 3 manifest
    additionally identifies the mapping workbook used to generate them, which
    prevents a current workbook from being packaged with an older output set.
    """
    manifest_path = Path(manifest_path)
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    roots = {name: Path(path) for name, path in repository_roots.items()}
    config_assets = _indexed_assets(
        manifest.get("config_assets"), section="config_assets"
    )
    data_assets = _indexed_assets(manifest.get("data_assets"), section="data_assets")

    missing_roles = [
        role for role in MAPPING_CHAIN_DATA_ROLES if role not in data_assets
    ]
    if missing_roles:
        raise RuntimeError(
            "The release manifest is missing mapping-chain bundle roles: "
            + ", ".join(missing_roles)
        )
    if MAPPING_WORKBOOK_ROLE not in config_assets:
        raise RuntimeError(
            f"The release manifest is missing config role {MAPPING_WORKBOOK_ROLE!r}."
        )
    if MAPPING_CHAIN_GENERATION_ROLE not in config_assets:
        raise RuntimeError(
            "The release manifest is missing config role "
            f"{MAPPING_CHAIN_GENERATION_ROLE!r}."
        )

    actual_hashes: dict[str, str] = {}
    resolved_paths: dict[str, Path] = {}
    for role in MAPPING_CHAIN_DATA_ROLES:
        asset = data_assets[role]
        expected = str(asset.get("sha256") or "").strip().lower()
        if len(expected) != 64:
            raise RuntimeError(
                f"Mapping bundle role {role!r} has no valid SHA-256 pin."
            )
        path = _resolve_asset(asset, roots, role=role)
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"Mapping bundle role {role!r} does not match its SHA-256 pin: "
                f"expected {expected}, found {actual} at {path}"
            )
        resolved_paths[role] = path
        actual_hashes[role] = actual

    generation_asset = config_assets[MAPPING_CHAIN_GENERATION_ROLE]
    generation_expected = str(generation_asset.get("sha256") or "").strip().lower()
    if len(generation_expected) != 64:
        raise RuntimeError(
            f"Mapping bundle role {MAPPING_CHAIN_GENERATION_ROLE!r} has no valid "
            "SHA-256 pin."
        )
    generation_path = _resolve_asset(
        generation_asset,
        roots,
        role=MAPPING_CHAIN_GENERATION_ROLE,
    )
    generation_actual = _sha256(generation_path)
    if generation_actual != generation_expected:
        raise RuntimeError(
            f"Mapping bundle role {MAPPING_CHAIN_GENERATION_ROLE!r} does not match "
            f"its SHA-256 pin: expected {generation_expected}, found "
            f"{generation_actual} at {generation_path}"
        )
    actual_hashes[MAPPING_CHAIN_GENERATION_ROLE] = generation_actual

    workbook_path = _resolve_asset(
        config_assets[MAPPING_WORKBOOK_ROLE],
        roots,
        role=MAPPING_WORKBOOK_ROLE,
    )
    workbook_hash = _sha256(workbook_path)
    generation_manifest = json.loads(
        generation_path.read_text(encoding="utf-8")
    )
    generated_from = str(
        generation_manifest.get("mapping_workbook_sha256") or ""
    ).strip().lower()
    if generated_from != workbook_hash:
        raise RuntimeError(
            "The mapping-chain artifacts were generated from a different mapping "
            f"workbook: Stage 3 records {generated_from or 'no hash'}, but "
            f"{workbook_path} is {workbook_hash}. Regenerate Stage 3 before "
            "refreshing the runtime."
        )

    return {
        "generation_run_id": str(generation_manifest.get("run_id") or ""),
        "mapping_workbook_sha256": workbook_hash,
        "artifact_sha256": actual_hashes,
    }
