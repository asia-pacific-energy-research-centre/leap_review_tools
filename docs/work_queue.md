# LEAP Review Web App Work Queue

## Completed

- 2026-08-12: Detect upload previews made stale by a Space restart, restore the
  main chooser, and ask the user to upload the export again instead of starting
  an empty run.
- 2026-08-12: Convert every LEAP-scaled Joule-family Energy Balance unit to
  petajoules, warn when an upload is not already None + Petajoule, and reject
  non-Joule unit families before a run starts.
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

## Planned

- Consider moving long workbook and dashboard stages into managed child
  processes if cancellation must interrupt the current atomic stage rather than
  waiting for its safe completion.
