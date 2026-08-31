# LEAP Review Web App Work Queue

## Completed

- 2026-09-01: Extend the all-years Australia validation fixture with explicit
  detailed Electricity Generation and CHP process trees. Retain every expected
  plant case, including zero-valued technologies, map each LEAP plant/fuel pair
  once to the combined ESTO Extended all-producers boundary, and validate that
  the two-source dashboard neither falls back to interim power placeholders nor
  drops or duplicates nonzero power values.

- 2026-08-31: Rebuild the all-years detailed AUS test fixture with Ninth Target
  product/year shares for the Services-versus-Residential split, populate
  Datacentres from 2023 using the Ninth `16.01.01` evidence, and keep every
  Buildings parent total conserved. Correct detailed non-road transport so the
  full calibrated domestic total is retained in 2022 while embedded positive
  international components are removed only in projection years; regenerate
  and audit the app mapping-chain outputs at the 2022/2023 boundary.

- 2026-08-31: Reconcile the all-years detailed AUS fixture at the source:
  calibrate 2022 Buildings to the ordinary ESTO Commercial/Residential split,
  represent marine and aviation bunkers as negative supply withdrawals, and
  make their detailed rows add exactly to the combined bunker boundary. Limit
  the validation ESTO overlay to mappings explicitly scoped `ESTO_EXTENDED`,
  preserving all ordinary `BOTH`-scope ESTO rows while added children conserve
  their nearest ordinary parent.

- 2026-08-31: Investigate LEAP's CSV Energy Balance export paths. Add a
  fail-closed parser for the Target/2022 fuel-column layout that restores
  stripped branch indentation only from a validated hierarchy template. The
  second 2022–2060 layout is the single `Fuels: All` total in two-decimal
  Thousand PJ, so it is explicitly rejected: it lacks the dashboard's product
  axis and rounds small detailed-road values to zero. Direct web upload remains
  unavailable pending one CSV contract with hierarchy, individual fuels,
  adequate precision, and all-year coverage.

- 2026-08-31: Add a validation-only ESTO Extended overlay that preserves each
  historical placeholder total and allocates it to detailed structural leaves
  using 2022 detailed LEAP shares. Explicit zero branches stay zero, legacy
  products without LEAP evidence retain their latest observed historical
  shape, and mapped terminal leaves are exposed as exact comparison rows.

- 2026-08-31: Restore pre-base-year ESTO history in detailed dummy dashboards
  and add standalone graphs for replacement-parent guardrails to the repeatable
  ESTO-versus-LEAP issue review bundle.

- 2026-08-31: Allow baseline-free discovery runs so the detailed dummy dataset
  can establish the corrected Extended-demand case set before that set is used
  to exclude known cases from real-export triage.

- 2026-08-31: Correct the ESTO-versus-LEAP gap queue to use provenance-backed
  ESTO Extended demand leaves rather than every graph in an Extended-capable
  dashboard; classify production/import/export as no-fix guardrails, retain
  replacement parents as conservation checks, and isolate ordinary balance
  differences from the placeholder-replacement fix queue.

- 2026-08-30: Add a repeatable ESTO-versus-LEAP base-year gap triage workflow
  that compares exact plotted traces, excludes graph identities already covered
  by a dummy detailed-sector baseline, assigns stable cross-run case IDs,
  preserves reviewer decisions in a case registry, and emits standalone graphs
  plus auditable candidate, exclusion, and coverage tables.

- 2026-08-26: Expand hosted runtime timing history to 100 measurements per
  process group, retain exact economy-count and standard/two-version run-shape
  metadata, keep Version 1 trace-only and Version 2 full component timings
  separate, and exclude median-deviation outliers only after eight comparable
  measurements exist.
- 2026-08-25: Hide Gradio 5 empty download placeholders in Results, and pass
  Version 1 / Version 2 progress plus cancellation through each sequential
  renderer so a long comparison identifies its active half and can stop at the
  renderer's safe cancellation boundaries.
- 2026-08-25: Apply Version 1 / Version 2 overlays to renderer aggregates named
  without the literal word `Total`, and offer a self-contained dashboard/data
  ZIP independently of the workbook-oriented complete-run archive.
- 2026-08-24: Disable green/yellow/red Version 1 / Version 2 chart borders
  while retaining the paired original/new comparison traces.
- 2026-08-24: Keep the two Version 1 / Version 2 selectors complementary:
  initialise the modal with an enabled **Compare versions** action, and when
  either role changes, move the other role to the remaining uploaded file.
- 2026-08-24: Complete WEBQ-003 Version 1 / Version 2 dashboard comparison:
  isolate trace-only/full output roots, align trace-only with the maintained
  default comparison scope and full chart set, add explicit paired legends and
  comparison borders, fail closed on an empty overlay, and show measured
  comparison-specific runtime wording.
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

- **WEBQ-002 — Audit coal-transformation comparison boundaries and baseline
  seeds for Coke ovens and Blast furnaces.** Australia shows material
  base-year differences for `09.08.01 Coke ovens (including own use)` and
  `09.08.02 Blast furnaces (including own use)`. Investigate separately:
  (1) whether the LEAP baseline seed reproduces the ESTO inclusive
  transformation-plus-own-use boundary; and (2) whether the Ninth-to-ESTO
  coal-products allocation preserves the appropriate scope and signs. Do not
  change baseline seed logic to compensate for a Ninth mapping discrepancy.
  Establish expected fuel-level values and conservation checks before changing
  either workflow, then regenerate the Australia dashboard to verify all three
  sources at the inclusive boundaries.
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
