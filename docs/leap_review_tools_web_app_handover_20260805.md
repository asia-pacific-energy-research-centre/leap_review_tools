# Handover: portable release → web app

**Date:** 2026-08-05
**From:** the session that built the portable Windows release (v0.1.0 → v0.3.0)
**To:** whoever is building `web_app/` (Gradio, Hugging Face bundle)
**Status of the exe:** working except one known bug, described in §3.

This is not a summary of everything that happened. It is the things that will
cost you a day each if you rediscover them yourself.

---

## 1. You are already sharing my code, whether or not you meant to

`web_app/app.py` calls `developer_launcher.run_balance_review_from_export`,
which delegates to `codebase/portable_release/commands.py`. **That is the same
function the exe calls.** So:

* changes I made to `commands.py` in the last few hours are live in your app;
* the bug in §3 is *your* bug too, in a different disguise;
* if you change `commands.py` for the web app, you change the exe.

That sharing is the right design — one implementation, two front ends — but it
means `commands.py` is now a shared boundary, not portable-release-private.
Nothing in its docstrings says that yet.

**The layering, as it now stands:**

```
web_app/app.py ─┐
                ├─→ developer_launcher ─→ commands.py ─→ the real workflows
portable_main ──┘                              │            (leap_initialisation,
   (the exe)                                   │             leap_dashboard)
                                               └─→ mapping_chain_client
                                                      └─→ leap_mappings worker
```

---

## 2. What transfers, and what does not

**Transfers — use it, do not rewrite it:**

| Thing | Where | Why it matters to you |
|---|---|---|
| `parse_years` | `commands.py` | "2022,2030" → `[2022, 2030]`. Handles commas, spaces, duplicates. |
| Input validation | `portable_release/validation.py` | Checks the export has the scenario/year sheets and Level 2 detail *before* a run starts. A web user needs this more than a CLI user, not less. |
| `workspace.py` | `portable_release/` | Discovers economies/scenarios/years from an exports folder. Already knows the newest-date rule and that `archive/` is ignored. |
| Progress steps + timings | `portable_release/progress.py` | The step keys and the recorded medians. A Gradio progress bar wants exactly this data. |
| Run manifests | `portable_release/provenance.py` | SHA-256 of every input, config and data file. This is how a result is traceable. Do not drop it because it is invisible in a web UI. |
| `CHANGELOG.md` + `bump_release_version.py` | `docs/`, `scripts/` | Version discipline, see §6. |

**Does not transfer — exe-specific:**

* the two-executable split and `mapping_chain_client`'s subprocess boundary
  (see §4 — but read it anyway, the *reason* still applies to you);
* `_build_stamp`, `_pause_before_closing`, the guided console flow;
* PyInstaller staging, `build_release.py`, the frozen `release_manifest.json`.

---

## 3. The open bug — and why it is probably worse for you

**Symptom (exe):** a multi-year balance review fails after ~12 minutes with

```
Canonical mapping workbook not found:
  <package>\leap_mappings\config\outlook_mappings_master.xlsx
```

**Cause:** `codebase/utilities/master_config.py` line ~10:

```python
REPO_ROOT = Path(__file__).resolve().parents[2]
LEAP_MAPPINGS_REPO_ROOT = REPO_ROOT.parent / "leap_mappings"
OUTLOOK_MAPPINGS_MASTER_PATH = LEAP_MAPPINGS_REPO_ROOT / "config" / "outlook_mappings_master.xlsx"
```

Computed **at import time**, from `__file__`, assuming `leap_mappings` is a
sibling directory of `leap_initialisation`. True in a maintainer's checkout.
False inside a frozen package. **Also false on Hugging Face**, unless your
bundle happens to reproduce that exact sibling layout.

`baseline_seed_balance_diagnostics` already monkeypatches the constant, but only
around the diagnostics step, restoring it on the way out — and the *workbook
build* runs after that window closes. A single-year run does not reach the
failing path; two years does.

**Commit `8f439a6` contains a proposed fix, marked UNVERIFIED.** It holds the
redirection across the whole command via `_canonical_workbook_pointed_at()`.
It is syntactically valid and has never been run against a frozen build or the
web app. Verify before trusting; delete without ceremony if your approach is
different.

**Why this matters to you specifically:** if your HF bundle is "self-contained",
every module-level `REPO_ROOT`-relative constant in these repos is a landmine of
this shape. Grep for them before you deploy:

```bash
grep -rn "parents\[[0-9]\]" codebase/ | grep -v test
```

The same class of bug bit the frozen build four separate times. It is the single
most expensive recurring problem in this codebase.

---

## 4. The `codebase` name collision (still relevant to you)

`leap_initialisation` and `leap_mappings` **both** name their top-level package
`codebase`, and both use absolute `codebase.x.y` imports. They cannot be on one
`sys.path` together — whichever is first wins and the other's imports resolve to
the wrong modules, usually as a confusing `AttributeError` rather than an
`ImportError`.

The exe solves this with two executables and a JSON-over-stdio subprocess. In a
web app you have the same constraint the moment you need both. Options:

* keep the subprocess boundary (`mapping_chain_client` already implements it,
  including streaming progress and capturing the worker log);
* or run the mapping chain out of process some other way.

What you cannot do is `import` both in one interpreter. This is documented in
`docs/leap_review_tools_handover_20260803.md` §1 and it has not changed.

---

## 5. Numbers you do not need to re-derive

