# LEAP Balance Review web app

This is a thin Gradio wrapper around the existing
`balance-review-from-export` orchestration. It is not a second implementation
of the diagnostics or workbook logic.

## Run locally

From the `leap_initialisation` repository root:

```powershell
python -m pip install -r web_app/requirements.txt
python web_app/app.py
```

Open `http://127.0.0.1:7860`.

The app accepts:

- one LEAP balance export workbook;
- one review year or a comma-separated list such as `2022,2030,2040`;
- the configured ESTO and 9th-edition source tables are used automatically.

The web interface does not expose an ESTO base-table upload. The shared
portable-release commands still accept an optional `esto_table_path` for the
desktop/portable workflow, but the web wrapper always passes `None` and uses
the configured pinned ESTO table.

It runs the diagnostics internally using the configured ESTO and 9th-edition
source tables, then returns the three-sheet balance-review workbook as the
desktop release. It also runs the dashboard workflow, displays the generated
interactive dashboard pages in the app, and offers a ZIP containing the full
dashboard folder/subfolders, workbook, diagnostics, and logs. The embedded
dashboard is fixed to the submitted economy and scenario; use the browser-local
saved archive dropdown to reopen earlier runs for comparison. Dashboard page
snapshots are kept in the user's browser, not in shared server storage, so a
public Space does not expose one user's saved dashboards to another user.
Download the current full ZIP if an external copy of the dashboard folder is
needed.

## Hugging Face deployment

The preferred deployment is a self-contained web-app repository containing an
`hf_bundle/` prepared from the three local sibling repositories. The Space then
runs from the bundled snapshot and does not need GitHub access at runtime.
Record the source commits in `hf_bundle/source_manifest.json` and refresh the
bundle locally whenever the source repositories change.

From this repository, review a bundle without copying it:

```python
from web_app.prepare_hf_bundle import prepare_hf_bundle
result = prepare_hf_bundle(dry_run=True)
print(result["manifest"])
```

After reviewing the source commits and file counts, call
`prepare_hf_bundle()` without `dry_run=True` to write the bundle into the
sibling `leap_review_web_app` repository.

The bundle should include only runtime code and required source/configuration
assets. It does not need Git history, tests, notebooks, old release builds, or
unrelated generated outputs. No bundled EXE or pre-existing diagnostics folder
is required.

For a Space whose working directory is the repository root, set the Space SDK
to Gradio and use `web_app/app.py` as the application file, or place the file
at the Space root as `app.py`.

For local development before preparing a bundle, the app expects the sibling
repositories by default:

```text
github/
  leap_initialisation/
  leap_mappings/
  leap_dashboard/
```

For another deployment layout, set `LEAP_MAPPINGS_ROOT` and
`LEAP_DASHBOARD_ROOT` before starting the app. The run preflight fails clearly
if those repositories or required source assets are absent.

See `docs/balance_review_web_app.md` for the bundle layout, refresh procedure,
commit provenance, and publication-safety requirements.

For the complete private-first Hugging Face Space creation, upload, smoke-test,
and update procedure, see
[`docs/leap_review_tools_hf_space_guide.md`](../docs/leap_review_tools_hf_space_guide.md).
