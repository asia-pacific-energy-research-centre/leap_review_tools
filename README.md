# LEAP review tools

The web app that turns one LEAP Energy Balance export into a balance-review
workbook and a comparison dashboard. This repository is the home of the tool
itself: the interface, its guide, and the script that assembles a runnable
copy.

## What lives here, and what does not

The analysis does **not** live here. It stays in the three source
repositories, which remain the single source of truth:

| Repository | Provides |
|---|---|
| `leap_initialisation` | diagnostics, the workbook builder, export inference, the run orchestration |
| `leap_mappings` | the mapping chain between LEAP, ESTO and the 9th Outlook |
| `leap_dashboard` | the dashboard renderer |

This repository holds the front end and the script that assembles the runtime
snapshot used by the deployment repository. The analysis remains in those
three source repositories; the prepared runtime is an explicit, reviewable
copy rather than a live link.

```text
web_app/            the Gradio app, its guide overlay and assets
scripts/            refresh_runtime.py — pulls the runtime closure in
docs/               design notes, the user guide, the guide prototype
runtime/            generated, git-ignored: the pulled closure
```

## Running it

Two modes, and the app picks between them itself.

**Development** — with `leap_initialisation`, `leap_mappings` and
`leap_dashboard` checked out beside this repository, just run it. Each source
repository is read from its live checkout, so a change there is picked up
immediately. A prepared `runtime/` takes precedence when it exists; point
`LEAP_RUNTIME_ROOT` at an empty/nonexistent location when live-source testing
is required:

```bash
python web_app/app.py
```

**Deployment** — pull a pinned copy of the runtime closure first. The app then
uses that instead of the live checkouts, so the running Space matches a known
set of commits:

```bash
python scripts/refresh_runtime.py --dry-run
python scripts/refresh_runtime.py
```

The refresh reads `config/portable_release_manifest.toml` from
`leap_initialisation`, copies only the paths it declares as runtime assets,
and writes `runtime/source_manifest.json` recording the commit and branch each
repository was on. It refuses to run against repositories with uncommitted
changes unless you pass `--allow-dirty`, so a prepared runtime always points at
committed source.

Set `LEAP_RUNTIME_ROOT` or `LEAP_SOURCE_PARENT` if either location differs.

### Never edit `runtime/`

`runtime/` is a copy. The next refresh rebuilds it from the three source
repositories, so a change typed into it directly survives until then and is
lost without a trace — the deployed app quietly loses a fix and nothing says
why. This has happened: a fix for hosted logging was committed to the deployed
copy of `portable_release/runtime.py` and did not exist in
`leap_initialisation` at all. It was caught by chance while diffing before a
deploy.

Fix it in the source repository, commit there, then refresh. To check that
nothing has drifted — before a deploy, and after pulling anyone else's work
into the source repositories:

```bash
python scripts/check_runtime_is_generated.py
python scripts/check_runtime_is_generated.py --runtime ../leap_review_web_app/runtime
```

It names every runtime file that differs from its source. A difference means
either an edit that is about to be discarded, or a source repository that has
moved on since the last refresh; the output says how to tell them apart.

## Publishing

The Hugging Face Space is a deployment copy of this repository, not a live
link to the source repositories: `web_app/`, `requirements.txt`, and a
prepared `runtime/`. Refresh the runtime, copy it into the Space checkout, run
the app locally against that prepared runtime, and only then publish. A
deployment must be rebuilt from a refreshed runtime whenever the source
repositories move on.

Before publishing anywhere public, check that every copied file is safe to
redistribute — a public repository containing the runtime also publishes the
copied code and data.
