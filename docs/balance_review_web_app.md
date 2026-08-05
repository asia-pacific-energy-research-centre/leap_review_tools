# Balance-review web app

The `web_app/` directory contains a thin Gradio wrapper around
`codebase/functions/balance_review_workbook_builder.py`.

## Scope

The app replaces the balance-review part of the portable Windows release:

1. upload one LEAP balance export workbook;
2. enter one review year or a comma-separated year list;
3. optionally upload an ESTO base-table CSV override;
4. run the existing diagnostics workflow;
5. run the existing Python workbook builder;
6. run the existing dashboard-from-export workflow;
7. view the generated dashboard pages in the app and download the three-sheet
   `.xlsx` result plus a combined diagnostic/dashboard bundle;
8. reopen saved dashboards from previous economy/scenario runs.

The economy and scenario are **not** asked for. LEAP writes its area name into
each sheet's title row and the scenario, year, and units into the row beneath,
so the export already states them. `infer_balance_export_identity` in
`codebase/utilities/leap_balance_export_resolver.py` reads them, and the year is
the only run input a user still supplies. That function is the single source of
truth for the inference; the web app does not carry its own copy.

Economy inference matches the LEAP area name against the APEC alias table in the
same module. A leading alias wins outright — real area names open with the
economy's short code, as in `aus clean slate 29_07` — and only when no alias
leads the name are whole-word matches elsewhere considered. Either tier must
yield exactly one economy, so an ambiguous or unrecognised name is reported as
unknown rather than guessed at. That case, and only that case, reveals an
economy-code field beside the year. A workbook covering more than one scenario
is refused, because nothing in it says which scenario a review should use.

The configured pinned ESTO table is used when no override is supplied. An
override changes the active ESTO vintage for that run; the existing synthetic
reference-row rules still apply.

The optional ESTO base-table override changes the dataset compared against in
both the balance-table review and dashboard. The dashboard uses the latest year
in that ESTO dataset as its base year. Dashboard year bounds remain internal
workflow defaults rather than user-facing controls.

The embedded dashboard is intentionally a single-run view. Its generated
economy selector and Reference/Target toggle are hidden, and the submitted
economy/scenario are shown in a fixed banner. The downloadable archive retains
the complete generated dashboard directory and its chart-bundle subfolder. A
compressed snapshot of every dashboard page is saved in the user's browser via
Gradio `BrowserState`; it is not stored in shared server-side archive storage.
This lets the same browser reopen earlier economy/scenario runs without a
persistent Hugging Face volume. The full ZIP remains a download for the current
run and should be saved by the user if they need an external archive. The app
retains the three most recent browser-local snapshots and provides a clear
button for removing them from that browser.

## Interface design

The interface uses a two-step flow: add the export and pick a year, then start
the review. The upload sits on one row beside the year, because those are now
the whole of the input; a readout beneath the row shows the economy, scenario,
and available years read back out of the uploaded file, so the user can see that
the inference was right before committing to a multi-minute run. The dropzone is
styled and worded as the page's primary action, since supplying the export is
the one thing the app cannot do for the user.

Two things the export can be wrong about are checked at upload rather than at
run time: a Level 1 export, which flattens the balance and leaves nothing to
compare, and a review year the export has no sheet for. Both used to surface
several minutes into a run that could not have succeeded.

The optional ESTO override and the
technical run summary are collapsed by default so the primary path stays clear.
The ESTO disclosure states what opening it does and carries a left-hand chevron
plus an "Optional — click to open" hint, because as a bare label with a distant
triangle it read as decoration rather than a control.
Results and browser-private dashboard views are grouped separately beneath the
run controls. The responsive visual system echoes the LEAP desktop application:
a light-blue workspace, orange action accents, compact blue-grey title bars,
and crisp bordered panels, without reproducing the desktop application's dense
toolbars or navigation tree. On wide screens, the user-supplied LEAP energy
wallpaper fills only the side gutters behind a translucent wash; narrow screens
fall back to the plain light-blue background. Focused form fields remain white
and use an orange outline regardless of the browser's light or dark preference.