Measured on this machine, 20_USA, `0804 TGT.xlsx`, v0.3.0:

| | |
|---|---|
| Balance review, one year | **3m 46s** |
| Dashboard, end to end | **5m 45s – 9m 19s** |
| — of which chart rendering | 3m 03s – 6m 32s |
| — of which LEAP/ESTO/9th comparison | 1m 56s – 2m 36s |
| Raw LEAP rows parsed | 139,156 |
| Converted to ESTO structure | 38,078 |
| Comparison rows | 289,132 |
| Charts rendered | 619 |
| Package size | 324 MB (3,191 files) |
| Data assets | 6 tables, 407 MB |

**These runtimes are the central UX problem you have inherited.** A user
watching a browser tab for nine minutes needs more reassurance than one watching
a console, not less. `logs/run_timings.json` holds real per-step medians; feed
them to a progress bar rather than a spinner.

### LEAP coverage on the dashboard — expect the question

266 of 529 charts carry a LEAP line. It is not evenly spread:

| Page | Charts with a LEAP line |
|---|---|
| Total demand | 7 of 7 |
| Supply | 76 of 81 |
| Power | 43 of 52 |
| **Industry** | **1 of 132** |
| **Buildings** | **1 of 32** |

This is **not a bug**, and I verified it against a dashboard built by the normal
`leap_dashboard` workflow, which looks the same. The LEAP model carries demand
as one aggregate branch (`All demand aggregated/Industry`, `/Buildings`, …) with
no sub-sector detail, declared in
`leap_mappings/config/all_demand_aggregated_components.json`. The aggregate does
appear — on one chart per page.

The user asked about this and reached the same conclusion. If it comes up again,
the answer is "the model, not the tools". Where it *would* be worth changing
something: nothing on those pages tells a reader why LEAP is absent. A web UI
could say so far more easily than a static page can.

---

## 6. Process things that are easy to drop

**Versioning.** Several builds went out as `0.1.0` with materially different
behaviour and nobody could tell which copy they had. Now:

```bash
python scripts/bump_release_version.py minor    # edits the manifest + opens a changelog entry
```

Manifest validation *warns* if the declared version has no `## <version>` entry
in `docs/CHANGELOG.md`. The changelog is written for the person **using** the
tools — not from the commit history. Keep that rule; it is why it is readable.

**The user guide is a Word document and is hand-edited.**
`docs/docx/LEAP Review Tools - user guide.docx` is the master. It carries
screenshots. `scripts/convert_docs.py` **refuses** to regenerate it
(`HAND_EDITED_DOCX`) because doing so would silently destroy them. Do not
re-enable that. `scripts/import_user_guide.py` pulls edits back from an
extracted release.

**Pins.** `config/portable_release_manifest.toml` pins every repo by commit.
Validation now warns when a pin falls behind commits that touch staged paths —
this was added because `leap_dashboard` sat two days behind a fix and shipped a
dashboard page the user had already removed. If the web app pins anything,
inherit this check rather than reinventing it.

**Untracked data tables** are pinned by `sha256` in the manifest. Six of them,
407 MB, gitignored by design. A changed table now fails the build instead of
shipping silently.

---

## 7. Standing constraints (from the user, not from me)

* **Do not distribute a release to colleagues, or run a network update, without
  explicit approval.** This has been in force the whole time.
* **Never push to remotes.**
* **Other agents write to these checkouts live.** Re-read `git status` before
  every commit; stage only files you authored; never `git add -A`; never resolve
  another agent's merge conflict.
* The release now ships **real APEC model output for six economies** as example
  inputs (6.9 MB). The user asked for it. Be deliberate about where that goes —
  a public Hugging Face Space is a different exposure from a ZIP sent to a
  colleague. **Confirm before deploying it anywhere public.**

---

## 8. The lesson that cost the most time

Every significant failure in this work was **at a seam**, and every one passed
its unit tests:

* `parse_years` was correct and unreachable — the dispatch coerced the value
  with `int()` before calling it.
* `master_config`'s path constant is correct in a checkout and wrong in a
  package (§3).
* The dashboard renderer's package worked staged and failed frozen, because a
  package `__init__` imported modules the closure walker never saw.
* A guide was staged but never copied into the package.
* The exe's console closed instantly on the one path I had only ever tested by
  piping stdin from a shell.

Static analysis and staged tests passed in all five cases. **Only running the
real artifact caught them.** For you that means: run the actual Gradio app in
the actual deployment environment, not just locally with the repos present.
"Works on my machine with sibling checkouts" is precisely the configuration that
hides §3.

---

## 9. Where to look

| | |
|---|---|
| This handover | `docs/leap_review_tools_web_app_handover_20260805.md` |
| Architecture, traps, release procedure | `docs/leap_review_tools.md` |
| Original build handover (§1 = the collision) | `docs/leap_review_tools_handover_20260803.md` |
| User-facing changes | `docs/CHANGELOG.md` |
| The shared command layer | `codebase/portable_release/commands.py` |
| Input validation | `codebase/portable_release/validation.py` |
| Export discovery | `codebase/portable_release/workspace.py` |
| Progress + timings | `codebase/portable_release/progress.py` |
| The open bug's fix (unverified) | commit `8f439a6` |

Repository state at handover: `leap_initialisation` at the commit above,
`leap_mappings` `1f4861b`, `leap_dashboard` `4792086`. Release version `0.3.0`.
