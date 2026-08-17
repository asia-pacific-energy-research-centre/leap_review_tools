# Colleague setup and run order

Use one parent folder containing these sibling clones:

```text
leap_initialisation/
leap_mappings/
leap_dashboard/
leap_review_tools/
```

Use the shared Python environment (currently
`C:\Users\Work\miniconda3\python.exe`) and install the initialisation
`environment.yml` if those imports are not already available. Place each
restricted Google Drive ZIP under its matching repository's `data_bundles/`
folder and run that repository's `scripts/extract_data_bundle.py`. No checksum
sidecar, manual file copy, or extra `.gitkeep` is required.

Run the read-only setup audit:

```powershell
C:\Users\Work\miniconda3\python.exe scripts\check_colleague_setup.py
```

If the repositories are elsewhere, set `LEAP_SOURCE_PARENT` first. Resolve all
`FAIL` lines. `WARN` means an optional capability or a normal next step, such
as the mapping output contract not existing before the first mapping run.

Run in this order:

1. In `leap_mappings`, run `codebase/run_mapping_pipeline.py` with no arguments.
   This uses committed mapping configuration and the bounded validation path.
   Maintainers alone should add the `generate` stage after mapping edits or use
   `--deep-validation` after checking time and disk requirements.
2. In `leap_dashboard`, run `codebase/common_esto_dashboard_workflow.py`. It
   reads the stable mapping output contract by default.
3. In `leap_initialisation`, start with the compressed projection and
   results-update preflights before any full-horizon reconciliation. LEAP COM
   import/scraping requires Windows, pywin32, LEAP installed, and the intended
   LEAP area open; file-only preparation and review do not.
4. In `leap_review_tools`, run `web_app/app.py` for live-source development.
   For deployment, refresh the runtime only from clean committed source repos,
   validate it, then copy/publish that prepared runtime.

The data ZIPs are ignored inputs and must stay outside GitHub. GitHub carries
code, configuration, documentation, and small reviewed contracts only.