## Source-of-truth rule

The web app imports the existing `balance-review-from-export` orchestration from
this repository. It does not copy the diagnostics or builder into a second
implementation and it does not invoke the packaged EXE. A local run reports
the current Git commit when Git is available. A deployment must be rebuilt from
the updated source checkout whenever the workflow changes.

For local development, the full workflow uses the exact live
`leap_initialisation`, `leap_mappings`, and `leap_dashboard` checkouts through
the same developer-mode context used by the release tooling. Set
`LEAP_MAPPINGS_ROOT` and `LEAP_DASHBOARD_ROOT` when the siblings are mounted
somewhere other than the default sibling directories.

## Hugging Face deployment bundle

The preferred public deployment is a self-contained `leap_review_web_app`
repository. Hugging Face should not need access to the three local sibling
repositories at runtime. Instead, prepare a versioned runtime bundle locally
from the sibling checkouts and commit the prepared files to the web-app
repository:

```text
github/
  leap_review_web_app/
    app.py
    requirements.txt
    hf_bundle/
      leap_initialisation/
      leap_mappings/
      leap_dashboard/
      source_manifest.json
  leap_initialisation/
  leap_mappings/
  leap_dashboard/
```

The bundle should contain only the runtime closure: imported Python modules,
required configuration, source tables, mapping results, and dashboard assets.
It does not need Git history, tests, notebooks, old release builds, or
unrelated generated outputs. The app must point its repository roots at the
three directories under `hf_bundle/`, not at the local sibling paths.

`source_manifest.json` records the source commit used for each copied
repository. This preserves provenance without making the Space dependent on
GitHub links at runtime. A bundle refresh follows this sequence:

1. update the three local sibling repositories;
2. run `web_app/prepare_hf_bundle.py` from a notebook or interactive Python
   session;
3. replace `hf_bundle/` and review `source_manifest.json`;
4. run the app locally against the bundle;
5. commit and push the prepared web-app repository;
6. allow Hugging Face to rebuild the Space.

The preparation step must reject missing sibling repositories and must not
silently fall back to stale files. Before publishing, verify that every copied
file is safe to redistribute: a public GitHub repository containing the bundle
also publishes the copied code and data. Keep the Space private or exclude the
affected files if any source material is confidential or has incompatible
redistribution terms.

The live-sibling layout remains the preferred development and debugging mode;
the prepared bundle is the preferred deployment and release mode.

The preparation function defaults to the sibling layout above and writes to
`../leap_review_web_app/hf_bundle/`. It supports a dry run for reviewing source
commits and file counts before copying:

```python
from web_app.prepare_hf_bundle import prepare_hf_bundle

result = prepare_hf_bundle(dry_run=True)
print(result["manifest"])
```

After reviewing the dry run, call `prepare_hf_bundle()` without `dry_run=True`.
By default it refuses dirty source repositories so the bundle manifest points
to committed source. `allow_dirty_sources=True` is available only for a local
experiment and should not be used for a public release.

## Parity notes

The user guide (`docs/leap_review_tools_user_guide.md`) already promised that the
tools read the workbook to work out its scenario when a filename does not say.
The web app takes that further: the filename convention exists so the CLI can
pick a file out of a folder, and a user who has uploaded one file has already
made that choice, so nothing about the export needs restating. The guide's other
two recurring points — that a Level 1 export cannot be compared, and that a red
error cell is a disagreement rather than a verdict on LEAP — are surfaced in the
app at the upload step and beside the results respectively.

The web flow preserves the main guided-flow behavior: one export is enough,
multiple review years can be entered as `2022,2030,2040`, each requested year
produces its own workbook, the optional ESTO table is passed to both diagnostics
and dashboard generation, and dashboard year bounds are configurable.

The old CLI-only `info`, `list`, `selfcheck`, repository-update, and
existing-diagnostics/comparison-data escape-hatch commands are not separate web
buttons. Repository preflight runs automatically at submission, and the web
flow intentionally starts from a LEAP export rather than requiring users to
manage diagnostic folders or large precomputed comparison files.
