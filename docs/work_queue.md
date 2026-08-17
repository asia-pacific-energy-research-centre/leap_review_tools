# LEAP Review Web App Work Queue

## Completed

- 2026-08-17: Add a read-only four-repository colleague setup audit and runbook
  covering Python dependencies, Git clones, both portable bundles, LEAP
  templates/USA balance exports, disk space, mapping-contract generation order,
  and the file-only versus Windows LEAP COM boundary.
- 2026-08-13: Preserve the complete portable dashboard bundle across browser
  snapshots, server publication and downloadable diagnostics archives, so both
  maintained comparison scopes and their mapping-diagnostics pages remain
  reachable instead of being flattened to one dashboard folder.
- 2026-08-12: Bundle and pass the published ESTO, LEAP and 9th provenance maps
  into web-dashboard renders, and distinguish omitted map files from genuine
  mapping-generation mismatches in the dashboard guide.
- 2026-08-12: Add a second, click-through image to the LEAP export guide step
  that tells users to keep model fuel groupings unchanged and choose **Fuels**
  under **Columns**.
- 2026-08-12: Clear the previous run's status when a valid new run starts so
  an obsolete failure cannot remain visible beside active progress.
- 2026-08-12: Detect upload previews made stale by a Space restart, restore the
  main chooser, and ask the user to upload the export again instead of starting
  an empty run.
- 2026-08-12: Convert every LEAP-scaled Joule-family Energy Balance unit to
  petajoules without a warning, and reject unrecognised unit families before a
  run starts with the instruction to use LEAP Units: None + Petajoule.
- 2026-08-12: Keep Run readiness server-authoritative after Gradio replaces the
  browser file input following a successful upload.
- 2026-08-12: Add a Cancel run control. Cancellation is requested immediately,
  stops later workflow stages and economies, and takes effect after the current
  workbook or dashboard stage reaches a safe boundary.
- 2026-08-12: Sequence export inspection before Run-readiness updates, place
  Cancel inside the active calculation strip, and merge native file actions
  into the parsed export's white row.
- 2026-08-12: Simplify the Results heading and document the separate browser
  dashboard and temporary server-download retention rules accurately.
- 2026-08-12: Keep browser dashboard records readable across normal app
  deployments and move plain-language backup guidance beside the controls it
  explains.
- 2026-08-12: Give complete run archives descriptive, timestamped filenames
  and remove the redundant archive explanation from Results.
- 2026-08-12: Restore the primary upload chooser immediately after **Use a
  different export** clears the merged file row.
- 2026-08-12: Label restored dashboard buttons with economy, scenarios and
  review years, adding the saved timestamp only when those details duplicate.
- 2026-08-12: Show saved-dashboard, restored-run, and archive-filename times in
  Tokyo time (`JST`), including conversion of legacy browser records stored as
  UTC; retain UTC only for opaque IDs and internal telemetry.

## Planned

- **WEBQ-001 — Migrate machine-only web/review intermediates to Parquet where
  measured.** `inventory_complete_no_code_only_candidate` (2026-08-16).
  Participate in the cross-repository storage job coordinated by
  `leap_initialisation/docs/work_queue.md` [44]. Inventory server-side source
  tables, caches, archive-staging frames, and runtime-bundle data with every
  producer/consumer. Benchmark Parquet+Zstandard against the current format;
  do not introduce pickle. Preserve uploaded/downloadable XLSX/CSV, review
  reports, browser storage, and dashboard JSON/HTML. If the server reads
  Parquet, pin and test `pyarrow` in the prepared runtime, regenerate the
  deployment bundle from committed sources, and prove manifest hashes plus
  end-to-end workbook/dashboard equivalence before removing an old format.
  The completed repository inventory found no pickle producer or disposable
  server cache: its tabular files are source/published Common ESTO contracts,
  runtime copies of those contracts, external inputs, or human-facing audit
  and workbook artifacts. Retain them until the versioned Common ESTO contract
  and every producer/consumer move atomically; do not add Parquet to this
  repository merely to create a second copy of the same contract.
- Consider moving long workbook and dashboard stages into managed child
  processes if cancellation must interrupt the current atomic stage rather than
  waiting for its safe completion.
