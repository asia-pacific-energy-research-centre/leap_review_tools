from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from web_app.runtime_bundle import (
    MAPPING_CHAIN_DATA_ROLES,
    MAPPING_CHAIN_GENERATION_ROLE,
    validate_mapping_chain_bundle,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture(tmp_path: Path) -> tuple[Path, dict[str, Path], dict[str, Path]]:
    mappings_root = tmp_path / "leap_mappings"
    workbook = mappings_root / "config" / "outlook_mappings_master.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"current mapping workbook")

    paths: dict[str, Path] = {}
    for role in MAPPING_CHAIN_DATA_ROLES:
        path = mappings_root / "artifacts" / f"{role}.dat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(role.encode("utf-8"))
        paths[role] = path

    generation_path = mappings_root / "artifacts" / "stage3_run_manifest.json"
    generation_path.write_text(
        json.dumps(
            {
                "run_id": "fixture-stage3",
                "mapping_workbook_sha256": _sha256(workbook),
            }
        ),
        encoding="utf-8",
    )
    paths[MAPPING_CHAIN_GENERATION_ROLE] = generation_path

    data_blocks = []
    for role in MAPPING_CHAIN_DATA_ROLES:
        path = paths[role]
        relative = path.relative_to(mappings_root).as_posix()
        data_blocks.append(
            "\n".join(
                (
                    "[[data_assets]]",
                    'repository = "leap_mappings"',
                    f'path = "{relative}"',
                    f'role = "{role}"',
                    f'sha256 = "{_sha256(path)}"',
                )
            )
        )
    manifest_path = tmp_path / "portable_release_manifest.toml"
    manifest_path.write_text(
        "\n".join(
            (
                "[[config_assets]]",
                'repository = "leap_mappings"',
                'path = "config/outlook_mappings_master.xlsx"',
                'role = "outlook_mappings_master"',
                "[[config_assets]]",
                'repository = "leap_mappings"',
                'path = "artifacts/stage3_run_manifest.json"',
                'role = "mapping_chain_generation_manifest"',
                f'sha256 = "{_sha256(generation_path)}"',
                *data_blocks,
            )
        ),
        encoding="utf-8",
    )
    return manifest_path, {"leap_mappings": mappings_root}, paths


def test_mapping_chain_bundle_accepts_one_pinned_generation(tmp_path: Path) -> None:
    manifest_path, roots, _ = _write_fixture(tmp_path)

    result = validate_mapping_chain_bundle(manifest_path, roots)

    assert result["generation_run_id"] == "fixture-stage3"
    assert set(result["artifact_sha256"]) == {
        *MAPPING_CHAIN_DATA_ROLES,
        MAPPING_CHAIN_GENERATION_ROLE,
    }


def test_mapping_chain_bundle_rejects_a_stale_artifact(tmp_path: Path) -> None:
    manifest_path, roots, paths = _write_fixture(tmp_path)
    paths["mapping_chain_relationships"].write_bytes(b"stale relationships")

    with pytest.raises(RuntimeError, match="mapping_chain_relationships.*SHA-256"):
        validate_mapping_chain_bundle(manifest_path, roots)


def test_mapping_chain_bundle_rejects_a_newer_workbook(tmp_path: Path) -> None:
    manifest_path, roots, _ = _write_fixture(tmp_path)
    workbook = roots["leap_mappings"] / "config" / "outlook_mappings_master.xlsx"
    workbook.write_bytes(b"newer workbook")

    with pytest.raises(RuntimeError, match="generated from a different mapping workbook"):
        validate_mapping_chain_bundle(manifest_path, roots)
