# LEAP Review Web App Work Queue

## Completed

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

## Planned

- Consider moving long workbook and dashboard stages into managed child
  processes if cancellation must interrupt the current atomic stage rather than
  waiting for its safe completion.
