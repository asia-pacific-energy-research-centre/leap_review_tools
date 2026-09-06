#%%
"""Gradio web application for the complete LEAP balance-review workflow.

The app calls the repository's existing ``balance-review-from-export``
orchestration. It does not reimplement diagnostics or workbook construction.
"""

from __future__ import annotations

import base64
import csv
import gzip
import html
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

# Running ``python web_app/app.py`` puts only ``web_app/`` on ``sys.path``.
# Add the repository root before importing sibling package modules so the
# documented direct-file launch behaves the same as ``python -m web_app.app``.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web_app.guide_overlay import GUIDE_CSS, GUIDE_HTML, GUIDE_JS
from web_app.runtime_bundle import validate_mapping_chain_bundle
from web_app.runtime_profile import (
    estimate_runtime,
    format_duration,
    format_runtime_note,
    load_runtime_profile,
    record_runtime_sample,
    samples_behind_estimate,
)
from web_app.version_comparison import apply_version_comparison

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DASHBOARD_MIN_YEAR = 2010
DEFAULT_DASHBOARD_MAX_YEAR = 2060
# Keep the browser-local payload within common localStorage quotas while still
# allowing comparison between several recent economy/scenario runs.
MAX_BROWSER_DASHBOARDS = 3
# Gradio encrypts BrowserState with this value. It must remain stable across
# deployments or a restart makes this browser's existing records unreadable.
BROWSER_STATE_SECRET = "leap-balance-review-browser-state-v1"
WEB_ARTIFACT_MAX_AGE_SECONDS = 48 * 60 * 60
WEB_ARTIFACT_PREFIXES = (
    "leap_balance_review_web_",
    "leap_balance_review_download_",
)
# Rendered dashboards are written here so the app can hand out a plain link
# instead of embedding a nine-hundred-pixel iframe in the page.
DASHBOARD_SERVE_ROOT = Path(tempfile.gettempdir()) / "leap_balance_review_dashboards"
# This repository is the project home. The analysis code stays in the three
# source repositories; `scripts/refresh_runtime.py` copies their declared
# runtime closure into `runtime/`. A prepared runtime wins when present, which
# is how a deployed Space runs; otherwise fall back to the live sibling
# checkouts, which is how a maintainer runs it while developing.
RUNTIME_ROOT = Path(os.getenv("LEAP_RUNTIME_ROOT", str(REPO_ROOT / "runtime")))
SOURCE_PARENT = Path(os.getenv("LEAP_SOURCE_PARENT", str(REPO_ROOT.parent)))
TOKYO_TIMEZONE = timezone(timedelta(hours=9), name="JST")
VERSION_COMPARISON_GREEN_PERCENT = 0.1
VERSION_COMPARISON_YELLOW_PERCENT = 5.0
PLOTLY_CDN_URL = "https://cdn.plot.ly/plotly-2.35.2.min.js"
PLOTLY_ARCHIVE_PATH = "a/plotly.min.js"
ARCHIVE_MANIFEST_PATH = "archive_manifest.json"


def _as_tokyo_time(value: datetime) -> datetime:
    """Return an aware datetime in Tokyo time; naive inputs are treated as UTC."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(TOKYO_TIMEZONE)


def _format_tokyo_timestamp(value: object, *, include_seconds: bool = False) -> str:
    """Format current and legacy saved timestamps for browser-facing labels."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return ""
        parsed = None
        for format_string, source_timezone in (
            ("%Y-%m-%d %H:%M:%S UTC", timezone.utc),
            ("%Y-%m-%d %H:%M UTC", timezone.utc),
            ("%Y-%m-%d %H:%M:%S JST", TOKYO_TIMEZONE),
            ("%Y-%m-%d %H:%M JST", TOKYO_TIMEZONE),
        ):
            try:
                parsed = datetime.strptime(text, format_string).replace(
                    tzinfo=source_timezone
                )
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return text
    format_string = "%Y-%m-%d %H:%M:%S JST" if include_seconds else "%Y-%m-%d %H:%M JST"
    return _as_tokyo_time(parsed).strftime(format_string)


def _source_root(name: str) -> Path:
    """Return the prepared runtime copy of a repository, or its live checkout."""
    prepared = RUNTIME_ROOT / name
    return prepared if prepared.is_dir() else SOURCE_PARENT / name


INITIALISATION_ROOT = _source_root("leap_initialisation")
if str(INITIALISATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INITIALISATION_ROOT))

APP_ASSETS_ROOT = Path(__file__).resolve().parent / "assets"
LEAP_WALLPAPER_PATH = APP_ASSETS_ROOT / "leap_energy_wallpaper.png"
LEAP_WALLPAPER_URL = f"/gradio_api/file={LEAP_WALLPAPER_PATH.as_posix()}"
# Any image dropped in here joins the wallpaper rotation; nothing to register.
WALLPAPER_DIR = APP_ASSETS_ROOT / "wallpapers"
WALLPAPER_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
WALLPAPER_WASH = "linear-gradient(rgba(13, 28, 46, 0.34), rgba(13, 28, 46, 0.46))"


