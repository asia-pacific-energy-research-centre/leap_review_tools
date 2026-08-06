#%%
"""Gradio web application for the complete LEAP balance-review workflow.

The app calls the repository's existing ``balance-review-from-export``
orchestration. It does not reimplement diagnostics or workbook construction.
"""

from __future__ import annotations

import json
import html
import base64
from concurrent.futures import ThreadPoolExecutor
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from web_app.guide_overlay import GUIDE_CSS, GUIDE_HTML, GUIDE_JS
from web_app.runtime_profile import (
    estimate_runtime,
    format_runtime_note,
    load_runtime_profile,
    record_runtime_sample,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DASHBOARD_MIN_YEAR = 2010
DEFAULT_DASHBOARD_MAX_YEAR = 2060
# Keep the browser-local payload within common localStorage quotas while still
# allowing comparison between several recent economy/scenario runs.
MAX_BROWSER_DASHBOARDS = 3
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
  padding: 0.6rem 0 1.6rem !important;
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
  margin-bottom: 0.3rem;
  color: var(--orange);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.step-heading strong {
  display: block;
  color: var(--ink);
  font-size: 1.2rem;
  letter-spacing: -0.035em;
  line-height: 1.15;
}
.step-heading p { margin: 0.15rem 0 0; color: var(--muted); font-size: 0.83rem; }
#upload-card, #results-card {
  gap: 0.5rem;
  padding: 1.1rem !important;
  border: 1px solid var(--line) !important;
  border-radius: 9px !important;
  background: var(--panel) !important;
  box-shadow: var(--panel-shadow);
}
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
#upload-row { align-items: center; }
#upload-card .step-heading, #results-card .step-heading { margin: 0 0 0.35rem; }
#upload-card .step-heading p, #results-card .step-heading p { font-size: 0.8rem; }
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
  min-height: 64px !important;
  max-height: none !important;
  padding: 0.7rem 1rem !important;
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
  gap: 0.6rem !important;
  height: auto !important;
  min-height: 0 !important;
  color: #ffffff !important;
  opacity: 1 !important;
}
#balance-upload > button .icon-wrap { width: 26px !important; margin: 0 !important; }
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
.gradio-container .icon-button-wrapper {
  position: static !important;
  min-height: 0 !important;
  margin: 0 0 0.35rem auto !important;
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
}
#export-readout {
  margin-top: 0.2rem;
}
.export-readout {
  padding: 0.55rem 0.8rem;
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
  grid-template-columns: minmax(0, 2fr) repeat(3, minmax(0, 1fr));
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
.upload-row.is-ok { border-left-color: #2f8f5b; }
.upload-row.is-warn { border-left-color: #e0912f; }
.upload-row.is-bad { border-left-color: #c0392b; }
.upload-row.is-bad .upload-error { grid-column: 2 / -1; color: #a8342a; }
#selection-row { gap: 0.8rem; margin-top: 0.15rem; }
/* A multi-file upload cannot produce a workbook, so the card says so by
   fading rather than vanishing: the option is still visible, just plainly
   out of reach. */
.output-card.is-unavailable { opacity: 0.45; }
.output-card.is-unavailable > div:first-child label { cursor: not-allowed; }
.output-card.is-unavailable .card-note::after {
  content: " Unavailable for a multi-file upload.";
  color: #a8342a;
  font-weight: 600;
}
.readout-chip {
  min-width: 116px;
  padding: 0.4rem 0.6rem;
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
/* The ESTO override is a genuine disclosure: say what opening it does, and
   keep the affordance on the left where the label is read from. */
/* Every disclosure on the page says what opening it does and puts the
   affordance on the left, where the label is read from. */
#workbook-note, #saved-reviews {
  margin-top: 0.35rem;
  border: 1px solid var(--line) !important;
  border-radius: 6px !important;
  background: #ffffff !important;
}
#workbook-note > button,
#saved-reviews > button {
  display: flex !important;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 0.85rem !important;
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
#saved-reviews > button::after { content: "Your earlier runs"; }
#workbook-note > button.open::after,
#saved-reviews > button.open::after { content: "Click to close"; }
#workbook-note > button .icon, #workbook-note > button svg,
#saved-reviews > button .icon, #saved-reviews > button svg { display: none !important; }
#workbook-note > div:last-child,
#saved-reviews > div:last-child { padding: 0.9rem 0.85rem !important; }
#technical-details { border-radius: 3px !important; border-color: var(--line) !important; }
#technical-details > button { color: #405a73 !important; font-weight: 650; background: #f2f6fa !important; }
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
  min-height: 46px;
  border: 1px solid #cf5a22 !important;
  border-radius: 3px !important;
  background: var(--orange) !important;
  color: #ffffff !important;
  font-weight: 750;
  box-shadow: 0 2px 5px rgba(188, 70, 24, 0.22);
}
#run-button:hover { background: #d45a20 !important; }
/* Locked while a build is in flight: still legible, plainly not pressable. */
#run-button:disabled, #run-button[disabled] {
  background: #d9a68c !important;
  border-color: #d9a68c !important;
  color: #ffffff !important;
  cursor: not-allowed !important;
  opacity: 1 !important;
  box-shadow: none !important;
}
#run-status textarea, #run-status input { font-size: 0.88rem; }
#results-empty {
  padding: 0.95rem 1.1rem;
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
.result-link.is-primary { font-size: 1.02rem; padding: 14px 24px; }
.result-hint { color: var(--muted); font-size: 0.84rem; }
.result-links.is-failed { padding: 0.7rem 0.9rem; border-left: 4px solid #c0392b;
  border-radius: 4px; background: #fdf4f3; }
/* Output file fields never receive an upload, so their dropzone is dead space. */
#results-card .file-preview { min-height: 0 !important; }
#results-card:not(:has(.file-preview)) #download-row { display: none !important; }
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
#clear-dashboards { align-self: end; max-width: 210px; }
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
#export-actions { gap: 0.5rem; align-items: center; }
/* The add-another picker is a file field, dressed to match the button it sits
   beside so the pair reads as two related actions. */
#add-export { border: 0 !important; background: transparent !important; min-width: 0 !important; }
#add-export > label.float { display: none !important; }
#add-export .file-preview-holder { display: none !important; }
#add-export > button {
  display: flex !important;
  height: auto !important;
  min-height: 0 !important;
  max-height: none !important;
  padding: 0.35rem 0.7rem !important;
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
  margin: 0.35rem 0 0.1rem;
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
  min-height: 54px;
  margin: 0 !important;
  padding: 0.75rem 0.95rem !important;
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
.output-card > div:not(:first-child) { padding: 0.7rem 0.85rem !important; }
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
  #advanced-options > button::after { display: none; }
  #clear-dashboards { align-self: stretch; max-width: none; }
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
  const install = () => {
    relabelUpload();
    placeGuideLaunch();
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
    let startedAt = null;
    window.setInterval(() => {
      const host = document.querySelector('#calculator-animation');
      if (!host) return;
      const face = host.querySelector('.run-stopwatch');
      const value = host.querySelector('.stopwatch-value');
      const remaining = host.querySelector('.stopwatch-remaining');
      const running = button.disabled;
      host.classList.toggle('is-running', running);
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
          : ' — longer than usual';
      }
    }, 500);
  };
  installStopwatch();
  installWallpaperSwitch();
  window.setTimeout(() => { install(); syncOutputCards(); }, 150);
  new MutationObserver(install).observe(document.body, { childList: true, subtree: true });
}
"""

APP_JS = APP_JS.replace(
    "__LEAP_WALLPAPERS__", json.dumps(available_wallpapers())
)

from codebase.portable_release import developer_launcher  # noqa: E402
from codebase.portable_release.settings import DeveloperSettings  # noqa: E402
from codebase.utilities.leap_balance_export_resolver import (  # noqa: E402
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


def _build_context(run_root: Path):
    """Build the same live-repository context used by developer mode."""
    settings = DeveloperSettings(
        source_path=INITIALISATION_ROOT
        / "config"
        / "portable_release_manifest.toml",
        repositories=_repository_roots(),
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
    diagnostics_directory: Path,
    run_directory: Path,
    dashboard_directory: Path | None = None,
    log_directory: Path | None = None,
) -> None:
    """Package derived diagnostics and the workbook for optional download."""
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for workbook_path in workbook_paths:
            bundle.write(workbook_path, arcname=f"workbooks/{workbook_path.name}")
        if diagnostics_directory.is_dir():
            for path in sorted(diagnostics_directory.rglob("*.csv")):
                bundle.write(path, arcname=f"diagnostics/{path.name}")
        for name in ("validation_report.txt", "run_manifest.json", "run_manifest.txt"):
            path = run_directory / name
            if path.is_file():
                bundle.write(path, arcname=name)
        if dashboard_directory is not None and dashboard_directory.is_dir():
            for path in sorted(dashboard_directory.rglob("*")):
                if path.is_file():
                    bundle.write(path, arcname=f"dashboard/{path.relative_to(dashboard_directory)}")
        if log_directory is not None and log_directory.is_dir():
            for path in sorted(log_directory.glob("*.log")):
                bundle.write(path, arcname=f"logs/{path.name}")


def _dashboard_pages(dashboard_directory: Path) -> list[str]:
    """Return dashboard page filenames suitable for the page selector."""
    return sorted(
        path.name
        for path in dashboard_directory.glob("*.html")
        if path.name != "index.html"
    )


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
                f"{record.get('years', '')} ({record.get('created_at', '')})",
                str(record["archive_id"]),
            )
        )
    return choices


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
) -> dict[str, object]:
    """Create a compressed browser-local snapshot of every dashboard page."""
    pages = {}
    for page_name in _dashboard_pages(dashboard_directory):
        page_path = dashboard_directory / page_name
        page_html = _inline_dashboard_chart_bundle(
            page_path,
            page_path.read_text(encoding="utf-8"),
        )
        pages[page_name] = _compress_dashboard_html(page_html)
    return {
        "archive_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "economy": economy,
        "scenario": scenario,
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


def _dashboard_page_title(page_name: str) -> str:
    """Return a readable page name for the generated dashboard index."""
    stem = re.sub(r"\.html$", "", str(page_name), flags=re.IGNORECASE)
    stem = re.sub(r"^\d+[_-]", "", stem)
    return stem.replace("_", " ").replace("-", " ").strip().capitalize() or page_name


def _publish_dashboard_pages(
    pages: dict[str, str],
    *,
    economy: str,
    scenario: str,
    years: object,
) -> str | None:
    """Write dashboard pages to a served folder and return the index URL.

    The pages are the same compressed snapshots kept in browser storage, so a
    saved review reopens through exactly the path a fresh one does. Serving
    them as files is what lets the app hand out a link rather than embed the
    dashboard in the page.
    """
    if not pages:
        return None
    run_directory = DASHBOARD_SERVE_ROOT / uuid4().hex
    run_directory.mkdir(parents=True, exist_ok=True)

    links = []
    for page_name in sorted(pages):
        page_html = _decompress_dashboard_html(str(pages[page_name]))
        safe_name = Path(str(page_name)).name
        (run_directory / safe_name).write_text(
            _locked_dashboard_html(page_html, scenario), encoding="utf-8"
        )
        links.append(
            f'<li><a href="{html.escape(safe_name)}">'
            f"{html.escape(_dashboard_page_title(safe_name))}</a></li>"
        )

    index_path = run_directory / "index.html"
    index_path.write_text(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>LEAP dashboard</title><style>"
        "body{font:15px/1.5 'Segoe UI',Arial,sans-serif;color:#1d2d3d;"
        "background:#edf3fa;margin:0;padding:2rem}"
        "h1{color:#1f3d5b;margin:0 0 .25rem}"
        ".meta{color:#5b7086;margin:0 0 1.5rem}"
        "ul{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:.6rem;max-width:900px}"
        "li{flex:1 1 220px}"
        "a{display:block;padding:.8rem 1rem;border:1px solid #c4d2e0;border-left:4px solid var(--orange);"
        "border-radius:3px;background:#fff;color:#1f3d5b;font-weight:600;text-decoration:none}"
        "a:hover{background:#fff6f1}"
        "</style></head><body>"
        "<h1>LEAP dashboard</h1>"
        f"<p class='meta'>{html.escape(str(economy))} &nbsp;|&nbsp; "
        f"{html.escape(str(scenario))} &nbsp;|&nbsp; {html.escape(str(years))}</p>"
        f"<ul>{''.join(links)}</ul></body></html>",
        encoding="utf-8",
    )
    return f"/gradio_api/file={index_path.as_posix()}"


def _locked_dashboard_html(page_html: str, scenario: str) -> str:
    """Pin a generated page to the scenario this run actually produced."""
    scenario_mode = {"reference": "ref", "target": "tgt"}.get(scenario.casefold(), "")
    locked_controls = """
<style>
  .dashboard-switcher, .scenario-toggle { display:none !important; }
</style>
<script>
  (function () {
    var mode = %s;
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
""" % json.dumps(scenario_mode)
    if "</body>" in page_html:
        return page_html.replace("</body>", locked_controls + "</body>", 1)
    return page_html + locked_controls


OUTPUT_WORKBOOK_LABEL = "Balance review workbook"
OUTPUT_DASHBOARD_LABEL = "Dashboard"

RESULTS_EMPTY_HTML = (
    "<div id='results-empty'>Nothing yet. Choose what to build above and press "
    "Run. Your workbook and dashboard links will appear here.</div>"
)


def _status_html(message: str) -> str:
    """Return run status as escaped markup, or nothing at all when silent."""
    text = str(message or "").strip()
    if not text:
        return ""
    tone = "is-failed" if text.lower().startswith("build failed") else "is-done"
    return f"<p class='run-status-line {tone}'>{html.escape(text)}</p>"


def _run_status_line(
    *,
    wants_workbook: bool,
    wants_dashboard: bool,
    dashboard_ok: bool,
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
    else:
        status = f"Built the {made}."
    if runtime_seconds:
        measured = []
        for label, key in (("workbook", "workbook"), ("dashboard", "dashboard")):
            seconds = runtime_seconds.get(key)
            if seconds is not None:
                measured.append(f"{label}: {seconds:.1f}s")
        if measured:
            status += " Runtime — " + ", ".join(measured) + "."
    return status


def _card_runtime_note_html(
    profile: dict[str, object], group: str, *, years: int, economies: int = 1
) -> str:
    """Return one process card's runtime note."""
    return (
        "<p class='card-note runtime-note'>"
        + html.escape(
            format_runtime_note(
                profile, process_group=group, years=years, economies=economies
            )
        )
        + "</p>"
    )


def _calculator_html(
    profile: dict[str, object], *, want_dashboard: bool, years: int, economies: int = 1
) -> str:
    """Return the working animation, carrying the elapsed clock with it.

    The stopwatch lives here rather than beside the estimates because this
    block is only shown while a build is running, so the clock appears exactly
    when it is useful and takes no space the rest of the time. It ticks in the
    browser: the build itself only yields every ten seconds.
    """
    group = "full_run" if want_dashboard else "workbook"
    estimate, _ = estimate_runtime(profile, process_group=group, years=years)
    if estimate and want_dashboard and economies > 1:
        dashboard_only, _ = estimate_runtime(profile, process_group="dashboard")
        estimate += (dashboard_only or 0) * (economies - 1)
    expected = f' data-expected="{int(estimate)}"' if estimate else ""
    return (
        f'<div id="calculator-animation" role="status" aria-live="polite"{expected}>'
        '<div class="calc-machine" aria-hidden="true">'
        '<div class="calc-display">CALCULATING</div>'
        '<div class="calc-keys">'
        + ('<i class="calc-key"></i>' * 6)
        + "</div></div>"
        '<div class="calc-caption">Checking balances and preparing your files'
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
) -> tuple[str, str]:
    """Re-quote the estimates for the years typed and the economies uploaded."""
    profile = _hosted_runtime_profile()
    years = max(len(_requested_years(year)), 1)
    economies = max(len(group_by_economy(read_uploads(balance_export_workbook))), 1)
    return (
        _card_runtime_note_html(profile, "workbook", years=years),
        _calculator_html(
            profile,
            want_dashboard=bool(want_dashboard) or not bool(want_workbook),
            years=years,
            economies=economies,
        ),
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


def clear_uploaded_export() -> tuple[object, str, object, object, object, object, object]:
    """Drop the loaded export so a different one can be added.

    Gradio's own clear control is an unlabelled icon, which is easy to miss;
    this is the same action said plainly. Results from a finished run are left
    alone, because those files are still valid and worth keeping.
    """
    import gradio as gr

    return (
        None,
        EXPORT_PROMPT_HTML,
        gr.Textbox(visible=False, value=""),
        gr.Button(visible=False),
        gr.Dropdown(choices=[], value=None, visible=False),
        gr.Dropdown(choices=[], value=None, visible=False),
    )


# Runs are executed off the request that started them, in a worker thread
# owned by the process rather than by a browser tab. Closing the tab, losing
# the connection or reloading the page therefore does not cancel a build: the
# page reattaches by job id and keeps polling. The dictionary lives as long as
# the process does, so a Space restart is still the end of a run.
RUN_JOBS: dict[str, dict[str, object]] = {}
RUN_JOBS_LOCK = threading.Lock()
JOB_RETENTION_SECONDS = 6 * 60 * 60


def _forget_stale_jobs() -> None:
    """Drop finished jobs nobody came back for."""
    cutoff = time.time() - JOB_RETENTION_SECONDS
    with RUN_JOBS_LOCK:
        for job_id in [
            key
            for key, job in RUN_JOBS.items()
            if job.get("state") != "running" and float(job.get("finished") or 0) < cutoff
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


def start_run(
    want_workbook: object,
    want_dashboard: object,
    year: object,
    economy_override: str,
    balance_export_workbook: object,
    browser_archives: object,
) -> str:
    """Begin a build in the background and return its job id.

    The uploaded files are copied before the worker starts, because Gradio
    clears its upload directory once the request that carried them ends.
    """
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
    )

    def worker() -> None:
        def report(message: str) -> None:
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
            )
            _set_job(
                job_id, state="done", finished=time.time(), result=result, message=""
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
    return job_id


def describe_job(job_id: object) -> str:
    """Return the worker's own progress line for a running job."""
    job = _job_snapshot(str(job_id or ""))
    if not job or job.get("state") != "running":
        return ""
    elapsed = int(time.time() - float(job.get("started") or time.time()))
    minutes, seconds = divmod(elapsed, 60)
    return f"{job.get('message') or 'Working.'} ({minutes}:{seconds:02d} elapsed)"


def lock_run_button() -> object:
    """Show the run as under way and refuse a second press."""
    import gradio as gr

    return gr.Button("Running…", interactive=False)


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
    process_group: str, elapsed_seconds: float, years: int | None = None
) -> None:
    """Fold one measured run into the profile the interface quotes.

    The committed file ships a seed so a freshly built Space can quote a
    duration immediately. Each hosted run replaces the oldest of the five
    samples, so the seed is displaced as real measurements arrive.

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
    if links:
        # One button per economy, named, so a multi-economy run is navigable.
        for link in links:
            label = (
                "Open the dashboard"
                if len(links) == 1
                else f"{link['economy']} dashboard"
            )
            parts.append(
                f"<a class='result-link is-primary' href='{html.escape(link['url'])}' "
                f"target='_blank' rel='noopener'>{html.escape(label)} "
                "<span aria-hidden='true'>↗</span></a>"
            )
    elif dashboard_url:
        parts.append(
            f"<a class='result-link is-primary' href='{html.escape(dashboard_url)}' "
            "target='_blank' rel='noopener'>Open the dashboard "
            "<span aria-hidden='true'>↗</span></a>"
        )
    elif wants_dashboard:
        reason = dashboard_error or "No dashboard was generated for this run."
        return (
            "<div class='result-links is-failed'>"
            f"<span class='result-hint'>{html.escape(reason)}</span></div>"
        )
    if workbook_count:
        noun = "workbook" if workbook_count == 1 else "workbooks"
        parts.append(
            f"<span class='result-hint'>Your review {noun} and the full run "
            "archive are ready to download below.</span>"
        )
    if not parts:
        return RESULTS_EMPTY_HTML
    return f"<div class='result-links'>{''.join(parts)}</div>"


EXPORT_PROMPT_HTML = (
    "<div class='export-readout is-waiting'>"
    "<span class='readout-label'>Waiting for your export</span>"
    "<p>Add one workbook above, or several at once, and we will read the "
    "economy and scenario straight out of each. Several exports build a "
    "dashboard; a single export can also build the review workbook.</p>"
    "</div>"
)


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
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _read_upload(path: Path) -> ExportUpload:
    """Return what one export declares, or why it cannot be used."""
    try:
        identity = infer_balance_export_identity(path)
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


def _uploads_table(uploads: list[ExportUpload]) -> str:
    """Return one row per uploaded file, saying what was read from it."""
    rows = []
    for upload in uploads:
        name = html.escape(upload.path.name)
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
            f"<li class='upload-row is-{state}'><strong>{name}</strong>{detail}</li>"
        )
    return f"<ul class='upload-list'>{''.join(rows)}</ul>"


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

    grouped = group_by_economy(uploads)
    economies = list(grouped)
    multiple = len(uploads) > 1
    readable = [upload for upload in uploads if upload.ok]
    unnamed = [upload for upload in readable if not upload.economy]

    # A workbook covers one economy and one scenario, so a multi-file upload
    # builds the dashboard instead. The card says so rather than silently
    # ignoring the choice.
    workbook_update = gr.Checkbox(value=not multiple, interactive=not multiple)
    dashboard_update = gr.Checkbox(value=True)

    if not readable:
        body = _uploads_table(uploads)
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

    all_years = sorted({year for upload in readable for year in upload.years})
    requested = _requested_years(year)
    year_update = gr.Textbox()
    if all_years and (not requested or set(requested) - set(all_years)):
        year_update = gr.Textbox(value=str(all_years[0]))

    if multiple:
        note = (
            f"{len(readable)} exports across {len(economies)} "
            f"{'economy' if len(economies) == 1 else 'economies'}. "
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
                body=_uploads_table(uploads) + f"<p>{html.escape(note)}</p>",
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
    year_span = (
        f"{upload.years[0]}–{upload.years[-1]}"
        if len(upload.years) > 1
        else str(upload.years[0])
    )
    if not upload.economy:
        body = (
            "<div class='readout-chips'>"
            + _readout_chip("Scenario", upload.scenario)
            + _readout_chip("Years in this export", year_span)
            + "</div>"
            f"<p>The LEAP area is named “{html.escape(upload.area_name)}”, "
            "which does not match an APEC economy. Enter the economy code below "
            "and everything else still comes from the export.</p>"
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
        "<div class='readout-chips'>"
        + _readout_chip("Economy", upload.economy)
        + _readout_chip("Scenario", upload.scenario)
        + _readout_chip("Years in this export", year_span)
        + "</div>"
        f"<p>LEAP area “{html.escape(upload.area_name)}”. Choose any "
        "review year within this range.</p>"
    )
    return (
        _export_readout_html(state="ready", label="Read from your export", body=body),
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
    dashboard_min_year: float = DEFAULT_DASHBOARD_MIN_YEAR,
    dashboard_max_year: float = DEFAULT_DASHBOARD_MAX_YEAR,
) -> tuple[str, str, object, str | None, str, object, object]:
    """Build the outputs a run asked for, from one LEAP export."""
    persistent_bundle: Path | None = None
    run_started = time.perf_counter()
    try:
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

        uploads = read_uploads(balance_export_workbook)
        if not uploads:
            raise ValueError("Please upload at least one LEAP Energy Balance export.")
        unreadable = [upload for upload in uploads if not upload.ok]
        readable = [upload for upload in uploads if upload.ok]
        if not readable:
            raise ValueError(unreadable[0].error)

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

        wanted_economies = [
            name
            for name in (
                economy_choice
                if isinstance(economy_choice, (list, tuple))
                else [economy_choice]
            )
            if str(name or "").strip() in grouped
        ]
        if not wanted_economies:
            wanted_economies = list(grouped)
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

        scenario_value = economy_uploads[0].scenario
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
        esto_path = None

        run_root = Path(tempfile.mkdtemp(prefix="leap_balance_review_web_"))
        local_esto = _copy_input(esto_path, run_root / "uploads") if esto_path else None
        # Every economy gets its own export folder, which is the shape the
        # dashboard resolver expects; it picks the newest file per scenario.
        export_directories: dict[str, Path] = {}
        for name in wanted_economies:
            directory = run_root / "exports" / _safe_filename_token(name)
            directory.mkdir(parents=True, exist_ok=True)
            for upload in grouped[name]:
                _copy_input(upload.path, directory)
            export_directories[name] = directory
        local_export = _copy_input(workbook_upload.path, run_root / "uploads")
        context = _build_context(run_root)

        result = None
        workbook_paths: list[Path] = []
        diagnostics_directory: Path | None = None
        workbook_seconds: float | None = None
        if wants_workbook:
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

        dashboard_error = None
        dashboard_directory: Path | None = None
        dashboard_page_names: list[str] = []
        dashboard_seconds: float | None = None
        # One dashboard per economy: the renderer covers a single economy, so
        # several are rendered in turn and reported separately.
        dashboards: list[dict[str, object]] = []
        if wants_dashboard:
            dashboard_started = time.perf_counter()
            for name in wanted_economies:
                if progress is not None:
                    progress(
                        f"Rendering the {name} dashboard "
                        f"({len(dashboards) + 1} of {len(wanted_economies)})."
                    )
                outcome = developer_launcher.run_dashboard_from_export(
                    context=context,
                    economy=name,
                    export_dir=export_directories[name],
                    esto_table_path=local_esto,
                    min_year=dashboard_min_year_value,
                    max_year=dashboard_max_year_value,
                    run_label="web",
                )
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

        persistent_workbooks: list[Path] = []
        persistent_bundle = None
        if wants_workbook and result is not None:
            persistent_dir = Path(
                tempfile.mkdtemp(prefix="leap_balance_review_download_")
            )
            for workbook_path in workbook_paths:
                target = persistent_dir / workbook_path.name
                shutil.copy2(workbook_path, target)
                persistent_workbooks.append(target)
            persistent_bundle = persistent_dir / (
                f"{_safe_filename_token(economy_value)}_"
                f"{_safe_filename_token(scenario_value)}_{year_value}_diagnostics.zip"
            )
            _write_diagnostics_bundle(
                bundle_path=persistent_bundle,
                workbook_paths=workbook_paths,
                diagnostics_directory=diagnostics_directory,
                run_directory=result.run_directory,
                dashboard_directory=dashboard_directory,
                log_directory=run_root / "logs",
            )

        # Each rendered economy gets its own snapshot and its own link.
        snapshots = []
        for rendered in dashboards:
            if rendered.get("error"):
                continue
            name = str(rendered["economy"])
            snapshot_for = _dashboard_snapshot(
                rendered["directory"],
                economy=name,
                scenario=scenario_value,
                years=years_built or "",
            )
            url = _publish_dashboard_pages(
                snapshot_for["pages"],
                economy=name,
                scenario=scenario_value,
                years=years_built or "",
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
        runtime_seconds = {
            "workbook": round(workbook_seconds, 1) if workbook_seconds is not None else None,
            "dashboard": round(dashboard_seconds, 1) if dashboard_seconds is not None else None,
            "full_run": round(time.perf_counter() - run_started, 1),
        }
        # Only successful runs are recorded, so a failure cannot drag the
        # quoted duration around.
        year_count = len(requested_years) or None
        for group, measured in runtime_seconds.items():
            if measured is None:
                continue
            # "full run" means both halves; recording a workbook-only or
            # dashboard-only run against it would understate the real total.
            if group == "full_run" and not (wants_workbook and wants_dashboard):
                continue
            _save_runtime_sample(group, measured, years=year_count)
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
            "economies_uploaded": sorted(grouped),
            "scenario": scenario_value,
            "years": years_built,
            "dashboard_min_year": dashboard_min_year_value,
            "dashboard_max_year": dashboard_max_year_value,
            "esto_table_used": run_outputs.get("esto_table_used"),
            "esto_base_year": run_outputs.get("esto_base_year"),
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
                if dashboard_links
                else "partial"
                if any(not d.get("error") for d in dashboards)
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
            "dashboard_storage": "browser-local",
            "runtime_seconds": runtime_seconds,
        }
        return (
            json.dumps(summary, indent=2, default=str),
            _status_html(
                _run_status_line(
                    wants_workbook=wants_workbook,
                    wants_dashboard=wants_dashboard,
                    dashboard_ok=bool(dashboard_links),
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
        )
    except Exception as error:  # Gradio should show a plain-language failure.
        return (
            "",
            _status_html(f"Build failed: {error}"),
            [],
            None,
            RESULTS_EMPTY_HTML,
            _dropdown_update(_browser_dashboard_choices(browser_archives), None),
            browser_archives if isinstance(browser_archives, list) else [],
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
            gr.skip(), gr.skip(),
            gr.Timer(active=False),
            gr.Button("Run", interactive=True),
            "",
            gr.skip(),
        )
    if job.get("state") == "running":
        return (
            gr.skip(), _status_html(describe_job(job_id)), gr.skip(), gr.skip(),
            gr.skip(), gr.skip(), gr.skip(),
            gr.Timer(active=True),
            gr.Button("Running…", interactive=False),
            str(job_id),
            gr.skip(),
        )
    if job.get("state") == "failed":
        return (
            "", _status_html(str(job.get("message") or "Build failed.")), [], None,
            RESULTS_EMPTY_HTML,
            _dropdown_update(_browser_dashboard_choices(saved), None),
            saved,
            gr.Timer(active=False),
            gr.Button("Run", interactive=True),
            "",
            gr.skip(),
        )
    result = job.get("result") or ()
    if len(result) != 7:
        return (
            "", _status_html("The run finished without producing outputs."), [], None,
            RESULTS_EMPTY_HTML,
            _dropdown_update(_browser_dashboard_choices(saved), None),
            saved,
            gr.Timer(active=False),
            gr.Button("Run", interactive=True),
            "",
            gr.skip(),
        )
    return (
        *result,
        gr.Timer(active=False),
        gr.Button("Run", interactive=True),
        "",
        _last_run_record(result[0], result[1], result[2], result[3], result[6]),
    )


def _last_run_record(
    summary_json: str,
    status_html: str,
    workbooks: object,
    bundle: object,
    archives: object,
) -> dict[str, object]:
    """Return the small record kept in the browser to restore a finished run.

    Only text and paths are stored. The dashboards themselves are already
    held as compressed pages in the archive store, so links are rebuilt from
    those rather than from server paths that a restart would invalidate.
    """
    records = archives if isinstance(archives, list) else []
    return {
        "summary": str(summary_json or ""),
        "status": str(status_html or ""),
        "workbooks": [str(path) for path in (workbooks or [])],
        "bundle": str(bundle) if bundle else "",
        "archive_ids": [
            str(record.get("archive_id"))
            for record in records
            if isinstance(record, dict) and record.get("archive_id")
        ][:MAX_BROWSER_DASHBOARDS],
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
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
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()

    archives = browser_archives if isinstance(browser_archives, list) else []
    wanted = set(record.get("archive_ids") or [])
    links: list[dict[str, str]] = []
    for archive in archives:
        if not isinstance(archive, dict) or archive.get("archive_id") not in wanted:
            continue
        try:
            url = _publish_dashboard_pages(
                archive.get("pages") or {},
                economy=str(archive.get("economy", "")),
                scenario=str(archive.get("scenario", "")),
                years=archive.get("years", ""),
            )
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if url:
            links.append({"economy": str(archive.get("economy", "")), "url": url})

    surviving = [path for path in record.get("workbooks") or [] if Path(path).is_file()]
    bundle = record.get("bundle") or ""
    bundle_path = bundle if bundle and Path(bundle).is_file() else None

    expired = bool(record.get("workbooks")) and not surviving
    note = (
        f"<span class='result-hint'>Restored from {html.escape(str(record.get('finished_at', '')))}."
        + (
            " The downloads from that run have since been cleared from the server."
            if expired
            else ""
        )
        + "</span>"
    )
    if not (links or surviving or expired):
        # Nothing survived worth showing; leave the panel in its resting state
        # rather than captioning an empty result.
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()

    if links or surviving:
        links_html = _result_links_html(
            dashboard_url=None,
            dashboard_links=links,
            dashboard_error=None,
            wants_dashboard=bool(links),
            workbook_count=len(surviving),
        ).replace("</div>", note + "</div>", 1)
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
    )


def resume_run(job_id: object, browser_archives: object):
    """Reattach to a run still going when the page was reopened."""
    import gradio as gr

    job = _job_snapshot(str(job_id or ""))
    if job is None:
        return gr.Timer(active=False), gr.Button("Run", interactive=True)
    if job.get("state") == "running":
        return gr.Timer(active=True), gr.Button("Running…", interactive=False)
    # A run that finished while the page was away is collected on the next tick.
    return gr.Timer(active=True), gr.Button("Running…", interactive=False)


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
        "rel='noopener'>Open this saved dashboard <span aria-hidden='true'>↗</span></a>"
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
            gr.HTML(
                "<p class='choose-label'>What should this run build?</p>"
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
                        value="2022",
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
                        "LEAP with ESTO and the 9th Outlook, for one economy at a "
                        "time. Works with any number of exports. Adds a few "
                        "minutes to the run.</p>"
                    )
                    gr.HTML(
                        "<p class='card-note runtime-note'>"
                        + html.escape(
                            format_runtime_note(
                                hosted_runtime_profile,
                                process_group="dashboard",
                            )
                        )
                        + "</p>"
                    )
            run_button = gr.Button(
                "Run",
                variant="primary",
                elem_id="run-button",
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
        )
        # The id of a run in flight, kept in the browser so a reopened page can
        # find its way back to work that is still going.
        active_job = gr.BrowserState(
            default_value="",
            storage_key="leap_balance_review_active_job",
        )
        run_timer = gr.Timer(3, active=False)
        # What the last finished run produced, so returning to the page shows
        # it again instead of an empty results panel.
        last_run = gr.BrowserState(
            default_value={},
            storage_key="leap_balance_review_last_run",
        )
        with gr.Column(elem_id="results-card"):
            gr.HTML(
                """<div class="step-heading"><span class="step-kicker">02 · Results</span>
                  <strong>Your dashboard and workbooks</strong>
                  <p>The dashboard opens in a new tab; the workbooks download.</p>
                </div>"""
            )
            result_links = gr.HTML(value=RESULTS_EMPTY_HTML, elem_id="result-links")
            with gr.Row(elem_id="download-row"):
                output = gr.File(
                    label="Review workbook(s)",
                    file_count="multiple",
                )
                diagnostics_bundle = gr.File(label="Complete run archive")
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
                "Reopen an earlier run from this browser",
                open=False,
                elem_id="saved-reviews",
            ):
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
                        "Saved reviews stay in this browser and are never stored "
                        "on the server.</span></div>"
                    ),
                    elem_id="saved-link",
                )

        for _control in (year, want_workbook, want_dashboard, balance_export_workbook):
            _control.change(
                fn=update_runtime_notes,
                inputs=[year, want_workbook, want_dashboard, balance_export_workbook],
                outputs=[workbook_runtime_note, calculator_animation],
            )
        clear_export_button.click(
            fn=clear_uploaded_export,
            outputs=[
                balance_export_workbook,
                export_readout,
                economy_override,
                clear_export_button,
                add_export,
            ],
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
        ]
        run_button.click(
            fn=lock_run_button,
            outputs=run_button,
        ).then(
            fn=start_run,
            inputs=[
                want_workbook,
                want_dashboard,
                year,
                economy_override,
                balance_export_workbook,
                browser_archives,
            ],
            outputs=active_job,
        ).then(
            fn=lambda: gr.Timer(active=True),
            outputs=run_timer,
        )
        run_timer.tick(
            fn=poll_run,
            inputs=[active_job, browser_archives],
            outputs=[
                *run_outputs_list,
                run_timer,
                run_button,
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
            outputs=[summary, status, output, diagnostics_bundle, result_links],
        ).then(
            fn=resume_run,
            inputs=[active_job, browser_archives],
            outputs=[run_timer, run_button],
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