def _wallpaper_title(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().capitalize() or stem


def available_wallpapers() -> list[dict[str, str]]:
    """Return the wallpapers the app can offer, newest additions included.

    The list is read at start-up from ``assets/wallpapers`` so a new image is
    added by copying a file in, not by editing this module.
    """
    choices: list[dict[str, str]] = []
    if LEAP_WALLPAPER_PATH.is_file():
        choices.append(
            {
                "name": "LEAP energy",
                "layers": f'{WALLPAPER_WASH}, url("{LEAP_WALLPAPER_URL}")',
            }
        )
    if WALLPAPER_DIR.is_dir():
        for path in sorted(WALLPAPER_DIR.iterdir()):
            if path.suffix.lower() not in WALLPAPER_SUFFIXES or not path.is_file():
                continue
            url = f"/gradio_api/file={path.as_posix()}"
            choices.append(
                {
                    "name": _wallpaper_title(path.stem),
                    "layers": f'{WALLPAPER_WASH}, url("{url}")',
                }
            )
    choices.append({"name": "Plain", "layers": "none"})
    return choices


APP_CSS = """
/* Design tokens shared with docs/web_guide_prototype/styles.css so the app and
   its guide read as one product. The wallpaper treatment is this app's own. */
:root {
  --leap-wallpaper:
    linear-gradient(rgba(13, 28, 46, 0.34), rgba(13, 28, 46, 0.46)),
    url("__LEAP_WALLPAPER_URL__");
  --ink: #173452;
  --muted: #65788d;
  --panel: #ffffff;
  --paper: #f4f7fb;
  --orange: #e7672a;
  --purple: #b08fff;
  --line: #cbd8e7;
  --panel-shadow: 0 16px 45px #17345216;
}
.gradio-container {
  /* The theme resolves its semantic fills to the dark palette even though this
     app never renders dark, which is why black bars kept surfacing one
     component at a time: loading overlays, table rows, checkboxes, panels and
     code blocks all read from these. Setting them once fixes the family
     rather than the symptom. */
  --background-fill-primary: #ffffff;
  --background-fill-secondary: #f4f7fb;
  --panel-background-fill: #ffffff;
  --panel-border-color: #cbd8e7;
  --border-color-primary: #cbd8e7;
  --border-color-accent-subdued: #cbd8e7;
  --block-label-border-color: #cbd8e7;
  --block-label-background-fill: #f4f7fb;
  --block-title-background-fill: transparent;
  --checkbox-background-color: #ffffff;
  --checkbox-background-color-hover: #ffffff;
  --checkbox-background-color-focus: #ffffff;
  --checkbox-background-color-selected: #e7672a;
  --checkbox-label-border-color: #cbd8e7;
  --checkbox-label-border-color-hover: #cbd8e7;
  --checkbox-label-border-color-selected: #e7672a;
  --checkbox-label-background-fill: #f4f7fb;
  --checkbox-label-background-fill-hover: #eef3f9;
  --checkbox-label-background-fill-selected: #fff1e8;
  --code-background-fill: #f4f7fb;
  --color-accent-soft: #fff1e8;
  --error-background-fill: #fdf4f3;
  --error-border-color: #e2b4ae;
  --input-background-fill-hover: #ffffff;
  --input-border-color-hover: #cbd8e7;
  --input-border-color-focus: #e7672a;
  --table-border-color: #cbd8e7;
  --table-odd-background-fill: #ffffff;
  --table-even-background-fill: #f4f7fb;
  --table-row-focus: #fff1e8;
  --accordion-text-color: #173452;
  --block-label-text-color: #173452;
  --block-title-text-color: #173452;
  --checkbox-label-text-color: #173452;
  --checkbox-label-text-color-selected: #173452;
  --table-text-color: #173452;
  --error-text-color: #a8342a;
  --button-secondary-text-color: #173452;
  --button-secondary-text-color-hover: #173452;
  --loader-color: #e7672a;
  --body-background-fill: transparent;
  --body-text-color: #173452;
  --block-background-fill: #ffffff;
  --block-border-color: #cbd8e7;
  --input-background-fill: #ffffff;
  --input-background-fill-focus: #ffffff;
  --input-border-color: #cbd8e7;
  --input-border-color-focus: #e7672a;
  --input-shadow-focus: 0 0 0 2px rgba(231, 103, 42, 0.18);
  max-width: 1180px !important;
  width: calc(100% - 2rem) !important;
  margin: 0 auto !important;
  padding: 0.15rem 0 0.1rem !important;
  color: #173452 !important;
  font: 15px/1.55 Inter, "Segoe UI", Arial, sans-serif !important;
}
body, gradio-app {
  background-color: #15263a !important;
  background-image: var(--leap-wallpaper) !important;
  background-position: center top !important;
  background-size: cover !important;
  background-attachment: fixed !important;
}
/* The wallpaper stays: it fills the gutters, the workspace floats on top. */
.gradio-container { background: transparent !important; }
.gradio-container .prose, .gradio-container label, .gradio-container span {
  color: inherit;
}
#app-hero {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 56px;
  margin-bottom: 1.1rem;
  padding: 0 20px;
  border-radius: 9px;
  background: var(--ink);
  box-shadow: var(--panel-shadow);
}
#app-hero .leap-mini-mark {
  display: grid;
  width: 29px;
  height: 29px;
  place-items: center;
  border-radius: 6px;
  background: var(--orange);
  color: #ffffff;
  font: 800 0.95rem/1 Arial, sans-serif;
}
/* The guide launcher is created by the overlay as a floating pill. Moved into
   the title bar it lines up with the workspace instead of the viewport edge,
   and stops overlapping the results panel. Two ids beat the overlay's own
   single-id rule regardless of stylesheet order. */
#app-hero #leap-guide-launch {
  position: static !important;
  right: auto !important;
  bottom: auto !important;
  margin-left: auto;
  padding: 0.38rem 0.95rem !important;
  border: 0 !important;
  border-radius: 999px !important;
  background: var(--orange) !important;
  color: #ffffff !important;
  font-size: 0.82rem !important;
  font-weight: 750 !important;
  letter-spacing: 0.01em;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.28) !important;
}
#app-hero #leap-guide-launch:hover { background: #f2762f !important; }
#app-hero #leap-guide-launch:active { background: #d45a20 !important; }
#app-hero #leap-guide-launch span {
  width: 1.05rem !important;
  height: 1.05rem !important;
  border-color: rgba(255, 255, 255, 0.85) !important;
  font-size: 0.72rem;
  font-weight: 800;
}
#app-hero .leap-wordmark {
  color: #ffffff;
  font-size: 1.05rem;
  font-weight: 700;
  letter-spacing: -0.01em;
}
/* Hugging Face injects its own repository bar at the top-right of a Space.
   That is exactly where this app's Guide action lives, so keep the useful HF
   links but turn the bar into a quieter bottom-right pill. Its position and
   size arrive as inline styles, hence the deliberate !important overrides. */
#huggingface-space-header {
  top: auto !important;
  right: 0.75rem !important;
  bottom: 0.75rem !important;
  transform: scale(0.82);
  transform-origin: bottom right;
  opacity: 0.86;
  transition: opacity 0.15s ease;
}
#huggingface-space-header:hover,
#huggingface-space-header:focus-within { opacity: 1; }
/* Section labels are a free-standing orange kicker above the panel, not a tab
   glued to it — the panels then read as cards the way the guide's do. */
.step-heading {
  margin: 0.9rem 0 0.45rem;
  padding: 0;
  border: 0;
  background: transparent;
}
.step-heading .step-kicker {
  display: block;
  margin-bottom: 0.15rem;
  color: var(--orange);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.step-heading strong {
  display: block;
  color: var(--ink);
  font-size: 1.12rem;
  letter-spacing: -0.035em;
  line-height: 1.15;
}
.step-heading p { margin: 0.1rem 0 0; color: var(--muted); font-size: 0.81rem; line-height: 1.35; }
#upload-card, #results-card {
  gap: 0.4rem;
  padding: 0.85rem 0.9rem !important;
  border: 1px solid var(--line) !important;
  border-radius: 9px !important;
  background: var(--panel) !important;
  box-shadow: var(--panel-shadow);
}
/* Gradio pads every HTML block by 10px top and bottom. Inside the cards the
   card's own gap already separates the blocks, so that padding is pure empty
   space -- roughly 60px of it down the page. */
#upload-card .html-container.padding,
#results-card .html-container.padding {
  padding-top: 0 !important;
  padding-bottom: 0 !important;
}
/* Gradio flashes a block's opacity between 0.5 and 1 twice a second while it
   waits for an update. The run timer ticks every three seconds, so panels that
   are not changing at all blinked for the whole run. The calculator already
   says a run is under way, so nothing is lost by holding these still. */
.gradio-container .pending,
.gradio-container .generating {
  animation: none !important;
  opacity: 1 !important;
  border-color: var(--line) !important;
}
#refresh-run { display: none !important; }
#upload-row, #action-row, #download-row, #dashboard-controls { gap: 0.8rem; }
#upload-row .gr-form { padding: 0.65rem 0.75rem; }
.gradio-container input, .gradio-container textarea, .gradio-container select {
  border-radius: 6px !important;
  border-color: var(--line) !important;
}
.gradio-container input:focus,
.gradio-container textarea:focus,
.gradio-container select:focus {
  border-color: var(--orange) !important;
  outline: none !important;
  background: #ffffff !important;
  color: #1d2d3d !important;
  -webkit-text-fill-color: #1d2d3d !important;
  box-shadow: 0 0 0 2px rgba(232, 93, 36, 0.18) !important;
}
.gradio-container span[data-testid="block-info"] {
  padding: 0 0 0.35rem !important;
  border: 0 !important;
  background: transparent !important;
  color: var(--ink) !important;
  font-weight: 700 !important;
}
/* Gradio stacks its own frame paddings above the banner -- the container, the
   fillable app and the block wrapper each add their own -- and spaces every
   top-level block by 16px. Together that was 34px of empty page before the
   banner and 62px between it and the first card. */
.gradio-container .app.fillable { padding: 0.25rem 0.25rem 0 !important; }
/* Gradio's own footer sits below the last card and is the last of the empty
   space at the bottom of the page. */
.gradio-container footer { margin: 0 !important; padding: 0.1rem 0 0 !important; }
.gradio-container .contain > * { gap: 0.5rem !important; }
.html-container:has(#app-hero) { padding: 0 !important; }
#upload-row { align-items: center; }
#upload-card .step-heading, #results-card .step-heading { margin: 0 0 0.2rem; }
#upload-card .step-heading p, #results-card .step-heading p { font-size: 0.8rem; }
/* The parsed row is the visible file row. Browser code copies Gradio's native
   size/download/remove controls into it, then keeps the original preview
   available off-screen so its real actions continue to work. */
#upload-card {
  display: grid !important;
  grid-template-columns: minmax(0, 1fr);
  align-items: stretch;
  column-gap: 0;
}
#upload-card > .block:first-child,
#upload-card > #export-actions,
#upload-card > #component-11,
#upload-card > #outputs-row,
#upload-card > #run-actions,
#upload-card > #run-button,
#upload-card > #run-status,
#upload-card > #calculator-holder,
#upload-card > #technical-details { grid-column: 1 / -1; }
#upload-card > #balance-upload {
  grid-column: 1 / -1;
  grid-row: 2;
  min-width: 0;
  padding: 0.35rem 0.45rem !important;
  border: 1px solid #c4d2e0 !important;
  border-radius: 6px !important;
  background: #f6f9fc !important;
}
#upload-card > #balance-upload:not(:has(table.file-preview)) {
  grid-column: 1 / -1;
  border-right: 1px solid #c4d2e0 !important;
  border-radius: 6px !important;
}
#upload-card > #balance-upload:not(:has(table.file-preview)) > button { width: 100%; }
#upload-card > #export-readout {
  grid-column: 1 / -1;
  grid-row: 2;
  align-self: stretch;
  min-width: 0;
  margin: 0;
  padding: 0.35rem 0.45rem !important;
  border: 1px solid #c4d2e0 !important;
  border-radius: 6px !important;
  background: #f6f9fc !important;
}
#export-readout .export-readout {
  height: 100%;
  box-sizing: border-box;
  border: 0;
  background: transparent;
}
#upload-card > #export-readout:not(:has(.export-readout)) { display: none !important; }
#balance-upload.is-merged-preview {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
  opacity: 0 !important;
  pointer-events: none !important;
}
/* Gradio's dropzone offers two ways in — drag here, or click — and renders the
   "- or -" between them. The choice is noise when only one route is obvious in
   a browser, and its orange block label reads as the button while the real
   click target is a transparent area behind it. So: hide the label, hide the
   alternative, and make the button itself the one orange thing to press.
   Dropping a file still works; it is simply no longer advertised. */
.gradio-container span.or { display: none !important; }
#balance-upload { border: 0 !important; overflow: visible !important; }
#balance-upload > label { display: none !important; }
#balance-upload > button {
  display: flex !important;
  height: auto !important;
  min-height: 52px !important;
  max-height: none !important;
  padding: 0.55rem 1rem !important;
  border: 1px solid #cf5a22 !important;
  border-radius: 3px !important;
  background: var(--orange) !important;
  color: #ffffff !important;
  font-size: 1rem !important;
  font-weight: 750 !important;
  box-shadow: 0 2px 5px rgba(188, 70, 24, 0.22);
}
#balance-upload > button:hover { background: #d45a20 !important; }
/* Gradio's dropzone wrap carries a 240px min-height for the drag target; with
   the drag affordance gone the button sizes to its own content instead. */
#balance-upload > button .wrap {
  gap: 0.5rem !important;
  height: auto !important;
  min-height: 0 !important;
  padding: 0 !important;
  color: #ffffff !important;
  opacity: 1 !important;
}
#balance-upload > button .icon-wrap { width: 22px !important; margin: 0 !important; }
#balance-upload > button .icon-wrap svg { color: #ffffff !important; opacity: 1 !important; }
/* Once a file is loaded, Gradio swaps the button for a preview: an orange
   block label, a slate-filled table row, and a bare clear button floating to
   one side. Reduce it to one quiet line — the readout underneath already
   states the economy, scenario and years, which is what a user needs. */
.gradio-container .file-preview-holder,
.gradio-container table.file-preview,
.gradio-container table.file-preview tbody {
  min-height: 0 !important;
  height: auto !important;
  margin: 0 !important;
  padding: 0 !important;
}
.gradio-container table.file-preview {
  width: 100% !important;
  overflow: hidden;
  border: 1px solid var(--line) !important;
  border-collapse: collapse !important;
  border-radius: 6px !important;
}
.gradio-container table.file-preview tr.file { background: var(--paper) !important; }
.gradio-container table.file-preview td {
  padding: 0.45rem 0.7rem !important;
  border: 0 !important;
  background: transparent !important;
  color: var(--ink) !important;
  font-size: 0.83rem !important;
}
.gradio-container table.file-preview a { color: var(--orange) !important; }
/* The workbooks and the archive are the point of a finished run, and their
   download sat as a faint arrow after the file size -- easy to read as a
   label rather than something to press. In the results card it is a pill. */
#download-row table.file-preview td.download a,
#balance-upload table.file-preview td.download a {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.75rem;
  border: 2px solid var(--orange) !important;
  border-radius: 999px;
  background: #fff6f1;
  font-weight: 700;
  text-decoration: none !important;
  white-space: nowrap;
}
#download-row table.file-preview td.download a:hover,
#balance-upload table.file-preview td.download a:hover {
  background: var(--orange);
  color: #ffffff !important;
}
#download-row table.file-preview td.download a .download-icon,
#balance-upload table.file-preview td.download a .download-icon {
  width: 1.3em;
  height: 1.3em;
  flex: 0 0 auto;
}
#download-row table.file-preview td.download,
#balance-upload table.file-preview td.download { text-align: right; }
/* Gradio floats an unlabelled upload and clear icon above a loaded file.
   Both actions are offered as named buttons below, so the icons are only a
   second, more cryptic way to do the same thing. */
.gradio-container .icon-button-wrapper { display: none !important; }
#export-readout {
  margin-top: 0.2rem;
}
.export-readout {
  padding: 0.35rem 0.65rem;
  border: 1px solid #c4d2e0;
  border-left-width: 4px;
  border-radius: 3px;
  background: #f6f9fc;
}
.export-readout .readout-label {
  display: block;
  margin-bottom: 0.25rem;
  color: #5b7086;
  font-size: 0.68rem;
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.export-readout p {
  margin: 0.5rem 0 0;
  color: #5b7086;
  font-size: 0.82rem;
  line-height: 1.45;
}
.export-readout .readout-chips { display: flex; flex-wrap: wrap; gap: 0.5rem; }
/* One row per uploaded file, so a mixed set can be checked at a glance. */
.upload-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 0.3rem; }
.upload-row {
  display: grid;
  grid-template-columns: minmax(0, 2fr) repeat(3, minmax(0, 1fr)) auto;
  gap: 0.6rem;
  align-items: baseline;
  padding: 0.35rem 0.55rem;
  border: 1px solid var(--line);
  border-left-width: 3px;
  border-radius: 4px;
  background: #ffffff;
  font-size: 0.8rem;
}
.upload-row strong { color: var(--ink); overflow-wrap: anywhere; }
.upload-row span { color: var(--muted); }
.upload-file-name { display: inline-flex; align-items: center; gap: 0.45rem; min-width: 0; }
.upload-version {
  flex: 0 0 auto;
  padding: 0.12rem 0.38rem;
  border: 1px solid #d7b08b;
  border-radius: 999px;
  background: #fff8f1;
  color: #7a451f !important;
  font-size: 0.68rem;
  font-weight: 750;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  white-space: nowrap;
}
.upload-native-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.45rem;
  min-width: 155px;
}
.upload-native-actions .upload-download {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  min-height: 28px;
  padding: 0.18rem 0.6rem;
  border: 1px solid var(--orange);
  border-radius: 999px;
  background: #fff6f1;
  color: var(--orange) !important;
  font-weight: 700;
  text-decoration: none !important;
  white-space: nowrap;
}
.upload-native-actions .upload-download:hover {
  background: var(--orange);
  color: #ffffff !important;
}
.upload-native-actions .download-icon {
  width: 1.2em;
  height: 1.2em;
  flex: 0 0 auto;
}
.upload-native-actions .upload-remove {
  display: grid;
  width: 28px;
  height: 28px;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #8ba0b6;
  font-size: 1.25rem;
  line-height: 1;
  cursor: pointer;
}
.upload-native-actions .upload-remove:hover { background: #c0392b; color: #ffffff; }
.upload-row.is-ok { border-left-color: #2f8f5b; }
.upload-row.is-warn { border-left-color: #e0912f; }
.upload-row.is-bad { border-left-color: #c0392b; }
/* A repeat is not an error: it is set aside, and reads that way. */
.upload-row.is-dupe { border-left-color: #97a9bd; background: #f4f7fb; }
.upload-row.is-dupe strong { color: var(--muted); font-weight: 650; }
.upload-duplicate { color: var(--muted); font-style: italic; }
.readout-repeat { color: #8a6321; font-weight: 600; }
#version-comparison-controls {
  position: fixed !important;
  inset: 0 !important;
  z-index: 1100 !important;
  padding: 1.25rem !important;
  background: rgba(35, 30, 24, 0.62) !important;
}
#version-comparison-dialog {
  width: min(620px, 100%) !important;
  min-width: 0 !important;
  min-height: 0 !important;
  height: auto !important;
  flex: 0 0 auto !important;
  align-self: flex-start !important;
  margin: 12vh auto 0 !important;
  padding: 1.15rem !important;
  border: 1px solid #e9c9ae !important;
  border-top: 4px solid var(--orange) !important;
  border-radius: 8px !important;
  background: #fffdf9 !important;
  box-shadow: 0 20px 55px rgba(31, 23, 16, 0.42) !important;
}
#version-comparison-dialog .version-prompt-title {
  margin: 0 0 0.4rem !important;
  color: var(--ink) !important;
  font-size: 1.1rem !important;
  font-weight: 800 !important;
}
#version-comparison-dialog .version-prompt-copy {
  margin: 0 0 1rem !important;
  color: #5f6470 !important;
}
#version-clear-exports,
#version-clear-exports button {
  border-color: #d9a472 !important;
  background: #ffffff !important;
  color: #793b19 !important;
}
#version-compare,
#version-compare button {
  background: var(--orange) !important;
  color: #ffffff !important;
}
#app-wait-overlay {
  display: none;
  position: fixed;
  inset: 0;
  z-index: 1200;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  background: rgba(35, 30, 24, 0.62);
}
body.app-is-processing #app-wait-overlay { display: flex; }
/* During a dashboard build, the calculator is the progress indicator. Do not
   cover it with the short upload-update overlay as Gradio refreshes fields. */
body.run-active #app-wait-overlay { display: none !important; }
#app-wait-overlay .wait-card {
  width: min(300px, 100%);
  padding: 1.2rem 1.4rem;
  border: 1px solid #e9c9ae;
  border-top: 4px solid var(--orange);
  border-radius: 8px;
  background: #fffdf9;
  color: var(--ink);
  text-align: center;
  box-shadow: 0 20px 55px rgba(31, 23, 16, 0.42);
}
#app-wait-overlay strong { display: block; font-size: 1.05rem; }
#app-wait-overlay span { display: block; margin-top: 0.3rem; color: #5f6470; font-size: 0.88rem; }
.app-is-processing #upload-card .pending,
.app-is-processing #upload-card .generating { visibility: hidden !important; }
}
.version-selection-error {
  display: block;
  margin-top: 0.4rem;
  color: #b42318;
  font-size: 0.82rem;
  font-weight: 650;
}
#build-choice-heading { align-items: center !important; gap: 0.45rem !important; }
#build-choice-heading { flex-wrap: nowrap !important; }
#build-choice-heading #build-choice-title {
  flex: 1 1 auto !important;
  width: auto !important;
  min-width: 0 !important;
  margin: 0 !important;
}
#build-choice-title .choose-label {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
#build-choice-title .choose-label span {
  margin-left: auto;
  white-space: nowrap;
}
#build-choice-heading > .form {
  flex: 0 0 var(--esto-vintage-width, 400px) !important;
  width: var(--esto-vintage-width, 400px) !important;
  min-width: 0 !important;
  margin-left: auto !important;
  padding: 0 !important;
}
#build-choice-heading #esto-vintage {
  flex: 1 1 auto !important;
  width: 100% !important;
  min-width: 0 !important;
  height: 2.35rem !important;
  min-height: 2.35rem !important;
  margin-top: 0 !important;
  padding: 0 !important;
}
#esto-vintage .container,
#esto-vintage .wrap,
#esto-vintage .wrap-inner {
  height: 100% !important;
  min-height: 0 !important;
}
#esto-vintage .secondary-wrap {
  position: relative !important;
  min-height: 2.35rem !important;
  border: 1px solid var(--line) !important;
  border-radius: 7px !important;
  background: #ffffff !important;
  box-shadow: none !important;
}
#esto-vintage .secondary-wrap::before {
  content: attr(data-display);
  position: absolute;
  inset: 0.5rem 2.5rem 0.35rem 0.8rem;
  overflow: hidden;
  color: var(--ink);
  font-size: 0.8rem;
  line-height: 1.2;
  white-space: nowrap;
  pointer-events: none;
}
#esto-vintage input {
  min-height: 2.35rem !important;
  padding-right: 2.5rem !important;
  color: transparent !important;
  -webkit-text-fill-color: transparent !important;
}
#esto-vintage .icon-wrap {
  right: 0.7rem !important;
  width: 1rem !important;
  height: 1rem !important;
  color: var(--muted) !important;
}
.unit-warning {
  margin: 0.45rem 0 0;
  padding: 0.45rem 0.6rem;
  border-left: 3px solid #e0912f;
  background: #fff8ec;
  color: #6f511c;
  font-weight: 600;
}
.results-superseded { opacity: 0.5; }
.results-superseded .superseded-note {
  margin: 0 0 0.5rem;
  color: #8a6321;
  font-weight: 700;
  font-size: 0.84rem;
  opacity: 1;
}
/* Nothing in the results card should ever animate. Gradio pulses a block's
   opacity while an update is pending, and the run timer ticks every three
   seconds, so a finished run's links blinked for the whole of the next one. */
#results-card, #results-card *, #result-links, #result-links * {
  animation: none !important;
}
/* The files below the links belong to the previous run too, so they fade with
   it. The class is set by the script that already watches the run button. */
body.run-active #download-row, body.run-active #output { opacity: 0.5; }
.upload-row.is-bad .upload-error { grid-column: 2 / -1; color: #a8342a; }
#selection-row { gap: 0.8rem; margin-top: 0.15rem; }
/* A multi-file upload cannot produce a workbook, so the card says so by
   fading rather than vanishing: the option is still visible, just plainly
   out of reach. */
.output-card.is-unavailable { opacity: 0.45; }
.output-card.is-unavailable > div:first-child label { cursor: not-allowed; }
#workbook-card:has(input:disabled) { opacity: 0.45; }
#workbook-card:has(input:disabled) > div:first-child label { cursor: not-allowed; }
.output-card.is-unavailable .card-note::after {
  content: " Unavailable for a multi-file upload.";
  color: #a8342a;
  font-weight: 600;
}
.readout-chip {
  min-width: 108px;
  padding: 0.3rem 0.55rem;
  border: 1px solid #c9d6e3;
  border-radius: 3px;
  background: #ffffff;
}
.readout-chip span {
  display: block;
  color: #7387a0;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.readout-chip strong { color: #1f3d5b; font-size: 0.98rem; }
.export-readout.is-waiting { border-left-color: #aebfd2; }
.export-readout.is-ready { border-left-color: #2f8f5b; background: #f3faf6; }
.export-readout.is-ready .readout-label { color: #2f7a52; }
.export-readout.is-partial { border-left-color: #e0912f; background: #fdf8f0; }
.export-readout.is-partial .readout-label { color: #b3701c; }
.export-readout.is-error { border-left-color: #c0392b; background: #fdf4f3; }
.export-readout.is-error .readout-label { color: #a8342a; }
.export-readout.is-error p { color: #7c3b34; }
#balance-upload table.file-preview {
  width: 100% !important;
  border: 0 !important;
  border-left: 3px solid #2f8f5b !important;
  border-radius: 4px !important;
  background: #ffffff !important;
}
#balance-upload .file-preview-holder {
  display: flex !important;
  height: 100% !important;
  align-items: center;
}
#balance-upload table.file-preview tr.file {
  height: 30px;
  background: #ffffff !important;
}
#balance-upload table.file-preview td.filename { display: none !important; }
#balance-upload table.file-preview td {
  height: 30px;
  padding: 0.2rem 0.35rem !important;
  background: transparent !important;
}
#balance-upload table.file-preview td.download a {
  min-height: 28px;
  padding: 0.18rem 0.55rem;
  border-width: 1px !important;
  font-size: 0.78rem;
}
#export-readout .readout-chips { gap: 0; }
#export-readout .readout-chip { border: 0; background: transparent; }
/* The ESTO override is a genuine disclosure: say what opening it does, and
   keep the affordance on the left where the label is read from. */
/* Every disclosure on the page says what opening it does and puts the
   affordance on the left, where the label is read from. */
#workbook-note, #saved-reviews {
  margin-top: 0.1rem;
  border: 1px solid var(--line) !important;
  border-radius: 6px !important;
  background: #ffffff !important;
}
#workbook-note > button,
#saved-reviews > button {
  display: flex !important;
  align-items: center;
  gap: 0.5rem;
  padding: 0.3rem 0.8rem !important;
  color: var(--ink) !important;
  font-weight: 700;
  background: var(--paper) !important;
}
#workbook-note > button::before,
#saved-reviews > button::before {
  content: "▸";
  color: var(--orange);
  font-size: 0.95rem;
  transition: transform 0.15s ease;
}
#workbook-note > button.open::before,
#saved-reviews > button.open::before { transform: rotate(90deg); }
#workbook-note > button::after,
#saved-reviews > button::after {
  margin-left: auto;
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 500;
  white-space: nowrap;
}
#workbook-note > button::after { content: "Guide"; }
#saved-reviews > button::after { content: "Up to 3 dashboards"; }
#workbook-note > button.open::after,
#saved-reviews > button.open::after { content: "Click to close"; }
#workbook-note > button .icon, #workbook-note > button svg,
#saved-reviews > button .icon, #saved-reviews > button svg { display: none !important; }
#workbook-note > div:last-child,
#saved-reviews > div:last-child { padding: 0.9rem 0.85rem !important; }
#technical-details { border-radius: 3px !important; border-color: var(--line) !important; }
#technical-details > button { color: #405a73 !important; font-weight: 650;
  padding: 0.3rem 0.8rem !important; background: #f2f6fa !important; }
#esto-note, #results-note {
  margin: 0;
  padding: 0.65rem 0.8rem;
  border-left: 3px solid var(--orange);
  border-radius: 2px;
  background: #f4f7fb;
  color: #5b7086;
  font-size: 0.82rem;
  line-height: 1.45;
}
#run-button {
  width: 100%;
  min-height: 42px;
  border: 1px solid #cf5a22 !important;
  border-radius: 3px !important;
  background: var(--orange) !important;
  color: #ffffff !important;
  font-weight: 750;
  box-shadow: 0 2px 5px rgba(188, 70, 24, 0.22);
}
#run-button:hover { background: #d45a20 !important; }
#run-actions { gap: 0; align-items: stretch; }
#run-actions > #run-button { flex: 1 1 auto !important; }
#cancel-run {
  min-height: 36px;
  margin-left: auto;
  padding: 0.35rem 0.85rem !important;
  border: 1px solid #b83b32 !important;
  border-radius: 3px !important;
  background: #ffffff !important;
  color: #a8342a !important;
  font-weight: 750;
  box-shadow: none !important;
}
#cancel-run:hover { background: #fff1ef !important; }
#cancel-run:disabled { color: #8a98a8 !important; border-color: #c8d2dc !important; }
#calculator-animation #cancel-run { flex: 0 0 auto; }
/* Locked while a build is in flight: still legible, plainly not pressable. */
#run-button:disabled, #run-button[disabled] {
  background: #d9a68c !important;
  border-color: #d9a68c !important;
  color: #ffffff !important;
  cursor: not-allowed !important;
  opacity: 1 !important;
  box-shadow: none !important;
}
#run-button[aria-disabled="true"] {
  background: #d9a68c !important;
  border-color: #d9a68c !important;
  color: #ffffff !important;
  cursor: not-allowed !important;
  opacity: 1 !important;
  box-shadow: none !important;
}
#run-status textarea, #run-status input { font-size: 0.88rem; }
#results-empty {
  padding: 0.55rem 1rem;
  border: 1px dashed var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--muted);
  text-align: center;
}
/* The dashboard is a link, not an embed, so the results panel stays one screen. */
.result-links { display: flex; flex-wrap: wrap; align-items: center; gap: 0.9rem; }
.result-link {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 12px 20px;
  border: 0;
  border-radius: 5px;
  background: var(--orange);
  color: #ffffff !important;
  font-weight: 700;
  text-decoration: none !important;
}
.result-link:hover { background: #d45a20; }
/* Sized in ems so the mark tracks the label it sits beside, and nudged down
   a hair because an arrow reads high against a capital letter. */
.result-link .link-icon {
  width: 1.05em;
  height: 1.05em;
  flex: 0 0 auto;
  opacity: 0.9;
  transform: translateY(0.5px);
}
.result-link.is-primary { font-size: 1.02rem; padding: 14px 24px; }
.result-hint { color: var(--muted); font-size: 0.84rem; }
.result-hint.is-warning { color: #a8342a; font-weight: 600; }
.result-links.is-failed { padding: 0.7rem 0.9rem; border-left: 4px solid #c0392b;
  border-radius: 4px; background: #fdf4f3; }
/* Output file fields never receive an upload, so their dropzone is dead space. */
#results-card .file-preview { min-height: 0 !important; }
/* Gradio 5 renders an empty File output as a large document icon rather than
   omitting its preview. Hide each empty output independently: a dashboard-only
   run should show its archive, not two empty workbook/archive placeholders. */
#download-row > .block:has(.empty[aria-label="Empty value"]) { display: none !important; }
/* No link and all three downloads empty means there is no result yet. The
   explicit output ids avoid depending on Gradio's generated component ids. */
#results-card:not(:has(#result-links .result-links)):has(#review-workbooks-download .empty[aria-label="Empty value"]):has(#dashboard-download .empty[aria-label="Empty value"]):has(#diagnostics-bundle .empty[aria-label="Empty value"]) {
  display: none !important;
}
#results-card:has(.file-preview) #results-empty { display: none; }
#download-row .block > button { display: none !important; }
#download-row .block {
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
}
#download-row > div > label {
  position: static !important;
  display: block !important;
  margin: 0 0 0.35rem !important;
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  color: var(--ink) !important;
  font-size: 0.78rem !important;
  font-weight: 700 !important;
  letter-spacing: 0.02em;
  box-shadow: none !important;
}
#download-row > div > label svg { display: none !important; }
#results-card .result-links { margin-bottom: 0.25rem; }
/* Keep the whole Results card out of the page until a result link, warning, or
   downloadable file has actually been returned. */
#results-card:not(:has(#result-links .result-links)):not(:has(.file-preview)) {
  display: none !important;
}
.results-summary { display: flex; align-items: flex-start; gap: 0.75rem; margin: 0; }
.results-summary .step-kicker { flex: 0 0 auto; margin-top: 0.18rem; }
.results-heading-copy { display: grid; gap: 0.12rem; }
.results-heading-copy strong { color: var(--ink); font-size: 1.02rem; line-height: 1.25; }
.results-heading-copy span { color: var(--muted); font-size: 0.8rem; line-height: 1.4; }
#saved-reviews-note {
  margin: 0.05rem 0 0.45rem;
  padding: 0.55rem 0.75rem;
  border-left: 3px solid var(--orange);
  border-radius: 2px;
  background: #fff8f3;
  color: var(--muted);
  font-size: 0.78rem;
  line-height: 1.45;
}
#saved-reviews-note p { margin: 0; }
@media (max-width: 640px) {
  .results-summary { display: grid; gap: 0.2rem; }
  .results-summary .step-kicker { margin-top: 0; }
}
#clear-dashboards {
  align-self: end;
  max-width: 210px;
  border: 1px solid var(--line) !important;
  background: #ffffff !important;
  background-image: none !important;
  color: var(--ink) !important;
  font-weight: 650;
  box-shadow: none !important;
}
#clear-dashboards:hover {
  border-color: #b9c9dc !important;
  background: #f4f7fb !important;
}
/* Gradio's own clear is an unlabelled icon, so the action is offered in
   words as well, kept quiet so it cannot compete with Run. */
#clear-export {
  align-self: flex-start;
  max-width: 220px;
  min-height: 0 !important;
  padding: 0.35rem 0.7rem !important;
  border: 1px solid var(--line) !important;
  border-radius: 5px !important;
  background: #ffffff !important;
  color: var(--muted) !important;
  font-size: 0.78rem !important;
  font-weight: 600 !important;
}
#clear-export:hover { background: var(--paper) !important; color: var(--ink) !important; }
#export-actions {
  gap: 0.5rem;
  align-items: center;
  justify-content: flex-start;
  flex-wrap: wrap;
}
/* Both actions are the same size and weight: neither is the primary one. */
#export-actions > * { flex: 0 0 auto !important; min-width: 0 !important; }
/* The add-another picker is a file field, dressed to match the button it sits
   beside so the pair reads as two related actions. */
#add-export {
  /* Gradio's auto-margin class centres a block in its row, which pushed this
     360px away from the button it belongs beside. */
  margin: 0 !important;
  width: auto !important;
  max-width: 220px !important;
  border: 0 !important;
  background: transparent !important;
  overflow: visible !important;
}
/* The dropzone reserves room for a drag target above its label; without it
   the button matches the height of the one beside it. */
#add-export > button .wrap { padding: 0 !important; min-height: 0 !important; height: auto !important; }
#add-export > label.float { display: none !important; }
#add-export .file-preview-holder { display: none !important; }
/* The stand-in remove cell matches the one Gradio draws for several files. */
#balance-upload td[data-single-remove],
#balance-upload table.file-preview td:last-child:not(.filename):not(.download) {
  width: 2.2rem;
  padding: 0.2rem 0.35rem !important;
  color: #8ba0b6 !important;
  font-size: 1.35rem !important;
  font-weight: 400 !important;
  line-height: 1 !important;
  text-align: center;
  cursor: pointer;
  user-select: none;
}
/* Visible as a control, but it has to be aimed at: the ring only fills on
   hover, and the cell is held away from the download beside it. */
#balance-upload td[data-single-remove]:hover,
#balance-upload table.file-preview td:last-child:not(.filename):not(.download):hover {
  color: #ffffff !important;
  background: #c0392b;
  border-radius: 4px;
}
#add-export > button {
  display: inline-flex !important;
  align-items: center;
  justify-content: center;
  width: auto !important;
  height: 33px !important;
  min-height: 0 !important;
  max-height: none !important;
  padding: 0 0.7rem !important;
  border: 1px solid var(--line) !important;
  border-radius: 5px !important;
  background: #ffffff !important;
  color: var(--muted) !important;
  font-size: 0.78rem !important;
  font-weight: 600 !important;
  box-shadow: none !important;
}
#add-export > button:hover { background: var(--paper) !important; color: var(--ink) !important; }
#add-export > button .wrap { min-height: 0 !important; height: auto !important; gap: 0.4rem !important; }
#add-export > button .icon-wrap { display: none !important; }
/* Vertical trim: the whole flow should read without hunting down the page. */
/* Gradio wraps inputs in a `.form` div with a dark slate fill; inside our own
   panels that reads as a stray black box, so it is neutralised wherever it
   appears in the run card. */
#upload-card .form, .output-card .form {
  border: 0 !important;
  background: transparent !important;
}
.run-status-line {
  margin: 0.2rem 0 0;
  color: var(--muted);
  font-size: 0.84rem;
  text-align: center;
}
.run-status-line.is-failed { color: #a8342a; font-weight: 600; }
/* The running step is carried here only so the script can read it; it is
   shown inside the calculator caption instead of on a line of its own. */
.run-status-line.is-step { display: none; }
#calculator-animation { min-height: 0; margin: 0; }
/* Elapsed time sits with the estimate it should be read against. */
.calc-caption .run-stopwatch {
  margin-left: 0.6rem;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.2rem 0.6rem;
  border: 1px solid #e7c3ae;
  border-radius: 999px;
  background: #fff6f1;
  color: var(--ink);
}
.calc-caption .stopwatch-value { font-variant-numeric: tabular-nums; font-weight: 750; }
.calc-caption .stopwatch-remaining { color: var(--muted); font-weight: 400; }
.calc-caption .stopwatch-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--orange);
  animation: stopwatch-pulse 1s ease-in-out infinite;
}
@keyframes stopwatch-pulse { 50% { opacity: 0.25; } }
/* Wallpaper switcher: deliberately out of the way, bottom-left, above the
   wallpaper it changes. */
#wallpaper-switch {
  position: fixed;
  left: 14px;
  bottom: 14px;
  z-index: 40;
  display: flex;
  align-items: center;
  gap: 0.45rem;
  padding: 7px 12px;
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 999px;
  background: rgba(23, 52, 82, 0.72);
  color: #eaf1f8;
  font: 600 0.76rem/1 Inter, "Segoe UI", Arial, sans-serif;
  cursor: pointer;
  backdrop-filter: blur(3px);
  transition: none;
}
#wallpaper-switch:hover { background: rgba(23, 52, 82, 0.92); border-color: rgba(255, 255, 255, 0.5); }
#wallpaper-switch:active { background: rgba(12, 30, 50, 0.95); }
#wallpaper-switch .swatch {
  width: 13px;
  height: 13px;
  border: 1px solid rgba(255, 255, 255, 0.55);
  border-radius: 3px;
  background: var(--orange);
}
@media (max-width: 760px) { #wallpaper-switch { display: none; } }
/* What to build: two selectable cards. Selection paints from an `is-selected`
   class set in the browser the moment the box changes, so a click never waits
   on a server round trip. (A pure `:has(:checked)` rule looks equivalent but
   this Chromium does not re-evaluate `:has()` when `:checked` flips, so the
   card would stay stuck in its previous state.) */
.choose-label {
  margin: 0.05rem 0 0;
  color: var(--ink);
  font-size: 0.86rem;
  font-weight: 700;
}
#outputs-row { gap: 0.7rem; align-items: stretch; }
.output-card, .output-card > div, .output-card label,
.output-card input {
  /* Gradio puts `transition: 0.2s` on all properties here. A transitioning
     declaration outranks author !important for as long as it runs, so the
     selected state never painted. Instant is what a snappy toggle wants. */
  transition: none !important;
}
.output-card {
  gap: 0 !important;
  padding: 0 !important;
  border: 1px solid var(--line) !important;
  border-radius: 7px !important;
  background: #ffffff !important;
  overflow: hidden;
}
/* Gradio scopes its own block border rule with three classes, so the selected
   state is pinned by id to be sure it wins. */
#workbook-card.is-selected, #dashboard-choice.is-selected {
  border-color: var(--orange) !important;
  box-shadow: inset 0 0 0 1px var(--orange);
}
/* Only the header toggles; the panel beneath it is ordinary content. */
.output-card > div:first-child label {
  display: flex !important;
  align-items: center;
  gap: 0.7rem;
  min-height: 44px;
  margin: 0 !important;
  padding: 0.5rem 0.85rem !important;
  background: var(--paper);
  color: var(--ink) !important;
  font-weight: 700 !important;
  cursor: pointer;
  user-select: none;
}
.output-card > div:first-child label:hover { background: #e9eff7 !important; }
.output-card > div:first-child label:active { background: #d3dee9 !important; }
#workbook-card.is-selected > div:first-child label:hover,
#dashboard-choice.is-selected > div:first-child label:hover { background: #ffe6d7 !important; }
#workbook-card.is-selected > div:first-child label:active,
#dashboard-choice.is-selected > div:first-child label:active { background: #ffd8c2 !important; }
#workbook-card.is-selected > div:first-child label,
#dashboard-choice.is-selected > div:first-child label {
  background: #fff1e8 !important;
}
.output-card input[type="checkbox"] {
  width: 19px;
  height: 19px;
  flex: 0 0 auto;
  accent-color: var(--orange);
  border: 1px solid #9fb3c8 !important;
  border-radius: 3px !important;
  background-color: #ffffff !important;
  box-shadow: none !important;
  transition: none !important;
  cursor: pointer;
}
.output-card input[type="checkbox"]:checked {
  border-color: var(--orange) !important;
  background-color: var(--orange) !important;
}
.output-card > div:not(:first-child) { padding: 0.55rem 0.85rem !important; }
.output-card .card-note {
  margin: 0;
  color: var(--muted);
  font-size: 0.8rem;
  line-height: 1.45;
}
/* An unticked card keeps its detail visible but plainly inert. */
.output-card:not(.is-selected) > div:not(:first-child) {
  opacity: 0.4;
  pointer-events: none;
}
#year-input .block, #year-input { padding: 0 !important; }
/* The year field sat in a pale hairline box that read as a label rather than
   an input, so it was easy to miss that anything was wanted here. */
#year-input textarea, #year-input input {
  border: 2px solid var(--line) !important;
  border-radius: 7px !important;
  padding: 0.5rem 0.65rem !important;
  background: #ffffff !important;
  font-size: 1rem !important;
  font-variant-numeric: tabular-nums;
  overflow-y: hidden;
  resize: none;
}
#year-input textarea:hover, #year-input input:hover { border-color: #9fb6cf !important; }
#year-input textarea:focus, #year-input input:focus {
  border-color: var(--orange) !important;
  box-shadow: 0 0 0 3px rgba(231, 103, 42, 0.18) !important;
}
#year-input textarea::placeholder, #year-input input::placeholder { color: #93a7bd !important; }
#calculator-animation { display: none; align-items: center; gap: 0.65rem;
  min-height: 62px; margin: 0.25rem 0; padding: 0.55rem 0.8rem;
  border-radius: 3px; background: linear-gradient(90deg, #fff4ed, #eef4fa);
  border: 1px solid #e7b49f; }
#calculator-animation.is-running { display: flex; }
.calc-machine { position: relative; width: 84px; height: 52px; padding: 5px;
  border: 3px solid #526d88; border-radius: 4px; background: #f8fbff;
  box-shadow: 3px 3px 0 #526d88; transform: rotate(-3deg); }
.calc-display {
  display: grid;
  place-items: center;
  height: 16px;
  padding: 0 3px;
  overflow: hidden;
  border-radius: 2px;
  background: #cfe0ef;
  color: #16324f;
  font: 700 8.5px/16px "Consolas", monospace;
  letter-spacing: 0.04em;
}
.calc-keys { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3px; margin-top: 5px; }
.calc-key { height: 6px; border-radius: 1px; background: var(--orange); }
.calc-key:nth-child(2n) { background: #5e8fbe; }
.calc-key:nth-child(3n) { background: #f09a43; }
.calc-caption { font-size: 0.86rem; color: #405a73; }
.calc-caption .calc-dots { display: inline-block; width: 1.2em; text-align: left; }
#calculator-animation.is-running .calc-machine { animation: calculator-wobble 0.65s ease-in-out infinite alternate; }
#calculator-animation.is-running .calc-key { animation: calculator-blink 0.8s steps(2, end) infinite; }
#calculator-animation.is-running .calc-key:nth-child(2) { animation-delay: 0.15s; }
#calculator-animation.is-running .calc-key:nth-child(3) { animation-delay: 0.3s; }
@keyframes calculator-wobble { from { transform: rotate(-5deg) translateY(1px); }
  to { transform: rotate(5deg) translateY(-2px); } }
@keyframes calculator-blink { 50% { filter: brightness(1.45); transform: scale(0.85); } }
@media (max-width: 760px) {
  body, gradio-app { background: #edf3fa !important; }
  .gradio-container { width: calc(100% - 1rem) !important; padding-top: 0.5rem !important; }
  #upload-row, #action-row, #download-row, #dashboard-controls { flex-direction: column; }
  #upload-row > div, #run-button { min-width: 100%; }
  .upload-row { grid-template-columns: minmax(0, 1fr); }
  .upload-native-actions { justify-content: flex-start; }
  #upload-card { display: flex !important; }
  #upload-card > #balance-upload,
  #upload-card > #export-readout { width: 100%; }
  #upload-card > #balance-upload,
  #upload-card > #export-readout {
    border: 1px solid #c4d2e0 !important;
    border-radius: 6px !important;
  }
  #advanced-options > button::after { display: none; }
  #clear-dashboards { align-self: stretch; max-width: none; }
  #huggingface-space-header { transform: scale(0.72); }
}
"""

APP_CSS = APP_CSS.replace("__LEAP_WALLPAPER_URL__", LEAP_WALLPAPER_URL)

APP_JS = """
() => {
  // Wallpaper choice is a browser preference, so it is stored and applied here
  // rather than round-tripped through Gradio.
  const WALLPAPERS = __LEAP_WALLPAPERS__;
  const STORE_KEY = 'leap_balance_review_wallpaper';
  let wallpaperIndex = 0;
  const applyWallpaper = (index, remember) => {
    if (!WALLPAPERS.length) return;
    wallpaperIndex = ((index % WALLPAPERS.length) + WALLPAPERS.length) % WALLPAPERS.length;
    const choice = WALLPAPERS[wallpaperIndex];
    document.documentElement.style.setProperty('--leap-wallpaper', choice.layers);
    const button = document.querySelector('#wallpaper-switch');
    if (button) button.title = 'Wallpaper: ' + choice.name + ' (click to change)';
    if (remember) { try { localStorage.setItem(STORE_KEY, choice.name); } catch (e) {} }
  };
  const installWallpaperSwitch = () => {
    if (document.querySelector('#wallpaper-switch')) return;
    const button = document.createElement('button');
    button.id = 'wallpaper-switch';
    button.type = 'button';
    button.innerHTML = '<span class="swatch" aria-hidden="true"></span>Change wallpaper';
    button.addEventListener('click', () => applyWallpaper(wallpaperIndex + 1, true));
    document.body.appendChild(button);
    let saved = null;
    try { saved = localStorage.getItem(STORE_KEY); } catch (e) {}
    const found = WALLPAPERS.findIndex((w) => w.name === saved);
    applyWallpaper(found >= 0 ? found : 0, false);
  };
  // Gradio writes the dropzone prompts as bare text nodes with no component
  // option behind them, so naming the required file has to happen here. The
  // drag-here half is dropped so the control reads as one button; dropping a
  // file still works, it is simply no longer offered as an alternative.
  const UPLOAD_WORDING = [
    ['Drop File Here', ''],
    ['Click to Upload', 'Choose your LEAP Energy Balance export(s) (.xlsx)'],
  ];
  const relabel = (selector, wording) => {
    const zone = document.querySelector(selector);
    if (!zone) return;
    const walker = document.createTreeWalker(zone, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const text = node.textContent.trim();
      const match = wording.find((pair) => pair[0] === text);
      if (match) node.textContent = match[1];
    }
  };
  const relabelUpload = () => {
    relabel('#balance-upload', UPLOAD_WORDING);
    relabel('#add-export', [
      ['Drop File Here', ''],
      ['Click to Upload', '+ Add another export'],
    ]);
  };
  // Reflect the tick immediately; Gradio's own round trip is far too slow to
  // be the thing that paints a button press.
  const syncOutputCards = () => {
    // A multi-file upload takes the workbook away; the readout carries the
    // flag so the card can be faded without another round trip.
    const readout = document.querySelector('#export-readout .export-readout');
    const multi = !!(readout && readout.dataset.multi === '1');
    document.querySelectorAll('.output-card').forEach((card) => {
      const box = card.querySelector('input[type="checkbox"]');
      if (box) card.classList.toggle('is-selected', box.checked);
    });
    const workbookCard = document.querySelector('#workbook-card');
    if (workbookCard) workbookCard.classList.toggle('is-unavailable', multi);
  };
  // Run readiness is server-owned. Gradio replaces its file input after an
  // upload, at which point the new browser input has no FileList even though
  // the parsed export remains valid on the server. Deriving readiness here
  // therefore disabled a correctly enabled Run button after every upload.
  document.addEventListener('change', (event) => {
    if (event.target.matches('.output-card input[type="checkbox"]')) syncOutputCards();
  }, true);
  document.addEventListener('click', (event) => {
    if (event.target.closest('.output-card label')) window.setTimeout(syncOutputCards, 0);
  }, true);
  // The overlay appends its launcher to the page body; the title bar is where
  // it belongs, and re-running is harmless once it is already there.
  const placeGuideLaunch = () => {
    const hero = document.querySelector('#app-hero');
    const launch = document.querySelector('#leap-guide-launch');
    if (hero && launch && launch.parentElement !== hero) hero.appendChild(launch);
  };
  // Cancel belongs to the work it stops. Gradio creates the button only while
  // a run is active; move that live component into the calculator strip when
  // it appears, preserving its callback and server-managed state.
  const placeCancelRun = () => {
    const animation = document.querySelector('#calculator-animation');
    const cancel = document.querySelector('#cancel-run');
    if (animation && cancel && cancel.parentElement !== animation) {
      animation.appendChild(cancel);
    }
  };
  // Gradio draws a remove cell on each file row only when more than one file
  // is loaded, so a single export could be replaced but not removed, and the
  // row looked unlike the rows beside it a moment earlier. Removing the only
  // export is exactly what the clear button does, so the cell is added here
  // and pointed at it.
  const addSingleFileRemove = () => {
    const holder = document.querySelector('#balance-upload');
    const clear = document.querySelector('#clear-export');
    if (!holder || !clear) return;
    const rows = [...holder.querySelectorAll('tr')]
      .filter((row) => row.querySelector('.filename'));
    const ourCells = [...holder.querySelectorAll('[data-single-remove]')];
    // A remove cell Gradio drew: any cell that is neither the name, nor the
    // download, nor the one we added. Testing the cell count instead would
    // count our own cell as Gradio's and take it straight back off again.
    const gradioRemoveCell = (row) => [...row.children].find((cell) =>
      !cell.classList.contains('filename')
      && !cell.classList.contains('download')
      && !cell.dataset.singleRemove);
    // Several files: Gradio draws its own on every row, so ours is spare.
    // This is what left two crosses side by side after a second export.
    if (rows.length !== 1 || gradioRemoveCell(rows[0])) {
      ourCells.forEach((cell) => cell.remove());
      return;
    }
    const row = rows[0];
    if (row.querySelector('[data-single-remove]')) return;
    const cell = document.createElement('td');
    cell.dataset.singleRemove = '1';
    cell.textContent = '×';
    cell.title = 'Remove this export';
    cell.setAttribute('role', 'button');
    cell.setAttribute('aria-label', 'Remove this export');
    cell.tabIndex = 0;
    const fire = () => { const b = clear.tagName === 'BUTTON' ? clear : clear.querySelector('button'); if (b) b.click(); };
    cell.addEventListener('click', fire);
    cell.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); fire(); }
    });
    row.appendChild(cell);
  };
  // Gradio ends each download link with a small arrow character, which sits
  // at whatever weight the font gives it. These are the files a run exists to
  // produce, so the mark is drawn instead, at the size of the text beside it.
  const DOWNLOAD_ICON =
    '<svg class="download-icon" viewBox="0 0 20 20" aria-hidden="true" fill="none" ' +
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M10 3v9"/><path d="M6 9l4 4 4-4"/><path d="M3.5 15.5v1A1.5 1.5 0 0 0 5 18h10a1.5 1.5 0 0 0 1.5-1.5v-1"/></svg>';
  const drawDownloadIcons = () => {
    document.querySelectorAll('#download-row td.download a, #balance-upload td.download a').forEach((link) => {
      if (link.dataset.iconDrawn === '1') return;
      link.dataset.iconDrawn = '1';
      const label = (link.textContent || '').replace(/[↓⇣⬇️]/g, '').trim();
      link.textContent = label;
      link.insertAdjacentHTML('beforeend', DOWNLOAD_ICON);
      if (!link.title) link.title = 'Download ' + label;
    });
  };
  // Gradio owns the real file controls, while the parsed row owns the useful
  // filename/economy/scenario/year context. Proxy the native actions into that
  // row so each export is one literal white bar rather than two joined boxes.
  const mergeUploadControls = () => {
    const holder = document.querySelector('#balance-upload');
    const parsedRows = [...document.querySelectorAll('#export-readout .upload-row')];
    const nativeRows = [...document.querySelectorAll('#balance-upload table.file-preview tr.file')];
    if (!holder) return;
    const hasMergedRows = !!(parsedRows.length && nativeRows.length);
    // Clearing the export removes both row sets. Undo the off-screen treatment
    // at the same moment so Gradio's original upload chooser becomes visible.
    holder.classList.toggle('is-merged-preview', hasMergedRows);
    if (!hasMergedRows) return;
    parsedRows.forEach((parsed, index) => {
      const native = nativeRows[index];
      if (!native) return;
      const sourceLink = native.querySelector('td.download a');
      const sourceRemove = native.querySelector('button[aria-label*="Remove"], [data-single-remove]');
      const signature = (sourceLink ? sourceLink.href : '') + '|' + (native.textContent || '').trim();
      const existing = parsed.querySelector('.upload-native-actions');
      if (existing && existing.dataset.signature === signature) return;
      if (existing) existing.remove();
      const actions = document.createElement('span');
      actions.className = 'upload-native-actions';
      actions.dataset.signature = signature;
      if (sourceLink) {
        const link = sourceLink.cloneNode(true);
        link.classList.add('upload-download');
        if (!link.querySelector('.download-icon')) {
          link.insertAdjacentHTML('beforeend', DOWNLOAD_ICON);
        }
        actions.appendChild(link);
      }
      if (sourceRemove) {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'upload-remove';
        remove.textContent = '×';
        remove.title = 'Remove this export';
        remove.setAttribute('aria-label', 'Remove this export');
        remove.addEventListener('click', () => sourceRemove.click());
        actions.appendChild(remove);
      }
      parsed.appendChild(actions);
    });
  };
  // The native dropdown value is a one-line input. Paint a compact copy so
  // the full ESTO vintage and arrow stay legible in one row.
  const styleEstoVintage = () => {
    const input = document.querySelector('#esto-vintage input');
    const wrap = document.querySelector('#esto-vintage .secondary-wrap');
    if (!input || !wrap) return;
    wrap.dataset.display = input.value || '';
    if (input.dataset.vintageBound !== '1') {
      input.dataset.vintageBound = '1';
      input.addEventListener('change', styleEstoVintage);
    }
  };
  // The input itself is deliberately compact. Measure all known labels once,
  // then make the control exactly wide enough for the longest one and pin it
  // to the right of the build heading.
  const sizeEstoVintage = () => {
    const label = document.querySelector('.choose-label span[data-vintage-labels]');
    const input = document.querySelector('#esto-vintage input');
    const form = document.querySelector('#build-choice-heading > .form');
    if (!label || !input || !form) return;
    let labels = [];
    try { labels = JSON.parse(label.dataset.vintageLabels || '[]'); } catch (e) {}
    if (!labels.length) return;
    const probe = document.createElement('span');
    probe.style.cssText = 'position:fixed;visibility:hidden;white-space:nowrap;font:inherit;font-size:0.8rem;';
    probe.style.fontFamily = getComputedStyle(input).fontFamily;
    document.body.appendChild(probe);
    const widest = Math.max(...labels.map((value) => {
      probe.textContent = value;
      return probe.getBoundingClientRect().width;
    }));
    probe.remove();
    form.style.setProperty('--esto-vintage-width', Math.ceil(widest + 84) + 'px');
  };
  // Upload parsing updates several controls independently. While Gradio is
  // applying those changes, use one small modal rather than exposing its
  // per-control "processing" states or allowing another click into the form.
  const installWaitOverlay = () => {
    if (!document.querySelector('#app-wait-overlay')) {
      const overlay = document.createElement('div');
      overlay.id = 'app-wait-overlay';
      overlay.setAttribute('role', 'status');
      overlay.setAttribute('aria-live', 'polite');
      overlay.innerHTML = '<div class="wait-card"><strong>Please wait</strong><span>Updating your exports</span></div>';
      document.body.appendChild(overlay);
    }
    const update = () => {
      const processing = document.querySelector(
        '#upload-card .pending, #upload-card .generating'
      );
      const runControl = document.querySelector('#run-button');
      const runButton = runControl && (runControl.tagName === 'BUTTON'
        ? runControl : runControl.querySelector('button'));
      const running = runButton && runButton.disabled &&
        (runButton.textContent || '').trim().toLowerCase().startsWith('running');
      document.body.classList.toggle('app-is-processing', !!processing && !running);
    };
    update();
    new MutationObserver(update).observe(document.body, {
      attributes: true,
      attributeFilter: ['class'],
      childList: true,
      subtree: true,
    });
  };
  const install = () => {
    relabelUpload();
    styleEstoVintage();
    placeGuideLaunch();
    placeCancelRun();
    addSingleFileRemove();
    drawDownloadIcons();
    mergeUploadControls();
    const button = runButtonEl();
    const animation = document.querySelector('#calculator-animation');
    const status = document.querySelector('#run-status textarea, #run-status input');
    if (!button || !animation || button.dataset.calculatorBound === '1') return;
    button.dataset.calculatorBound = '1';
    let running = false;
    let timer = null;
    const stop = () => {
      running = false;
      animation.classList.remove('is-running');
      if (timer) window.clearInterval(timer);
      timer = null;
    };
    button.addEventListener('click', () => {
      const initialStatus = status ? (status.value || '') : '';
      running = true;
      animation.classList.add('is-running');
      timer = window.setInterval(() => {
        const rawValue = status ? (status.value || '') : '';
        const value = rawValue.toLowerCase();
        if (rawValue && rawValue !== initialStatus &&
            !value.includes('running') && !value.includes('starting')) stop();
      }, 300);
      window.setTimeout(() => { if (running) stop(); }, 900000);
    });
  };
  // Elapsed time ticks in the browser: the build only yields every ten
  // seconds, which is far too coarse to watch.
  // Gradio puts elem_id on the button itself, so look for both shapes.
  const runButtonEl = () => {
    const node = document.querySelector('#run-button');
    if (!node) return null;
    return node.tagName === 'BUTTON' ? node : node.querySelector('button');
  };
  // One poller drives both the working animation and the clock inside it,
  // keyed off the run button's locked state. The previous binding watched a
  // status textbox that no longer carries a heartbeat.
  const installStopwatch = () => {
    const button = runButtonEl();
    if (!button || button.dataset.stopwatchBound === '1') return;
    button.dataset.stopwatchBound = '1';
    const clock = (total) => {
      const mins = Math.floor(total / 60);
      return mins + ':' + String(total % 60).padStart(2, '0');
    };
    // "Longer than usual" reads as something having gone wrong. Naming what
    // the estimate is measured against says the same thing without the alarm.
    const longerThanNote = (host) => {
      const samples = parseInt(host.dataset.samples || '0', 10);
      if (samples > 1) {
        return ' — longer than the average of the last ' + samples + ' runs';
      }
      if (samples === 1) return ' — longer than the one run measured so far';
      return ' — still going';
    };
    let startedAt = null;
    // The worker announces each step it starts. The server drops that text
    // into a hidden line; the caption is the place it belongs, next to the
    // clock, so it is copied across whenever it changes.
    const idleCaption = 'Checking balances and preparing your files';
    window.setInterval(() => {
      const host = document.querySelector('#calculator-animation');
      if (!host) return;
      const face = host.querySelector('.run-stopwatch');
      const value = host.querySelector('.stopwatch-value');
      const remaining = host.querySelector('.stopwatch-remaining');
      const step = host.querySelector('.calc-step');
      // A disabled Run button can also mean the form is not ready yet (for
      // example, on a fresh browser with no export or review year).  The
      // running label is the state set by lock_run_button/resume_run and is
      // therefore the part that distinguishes active work from idle input.
      const running = button.disabled &&
        (button.textContent || '').trim().toLowerCase().startsWith('running');
      host.classList.toggle('is-running', running);
      document.body.classList.toggle('run-active', running);
      if (step) {
        const announced = document.querySelector('#run-status .is-step');
        let text = idleCaption;
        if (running && announced) {
          text = (announced.textContent || '').trim();
          // The caption already trails an animated ellipsis.
          if (text.endsWith('.')) text = text.slice(0, -1);
        }
        if (step.textContent !== text) step.textContent = text;
      }
      if (!running) {
        startedAt = null;
        if (face) face.hidden = true;
        return;
      }
      if (startedAt === null) startedAt = Date.now();
      if (!face || !value) return;
      face.hidden = false;
      const elapsed = Math.floor((Date.now() - startedAt) / 1000);
      value.textContent = clock(elapsed);
      const expected = parseInt(host.dataset.expected || '0', 10);
      if (expected > 0 && remaining) {
        const left = expected - elapsed;
        remaining.textContent = left > 0
          ? ' — about ' + clock(left) + ' to go'
          : longerThanNote(host);
      }
    }, 500);
  };
  // Ask the server where the run got to as soon as the tab is looked at
  // again. Background tabs have their timers throttled, so without this the
  // page can sit on a stale "running" for as long as the browser felt like
  // sleeping. Only while a run is believed to be under way: idle presses
  // would be answered, but they would be noise.
  const installWakeRefresh = () => {
    if (document.body.dataset.wakeRefreshBound === '1') return;
    document.body.dataset.wakeRefreshBound = '1';
    let lastAsked = 0;
    const askNow = () => {
      const button = runButtonEl();
      const refresh = document.querySelector('#refresh-run');
      if (!button || !refresh || !button.disabled) return;
      const now = Date.now();
      // A tab can fire visibility and focus together; one ask is enough.
      if (now - lastAsked < 1000) return;
      lastAsked = now;
      (refresh.tagName === 'BUTTON' ? refresh : refresh.querySelector('button')).click();
    };
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') askNow();
    });
    window.addEventListener('focus', askNow);
    window.addEventListener('pageshow', askNow);
  };
  installWakeRefresh();
  installStopwatch();
  installWallpaperSwitch();
  installWaitOverlay();
  window.setTimeout(() => { install(); styleEstoVintage(); sizeEstoVintage(); syncOutputCards(); }, 150);
  new MutationObserver(install).observe(document.body, { childList: true, subtree: true });
}
"""

APP_JS = APP_JS.replace(
    "__LEAP_WALLPAPERS__", json.dumps(available_wallpapers())
)

from codebase.portable_release import developer_launcher  # noqa: E402
from codebase.portable_release.settings import DeveloperSettings  # noqa: E402
from codebase.utilities.leap_balance_export_resolver import (  # noqa: E402
    balance_export_unit_to_petajoule_multiplier,
    infer_balance_export_identity,
    inspect_balance_export_detail,
)


def _path_from_gradio_file(value: object, *, description: str) -> Path:
    """Return a validated local path from a Gradio File component value."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"Please upload {description}.")
    raw_path = getattr(value, "name", value)
    path = Path(str(raw_path))
    if not path.is_file():
        raise FileNotFoundError(f"Uploaded file was not found: {path.name}")
    return path


def _cleanup_stale_web_artifacts(
    *, max_age_seconds: int = WEB_ARTIFACT_MAX_AGE_SECONDS
) -> list[Path]:
    """Remove only this app's expired temporary run and download folders.

    Gradio keeps returned files available after a callback finishes, so the
    current run's artifacts cannot be deleted immediately. A bounded cleanup
    at the beginning of later runs prevents a long-lived Space from growing
    without limit while preserving recent downloads for a reasonable period.
    """
    now = datetime.now(timezone.utc).timestamp()
    removed: list[Path] = []
    temp_root = Path(tempfile.gettempdir())
    candidates = [
        candidate
        for prefix in WEB_ARTIFACT_PREFIXES
        for candidate in temp_root.glob(f"{prefix}*")
    ]
    if DASHBOARD_SERVE_ROOT.is_dir():
        candidates.extend(DASHBOARD_SERVE_ROOT.iterdir())
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        try:
            age_seconds = now - candidate.stat().st_mtime
            if age_seconds <= max_age_seconds:
                continue
            shutil.rmtree(candidate)
            removed.append(candidate)
        except (FileNotFoundError, OSError):
            # Another cleanup or the operating system may have removed a
            # file between the directory scan and deletion.
            continue
    return removed


def _safe_filename_token(value: object) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value).strip())
    return token.strip("_") or "unknown"


def _complete_run_archive_name(
    economy: object,
    scenario: object,
    *,
    created_at: datetime | None = None,
) -> str:
    """Return a recognisable, unique filename for a run's complete ZIP."""
    timestamp = _as_tokyo_time(created_at or datetime.now(timezone.utc))
    return (
        f"{_safe_filename_token(economy)}_"
        f"{_safe_filename_token(scenario)}_complete_run_archive_"
        f"{timestamp.strftime('%d%m%y_%H%M%S')}.zip"
    )


def _dashboard_archive_name(
    economy: object,
    scenario: object,
    *,
    created_at: datetime | None = None,
) -> str:
    """Return a short, recognisable filename for a self-contained dashboard ZIP."""
    timestamp = _as_tokyo_time(created_at or datetime.now(timezone.utc))
    economy_token = _safe_filename_token(economy)[:16].rstrip("_-") or "unknown"
    scenario_token = _safe_filename_token(scenario)[:12].rstrip("_-") or "unknown"
    return (
        f"{economy_token}_{scenario_token}_dashboard_"
        f"{timestamp.strftime('%d%m%y_%H%M%S')}.zip"
    )


def _source_commit() -> str:
    """Return the current source commit for the run summary."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
        if result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    return "source checkout commit unavailable"


def _repository_roots() -> dict[str, Path]:
    """Resolve the three source repositories for a run.

    Each one independently prefers its prepared copy under ``runtime/`` and
    falls back to the live sibling checkout, so a partially prepared tree still
    runs rather than failing on the first missing directory.
    """
    return {name: _source_root(name) for name in (
        "leap_initialisation",
        "leap_mappings",
        "leap_dashboard",
    )}


_ESTO_VINTAGE_PATTERN = re.compile(
    r"^00APEC_(\d{4})_low_with_subtotals(_PRELIMINARY)?\.csv$"
)
DEFAULT_ESTO_VINTAGE = "2024"


def _esto_vintage_choices() -> list[tuple[str, str]]:
    """Return maintained ESTO releases available to this local app."""
    choices: list[tuple[str, str]] = []
    for path in sorted(
        (INITIALISATION_ROOT / "data").glob("00APEC_*_low_with_subtotals*.csv")
    ):
        match = _ESTO_VINTAGE_PATTERN.match(path.name)
        if not match:
            continue
        issue, is_preliminary = match.group(1), bool(match.group(2))
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle), [])
        years = [int(column) for column in header if str(column).isdigit()]
        base_year = max(years) if years else "unknown"
        suffix = " — preliminary" if is_preliminary else ""
        choices.append((f"ESTO {issue} (base year {base_year}){suffix}", issue))
    if not choices:
        raise FileNotFoundError(
            "No maintained ESTO vintage was found in the active release."
        )
    return choices


def _esto_table_for_vintage(vintage: object) -> Path:
    """Resolve the selected maintained ESTO table."""
    issue = str(vintage or "").strip()
    for suffix in ("", "_PRELIMINARY"):
        candidate = (
            INITIALISATION_ROOT
            / "data"
            / f"00APEC_{issue}_low_with_subtotals{suffix}.csv"
        )
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"ESTO vintage {issue!r} is not available in this release.")


def _default_esto_vintage(choices: list[tuple[str, str]]) -> str:
    """Keep the reviewed 2024 ESTO vintage selected when it is packaged."""
    for _, issue in choices:
        if issue == DEFAULT_ESTO_VINTAGE:
            return issue
    raise FileNotFoundError(
        f"Required default ESTO vintage {DEFAULT_ESTO_VINTAGE!r} is not available."
    )


def _esto_base_year(path: Path) -> int:
    """Read the final ESTO history year without loading the whole table."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle), [])
    years = [int(column) for column in header if str(column).isdigit()]
    if not years:
        raise ValueError(f"The selected ESTO file has no year columns: {path.name}")
    return max(years)


def adjust_review_year_for_vintage(vintage: object, year: object) -> str:
    """Keep workbook years compatible with the selected ESTO base year."""
    text = str(year or "").strip()
    if not text:
        return text
    requested = _requested_years(text)
    if not requested:
        return text
    base_year = _esto_base_year(_esto_table_for_vintage(vintage))
    adjusted = sorted({max(value, base_year) for value in requested})
    return ", ".join(str(value) for value in adjusted)


def _build_context(run_root: Path):
    """Build the same live-repository context used by developer mode."""
    repository_roots = _repository_roots()
    validate_mapping_chain_bundle(
        INITIALISATION_ROOT / "config" / "portable_release_manifest.toml",
        repository_roots,
    )
    settings = DeveloperSettings(
        source_path=INITIALISATION_ROOT
        / "config"
        / "portable_release_manifest.toml",
        repositories=repository_roots,
        output_root=run_root / "output",
        input_root=run_root / "input",
        log_root=run_root / "logs",
    )
    context = developer_launcher.build_context(settings=settings)
    context.require_ready()
    context.activate_sys_path()
    return context


def _copy_input(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    copied = destination / source.name
    shutil.copy2(source, copied)
    return copied


def _write_diagnostics_bundle(
    *,
    bundle_path: Path,
    workbook_paths: list[Path],
    diagnostics_directory: Path | None,
    run_directory: Path,
    dashboard_directory: Path | None = None,
    log_directory: Path | None = None,
    uploaded_export_paths: list[Path] | None = None,
) -> None:
    """Package source exports, derived diagnostics, and a self-contained dashboard."""
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        manifest: dict[str, object] = {
            "format_version": 1,
            "archive_type": "complete_run",
            "uploaded_balance_exports": _add_uploaded_exports(
                bundle, uploaded_export_paths or []
            ),
            "review_workbooks": [],
        }
        workbook_records = manifest["review_workbooks"]
        assert isinstance(workbook_records, list)
        for index, workbook_path in enumerate(workbook_paths, start=1):
            archive_path = f"w/review_{index:02d}{workbook_path.suffix.lower()}"
            bundle.write(workbook_path, arcname=archive_path)
            workbook_records.append(
                {
                    "archive_path": archive_path,
                    "original_filename": workbook_path.name,
                }
            )
        if diagnostics_directory is not None and diagnostics_directory.is_dir():
            for path in sorted(diagnostics_directory.rglob("*")):
                if path.is_file():
                    bundle.write(
                        path,
                        arcname="r/" + path.relative_to(diagnostics_directory).as_posix(),
                    )
        for name in ("validation_report.txt", "run_manifest.json", "run_manifest.txt"):
            path = run_directory / name
            if path.is_file():
                bundle.write(path, arcname=f"r/{name}")
        if dashboard_directory is not None:
            manifest["dashboard"] = _add_dashboard_files(
                bundle,
                dashboard_directory,
                include_run_diagnostics=True,
            )
        if log_directory is not None and log_directory.is_dir():
            for path in sorted(log_directory.glob("*.log")):
                bundle.write(path, arcname=f"l/{path.name}")
        _write_archive_manifest(bundle, manifest)


def _dashboard_archive_entries(
    dashboard_directory: Path,
    *,
    include_run_diagnostics: bool,
) -> tuple[Path, dict[Path, str], list[dict[str, object]]]:
    """Map renderer paths to a compact, stable dashboard archive layout."""
    bundle_root = _dashboard_bundle_root(dashboard_directory)
    entries: dict[Path, str] = {}
    dashboard_sets: list[dict[str, object]] = []

    if bundle_root != dashboard_directory.parent:
        dashboard_roots = sorted(
            path.parent
            for path in bundle_root.glob("*/dashboards")
            if path.parent.name != "diagnostics"
        )
    else:
        dashboard_roots = [dashboard_directory.parent]

    for index, dashboard_root in enumerate(dashboard_roots):
        pages = (
            dashboard_root / "dashboards"
            if (dashboard_root / "dashboards").is_dir()
            else dashboard_directory
        )
        archive_root = f"d/{index}"
        dashboard_sets.append(
            {
                "archive_root": archive_root,
                "source_directory": dashboard_root.name,
            }
        )
        for source_name, archive_name in (
            ("dashboards", "p"),
            ("chart_bundles", "c"),
            ("supporting_files", "s"),
        ):
            source_directory = dashboard_root / source_name
            if source_name == "dashboards":
                source_directory = pages
            if not source_directory.is_dir():
                continue
            for path in sorted(source_directory.rglob("*")):
                if path.is_file():
                    entries[path.resolve()] = (
                        f"{archive_root}/{archive_name}/"
                        f"{path.relative_to(source_directory).as_posix()}"
                    )

    diagnostics_root = bundle_root / "diagnostics"
    for source_name, archive_name in (("dashboards", "p"), ("supporting_files", "s")):
        source_directory = diagnostics_root / source_name
        if source_directory.is_dir():
            for path in sorted(source_directory.rglob("*")):
                if path.is_file():
                    entries[path.resolve()] = (
                        f"x/{archive_name}/{path.relative_to(source_directory).as_posix()}"
                    )

    if include_run_diagnostics:
        mapping_chain = bundle_root / "mapping_chain"
        if mapping_chain.is_dir():
            for path in sorted(mapping_chain.rglob("*")):
                if path.is_file():
                    entries[path.resolve()] = (
                        "m/" + path.relative_to(mapping_chain).as_posix()
                    )

    shortcut = bundle_root / "OPEN THE DASHBOARD.html"
    if shortcut.is_file():
        entries[shortcut.resolve()] = "OPEN THE DASHBOARD.html"
    elif bundle_root == dashboard_directory.parent:
        shortcut = dashboard_directory.parent / "OPEN THE DASHBOARD.html"
        if shortcut.is_file():
            entries[shortcut.resolve()] = "OPEN THE DASHBOARD.html"

    return bundle_root, entries, dashboard_sets


def _rewrite_archive_html(
    page_html: str,
    *,
    source_path: Path,
    archive_path: str,
    entries: dict[Path, str],
) -> str:
    """Rewrite local HTML references after compacting the archive tree."""
    replacements: list[tuple[str, str]] = []
    archive_parent = posixpath.dirname(archive_path) or "."
    for target_path, target_archive_path in entries.items():
        source_reference = os.path.relpath(
            target_path, start=source_path.resolve().parent
        ).replace("\\", "/")
        archive_reference = posixpath.relpath(target_archive_path, archive_parent)
        replacements.append((source_reference, archive_reference))

    plotly_reference = posixpath.relpath(PLOTLY_ARCHIVE_PATH, archive_parent)
    replacements.append((PLOTLY_CDN_URL, plotly_reference))
    for source_reference, archive_reference in sorted(
        replacements, key=lambda item: len(item[0]), reverse=True
    ):
        for quote in ('"', "'"):
            page_html = page_html.replace(
                f"{quote}{source_reference}{quote}",
                f"{quote}{archive_reference}{quote}",
            )
        page_html = page_html.replace(
            f"url={source_reference}", f"url={archive_reference}"
        )
    return page_html


def _add_dashboard_files(
    bundle: zipfile.ZipFile,
    dashboard_directory: Path,
    *,
    include_run_diagnostics: bool = False,
) -> dict[str, object]:
    """Add an offline-capable dashboard using short internal paths."""
    if not dashboard_directory.is_dir():
        return {"dashboard_sets": [], "entry_count": 0}

    plotly_bundle = _plotly_offline_bundle_path()
    _, entries, dashboard_sets = _dashboard_archive_entries(
        dashboard_directory,
        include_run_diagnostics=include_run_diagnostics,
    )
    for path, archive_path in sorted(entries.items(), key=lambda item: item[1]):
        if path.suffix.casefold() == ".html":
            page_html = _rewrite_archive_html(
                path.read_text(encoding="utf-8"),
                source_path=path,
                archive_path=archive_path,
                entries=entries,
            )
            bundle.writestr(archive_path, page_html.encode("utf-8"))
        else:
            bundle.write(path, arcname=archive_path)
    bundle.write(plotly_bundle, arcname=PLOTLY_ARCHIVE_PATH)
    return {
        "dashboard_sets": dashboard_sets,
        "entry_count": len(entries) + 1,
        "plotly_archive_path": PLOTLY_ARCHIVE_PATH,
        "run_diagnostics_included": include_run_diagnostics,
    }


def _plotly_offline_bundle_path() -> Path:
    """Return Plotly's installed browser runtime for offline dashboard archives."""
    import plotly

    bundle_path = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    if not bundle_path.is_file():
        raise FileNotFoundError(
            "The installed Plotly package does not include plotly.min.js, "
            "which is required to create an offline dashboard archive."
        )
    return bundle_path


def _add_uploaded_exports(
    bundle: zipfile.ZipFile, uploaded_export_paths: list[Path]
) -> list[dict[str, str]]:
    """Add source exports under canonical names and return manifest records."""
    seen_paths: set[Path] = set()
    records: list[dict[str, str]] = []
    for export_path in uploaded_export_paths:
        export_path = Path(export_path)
        resolved_path = export_path.resolve()
        if resolved_path in seen_paths or not export_path.is_file():
            continue
        seen_paths.add(resolved_path)
        archive_path = f"in/source_{len(records) + 1:02d}{export_path.suffix.lower()}"
        bundle.write(export_path, arcname=archive_path)
        records.append(
            {
                "archive_path": archive_path,
                "original_filename": export_path.name,
            }
        )
    return records


def _write_archive_manifest(
    bundle: zipfile.ZipFile, manifest: dict[str, object]
) -> None:
    """Write human-readable metadata for canonical archive filenames."""
    bundle.writestr(
        ARCHIVE_MANIFEST_PATH,
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
    )


def _write_dashboard_bundle(
    *,
    bundle_path: Path,
    dashboard_directory: Path,
    uploaded_export_paths: list[Path] | None = None,
) -> None:
    """Write an offline dashboard archive with its uploaded balance exports."""
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        manifest: dict[str, object] = {
            "format_version": 1,
            "archive_type": "dashboard_review",
            "uploaded_balance_exports": _add_uploaded_exports(
                bundle, uploaded_export_paths or []
            ),
        }
        manifest["dashboard"] = _add_dashboard_files(bundle, dashboard_directory)
        _write_archive_manifest(bundle, manifest)


def _dashboard_pages(dashboard_directory: Path) -> list[str]:
    """Return dashboard page filenames suitable for the page selector."""
    return sorted(
        path.name
        for path in dashboard_directory.glob("*.html")
        if path.name != "index.html"
    )


def _dashboard_bundle_root(dashboard_directory: Path) -> Path:
    """Return the root shared by comparison-basis and diagnostics folders."""
    dashboard_directory = Path(dashboard_directory)
    dashboard_key = dashboard_directory.parent.name
    if dashboard_directory.name == "dashboards" and re.fullmatch(
        r"\d{2}[A-Za-z]{2,}(?:__[A-Za-z0-9_-]+)?",
        dashboard_key,
    ):
        return dashboard_directory.parent.parent
    return dashboard_directory.parent


def _dashboard_bundle_pages(dashboard_directory: Path) -> list[tuple[str, Path]]:
    """Return every HTML page needed for basis switching and diagnostics."""
    root = _dashboard_bundle_root(dashboard_directory)
    if root == dashboard_directory.parent:
        return [
            (path.name, path)
            for path in sorted(dashboard_directory.glob("*.html"))
            if path.name != "index.html"
        ]
    return [
        (path.relative_to(root).as_posix(), path)
        for path in sorted(root.glob("*/dashboards/*.html"))
        if path.name != "index.html"
    ]


def _compress_dashboard_html(page_html: str) -> str:
    compressed = gzip.compress(page_html.encode("utf-8"), compresslevel=9)
    return base64.b64encode(compressed).decode("ascii")


def _decompress_dashboard_html(encoded_html: str) -> str:
    compressed = base64.b64decode(encoded_html.encode("ascii"))
    return gzip.decompress(compressed).decode("utf-8")


def _browser_dashboard_choices(records: object) -> list[tuple[str, str]]:
    if not isinstance(records, list):
        return []
    choices = []
    for record in records:
        if not isinstance(record, dict) or not record.get("archive_id"):
            continue
        choices.append(
            (
                f"{record.get('economy', 'unknown')} / "
                f"{record.get('scenario', 'unknown')} / "
                f"{record.get('years', '')} "
                f"({_run_timestamp_label(record)})",
                str(record["archive_id"]),
            )
        )
    return choices


def _run_timestamp_label(record: object) -> str:
    """Return one consistent, compact timestamp for a prior run."""
    value = record.get("created_at") if isinstance(record, dict) else record
    formatted = _format_tokyo_timestamp(value, include_seconds=True)
    if formatted:
        try:
            parsed = datetime.strptime(
                formatted, "%Y-%m-%d %H:%M:%S JST"
            ).replace(tzinfo=TOKYO_TIMEZONE)
            return parsed.strftime("%d %b %Y %H:%M:%S JST")
        except ValueError:
            pass
    if isinstance(record, dict):
        archive_id = str(record.get("archive_id") or "")
        match = re.match(r"^(\d{8}T\d{6}Z)", archive_id)
        if match:
            try:
                parsed = datetime.strptime(
                    match.group(1), "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=timezone.utc)
                return _as_tokyo_time(parsed).strftime("%d %b %Y %H:%M:%S JST")
            except ValueError:
                pass
    return "Time unavailable"


def _saved_dashboard_button_labels(records: list[dict[str, object]]) -> list[str]:
    """Describe saved dashboards with the same timestamp format every time."""
    base_labels: list[str] = []
    for record in records:
        economy = str(record.get("economy") or "Unknown economy")
        scenarios = [
            str(name).strip()
            for name in (record.get("scenarios") or [])
            if str(name).strip()
        ]
        if not scenarios and str(record.get("scenario") or "").strip():
            scenarios = [str(record["scenario"]).strip()]
        details = [economy]
        if scenarios:
            details.append(" + ".join(dict.fromkeys(scenarios)))
        years = str(record.get("years") or "").strip()
        if years:
            details.append(years)
        base_labels.append(" · ".join(details))

    return [
        (
            f"{base_label} · {_run_timestamp_label(record)} dashboard"
            if base_labels.count(base_label) > 1
            else f"{base_label} dashboard"
        )
        for record, base_label in zip(records, base_labels)
    ]


def _browser_dashboard_record(
    archive_id: str | None,
    records: object,
) -> dict[str, object] | None:
    if not archive_id or not isinstance(records, list):
        return None
    return next(
        (
            record
            for record in records
            if isinstance(record, dict) and record.get("archive_id") == archive_id
        ),
        None,
    )


def _dashboard_snapshot(
    dashboard_directory: Path,
    *,
    economy: str,
    scenario: str,
    years: object,
    scenarios: object = (),
) -> dict[str, object]:
    """Create a compressed browser-local snapshot of every dashboard page."""
    pages = {}
    for page_name, page_path in _dashboard_bundle_pages(dashboard_directory):
        page_html = _inline_dashboard_chart_bundle(
            page_path,
            page_path.read_text(encoding="utf-8"),
        )
        pages[page_name] = _compress_dashboard_html(page_html)
    return {
        "archive_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}",
        "created_at": _format_tokyo_timestamp(datetime.now(timezone.utc)),
        "economy": economy,
        "scenario": scenario,
        # Kept beside it so a restored review knows whether the economy had
        # both scenarios, which is what decides the toggle.
        "scenarios": [str(name) for name in (scenarios or ()) if str(name).strip()]
        or ([scenario] if str(scenario).strip() else []),
        "years": years,
        "pages": pages,
        "storage": "browser-local",
    }


def _inline_dashboard_chart_bundle(page_path: Path, page_html: str) -> str:
    """Inline the page's generated chart bundle for iframe rendering."""
    marker = 'src="../chart_bundles/'
    rendered = page_html
    while marker in rendered:
        start = rendered.index(marker) + len(marker)
        end = rendered.index('"', start)
        bundle_name = rendered[start:end]
        bundle_path = page_path.parent.parent / "chart_bundles" / bundle_name
        if not bundle_path.is_file():
            break
        bundle_text = bundle_path.read_text(encoding="utf-8")
        script_tag = f"<script>\n{bundle_text}\n</script>"
        old_tag = f'<script src="../chart_bundles/{bundle_name}"></script>'
        rendered = rendered.replace(old_tag, script_tag, 1)
    return rendered


def _publish_dashboard_pages(
    pages: dict[str, str],
    *,
    economy: str,
    scenario: str,
    years: object,
    scenarios: object = (),
) -> str | None:
    """Write dashboard pages and return the Energy balance overview URL.

    The pages are the same compressed snapshots kept in browser storage, so a
    saved review reopens through exactly the path a fresh one does. Serving
    them as files is what lets the app hand out a link rather than embed the
    dashboard in the page.
    """
    if not pages:
        return None
    run_directory = DASHBOARD_SERVE_ROOT / uuid4().hex
    run_directory.mkdir(parents=True, exist_ok=True)

    # An economy uploaded with both scenarios keeps its toggle; one uploaded
    # with a single scenario is pinned to it. A review saved before this was
    # recorded carries one scenario, which is the pinned case.
    available = [str(name) for name in (scenarios or ()) if str(name).strip()]
    if not available:
        available = [scenario] if str(scenario).strip() else []
    modes = {_scenario_mode(name) for name in available}
    allow_switching = len({mode for mode in modes if mode}) > 1

    for page_name in sorted(pages):
        page_html = _decompress_dashboard_html(str(pages[page_name]))
        relative_path = Path(str(page_name).replace("\\", "/"))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"Unsafe dashboard page path: {page_name!r}")
        destination = run_directory / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            _locked_dashboard_html(
                page_html, scenario, allow_switching=allow_switching
            ),
            encoding="utf-8",
        )

    page_names = [Path(str(page_name).replace("\\", "/")) for page_name in pages]
    preferred_page = next(
        (
            page_name
            for preferred in ("energy_balance_overview.html", "total_demand.html")
            for page_name in sorted(
                page_names,
                key=lambda path: (len(path.parts), path.as_posix()),
            )
            if page_name.name == preferred and "__" not in page_name.as_posix()
        ),
        sorted(page_names, key=lambda path: (len(path.parts), path.as_posix()))[0],
    )
    return f"/gradio_api/file={(run_directory / preferred_page).as_posix()}"


def _scenario_mode(scenario: str) -> str:
    """Return the renderer's mode token for a scenario name, if it has one."""
    return {"reference": "ref", "target": "tgt"}.get(str(scenario).casefold(), "")


def _locked_dashboard_html(
    page_html: str, scenario: str, *, allow_switching: bool = False
) -> str:
    """Pin a generated page to the scenario this run actually produced.

    A page always carries the renderer's Reference/Target toggle, but it only
    means anything when the economy was uploaded with both: switching to a
    scenario with no export behind it empties every chart. So the toggle is
    left in place for an economy that has both scenarios and taken away for
    one that does not, where the single scenario is pinned instead.

    The dashboard switcher goes either way. It jumps between dashboards in the
    renderer's own output folder, which is not what this app hands out.
    """
    scenario_mode = "" if allow_switching else _scenario_mode(scenario)
    hidden = (
        ".dashboard-switcher"
        if allow_switching
        else ".dashboard-switcher, .scenario-toggle"
    )
    locked_controls = """
<style>
  %(hidden)s { display:none !important; }
</style>
<script>
  (function () {
    var mode = %(mode)s;
    if (mode) {
      try { localStorage.setItem('common-esto-scenario-mode', mode); } catch (e) {}
      if (window.applyScenarioMode) {
        document.querySelectorAll('[data-plot-id]').forEach(function (plot) {
          window.applyScenarioMode(plot);
        });
      }
    }
  }());
</script>
""" % {"hidden": hidden, "mode": json.dumps(scenario_mode)}
    if "</body>" in page_html:
        return page_html.replace("</body>", locked_controls + "</body>", 1)
    return page_html + locked_controls


OUTPUT_WORKBOOK_LABEL = "Balance review workbook"
OUTPUT_DASHBOARD_LABEL = "Dashboard"

# An empty result is represented by empty HTML. The Results card is revealed
# only when a callback returns a result link, warning, or downloadable file.
RESULTS_EMPTY_HTML = ""


def _status_html(message: str, *, tone: str = "") -> str:
    """Return run status as escaped markup, or nothing at all when silent.

    The ``is-step`` tone is written for the browser rather than the reader: it
    is hidden by CSS, and the script copies its text into the calculator
    caption so a run's progress is announced where the clock already is.
    """
    text = str(message or "").strip()
    if not text:
        return ""
    if not tone:
        tone = "is-failed" if text.lower().startswith("build failed") else "is-done"
    return f"<p class='run-status-line {tone}'>{html.escape(text)}</p>"


def job_step(job_id: object) -> str:
    """Return the worker's current step for a running job, without a clock."""
    job = _job_snapshot(str(job_id or ""))
    if not job or job.get("state") not in {"running", "cancel_requested"}:
        return ""
    return str(job.get("message") or "Working")


def _run_status_line(
    *,
    wants_workbook: bool,
    wants_dashboard: bool,
    dashboard_ok: bool,
    partial_note: str = "",
    runtime_seconds: dict[str, float | None] | None = None,
) -> str:
    """Return a plain-language summary of what a finished run produced."""
    built = []
    if wants_workbook:
        built.append("review workbook")
    if wants_dashboard and dashboard_ok:
        built.append("dashboard")
    if not built:
        return "Nothing was built."
    made = " and ".join(built)
    if wants_dashboard and not dashboard_ok:
        status = f"Built the {made}; the dashboard failed."
    elif partial_note:
        status = f"Built the {made}. {partial_note}"
    else:
        status = f"Built the {made}."
    if runtime_seconds:
        measured = []
        for label, key in (("workbook", "workbook"), ("dashboard", "dashboard")):
            seconds = runtime_seconds.get(key)
            if seconds is not None:
                # Minutes and seconds: "159.5s" is a number to convert before
                # it means anything, and the tenth was never worth reading.
                measured.append(f"{label}: {format_duration(seconds)}")
        if measured:
            status += " Runtime — " + ", ".join(measured) + "."
    return status


def _card_runtime_note_html(
    profile: dict[str, object],
    group: str,
    *,
    years: int,
    economies: int = 1,
    version_comparison: bool = False,
) -> str:
    """Return one process card's runtime note."""
    return (
        "<p class='card-note runtime-note'>"
        + html.escape(
            format_runtime_note(
                profile,
                process_group=group,
                years=years,
                economies=economies,
                version_comparison=version_comparison,
            )
        )
        + "</p>"
    )


def _calculator_html(
    profile: dict[str, object],
    *,
    want_dashboard: bool,
    years: int,
    economies: int = 1,
    version_comparison: bool = False,
) -> str:
    """Return the working animation, carrying the elapsed clock with it.

    The stopwatch lives here rather than beside the estimates because this
    block is only shown while a build is running, so the clock appears exactly
    when it is useful and takes no space the rest of the time. It ticks in the
    browser: the build itself only yields every ten seconds.
    """
    group = (
        "dashboard"
        if version_comparison and want_dashboard
        else "full_run"
        if want_dashboard
        else "workbook"
    )
    estimate, _ = estimate_runtime(
        profile,
        process_group=group,
        years=years,
        economies=economies,
        version_comparison=version_comparison,
    )
    expected = f' data-expected="{int(estimate)}"' if estimate else ""
    # How many measurements the estimate rests on, so the page can name what it
    # is comparing against rather than implying something has gone wrong.
    samples = samples_behind_estimate(
        profile,
        process_group=group,
        years=years,
        economies=economies,
        version_comparison=version_comparison,
    )
    expected += f' data-samples="{samples}"'
    return (
        f'<div id="calculator-animation" role="status" aria-live="polite"{expected}>'
        '<div class="calc-machine" aria-hidden="true">'
        '<div class="calc-display">CALCULATING</div>'
        '<div class="calc-keys">'
        + ('<i class="calc-key"></i>' * 6)
        + "</div></div>"
        '<div class="calc-caption">'
        '<span class="calc-step">Checking balances and preparing your files</span>'
        '<span class="calc-dots">...</span>'
        '<span class="run-stopwatch" hidden>'
        '<span class="stopwatch-dot" aria-hidden="true"></span>'
        'Elapsed <strong class="stopwatch-value">0:00</strong>'
        '<span class="stopwatch-remaining"></span>'
        "</span></div></div>"
    )


def update_runtime_notes(
    year: object,
    want_workbook: object,
    want_dashboard: object,
    balance_export_workbook: object = None,
    compare_versions: object = False,
) -> tuple[str, str, str, object]:
    """Re-quote the estimates for the years typed and the economies uploaded."""
    import gradio as gr

    profile = _hosted_runtime_profile()
    years = max(len(_requested_years(year)), 1)
    uploads = without_duplicates(read_uploads(balance_export_workbook))
    economies = max(len(group_by_economy(uploads)), 1)
    ready_to_run = bool(_uploaded_paths(balance_export_workbook)) and bool(
        _requested_years(year)
    )
    return (
        _card_runtime_note_html(profile, "workbook", years=years),
        _card_runtime_note_html(
            profile,
            "dashboard",
            years=years,
            economies=economies,
            version_comparison=bool(compare_versions),
        ),
        _calculator_html(
            profile,
            want_dashboard=bool(want_dashboard) or not bool(want_workbook),
            years=years,
            economies=economies,
            version_comparison=bool(compare_versions),
        ),
        gr.Button("Run", interactive=ready_to_run),
    )


def toggle_add_export(balance_export_workbook: object) -> object:
    """Show the add-another picker only once something has been chosen."""
    import gradio as gr

    return gr.File(visible=bool(_uploaded_paths(balance_export_workbook)))


def append_uploaded_exports(current: object, added: object) -> tuple[object, object]:
    """Add files to the set already chosen, rather than replacing it.

    A file already present is not added twice: uploads land in their own
    temporary directory, so sameness is judged by name and size rather than
    by path.
    """
    paths = _uploaded_paths(current)
    seen = {(path.name, path.stat().st_size) for path in paths}
    for path in _uploaded_paths(added):
        key = (path.name, path.stat().st_size)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    # Clearing the second picker lets the same file be added again later.
    return [str(path) for path in paths], None


def clear_uploaded_export() -> tuple[
    object, str, object, object, object, object, object, object, bool, str
]:
    """Drop the loaded export so a different one can be added.

    Gradio's own clear control is an unlabelled icon, which is easy to miss;
    this is the same action said plainly. Results from a finished run are left
    alone, because those files are still valid and worth keeping.

    One value per wired output, no more: the two trailing dropdowns this used
    to return were the economy and scenario pickers, removed when the run
    started rendering every economy provided. Gradio raised on the extras
    rather than ignoring them, so the button that clears the export was the
    one control that could not be pressed.
    """
    import gradio as gr

    return (
        None,
        EXPORT_PROMPT_HTML,
        gr.Textbox(visible=False, value=""),
        gr.Button(visible=False),
        gr.File(visible=False),
        gr.Column(visible=False),
        gr.Dropdown(value=None, choices=[]),
        gr.Dropdown(value=None, choices=[]),
        False,
        "",
    )


# Runs are executed off the request that started them, in a worker thread
# owned by the process rather than by a browser tab. Closing the tab, losing
# the connection or reloading the page therefore does not cancel a build: the
# page reattaches by job id and keeps polling. The dictionary lives as long as
# the process does, so a Space restart is still the end of a run.
RUN_JOBS: dict[str, dict[str, object]] = {}
RUN_JOBS_LOCK = threading.Lock()
JOB_RETENTION_SECONDS = 6 * 60 * 60


class RunCancelled(Exception):
    """Raised at a safe workflow boundary after a user requests cancellation."""


def _forget_stale_jobs() -> None:
    """Drop finished jobs nobody came back for."""
    cutoff = time.time() - JOB_RETENTION_SECONDS
    with RUN_JOBS_LOCK:
        for job_id in [
            key
            for key, job in RUN_JOBS.items()
            if job.get("state") not in {"running", "cancel_requested"}
            and float(job.get("finished") or 0) < cutoff
        ]:
            RUN_JOBS.pop(job_id, None)


def _job_snapshot(job_id: str) -> dict[str, object] | None:
    with RUN_JOBS_LOCK:
        job = RUN_JOBS.get(job_id)
        return dict(job) if job else None


def _set_job(job_id: str, **fields: object) -> None:
    with RUN_JOBS_LOCK:
        job = RUN_JOBS.setdefault(job_id, {})
        job.update(fields)


def _job_cancel_requested(job_id: str) -> bool:
    """Return whether cancellation was requested for this background job."""
    with RUN_JOBS_LOCK:
        job = RUN_JOBS.get(job_id) or {}
        signal = job.get("cancel_signal")
        return bool(isinstance(signal, threading.Event) and signal.is_set())


def _raise_if_cancelled(cancellation_check: object = None) -> None:
    """Stop at a safe boundary when the supplied cancellation check is true."""
    if callable(cancellation_check) and cancellation_check():
        raise RunCancelled("Run cancelled by the user.")


def start_run(
    want_workbook: object,
    want_dashboard: object,
    year: object,
    economy_override: str,
    balance_export_workbook: object,
    browser_archives: object,
    upload_is_live: object = True,
    esto_vintage_choice: object = None,
    compare_versions: object = False,
    original_export_name: object = None,
    new_export_name: object = None,
) -> tuple[str, object]:
    """Begin a build in the background and return its job id.

    The uploaded files are copied before the worker starts, because Gradio
    clears its upload directory once the request that carried them ends.
    """
    import gradio as gr

    if not upload_is_live:
        return "", gr.Button(visible=False)

    _forget_stale_jobs()
    job_id = uuid4().hex
    keep_root = Path(tempfile.mkdtemp(prefix="leap_balance_review_web_"))
    kept = []
    for path in _uploaded_paths(balance_export_workbook):
        kept.append(str(_copy_input(path, keep_root / "uploads")))

    _set_job(
        job_id,
        state="running",
        started=time.time(),
        finished=None,
        message="Starting the run.",
        result=None,
        cancel_signal=threading.Event(),
    )

    def worker() -> None:
        def report(message: str) -> None:
            if not _job_cancel_requested(job_id):
                _set_job(job_id, message=message)

        try:
            result = build_review_from_export(
                want_workbook,
                want_dashboard,
                year,
                economy_override,
                kept,
                None,
                None,
                browser_archives,
                progress=report,
                cancellation_check=lambda: _job_cancel_requested(job_id),
                esto_vintage_choice=esto_vintage_choice,
                compare_versions=compare_versions,
                original_export_name=original_export_name,
                new_export_name=new_export_name,
            )
            _raise_if_cancelled(lambda: _job_cancel_requested(job_id))
            _set_job(
                job_id, state="done", finished=time.time(), result=result, message=""
            )
        except RunCancelled:
            _set_job(
                job_id,
                state="cancelled",
                finished=time.time(),
                message="Run cancelled. No new results were saved.",
                result=None,
            )
        except Exception as error:  # A crash must still reach the page.
            _set_job(
                job_id,
                state="failed",
                finished=time.time(),
                message=f"Build failed: {error}",
                result=None,
            )

    # Not a daemon: a build that has started should be allowed to finish.
    threading.Thread(target=worker, name=f"leap-run-{job_id[:8]}", daemon=False).start()
    return job_id, gr.Button("Cancel run", visible=True, interactive=True)


def activate_run_timer(job_id: object) -> object:
    """Poll only when a background job was actually started."""
    import gradio as gr

    return gr.Timer(active=bool(str(job_id or "").strip()))


def cancel_run(job_id: object) -> tuple[object, str]:
    """Request cancellation and disable the control until the worker stops."""
    import gradio as gr

    key = str(job_id or "")
    with RUN_JOBS_LOCK:
        job = RUN_JOBS.get(key)
        if not job or job.get("state") not in {"running", "cancel_requested"}:
            return gr.Button(visible=False), gr.skip()
        signal = job.get("cancel_signal")
        if isinstance(signal, threading.Event):
            signal.set()
        job.update(
            state="cancel_requested",
            message="Cancelling after the current step finishes safely.",
        )
    return (
        gr.Button("Cancelling…", visible=True, interactive=False),
        _status_html("Cancelling after the current step finishes safely.", tone="is-step"),
    )


def superseded_results_html(current: object) -> str:
    """Mark results still on screen as belonging to the run being replaced.

    A finished run's links stay put while the next one works, and a dashboard
    link that opens is easily read as this run having finished. The panel says
    whose results these are instead.
    """
    text = str(current or "").strip()
    if not text or "results-empty" in text or "results-superseded" in text:
        return text
    return (
        "<div class='results-superseded'>"
        "<p class='superseded-note'>A new run is under way. These are the "
        "results of your previous run, and will be replaced when it finishes."
        "</p>" + text + "</div>"
    )


def lock_run_button(result_links: object = "") -> tuple[object, str]:
    """Show the run as under way, and disown the results it will replace."""
    import gradio as gr

    return gr.Button("Running…", interactive=False), superseded_results_html(
        result_links
    )


def release_run_button() -> object:
    """Return the button to its resting state once a run has finished."""
    import gradio as gr

    return gr.Button("Run", interactive=True)


def _runtime_profile_path() -> Path:
    """Return the profile this deployment reads and writes."""
    configured = os.getenv("LEAP_RUNTIME_PROFILE_PATH", "").strip()
    if configured:
        return Path(configured)
    root_copy = REPO_ROOT / "runtime_stats_remote.json"
    if root_copy.is_file():
        return root_copy
    return REPO_ROOT / "web_app" / "runtime_stats_remote.json"


def _hosted_runtime_profile() -> dict[str, object]:
    """Read the committed HF profile without creating local runtime state."""
    return load_runtime_profile(_runtime_profile_path())


def _save_runtime_sample(
    process_group: str,
    elapsed_seconds: float,
    years: int | None = None,
    *,
    economies: int = 1,
    version_comparison: bool = False,
) -> None:
    """Fold one measured run into the profile the interface quotes.

    The committed file ships a seed so a freshly built Space can quote a
    duration immediately. Each hosted run replaces the oldest sample once
    the window is full, so the seed is displaced as real measurements
    arrive.

    A container's filesystem does not survive a rebuild, so samples written
    here last until the Space is rebuilt and then fall back to the committed
    seed. Making them outlive a rebuild would mean committing the file back to
    the repository from inside the Space, which needs a write token.
    """
    path = _runtime_profile_path()
    try:
        updated = record_runtime_sample(
            load_runtime_profile(path),
            process_group=process_group,
            elapsed_seconds=elapsed_seconds,
            years=years,
            economies=economies,
            version_comparison=version_comparison,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        # A read-only or full filesystem must not fail a completed build.
        return


def _result_links_html(
    *,
    dashboard_url: str | None,
    dashboard_error: str | None,
    wants_dashboard: bool,
    workbook_count: int,
    dashboard_links: list[dict[str, str]] | None = None,
) -> str:
    """Return the compact links panel shown once a run has finished."""
    parts = []
    links = dashboard_links or ([] if not dashboard_url else [])
    if links and dashboard_error:
        parts.append(
            "<span class='result-hint is-warning'>Some economies did not "
            f"render: {html.escape(dashboard_error)}</span>"
        )
    if links:
        # One button per economy, named, so a multi-economy run is navigable.
        for link in links:
            label = link.get("label") or (
                "Open the dashboard" if len(links) == 1 else f"{link['economy']} dashboard"
            )
            parts.append(
                f"<a class='result-link is-primary' href='{html.escape(link['url'])}' "
                f"target='_blank' rel='noopener'>{html.escape(label)} "
                + EXTERNAL_LINK_ICON
                + "</a>"
            )
    elif dashboard_url:
        parts.append(
            f"<a class='result-link is-primary' href='{html.escape(dashboard_url)}' "
            "target='_blank' rel='noopener'>Open the dashboard "
            + EXTERNAL_LINK_ICON
            + "</a>"
        )
    elif wants_dashboard:
        reason = dashboard_error or "No dashboard was generated for this run."
        return (
            "<div class='result-links is-failed'>"
            f"<span class='result-hint'>{html.escape(reason)}</span></div>"
        )
    if not parts:
        return RESULTS_EMPTY_HTML
    return f"<div class='result-links'>{''.join(parts)}</div>"


# An "opens in a new tab" mark, drawn rather than typed: the arrow character
# rendered at a different weight and baseline from the label beside it.
EXTERNAL_LINK_ICON = (
    "<svg class='link-icon' viewBox='0 0 20 20' aria-hidden='true' "
    "focusable='false' fill='none' stroke='currentColor' stroke-width='1.7' "
    "stroke-linecap='round' stroke-linejoin='round'>"
    "<path d='M8 4H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-3'/>"
    "<path d='M12 3h5v5'/><path d='M17 3l-7 7'/></svg>"
)


# The upload button is the complete empty state. Once a file arrives,
# inspect_uploaded_export fills the right side of the merged export strip.
EXPORT_PROMPT_HTML = ""


def _readout_chip(label: str, value: str) -> str:
    return (
        "<div class='readout-chip'>"
        f"<span>{html.escape(label)}</span>"
        f"<strong>{html.escape(value)}</strong>"
        "</div>"
    )


def _export_readout_html(
    *, state: str, label: str, body: str, multiple: bool = False
) -> str:
    # The flag is read by the page script, which dims the workbook card when a
    # multi-file upload has taken that option away.
    flag = " data-multi='1'" if multiple else ""
    return (
        f"<div class='export-readout is-{state}'{flag}>"
        f"<span class='readout-label'>{html.escape(label)}</span>"
        f"{body}</div>"
    )


@dataclass(frozen=True)
class ExportUpload:
    """One uploaded export, with whatever could be read from it."""

    path: Path
    economy: str = ""
    scenario: str = ""
    years: tuple[int, ...] = ()
    area_name: str = ""
    units: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _read_upload(path: Path) -> ExportUpload:
    """Return what one export declares, or why it cannot be used."""
    try:
        identity = infer_balance_export_identity(path)
    except Exception as error:
        return ExportUpload(path=path, error=str(error))
    try:
        balance_export_unit_to_petajoule_multiplier(identity.units)
    except ValueError:
        declared_units = str(identity.units or "").strip()
        unit_detail = f" uses {declared_units!r}" if declared_units else " has no recognized unit"
        return ExportUpload(
            path=path,
            units=declared_units,
            error=(
                f"This export{unit_detail}. Set LEAP Units to None + Petajoule "
                "and export it again."
            ),
        )
    try:
        detail = inspect_balance_export_detail(path)
    except Exception as error:
        return ExportUpload(path=path, error=str(error))
    if not detail.has_level2_detail:
        return ExportUpload(
            path=path,
            error=(
                f"LEAP wrote this as a {detail.detected_level_label} export, which "
                "flattens the balance into single rows. Export it again with at "
                "least Level 2 detail."
            ),
        )
    return ExportUpload(
        path=path,
        economy=identity.economy,
        scenario=identity.scenario,
        years=identity.years,
        area_name=identity.area_name,
        units=identity.units,
    )


def _uploaded_paths(value: object) -> list[Path]:
    """Return the uploaded files, whether Gradio gave one or many."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    paths = []
    for item in items:
        if item is None or str(item).strip() == "":
            continue
        raw = getattr(item, "name", item)
        path = Path(str(raw))
        if path.is_file():
            paths.append(path)
    return paths


def read_uploads(value: object) -> list[ExportUpload]:
    """Return every uploaded export in a stable order."""
    return [_read_upload(path) for path in sorted(_uploaded_paths(value))]


def group_by_economy(uploads: list[ExportUpload]) -> dict[str, list[ExportUpload]]:
    """Group readable uploads by the economy each one declares."""
    grouped: dict[str, list[ExportUpload]] = {}
    for upload in uploads:
        if upload.ok and upload.economy:
            grouped.setdefault(upload.economy, []).append(upload)
    return dict(sorted(grouped.items()))


def scenarios_for(uploads: list[ExportUpload], economy: str) -> list[str]:
    """Return the scenarios available for one economy, in a stable order."""
    scenarios = {
        upload.scenario
        for upload in uploads
        if upload.ok and upload.economy == economy and upload.scenario
    }
    return sorted(scenarios)


def duplicate_uploads(uploads: list[ExportUpload]) -> dict[str, str]:
    """Map each superseded upload to the earlier one it repeats.

    Two files with different names can hold the same export. Nothing downstream
    can tell them apart -- they declare one economy, one scenario and one year
    range -- so the second is not a second export, it is the same one twice.
    Rendering it again would only cost the wait, and quietly suggest the run
    covered more ground than it did.

    The earliest upload wins, so the copy added last is the one set aside,
    which is the one the user just chose and can most easily reconsider.
    """
    first_seen: dict[tuple[str, str, tuple[int, ...]], ExportUpload] = {}
    superseded: dict[str, str] = {}
    for upload in sorted(uploads, key=_upload_added_at):
        if not upload.ok:
            continue
        key = (upload.economy, upload.scenario, upload.years)
        kept = first_seen.get(key)
        if kept is None:
            first_seen[key] = upload
            continue
        superseded[upload.path.name] = kept.path.name
    return superseded


def _upload_added_at(upload: ExportUpload) -> float:
    """Return when an upload arrived, tolerating a file that has since gone."""
    try:
        return upload.path.stat().st_mtime
    except OSError:
        return 0.0


def without_duplicates(uploads: list[ExportUpload]) -> list[ExportUpload]:
    """Return the uploads a run should actually use."""
    superseded = duplicate_uploads(uploads)
    return [upload for upload in uploads if upload.path.name not in superseded]


def selected_version_uploads(
    uploads: list[ExportUpload], original_name: object, new_name: object
) -> tuple[ExportUpload, ExportUpload]:
    """Return two explicitly labelled exports after strict identity checks."""
    by_name = {upload.path.name: upload for upload in uploads if upload.ok}
    original = by_name.get(str(original_name or ""))
    new = by_name.get(str(new_name or ""))
    if original is None or new is None or original.path == new.path:
        raise ValueError("Choose two different uploaded files as Original and New.")
    original_identity = (original.economy, original.scenario, original.years)
    new_identity = (new.economy, new.scenario, new.years)
    if original_identity != new_identity:
        raise ValueError(
            "Version comparison requires the same economy, scenario, and year "
            "coverage in both files."
        )
    return original, new


def _matching_version_pair(uploads: list[ExportUpload]) -> list[ExportUpload]:
    """Return the one eligible two-file version pair, if there is one."""
    readable = [upload for upload in uploads if upload.ok]
    if len(readable) != 2:
        return []
    first, second = readable
    identity = lambda upload: (upload.economy, upload.scenario, upload.years)
    return readable if identity(first) == identity(second) else []


def version_comparison_control_updates(
    balance_export_workbook: object,
) -> tuple[object, object, object, bool, str, object]:
    """Open the version prompt only for one matching pair of exports."""
    import gradio as gr

    pair = _matching_version_pair(read_uploads(balance_export_workbook))
    if not pair:
        return (
            gr.Column(visible=False),
            gr.Dropdown(),
            gr.Dropdown(),
            False,
            "",
            gr.Button(interactive=False),
        )
    names = [upload.path.name for upload in pair]
    return (
        gr.Column(visible=True),
        gr.Dropdown(choices=names, value=names[0]),
        gr.Dropdown(choices=names, value=names[1]),
        False,
        "",
        gr.Button(interactive=True),
    )


def version_comparison_selection_update(
    original_name: object, new_name: object
) -> tuple[object, str]:
    """Keep Compare unavailable until the two selected files differ."""
    import gradio as gr

    if not str(original_name or "") or str(original_name) == str(new_name or ""):
        return (
            gr.Button(interactive=False),
            "<span class='version-selection-error'>Choose different files.</span>",
        )
    return gr.Button(interactive=True), ""


def synchronise_version_comparison_role(
    changed_name: object, balance_export_workbook: object
) -> tuple[object, object, str]:
    """Keep the other role on the remaining file in a two-file comparison."""
    import gradio as gr

    pair = _matching_version_pair(read_uploads(balance_export_workbook))
    names = [upload.path.name for upload in pair]
    selected = str(changed_name or "")
    if len(names) != 2 or selected not in names:
        return (
            gr.Dropdown(choices=names),
            gr.Button(interactive=False),
            "<span class='version-selection-error'>Choose a version file.</span>",
        )
    remaining = names[1] if selected == names[0] else names[0]
    return (
        gr.Dropdown(choices=names, value=remaining),
        gr.Button(interactive=True),
        "",
    )


def confirm_version_comparison(
    original_name: object, new_name: object
) -> tuple[bool, object, object, object]:
    """Accept the two selected roles and switch the run to dashboard-only."""
    import gradio as gr

    if not str(original_name or "") or str(original_name) == str(new_name or ""):
        return (
            False,
            gr.Checkbox(),
            gr.Checkbox(),
            gr.Column(visible=True),
        )
    return (
        True,
        gr.Checkbox(value=False, interactive=False),
        gr.Checkbox(value=True),
        gr.Column(visible=False),
    )


def _uploads_table(
    uploads: list[ExportUpload],
    superseded: dict[str, str] | None = None,
    version_roles: dict[str, str] | None = None,
) -> str:
    """Return one row per uploaded file, saying what was read from it."""
    superseded = superseded or {}
    version_roles = version_roles or {}
    rows = []
    for upload in uploads:
        name = html.escape(upload.path.name)
        role = version_roles.get(upload.path.name)
        version_badge = (
            f"<span class='upload-version'>{html.escape(role)}</span>" if role else ""
        )
        file_name = f"<span class='upload-file-name'><strong>{name}</strong>{version_badge}</span>"
        repeats = superseded.get(upload.path.name)
        if repeats:
            detail = (
                "<span class='upload-duplicate'>Same economy, scenario and "
                f"years as {html.escape(repeats)} — not used</span>"
            )
            rows.append(
                f"<li class='upload-row is-dupe'>{file_name}{detail}</li>"
            )
            continue
        if upload.ok:
            span = (
                f"{upload.years[0]}–{upload.years[-1]}"
                if len(upload.years) > 1
                else (str(upload.years[0]) if upload.years else "")
            )
            detail = (
                f"<span>{html.escape(upload.economy or 'unknown economy')}</span>"
                f"<span>{html.escape(upload.scenario)}</span>"
                f"<span>{html.escape(span)}</span>"
            )
            state = "ok" if upload.economy else "warn"
        else:
            detail = f"<span class='upload-error'>{html.escape(upload.error)}</span>"
            state = "bad"
        rows.append(
            f"<li class='upload-row is-{state}'>{file_name}{detail}</li>"
        )
    return f"<ul class='upload-list'>{''.join(rows)}</ul>"


def version_comparison_readout(
    balance_export_workbook: object,
    original_name: object,
    new_name: object,
    compare_versions: object,
) -> object:
    """Record the chosen version roles in the existing upload details."""
    import gradio as gr

    if not compare_versions:
        return gr.skip()
    original = str(original_name or "")
    new = str(new_name or "")
    if not original or original == new:
        return gr.skip()
    uploads = read_uploads(balance_export_workbook)
    roles = {original: "Version 1", new: "Version 2"}
    uploaded_names = {upload.path.name for upload in uploads}
    if not all(name in uploaded_names for name in roles):
        return gr.skip()
    return _export_readout_html(
        state="ready",
        label="Read from your exports",
        body=(
            _uploads_table(uploads, version_roles=roles)
            + "<p>This is a Version 1 / Version 2 comparison: Version 1 is "
            "rendered as lightweight comparison traces, then overlaid on the "
            "full Version 2 dashboard.</p>"
        ),
        multiple=True,
    )


def inspect_uploaded_export(
    balance_export_workbook: object,
    year: object,
) -> tuple[str, object, object, object, object, object]:
    """Read what every uploaded export declares and shape the run around it.

    One export behaves as before. Several switch the run to dashboard mode:
    a workbook is built for a single economy and scenario, so offering it for
    a mixed upload would only produce a workbook for whichever file happened
    to be first. The economy and scenario to render are chosen from what was
    actually provided rather than from a fixed list.
    """
    import gradio as gr

    hidden_economy = gr.Textbox(visible=False, value="")
    uploads = read_uploads(balance_export_workbook)
    if not uploads:
        return (
            EXPORT_PROMPT_HTML,
            hidden_economy,
            gr.Textbox(),
            gr.Button(visible=False),
            gr.Checkbox(),
            gr.Checkbox(),
        )

    # A matching pair is held neutral while the popup asks whether it is a
    # version comparison. Do not label either file as unused before the user
    # has made that choice.
    version_pair = _matching_version_pair(uploads)
    superseded = {} if version_pair else duplicate_uploads(uploads)
    effective = uploads if version_pair else [
        upload for upload in uploads if upload.path.name not in superseded
    ]
    grouped = group_by_economy(effective)
    economies = list(grouped)
    multiple = len(effective) > 1 and not version_pair
    readable = [upload for upload in effective if upload.ok]
    unnamed = [upload for upload in readable if not upload.economy]

    # A workbook covers one economy and one scenario, so a multi-file upload
    # builds the dashboard instead. The card says so rather than silently
    # ignoring the choice.
    workbook_update = gr.Checkbox(value=not multiple, interactive=not multiple)
    dashboard_update = gr.Checkbox(value=True)

    if not readable:
        body = _uploads_table(uploads, superseded)
        return (
            _export_readout_html(
                state="error",
                label="These exports could not be read",
                body=body,
                multiple=multiple,
            ),
            hidden_economy,
            gr.Textbox(),
            gr.Button(visible=True),
            workbook_update,
            dashboard_update,
        )

    # The export's own base year, and the one after it: the base year shows
    # whether the balance starts right, and the next projection year shows
    # whether it stays right. A year the user typed is only replaced when this
    # upload cannot serve it.
    all_years = sorted({year for upload in readable for year in upload.years})
    requested = _requested_years(year)
    year_update = gr.Textbox()
    if all_years and (not requested or set(requested) - set(all_years)):
        default_years = [all_years[0]]
        if len(all_years) > 1:
            default_years.append(all_years[1])
        year_update = gr.Textbox(value=", ".join(str(y) for y in default_years))

    if multiple:
        ignored = ""
        if superseded:
            names = ", ".join(sorted(superseded))
            ignored = (
                f" {names} {'repeats' if len(superseded) == 1 else 'repeat'} an "
                "export already added, so "
                f"{'it is' if len(superseded) == 1 else 'they are'} not used."
            )
        note = (
            f"{len(readable)} "
            f"{'export' if len(readable) == 1 else 'exports'} across "
            f"{len(economies)} "
            f"{'economy' if len(economies) == 1 else 'economies'}.{ignored} "
            f"A dashboard is built for "
            f"{'that economy' if len(economies) == 1 else 'each of them'}. "
            "The review workbook covers a single export, so it is unavailable "
            "for this upload."
        )
        state = "ready" if not unnamed else "partial"
        return (
            _export_readout_html(
                state=state,
                label="Read from your exports",
                body=(
                    _uploads_table(uploads, superseded)
                    + f"<p>{html.escape(note)}</p>"
                ),
                multiple=True,
            ),
            gr.Textbox(visible=bool(unnamed)),
            year_update,
            gr.Button(visible=True),
            workbook_update,
            dashboard_update,
        )

    upload = readable[0]
    if not upload.ok:
        pass
    # Two names for one export collapse to a single export, so this branch is
    # reached with a file the user added and cannot see the fate of. Say so
    # here, or the upload list shows two rows and the readout describes one.
    repeats_note = ""
    if superseded:
        names = ", ".join(sorted(superseded))
        repeats_note = (
            f"<p class='readout-repeat'>{html.escape(names)} holds this same "
            "export under another name, so it is not used.</p>"
        )
    year_span = (
        f"{upload.years[0]}–{upload.years[-1]}"
        if len(upload.years) > 1
        else str(upload.years[0])
    )
    if not upload.economy:
        body = (
            _uploads_table(uploads, superseded)
            + f"<p>The LEAP area is named “{html.escape(upload.area_name)}”, "
            "which does not match an APEC economy. Enter the economy code below "
            "and everything else still comes from the export.</p>"
            + repeats_note
        )
        return (
            _export_readout_html(
                state="partial", label="Read from your export", body=body
            ),
            gr.Textbox(visible=True),
            year_update,
            gr.Button(visible=True),
            workbook_update,
            gr.Checkbox(),
        )

    body = (
        _uploads_table(uploads, superseded)
        + f"<p>LEAP area “{html.escape(upload.area_name)}”. Choose any "
        "review year within this range.</p>"
        + repeats_note
    )
    return (
        _export_readout_html(
            state="partial" if superseded else "ready",
            label="Read from your export",
            body=body,
        ),
        hidden_economy,
        year_update,
        gr.Button(visible=True),
        workbook_update,
        gr.Checkbox(),
    )


def _requested_years(year: object) -> list[int]:
    """Return the review years a user typed, ignoring anything unparseable."""
    years = []
    for token in str(year or "").replace(";", ",").split(","):
        token = token.strip()
        if token.isdigit():
            years.append(int(token))
    return years


def _run_version_comparison_pair(
    *,
    context: object,
    run_root: Path,
    economy: str,
    scenario: str,
    original_upload: ExportUpload,
    new_upload: ExportUpload,
    esto_table_path: Path | None,
    min_year: int,
    max_year: int,
    green_percent: float,
    yellow_percent: float,
    progress: object = None,
    cancellation_check: object = None,
) -> tuple[object, dict[str, int], dict[str, float]]:
    """Render isolated version roots, then overlay Version 1 onto Version 2."""
    outcomes: dict[str, object] = {}
    elapsed: dict[str, float] = {}
    for role, upload in (("original", original_upload), ("new", new_upload)):
        _raise_if_cancelled(cancellation_check)
        if callable(progress):
            version_label = "Version 1" if role == "original" else "Version 2"
            mode = "comparison traces" if role == "original" else "full dashboard"
            progress(f"Rendering {version_label} ({mode}).")
        role_directory = run_root / "version_exports" / role
        role_directory.mkdir(parents=True, exist_ok=True)
        _copy_input(upload.path, role_directory)
        # Each renderer clears its output root. Isolation is therefore part of
        # the correctness contract, not merely tidier output organisation.
        role_context = replace(
            context,
            output_root=context.output_root / "version_comparison" / role,
            log_root=context.log_root / "version_comparison" / role,
        )
        started = time.perf_counter()
        outcomes[role] = developer_launcher.run_dashboard_from_export(
            context=role_context,
            economy=economy,
            export_dir=role_directory,
            esto_table_path=esto_table_path,
            min_year=min_year,
            max_year=max_year,
            run_label=f"web-version-{role}",
            trace_only=role == "original",
            cancellation_check=cancellation_check,
        )
        elapsed[role] = time.perf_counter() - started
        _raise_if_cancelled(cancellation_check)

    original_outcome = outcomes["original"]
    new_outcome = outcomes["new"]
    if not original_outcome.ok:
        raise RuntimeError(
            original_outcome.error or "Version 1 trace generation failed."
        )
    counts: dict[str, int] = {}
    if new_outcome.ok:
        original_root = Path(original_outcome.outputs["comparison_trace_root"])
        # A full multi-scope render reports its container as ``output_root``;
        # the default comparable dashboard is the parent of its declared
        # chart-bundle directory.
        new_root = Path(new_outcome.outputs["chart_bundle_directory"]).parent
        if original_root.resolve() == new_root.resolve():
            raise RuntimeError("Version 1 and Version 2 used the same output root.")
        counts = apply_version_comparison(
            original_root,
            new_root,
            scenario=scenario,
            green_percent=green_percent,
            yellow_percent=yellow_percent,
        )
    return new_outcome, counts, elapsed


def build_review_from_export(
    want_workbook: object,
    want_dashboard: object,
    year: object,
    economy_override: str,
    balance_export_workbook: object,
    economy_choice: object = None,
    scenario_choice: object = None,
    browser_archives: object = None,
    progress: object = None,
    cancellation_check: object = None,
    dashboard_min_year: float = DEFAULT_DASHBOARD_MIN_YEAR,
    dashboard_max_year: float = DEFAULT_DASHBOARD_MAX_YEAR,
    esto_vintage_choice: object = None,
    compare_versions: object = False,
    original_export_name: object = None,
    new_export_name: object = None,
) -> tuple[str, str, object, str | None, str, object, object, str | None]:
    """Build the outputs a run asked for, from one LEAP export."""
    persistent_bundle: Path | None = None
    persistent_dashboard_bundle: Path | None = None
    run_started = time.perf_counter()
    try:
        _raise_if_cancelled(cancellation_check)
        _cleanup_stale_web_artifacts()
        wants_workbook = bool(want_workbook)
        wants_dashboard = bool(want_dashboard)
        if not (wants_workbook or wants_dashboard):
            raise ValueError(
                "Choose at least one thing to build: the review workbook, "
                "the dashboard, or both."
            )
        wanted = {
            name
            for name, on in (("workbook", wants_workbook), ("dashboard", wants_dashboard))
            if on
        }
        year_value = str(year or "").strip()
        if wants_workbook and not year_value:
            raise ValueError("Enter one or more review years, for example 2022,2030.")
        dashboard_min_year_value = int(dashboard_min_year)
        dashboard_max_year_value = int(dashboard_max_year)

        is_version_comparison = bool(compare_versions)
        uploads = read_uploads(balance_export_workbook)
        if not uploads:
            raise ValueError("Please upload at least one LEAP Energy Balance export.")
        # An export uploaded twice under two names is one export. Building it
        # again would double the wait for a second copy of the same dashboard.
        if not is_version_comparison:
            uploads = without_duplicates(uploads)
        unreadable = [upload for upload in uploads if not upload.ok]
        readable = [upload for upload in uploads if upload.ok]
        if not readable:
            raise ValueError(unreadable[0].error)
        if is_version_comparison:
            if wants_workbook or not wants_dashboard:
                raise ValueError("Version comparison builds a dashboard only.")
            original_upload, new_upload = selected_version_uploads(
                readable, original_export_name, new_export_name
            )
            green_percent_value = VERSION_COMPARISON_GREEN_PERCENT
            yellow_percent_value = VERSION_COMPARISON_YELLOW_PERCENT
        else:
            original_upload = new_upload = None
            green_percent_value = yellow_percent_value = None

        override = str(economy_override or "").strip()
        grouped = group_by_economy(readable)
        if not grouped and override:
            # A single unrecognised area name can still be named by hand.
            grouped = {override: readable}
        if not grouped:
            raise ValueError(
                f"The LEAP area in {readable[0].path.name!r} is named "
                f"{readable[0].area_name!r}, which does not match an APEC economy. "
                "Enter the economy code so the run knows which one to use."
            )

        if is_version_comparison:
            assert original_upload is not None and new_upload is not None
            economy_value = original_upload.economy
            scenario_value = original_upload.scenario
            wanted_economies = [economy_value]
            economy_uploads = [original_upload, new_upload]
        else:
            economy_value = ""
            scenario_value = ""
            economy_uploads = []

        selected_economies = [
            name
            for name in (
                economy_choice
                if isinstance(economy_choice, (list, tuple))
                else [economy_choice]
            )
            if str(name or "").strip() in grouped
        ]
        if not is_version_comparison:
            wanted_economies = selected_economies or list(grouped)
            economy_value = wanted_economies[0]
            economy_uploads = grouped[economy_value]

        # A workbook is built for one economy and scenario. With several files
        # uploaded the interface offers only the dashboard, and this guard keeps
        # a direct API call honest about the same limit.
        if wants_workbook and len(readable) > 1:
            raise ValueError(
                "The review workbook covers a single export. Upload one export "
                "for a workbook, or build the dashboard from this set."
            )

        scenario_value = scenario_value or economy_uploads[0].scenario
        wanted_scenario = str(scenario_choice or "").strip()
        if wanted_scenario:
            for upload in economy_uploads:
                if upload.scenario == wanted_scenario:
                    scenario_value = wanted_scenario
                    break

        workbook_upload = next(
            (upload for upload in economy_uploads if upload.scenario == scenario_value),
            economy_uploads[0],
        )
        requested_years = _requested_years(year_value) if wants_workbook else []
        missing_years = sorted(set(requested_years) - set(workbook_upload.years))
        if missing_years:
            available = f"{workbook_upload.years[0]}–{workbook_upload.years[-1]}"
            raise ValueError(
                f"This export has no sheet for {', '.join(str(y) for y in missing_years)}. "
                f"It covers {available}."
            )
        vintage_choices = _esto_vintage_choices()
        selected_vintage = str(esto_vintage_choice or vintage_choices[-1][1])
        esto_path = _esto_table_for_vintage(selected_vintage)
        esto_base_year = _esto_base_year(esto_path)
        if wants_workbook and requested_years:
            requested_years = sorted(
                {max(value, esto_base_year) for value in requested_years}
            )
            year_value = ", ".join(str(value) for value in requested_years)

        run_root = Path(tempfile.mkdtemp(prefix="leap_balance_review_web_"))
        local_esto = _copy_input(esto_path, run_root / "uploads") if esto_path else None
        # Every economy gets its own export folder, which is the shape the
        # dashboard resolver expects; it picks the newest file per scenario.
        export_directories: dict[str, Path] = {}
        for name in wanted_economies:
            directory = run_root / "exports" / _safe_filename_token(name)
            directory.mkdir(parents=True, exist_ok=True)
            for upload in (economy_uploads if is_version_comparison else grouped[name]):
                _copy_input(upload.path, directory)
            export_directories[name] = directory
        local_export = _copy_input(workbook_upload.path, run_root / "uploads")
        context = _build_context(run_root)
        _raise_if_cancelled(cancellation_check)

        result = None
        workbook_paths: list[Path] = []
        diagnostics_directory: Path | None = None
        workbook_seconds: float | None = None
        if wants_workbook:
            _raise_if_cancelled(cancellation_check)
            workbook_started = time.perf_counter()
            result = developer_launcher.run_balance_review_from_export(
                context=context,
                economy=economy_value,
                scenario=scenario_value,
                year=year_value,
                balance_export_workbook=local_export,
                esto_table_path=local_esto,
                run_label="web",
            )
            if not result.ok:
                raise RuntimeError(result.error or "The balance-review workflow failed.")
            workbook_paths = [Path(path) for path in result.outputs["workbooks"]]
            if not workbook_paths or not all(path.is_file() for path in workbook_paths):
                raise FileNotFoundError(
                    "The workflow completed without producing a workbook."
                )
            diagnostics_directory = Path(result.outputs["diagnostics_directory"])
            workbook_seconds = time.perf_counter() - workbook_started
            _raise_if_cancelled(cancellation_check)

        dashboard_error = None
        dashboard_directory: Path | None = None
        dashboard_page_names: list[str] = []
        dashboard_seconds: float | None = None
        version_state_counts: dict[str, int] = {}
        comparison_role_seconds: dict[str, float] = {}
        # One dashboard per economy: the renderer covers a single economy, so
        # several are rendered in turn and reported separately.
        dashboards: list[dict[str, object]] = []
        if wants_dashboard:
            dashboard_started = time.perf_counter()
            for name in wanted_economies:
                _raise_if_cancelled(cancellation_check)
                if progress is not None:
                    progress(
                        f"Rendering the {name} dashboard "
                        f"({len(dashboards) + 1} of {len(wanted_economies)})."
                    )
                # One economy failing must not discard the ones already
                # rendered: a multi-economy run is too long to lose whole.
                try:
                    if is_version_comparison:
                        assert original_upload is not None and new_upload is not None
                        (
                            outcome,
                            version_state_counts,
                            comparison_role_seconds,
                        ) = _run_version_comparison_pair(
                            context=context,
                            run_root=run_root,
                            economy=name,
                            scenario=scenario_value,
                            original_upload=original_upload,
                            new_upload=new_upload,
                            esto_table_path=local_esto,
                            min_year=dashboard_min_year_value,
                            max_year=dashboard_max_year_value,
                            green_percent=green_percent_value,
                            yellow_percent=yellow_percent_value,
                            progress=progress,
                            cancellation_check=cancellation_check,
                        )
                    else:
                        outcome = developer_launcher.run_dashboard_from_export(
                            context=context,
                            economy=name,
                            export_dir=export_directories[name],
                            esto_table_path=local_esto,
                            min_year=dashboard_min_year_value,
                            max_year=dashboard_max_year_value,
                            run_label="web",
                        )
                except Exception as error:  # noqa: BLE001 - reported per economy
                    dashboards.append({"economy": name, "error": str(error)})
                    continue
                if outcome.ok:
                    index_path = Path(outcome.outputs["dashboard_index"])
                    dashboards.append(
                        {
                            "economy": name,
                            "directory": index_path.parent,
                            "pages": _dashboard_pages(index_path.parent),
                        }
                    )
                else:
                    dashboards.append(
                        {
                            "economy": name,
                            "error": outcome.error or "Dashboard generation failed.",
                        }
                    )
                _raise_if_cancelled(cancellation_check)
            dashboard_seconds = time.perf_counter() - dashboard_started
            failures = [d for d in dashboards if d.get("error")]
            if failures and len(failures) == len(dashboards):
                dashboard_error = str(failures[0]["error"])
            elif failures:
                dashboard_error = "; ".join(
                    f"{d['economy']}: {d['error']}" for d in failures
                )
            rendered = [d for d in dashboards if not d.get("error")]
            if rendered:
                dashboard_directory = rendered[0]["directory"]
                dashboard_page_names = list(rendered[0]["pages"])

        run_outputs = result.outputs if result is not None else {}
        years_built = run_outputs.get("years", year_value) if wants_workbook else None
        saved_dashboard_years = (
            years_built
            or year_value
            or f"{dashboard_min_year_value}–{dashboard_max_year_value}"
        )

        persistent_workbooks: list[Path] = []
        persistent_bundle = None
        persistent_dashboard_bundle = None
        # Preserve every valid source file the visitor supplied, including
        # other selected economies or both versions of a comparison.
        uploaded_export_paths = [upload.path for upload in readable]
        _raise_if_cancelled(cancellation_check)
        persistent_dir: Path | None = None
        if (wants_workbook and result is not None) or dashboard_directory is not None:
            persistent_dir = Path(
                tempfile.mkdtemp(prefix="leap_balance_review_download_")
            )
        if wants_workbook and result is not None and persistent_dir is not None:
            for workbook_path in workbook_paths:
                target = persistent_dir / workbook_path.name
                shutil.copy2(workbook_path, target)
                persistent_workbooks.append(target)
            persistent_bundle = persistent_dir / _complete_run_archive_name(
                economy_value,
                scenario_value,
            )
            _write_diagnostics_bundle(
                bundle_path=persistent_bundle,
                workbook_paths=workbook_paths,
                diagnostics_directory=diagnostics_directory,
                run_directory=result.run_directory,
                dashboard_directory=dashboard_directory,
                log_directory=run_root / "logs",
                uploaded_export_paths=uploaded_export_paths,
            )
        if dashboard_directory is not None and persistent_dir is not None:
            persistent_dashboard_bundle = persistent_dir / _dashboard_archive_name(
                economy_value,
                scenario_value,
            )
            _write_dashboard_bundle(
                bundle_path=persistent_dashboard_bundle,
                dashboard_directory=dashboard_directory,
                uploaded_export_paths=uploaded_export_paths,
            )

        # Each rendered economy gets its own snapshot and its own link.
        snapshots = []
        _raise_if_cancelled(cancellation_check)
        for rendered in dashboards:
            if rendered.get("error"):
                continue
            name = str(rendered["economy"])
            # The scenarios this economy was uploaded with. Reading them per
            # economy matters: taking the run's first scenario would pin a
            # target-only economy to a reference view that has no data.
            economy_scenarios = list(
                dict.fromkeys(
                    upload.scenario
                    for upload in grouped.get(name, ())
                    if upload.scenario
                )
            )
            economy_scenario = economy_scenarios[0] if economy_scenarios else scenario_value
            snapshot_for = _dashboard_snapshot(
                rendered["directory"],
                economy=name,
                scenario=economy_scenario,
                years=saved_dashboard_years,
                scenarios=economy_scenarios,
            )
            url = _publish_dashboard_pages(
                snapshot_for["pages"],
                economy=name,
                scenario=economy_scenario,
                years=saved_dashboard_years,
                scenarios=economy_scenarios,
            )
            snapshots.append({"economy": name, "snapshot": snapshot_for, "url": url})

        snapshot = snapshots[0]["snapshot"] if snapshots else None
        dashboard_url = snapshots[0]["url"] if snapshots else None
        dashboard_links = [
            {"economy": item["economy"], "url": item["url"]}
            for item in snapshots
            if item["url"]
        ]
        existing_archives = browser_archives if isinstance(browser_archives, list) else []
        browser_archive_records = (
            [item["snapshot"] for item in snapshots]
            + existing_archives[: max(MAX_BROWSER_DASHBOARDS - len(snapshots), 0)]
        )[:MAX_BROWSER_DASHBOARDS] if snapshots else existing_archives[:MAX_BROWSER_DASHBOARDS]

        build_result = run_outputs.get("build_result", {})
        _raise_if_cancelled(cancellation_check)
        runtime_seconds = {
            "workbook": round(workbook_seconds, 1) if workbook_seconds is not None else None,
            "dashboard": round(dashboard_seconds, 1) if dashboard_seconds is not None else None,
            "version_1_trace_only": (
                round(comparison_role_seconds["original"], 1)
                if "original" in comparison_role_seconds
                else None
            ),
            "version_2_full": (
                round(comparison_role_seconds["new"], 1)
                if "new" in comparison_role_seconds
                else None
            ),
            "full_run": round(time.perf_counter() - run_started, 1),
        }
        # Only successful runs are recorded, so a failure cannot drag the
        # quoted duration around.
        year_count = len(requested_years) or None
        # Keep the measured dashboard total together with its rendered economy
        # count. The estimator matches that exact shape instead of treating a
        # one-economy run as evidence for a multi-economy run.
        rendered_count = max(len([d for d in dashboards if not d.get("error")]), 1)
        for group, measured in runtime_seconds.items():
            if measured is None:
                continue
            if group in {"version_1_trace_only", "version_2_full"}:
                continue
            # "full run" means both halves; recording a workbook-only or
            # dashboard-only run against it would understate the real total.
            if group == "full_run" and not (wants_workbook and wants_dashboard):
                continue
            _save_runtime_sample(
                group,
                measured,
                years=year_count,
                economies=(
                    rendered_count if group in {"dashboard", "full_run"} else 1
                ),
                version_comparison=is_version_comparison,
            )
        if is_version_comparison and comparison_role_seconds:
            _save_runtime_sample(
                "dashboard_trace_only",
                comparison_role_seconds["original"],
                version_comparison=True,
            )
            _save_runtime_sample(
                "dashboard_version_2_full",
                comparison_role_seconds["new"],
                version_comparison=True,
            )
        summary = {
            "status": "succeeded",
            "source_commit": _source_commit(),
            "requested_outputs": sorted(wanted),
            "economy": economy_value,
            "economy_source": (
                "leap_area_name" if workbook_upload.economy else "user_supplied"
            ),
            "leap_area_name": workbook_upload.area_name,
            "exports_used": [upload.path.name for upload in economy_uploads],
            "version_comparison": (
                {
                    "original": original_upload.path.name,
                    "new": new_upload.path.name,
                    "green_percent": green_percent_value,
                    "yellow_percent": yellow_percent_value,
                    "card_state_counts": version_state_counts,
                }
                if is_version_comparison
                and original_upload is not None
                and new_upload is not None
                else None
            ),
            "economies_uploaded": sorted(grouped),
            "scenario": scenario_value,
            "years": years_built,
            "dashboard_min_year": dashboard_min_year_value,
            "dashboard_max_year": dashboard_max_year_value,
            "esto_table_used": run_outputs.get("esto_table_used"),
            "esto_base_year": run_outputs.get("esto_base_year") or esto_base_year,
            "esto_vintage": selected_vintage,
            "diagnostics_directory": (
                str(diagnostics_directory) if diagnostics_directory else None
            ),
            "comparison_state_counts": build_result.get("comparisonStateCounts", {}),
            "missing_audit_rows": build_result.get("missingAuditRows"),
            "formula_error_cells": build_result.get("formulaErrorCells", []),
            "workbook_status": "succeeded" if wants_workbook else "not requested",
            "dashboard_status": (
                "not requested"
                if not wants_dashboard
                else "succeeded"
                if dashboard_links and not any(d.get("error") for d in dashboards)
                else "partial"
                if dashboard_links
                else "failed"
            ),
            "dashboard_error": dashboard_error,
            "dashboard_pages": dashboard_page_names,
            "dashboards_rendered": [
                d["economy"] for d in dashboards if not d.get("error")
            ],
            "dashboards_failed": [
                d["economy"] for d in dashboards if d.get("error")
            ],
            "dashboard_archive_id": snapshot["archive_id"] if snapshot else None,
            "dashboard_archive_ids": [
                item["snapshot"]["archive_id"]
                for item in snapshots
                if item.get("snapshot", {}).get("archive_id")
            ],
            "dashboard_storage": "browser-local",
            "runtime_seconds": runtime_seconds,
        }
        _raise_if_cancelled(cancellation_check)
        return (
            json.dumps(summary, indent=2, default=str),
            _status_html(
                _run_status_line(
                    wants_workbook=wants_workbook,
                    wants_dashboard=wants_dashboard,
                    dashboard_ok=bool(dashboard_links),
                    partial_note=(
                        "Some economies did not render."
                        if dashboard_links and dashboard_error
                        else ""
                    ),
                    runtime_seconds=runtime_seconds,
                )
            ),
            [str(path) for path in persistent_workbooks],
            str(persistent_bundle) if persistent_bundle else None,
            _result_links_html(
                dashboard_url=dashboard_url,
                dashboard_links=dashboard_links,
                dashboard_error=dashboard_error,
                wants_dashboard=wants_dashboard,
                workbook_count=len(persistent_workbooks),
            ),
            _dropdown_update(
                _browser_dashboard_choices(browser_archive_records),
                snapshot["archive_id"] if snapshot else None,
            ),
            browser_archive_records,
            (
                str(persistent_dashboard_bundle)
                if persistent_dashboard_bundle
                else None
            ),
        )
    except RunCancelled:
        raise
    except Exception as error:  # Gradio should show a plain-language failure.
        return (
            "",
            _status_html(f"Build failed: {error}"),
            [],
            None,
            RESULTS_EMPTY_HTML,
            _dropdown_update(_browser_dashboard_choices(browser_archives), None),
            browser_archives if isinstance(browser_archives, list) else [],
            None,
        )


def _dropdown_update(choices: list[object], value: object) -> object:
    """Return a real Dropdown component update for Gradio 5."""
    import gradio as gr

    return gr.Dropdown(choices=choices, value=value)


def poll_run(job_id: object, browser_archives: object):
    """Return the outputs of a finished job, or keep the page waiting.

    Called on a timer, so a page that was closed and reopened picks the run up
    exactly where it is rather than restarting it.
    """
    import gradio as gr

    saved = browser_archives if isinstance(browser_archives, list) else []
    job = _job_snapshot(str(job_id or ""))
    if job is None:
        # Nothing to watch: an unknown or forgotten id stops the timer.
        return (
            gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
            gr.skip(), gr.skip(), gr.skip(),
            gr.Timer(active=False),
            gr.Button("Run", interactive=True),
            gr.Button(visible=False),
            "",
            gr.skip(),
        )
    if job.get("state") in {"running", "cancel_requested"}:
        cancelling = job.get("state") == "cancel_requested"
        return (
            # Hidden while running: the script moves this text into the
            # calculator caption, beside the clock, rather than showing a
            # second progress line of its own.
            gr.skip(), _status_html(job_step(job_id), tone="is-step"),
            gr.skip(), gr.skip(),
            gr.skip(), gr.skip(), gr.skip(), gr.skip(),
            gr.Timer(active=True),
            gr.Button("Running…", interactive=False),
            gr.Button(
                "Cancelling…" if cancelling else "Cancel run",
                visible=True,
                interactive=not cancelling,
            ),
            str(job_id),
            gr.skip(),
        )
    if job.get("state") == "cancelled":
        return (
            gr.skip(),
            _status_html(str(job.get("message") or "Run cancelled.")),
            gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
            gr.Timer(active=False),
            gr.Button("Run", interactive=True),
            gr.Button(visible=False),
            "",
            gr.skip(),
        )
    if job.get("state") == "failed":
        return (
            # Keep the files from the last completed run on screen.  A page
            # refresh restores those first, and clearing them here made them
            # appear briefly before the failed poll replaced them with empty
            # components.  The failure message is still current and the run
            # controls below are released as usual.
            gr.skip(),
            _status_html(str(job.get("message") or "Build failed.")),
            gr.skip(),
            gr.skip(),
            gr.skip(),
            _dropdown_update(_browser_dashboard_choices(saved), None),
            saved,
            gr.skip(),
            gr.Timer(active=False),
            gr.Button("Run", interactive=True),
            gr.Button(visible=False),
            "",
            gr.skip(),
        )
    result = job.get("result") or ()
    if len(result) != 8:
        return (
            "", _status_html("The run finished without producing outputs."), [], None,
            RESULTS_EMPTY_HTML,
            _dropdown_update(_browser_dashboard_choices(saved), None),
            saved,
            None,
            gr.Timer(active=False),
            gr.Button("Run", interactive=True),
            gr.Button(visible=False),
            "",
            gr.skip(),
        )
    return (
        *result,
        gr.Timer(active=False),
        gr.Button("Run", interactive=True),
        gr.Button(visible=False),
        "",
        _last_run_record(
            result[0], result[1], result[2], result[3], result[7], result[6]
        ),
    )


def _last_run_record(
    summary_json: str,
    status_html: str,
    workbooks: object,
    bundle: object,
    dashboard_bundle: object,
    archives: object,
) -> dict[str, object]:
    """Return the small record kept in the browser to restore a finished run.

    Only text and paths are stored. The dashboards themselves are already
    held as compressed pages in the archive store, so links are rebuilt from
    those rather than from server paths that a restart would invalidate.
    """
    try:
        summary = json.loads(summary_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        summary = {}
    if not isinstance(summary, dict):
        summary = {}
    current_archive_ids = summary.get("dashboard_archive_ids") or []
    if not current_archive_ids and summary.get("dashboard_archive_id"):
        # Compatibility with records written before multi-economy runs stored
        # all current snapshot ids in the summary.
        current_archive_ids = [summary["dashboard_archive_id"]]
    if not isinstance(current_archive_ids, list):
        current_archive_ids = []
    return {
        "summary": str(summary_json or ""),
        "status": str(status_html or ""),
        "workbooks": [str(path) for path in (workbooks or [])],
        "bundle": str(bundle) if bundle else "",
        "dashboard_bundle": str(dashboard_bundle) if dashboard_bundle else "",
        "archive_ids": [str(archive_id) for archive_id in current_archive_ids],
        "finished_at": _format_tokyo_timestamp(datetime.now(timezone.utc)),
    }


def restore_last_run(last_run: object, browser_archives: object):
    """Put a finished run back on the page when someone returns to it.

    Dashboards are republished from the snapshots held in this browser, so
    they survive a Space restart. Downloads are only offered if the files are
    still on the server, because those cannot be rebuilt from the browser.
    """
    import gradio as gr

    record = last_run if isinstance(last_run, dict) else {}
    if not record.get("summary") and not record.get("archive_ids"):
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()

    archives = browser_archives if isinstance(browser_archives, list) else []
    wanted = set(record.get("archive_ids") or [])
    links: list[dict[str, str]] = []
    link_records: list[dict[str, object]] = []
    for archive in archives:
        if not isinstance(archive, dict) or archive.get("archive_id") not in wanted:
            continue
        try:
            url = _publish_dashboard_pages(
                archive.get("pages") or {},
                economy=str(archive.get("economy", "")),
                scenario=str(archive.get("scenario", "")),
                years=archive.get("years", ""),
                scenarios=archive.get("scenarios") or (),
            )
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if url:
            link_records.append(archive)
            links.append({"economy": str(archive.get("economy", "")), "url": url})

    for link, label in zip(links, _saved_dashboard_button_labels(link_records)):
        link["label"] = label

    surviving = [path for path in record.get("workbooks") or [] if Path(path).is_file()]
    bundle = record.get("bundle") or ""
    bundle_path = bundle if bundle and Path(bundle).is_file() else None
    dashboard_bundle = record.get("dashboard_bundle") or ""
    dashboard_bundle_path = (
        dashboard_bundle
        if dashboard_bundle and Path(dashboard_bundle).is_file()
        else None
    )

    expected_downloads = bool(record.get("workbooks") or bundle or dashboard_bundle)
    surviving_downloads = bool(surviving or bundle_path or dashboard_bundle_path)
    expired = expected_downloads and not surviving_downloads
    note = (
        f"<span class='result-hint'>Restored from "
        f"{html.escape(_run_timestamp_label({'created_at': record.get('finished_at', '')}))}."
        + (
            " The downloads from that run have since been cleared from the server."
            if expired
            else ""
        )
        + "</span>"
    )
    available_downloads = bool(surviving or bundle_path or dashboard_bundle_path)
    if not (links or available_downloads or expired):
        # Nothing survived worth showing; leave the panel in its resting state
        # rather than captioning an empty result.
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()

    if links or available_downloads:
        links_html = _result_links_html(
            dashboard_url=None,
            dashboard_links=links,
            dashboard_error=None,
            wants_dashboard=bool(links),
            workbook_count=len(surviving),
        )
        if links_html:
            links_html = links_html.replace("</div>", note + "</div>", 1)
        else:
            links_html = f"<div class='result-links'>{note}</div>"
    else:
        # Only the fact of expiry is left to report, so say that plainly
        # rather than dressing up the empty state.
        links_html = f"<div class='result-links'>{note}</div>"

    return (
        record.get("summary") or gr.skip(),
        record.get("status") or gr.skip(),
        surviving,
        bundle_path,
        links_html,
        dashboard_bundle_path,
    )


def resume_run(job_id: object, browser_archives: object):
    """Reattach to a run still going when the page was reopened."""
    import gradio as gr

    job = _job_snapshot(str(job_id or ""))
    if job is None:
        return (
            gr.Timer(active=False),
            gr.Button("Run", interactive=True),
            gr.Button(visible=False),
        )
    if job.get("state") in {"running", "cancel_requested"}:
        cancelling = job.get("state") == "cancel_requested"
        return (
            gr.Timer(active=True),
            gr.Button("Running…", interactive=False),
            gr.Button(
                "Cancelling…" if cancelling else "Cancel run",
                visible=True,
                interactive=not cancelling,
            ),
        )
    # A run that finished while the page was away is collected on the next tick.
    # Keep this distinct from "Running…": the calculator animation is keyed
    # to that label, and a terminal job should not make a refresh look like a
    # new multi-minute calculation while its final result is being collected.
    return (
        gr.Timer(active=True),
        gr.Button("Checking results…", interactive=False),
        gr.Button(visible=False),
    )


def prepare_run(
    balance_export_workbook: object,
    result_links: object = "",
) -> tuple[object, object, str, object, str, object, object, object, str]:
    """Lock a valid run, or reset an upload lost during a server restart.

    A browser can retain the parsed upload row while a Space rebuild removes
    the corresponding temporary server file. Treat that as a stale preview,
    not as a new build with no inputs.
    """
    import gradio as gr

    if _uploaded_paths(balance_export_workbook):
        locked_button, superseded_links = lock_run_button(result_links)
        return (
            True,
            locked_button,
            superseded_links,
            gr.skip(),
            gr.skip(),
            gr.skip(),
            gr.skip(),
            gr.skip(),
            "",
        )

    message = (
        "The app was updated and no longer has this uploaded file. "
        "Please upload the export again to continue."
    )
    readout = _export_readout_html(
        state="error",
        label="Please upload this export again",
        body=f"<p>{html.escape(message)}</p>",
    )
    return (
        False,
        gr.Button("Run", interactive=False),
        str(result_links or ""),
        None,
        readout,
        gr.Textbox(visible=False, value=""),
        gr.Button(visible=False),
        gr.File(visible=False),
        _status_html(message),
    )


def select_dashboard_archive(
    archive_id: str | None,
    browser_archives: object,
) -> str:
    """Republish a saved browser-local dashboard and return a link to it."""
    record = _browser_dashboard_record(archive_id, browser_archives)
    if record is None:
        return "<div class='result-links'><span class='result-hint'>Select a saved review.</span></div>"
    try:
        url = _publish_dashboard_pages(
            record.get("pages") or {},
            economy=str(record.get("economy", "")),
            scenario=str(record.get("scenario", "")),
            years=record.get("years", ""),
            scenarios=record.get("scenarios") or (),
        )
    except (OSError, ValueError, UnicodeDecodeError) as error:
        return (
            "<div class='result-links is-failed'><span class='result-hint'>"
            f"Could not restore this saved dashboard: {html.escape(str(error))}"
            "</span></div>"
        )
    if not url:
        return "<div class='result-links'><span class='result-hint'>This saved review has no dashboard pages.</span></div>"
    return (
        "<div class='result-links'>"
        f"<a class='result-link' href='{html.escape(url)}' target='_blank' "
        "rel='noopener'>Open this saved dashboard " + EXTERNAL_LINK_ICON + "</a>"
        "</div>"
    )


def load_browser_archives(browser_archives: object) -> object:
    """Populate the archive selector from the user's local browser state."""
    choices = _browser_dashboard_choices(browser_archives)
    return _dropdown_update(choices, choices[0][1] if choices else None)


def clear_browser_archives() -> tuple[list[object], object, str]:
    """Clear only this browser's saved dashboard snapshots."""
    return (
        [],
        _dropdown_update([], None),
        "<div class='result-links'><span class='result-hint'>Saved reviews cleared "
        "from this browser.</span></div>",
    )


def create_app():
    """Create the web interface for local or Hugging Face execution."""
    import gradio as gr

    DASHBOARD_SERVE_ROOT.mkdir(parents=True, exist_ok=True)
    gr.set_static_paths(
        paths=[LEAP_WALLPAPER_PATH, WALLPAPER_DIR, DASHBOARD_SERVE_ROOT]
    )
    theme = gr.themes.Soft(
        primary_hue="orange",
        secondary_hue="blue",
        neutral_hue="slate",
        radius_size="lg",
    )
    with gr.Blocks(
        title="LEAP Balance Review",
        theme=theme,
        css=APP_CSS + GUIDE_CSS,
        js=APP_JS.rstrip()[:-1] + GUIDE_JS + "\n}",
    ) as app:
        hosted_runtime_profile = _hosted_runtime_profile()
        gr.HTML(
            """<header id="app-hero">
              <span class="leap-mini-mark">L</span>
              <span class="leap-wordmark">LEAP Balance Review</span>
            </header>"""
        )
        gr.HTML(GUIDE_HTML, elem_id="guide-overlay")

        with gr.Column(elem_id="upload-card"):
            gr.HTML(
                """<div class="step-heading"><span class="step-kicker">01 · Run</span>
                  <strong>Add your export(s), choose what to build</strong>
                  <p>The economy and scenario are read from each export. Add
                  several to compare economies or scenarios in one dashboard;
                  the review workbook needs a single export.</p>
                </div>"""
            )
            balance_export_workbook = gr.File(
                label="Your LEAP Energy Balance export(s) (required)",
                file_types=[".xlsx", ".xlsm"],
                type="filepath",
                file_count="multiple",
                elem_id="balance-upload",
            )
            economy_override = gr.Textbox(
                label="Economy code",
                value="",
                placeholder="e.g. 20_USA",
                info="Only needed when the LEAP area name is unrecognised.",
                visible=False,
                elem_id="economy-override",
            )
            export_readout = gr.HTML(
                value=EXPORT_PROMPT_HTML,
                elem_id="export-readout",
            )
            with gr.Column(
                visible=False, elem_id="version-comparison-controls"
            ) as version_comparison_controls:
                with gr.Column(elem_id="version-comparison-dialog"):
                    gr.HTML(
                        "<p class='version-prompt-title'>Compare these exports?</p>"
                        "<p class='version-prompt-copy'>They have the same economy, "
                        "scenario and years.</p>"
                    )
                    with gr.Row():
                        original_export_name = gr.Dropdown(
                            label="Original version (filename)"
                        )
                        new_export_name = gr.Dropdown(label="New version (filename)")
                    version_selection_note = gr.HTML(
                        value="", elem_id="version-selection-note"
                    )
                    with gr.Row():
                        dismiss_version_button = gr.Button(
                            "Clear exports", elem_id="version-clear-exports"
                        )
                        confirm_version_button = gr.Button(
                            "Compare versions",
                            variant="primary",
                            elem_id="version-compare",
                        )
            compare_versions = gr.State(False)
            with gr.Row(elem_id="export-actions"):
                clear_export_button = gr.Button(
                    "Use a different export",
                    size="sm",
                    visible=False,
                    elem_id="clear-export",
                )
                # A second picker whose only job is to append: Gradio replaces
                # the selection when a multi-file field is used again, so
                # adding to a set otherwise means re-choosing all of it.
                add_export = gr.File(
                    label="Add another export",
                    file_types=[".xlsx", ".xlsm"],
                    type="filepath",
                    file_count="multiple",
                    visible=False,
                    elem_id="add-export",
                )
            esto_vintage_options = _esto_vintage_choices()
            esto_vintage_labels = json.dumps(
                [label for label, _value in esto_vintage_options]
            )
            with gr.Row(elem_id="build-choice-heading"):
                with gr.Column(
                    scale=1, min_width=0, elem_id="build-choice-title"
                ):
                    gr.HTML(
                        "<p class='choose-label'>What should this run build? "
                        f"<span data-vintage-labels='{html.escape(esto_vintage_labels, quote=True)}'>"
                        "ESTO vintage:</span></p>"
                    )
                esto_vintage = gr.Dropdown(
                    label="ESTO vintage",
                    show_label=False,
                    choices=esto_vintage_options,
                    value=_default_esto_vintage(esto_vintage_options),
                    allow_custom_value=False,
                    filterable=False,
                    elem_id="esto-vintage",
                    scale=0,
                    min_width=285,
                )
            with gr.Row(elem_id="outputs-row"):
                with gr.Column(elem_classes=["output-card"], elem_id="workbook-card"):
                    want_workbook = gr.Checkbox(
                        label=OUTPUT_WORKBOOK_LABEL,
                        value=True,
                        elem_id="want-workbook",
                        container=False,
                    )
                    year = gr.Textbox(
                        label="Which year(s) should the workbook review?",
                        value="",
                        placeholder="2022 or 2022, 2030, 2040",
                        info="Separate multiple years with commas.",
                        elem_id="year-input",
                    )
                    workbook_runtime_note = gr.HTML(
                        _card_runtime_note_html(
                            hosted_runtime_profile, "workbook", years=1
                        ),
                        elem_id="workbook-runtime-note",
                    )
                with gr.Column(elem_classes=["output-card"], elem_id="dashboard-choice"):
                    want_dashboard = gr.Checkbox(
                        label=OUTPUT_DASHBOARD_LABEL,
                        value=True,
                        elem_id="want-dashboard",
                        container=False,
                    )
                    gr.HTML(
                        "<p class='card-note'>Interactive sector pages comparing "
                        "LEAP with ESTO and the 9th Outlook, one dashboard per "
                        "economy. Upload both scenarios for an economy and its "
                        "dashboard gets a Reference/Target toggle. Adds a few "
                        "minutes to the run.</p>"
                    )
                    dashboard_runtime_note = gr.HTML(
                        "<p class='card-note runtime-note'>"
                        + html.escape(
                            format_runtime_note(
                                hosted_runtime_profile,
                                process_group="dashboard",
                            )
                        )
                        + "</p>"
                    )
            with gr.Row(elem_id="run-actions"):
                run_button = gr.Button(
                    "Run",
                    variant="primary",
                    interactive=False,
                    elem_id="run-button",
                )
                cancel_button = gr.Button(
                    "Cancel run",
                    visible=False,
                    elem_id="cancel-run",
                )
            status = gr.HTML(value="", elem_id="run-status")
            calculator_animation = gr.HTML(
                _calculator_html(hosted_runtime_profile, want_dashboard=True, years=1),
                elem_id="calculator-holder",
            )
            with gr.Accordion(
                "Technical run details",
                open=False,
                elem_id="technical-details",
            ):
                summary = gr.Code(
                    label="Run summary",
                    language="json",
                    interactive=False,
                )


        browser_archives = gr.BrowserState(
            default_value=[],
            storage_key="leap_balance_review_dashboard_archives",
            secret=BROWSER_STATE_SECRET,
        )
        # The id of a run in flight, kept in the browser so a reopened page can
        # find its way back to work that is still going.
        active_job = gr.BrowserState(
            default_value="",
            storage_key="leap_balance_review_active_job",
            secret=BROWSER_STATE_SECRET,
        )
        upload_is_live = gr.State(False)
        run_timer = gr.Timer(3, active=False)
        # A Gradio timer ticks in the browser, and a browser throttles or
        # suspends timers in a tab that is not being looked at. So a run that
        # finished while the user was in another window stays "running" on
        # screen until the tab wakes up. This button is pressed by the page
        # the moment it becomes visible again, asking the same question the
        # timer asks, so returning to the tab shows the truth immediately.
        # Rendered but hidden in CSS: a Gradio component with
        # visible=False is absent from the page, and the script has to
        # be able to press this one.
        refresh_run = gr.Button("", elem_id="refresh-run")
        # What the last finished run produced, so returning to the page shows
        # it again instead of an empty results panel.
        last_run = gr.BrowserState(
            default_value={},
            storage_key="leap_balance_review_last_run",
            secret=BROWSER_STATE_SECRET,
        )
        with gr.Column(elem_id="results-card"):
            gr.HTML(
                """<div class="step-heading">
                  <div class="results-summary">
                    <span class="step-kicker">02 · Results</span>
                    <div class="results-heading-copy">
                      <strong>Your dashboard and workbooks</strong>
                      <span>Open the dashboard or download the files from this run.</span>
                    </div>
                  </div>
                </div>"""
            )
            result_links = gr.HTML(value=RESULTS_EMPTY_HTML, elem_id="result-links")
            with gr.Row(elem_id="download-row"):
                output = gr.File(
                    label="Review workbook(s)",
                    file_count="multiple",
                    elem_id="review-workbooks-download",
                )
                dashboard_download = gr.File(
                    label="Dashboard archive (.zip)",
                    elem_id="dashboard-download",
                )
                diagnostics_bundle = gr.File(
                    label="Complete run archive (.zip)",
                    elem_id="diagnostics-bundle",
                )
            with gr.Accordion(
                "How to read the review workbook",
                open=False,
                elem_id="workbook-note",
            ):
                gr.Markdown(
                    "Read the three sheets in order. A red cell on "
                    "**LEAP – Source Error** is a disagreement between the model "
                    "and the source data, not a verdict on which one is wrong: it "
                    "can be the LEAP calculation, the mapping behind the "
                    "comparison, or the baseline seed values.",
                    elem_id="results-note",
                )
            with gr.Accordion(
                "Recent dashboards saved in this browser",
                open=False,
                elem_id="saved-reviews",
            ):
                gr.Markdown(
                    "This browser can remember up to three dashboards for quick "
                    "reopening. They are not a backup: the oldest is replaced when "
                    "a fourth is saved, while website updates, clearing site data, "
                    "private browsing, storage limits, or switching browser or "
                    "device may remove them. Download the **Dashboard archive "
                    "(.zip)** to keep a permanent copy.",
                    elem_id="saved-reviews-note",
                )
                with gr.Row(elem_id="dashboard-controls"):
                    dashboard_archive = gr.Dropdown(
                        label="Saved review",
                        choices=[],
                        value=None,
                        interactive=True,
                        scale=3,
                    )
                    clear_dashboard_button = gr.Button(
                        "Clear saved reviews",
                        size="sm",
                        elem_id="clear-dashboards",
                        scale=1,
                    )
                saved_link = gr.HTML(
                    value=(
                        "<div class='result-links'><span class='result-hint'>"
                        "Choose a recent dashboard above to reopen it.</span></div>"
                    ),
                    elem_id="saved-link",
                )

        for _control in (year, want_workbook, want_dashboard):
            _control.change(
                fn=update_runtime_notes,
                inputs=[
                    year,
                    want_workbook,
                    want_dashboard,
                    balance_export_workbook,
                    compare_versions,
                ],
                outputs=[
                    workbook_runtime_note,
                    dashboard_runtime_note,
                    calculator_animation,
                    run_button,
                ],
            )
        clear_export_outputs = [
            balance_export_workbook,
            export_readout,
            economy_override,
            clear_export_button,
            add_export,
            version_comparison_controls,
            original_export_name,
            new_export_name,
            compare_versions,
            version_selection_note,
        ]
        clear_export_button.click(
            fn=clear_uploaded_export,
            outputs=clear_export_outputs,
        )
        add_export.change(
            fn=append_uploaded_exports,
            inputs=[balance_export_workbook, add_export],
            outputs=[balance_export_workbook, add_export],
        )
        balance_export_workbook.change(
            fn=toggle_add_export,
            inputs=balance_export_workbook,
            outputs=add_export,
        )
        balance_export_workbook.change(
            fn=inspect_uploaded_export,
            inputs=[balance_export_workbook, year],
            outputs=[
                export_readout,
                economy_override,
                year,
                clear_export_button,
                want_workbook,
                want_dashboard,
            ],
        ).then(
            fn=adjust_review_year_for_vintage,
            inputs=[esto_vintage, year],
            outputs=year,
        ).then(
            fn=version_comparison_control_updates,
            inputs=balance_export_workbook,
            outputs=[
                version_comparison_controls,
                original_export_name,
                new_export_name,
                compare_versions,
                version_selection_note,
                confirm_version_button,
            ],
        ).then(
            # Inspection can populate the review year and change which outputs
            # are available. Quote readiness only after those values settle;
            # parallel callbacks raced and left Run disabled until Dashboard
            # was clicked a second time.
            fn=update_runtime_notes,
            inputs=[
                year,
                want_workbook,
                want_dashboard,
                balance_export_workbook,
                compare_versions,
            ],
            outputs=[
                workbook_runtime_note,
                dashboard_runtime_note,
                calculator_animation,
                run_button,
            ],
        )
        esto_vintage.change(
            fn=adjust_review_year_for_vintage,
            inputs=[esto_vintage, year],
            outputs=year,
        )
        confirm_version_button.click(
            fn=confirm_version_comparison,
            inputs=[original_export_name, new_export_name],
            outputs=[
                compare_versions,
                want_workbook,
                want_dashboard,
                version_comparison_controls,
            ],
        ).then(
            fn=version_comparison_readout,
            inputs=[
                balance_export_workbook,
                original_export_name,
                new_export_name,
                compare_versions,
            ],
            outputs=export_readout,
        ).then(
            fn=update_runtime_notes,
            inputs=[
                year,
                want_workbook,
                want_dashboard,
                balance_export_workbook,
                compare_versions,
            ],
            outputs=[
                workbook_runtime_note,
                dashboard_runtime_note,
                calculator_animation,
                run_button,
            ],
        )
        original_export_name.change(
            fn=synchronise_version_comparison_role,
            inputs=[original_export_name, balance_export_workbook],
            outputs=[
                new_export_name,
                confirm_version_button,
                version_selection_note,
            ],
        )
        new_export_name.change(
            fn=synchronise_version_comparison_role,
            inputs=[new_export_name, balance_export_workbook],
            outputs=[
                original_export_name,
                confirm_version_button,
                version_selection_note,
            ],
        )
        dismiss_version_button.click(
            fn=clear_uploaded_export,
            outputs=clear_export_outputs,
        ).then(
            fn=update_runtime_notes,
            inputs=[
                year,
                want_workbook,
                want_dashboard,
                balance_export_workbook,
                compare_versions,
            ],
            outputs=[
                workbook_runtime_note,
                dashboard_runtime_note,
                calculator_animation,
                run_button,
            ],
        )
        # Starting a run hands the work to a background worker and remembers
        # its id in the browser, so closing the tab does not cancel the build
        # and reopening the page reattaches to it.
        run_outputs_list = [
            summary,
            status,
            output,
            diagnostics_bundle,
            result_links,
            dashboard_archive,
            browser_archives,
            dashboard_download,
        ]
        run_button.click(
            fn=prepare_run,
            inputs=[balance_export_workbook, result_links],
            outputs=[
                upload_is_live,
                run_button,
                result_links,
                balance_export_workbook,
                export_readout,
                economy_override,
                clear_export_button,
                add_export,
                status,
            ],
        ).then(
            fn=start_run,
            inputs=[
                want_workbook,
                want_dashboard,
                year,
                economy_override,
                balance_export_workbook,
                browser_archives,
                upload_is_live,
                esto_vintage,
                compare_versions,
                original_export_name,
                new_export_name,
            ],
            outputs=[active_job, cancel_button],
        ).then(
            fn=activate_run_timer,
            inputs=active_job,
            outputs=run_timer,
        )
        run_timer.tick(
            fn=poll_run,
            inputs=[active_job, browser_archives],
            outputs=[
                *run_outputs_list,
                run_timer,
                run_button,
                cancel_button,
                active_job,
                last_run,
            ],
        )
        cancel_button.click(
            fn=cancel_run,
            inputs=active_job,
            outputs=[cancel_button, status],
        )
        refresh_run.click(
            fn=poll_run,
            inputs=[active_job, browser_archives],
            outputs=[
                *run_outputs_list,
                run_timer,
                run_button,
                cancel_button,
                active_job,
                last_run,
            ],
        )
        dashboard_archive.change(
            fn=select_dashboard_archive,
            inputs=[dashboard_archive, browser_archives],
            outputs=saved_link,
        )
        clear_dashboard_button.click(
            fn=clear_browser_archives,
            outputs=[browser_archives, dashboard_archive, saved_link],
        )
        app.load(
            fn=load_browser_archives,
            inputs=browser_archives,
            outputs=dashboard_archive,
        ).then(
            fn=restore_last_run,
            inputs=[last_run, browser_archives],
            outputs=[
                summary,
                status,
                output,
                diagnostics_bundle,
                result_links,
                dashboard_download,
            ],
        ).then(
            fn=resume_run,
            inputs=[active_job, browser_archives],
            outputs=[run_timer, run_button, cancel_button],
        )
    return app


#%%
if __name__ == "__main__":
    APP = create_app()
    APP.launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )

#%%
