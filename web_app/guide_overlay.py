"""Screenshot-led, front-end-only guided tour for the Gradio web app."""

from __future__ import annotations

import base64
from pathlib import Path


ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "guide"


def _image_data(filename: str) -> str:
    path = ASSET_ROOT / filename
    if not path.is_file():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    mime_type = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
    return f"data:{mime_type};base64,{encoded}"


GUIDE_HTML = f"""
<button id="leap-guide-launch" type="button" aria-haspopup="dialog">
  <span aria-hidden="true">?</span> Guide
</button>
<div id="leap-guide-backdrop" hidden></div>
<aside id="leap-guide-popover" hidden role="dialog" aria-modal="true"
       aria-labelledby="leap-guide-title">
  <div class="leap-guide-progress"><span id="leap-guide-step">1</span>
    of <span id="leap-guide-total">8</span>
    <button id="leap-guide-close" type="button" aria-label="Close guide">×</button>
  </div>
  <div class="leap-guide-kicker">LEAP BALANCE REVIEW GUIDE</div>
  <h2 id="leap-guide-title"></h2>
  <p id="leap-guide-copy"></p>
  <img id="leap-guide-image" alt="" hidden>
  <div class="leap-guide-actions">
    <button id="leap-guide-back" type="button">Back</button>
    <button id="leap-guide-next" type="button">Next <span>→</span></button>
  </div>
</aside>
"""


GUIDE_CSS = """
/* Every part of the overlay is fixed-position, so the block Gradio wraps them
   in should take no room in the page flow. Left alone it reserved 20px, which
   with the surrounding gaps opened a 36px hole under the banner. */
#guide-overlay {
  height: 0 !important;
  min-height: 0 !important;
  padding: 0 !important;
  overflow: visible !important;
}
#leap-guide-launch {
  position: fixed; right: 1.15rem; bottom: 1.15rem; z-index: 40;
  border: 0; border-radius: 999px; padding: .7rem 1rem;
  background: #e7672a; color: #fff; font-weight: 750;
  box-shadow: 0 8px 24px #17345240; cursor: pointer;
}
#leap-guide-launch span { display: inline-grid; place-items: center; width: 1.25rem;
  height: 1.25rem; margin-right: .35rem; border: 1px solid #fff; border-radius: 50%; }
#leap-guide-backdrop { position: fixed; inset: 0; z-index: 50; background: #102a4666; }
/* A column so the picture can take whatever height the words leave, rather
   than both claiming their own maximum and overflowing the screen. */
#leap-guide-popover { position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%);
  z-index: 60; width: min(720px, calc(100vw - 2rem)); max-height: calc(100vh - 1rem);
  display: flex; flex-direction: column;
  overflow: hidden; padding: 0.7rem 1.15rem 0.75rem; border: 1px solid #cbd8e7; border-radius: 9px;
  background: #fff; color: #173452; box-shadow: 0 22px 70px #0d254c55; }
#leap-guide-popover.guide-has-image { width: min(1760px, calc(100vw - 1.5rem)); }
#leap-guide-popover.guide-image-tall { width: min(760px, calc(100vw - 2rem)); }
#leap-guide-popover[hidden], #leap-guide-backdrop[hidden] { display: none !important; }
.leap-guide-progress { color: #65788d; font-size: .72rem; letter-spacing: .08em; flex: 0 0 auto; }
#leap-guide-close { float: right; border: 0; background: transparent; color: #65788d;
  font-size: 1.3rem; line-height: 1; cursor: pointer; }
.leap-guide-kicker { margin-top: .15rem; color: #e7672a; font-size: .65rem;
  font-weight: 800; letter-spacing: .14em; flex: 0 0 auto; }
#leap-guide-title { margin: .1rem 0 .3rem; font-size: 1.22rem; line-height: 1.15; flex: 0 0 auto; }
#leap-guide-copy { margin: 0 0 .5rem; color: #65788d; font-size: .88rem; line-height: 1.45;
  flex: 0 1 auto; max-height: 38vh; overflow: auto; }
#leap-guide-image { display: block; width: 100%; flex: 1 1 auto; min-height: 90px;
  max-height: 72vh; object-fit: contain;
  object-position: center; padding: .4rem; border: 1px solid #cbd8e7;
  border-radius: 5px; background: #f3f6fa; }
#leap-guide-image[hidden] { display: none; }
.leap-guide-actions { display: flex; align-items: center; justify-content: space-between;
  margin-top: .5rem; flex: 0 0 auto; }
.leap-guide-actions button { border: 0; border-radius: 4px; padding: .45rem .85rem;
  background: transparent; color: #173452; cursor: pointer; }
#leap-guide-next { background: #e7672a; color: #fff; font-weight: 700; }
.leap-guide-highlight { position: relative !important; z-index: 55 !important;
  box-shadow: 0 0 0 5px #ff9868, 0 0 0 9px #fff !important; }
"""


GUIDE_JS = """
() => {
  const steps = [
    { target: '#app-hero', title: 'What this app is for', copy: 'Two jobs, one tool. Any LEAP model can be checked against ESTO history and the 9th Outlook at any point in modelling, using the dashboard. And a model being initialised from the baseline seed can have its energy balance reviewed against the source data it should match — through the same dashboard, or through the workbook that lists every disagreeing cell. Upload one export or several: several give you the dashboard across economies and scenarios, one gives you the workbook as well.', image: '__PURPOSE_IMAGE__' },
    { target: '#balance-upload', title: 'Where this app fits in LEAP initialisation', copy: 'This guided tour covers the review stage of the wider LEAP initialisation process. The major sequence is: import the baseline seed and run LEAP, do a quick LEAP review, use this balance review app, inspect the dashboard, then resolve any material issue and repeat. The web app supports steps 2b and 2c; LEAP remains the source of the baseline and the fixes.', image: '__INITIALISATION_IMAGE__' },
    { target: '#balance-upload', title: 'Prepare the right export', copy: 'In LEAP, use Petajoules and Detail Level 2 or deeper; a Level 1 export is too shallow to compare meaningfully. Add as many exports as you like here — each is read for its own economy, scenario and years.', image: '__EXPORT_IMAGE__' },
    { target: '#year-input', title: 'Choose the review year(s)', copy: 'Enter one year such as 2022, or several comma-separated years such as 2022, 2030, 2040. These control the workbook review.', image: '' },
    { target: '#outputs-wanted', title: 'Choose what to build', copy: 'Keep workbook and dashboard selected when you want both outputs. The dashboard is the visual overview; the workbook is the detailed worklist. With several exports uploaded the workbook fades out, because it covers a single economy and scenario — pick which economy and scenario to render just above.', image: '' },
    { target: '#run-button', title: 'Start the review', copy: 'Run the workflow. Diagnostics, workbooks, dashboard pages, and archives appear in Results when processing finishes. The run can take several minutes.', image: '' },
    { target: '#workbook-note, #results-card', title: 'Read the workbook in order', copy: 'Use LEAP Values to orient yourself, LEAP – Source Error to find disagreements, and Full Expected Source to see the full ninth/ESTO balance table for that year.', image: '__WORKBOOK_IMAGE__' },
    { target: '#saved-reviews, #results-card', title: 'Explore and save the dashboard', copy: 'Open the dashboard link to see the whole picture. Saved reviews stay in this browser; download the complete archive when you need a durable copy.', image: '__DASHBOARD_IMAGE__' }
  ];
  const installGuide = () => {
    const $ = (selector) => document.querySelector(selector);
    const launch = $('#leap-guide-launch'); const popover = $('#leap-guide-popover');
    const backdrop = $('#leap-guide-backdrop'); const image = $('#leap-guide-image');
    if (!launch || !popover || !backdrop) return false;
    if (launch.dataset.guideBound === '1') return true;
    launch.dataset.guideBound = '1';
    let current = 0;
    const updateImageSizing = () => {
      popover.classList.remove('guide-has-image', 'guide-image-tall');
      if (image.hidden || !image.naturalWidth || !image.naturalHeight) return;
      popover.classList.add('guide-has-image');
      if (image.naturalHeight > image.naturalWidth) popover.classList.add('guide-image-tall');
    };
    image.addEventListener('load', updateImageSizing);
    const resolveTarget = (selector) => selector.split(',').map((part) => $(part.trim())).find(Boolean) || $('#upload-card');
  const show = (index) => {
    current = Math.max(0, Math.min(index, steps.length - 1));
    document.querySelectorAll('.leap-guide-highlight').forEach((node) => node.classList.remove('leap-guide-highlight'));
    const step = steps[current]; const target = resolveTarget(step.target);
    $('#leap-guide-step').textContent = String(current + 1); $('#leap-guide-total').textContent = String(steps.length);
    $('#leap-guide-title').textContent = step.title; $('#leap-guide-copy').textContent = step.copy;
    image.hidden = !step.image; image.src = step.image || ''; image.alt = step.title;
    updateImageSizing();
    $('#leap-guide-back').style.visibility = current ? 'visible' : 'hidden';
    $('#leap-guide-next').innerHTML = current === steps.length - 1 ? 'Done <span>✓</span>' : 'Next <span>→</span>';
    target.classList.add('leap-guide-highlight'); target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  const close = () => { popover.hidden = true; backdrop.hidden = true; document.querySelectorAll('.leap-guide-highlight').forEach((node) => node.classList.remove('leap-guide-highlight')); };
    launch.addEventListener('click', () => { popover.hidden = false; backdrop.hidden = false; show(0); });
    $('#leap-guide-close').addEventListener('click', close); backdrop.addEventListener('click', close);
    $('#leap-guide-next').addEventListener('click', () => current === steps.length - 1 ? close() : show(current + 1));
    $('#leap-guide-back').addEventListener('click', () => show(current - 1));
    return true;
  };
  if (!installGuide()) {
    const observer = new MutationObserver(() => { if (installGuide()) observer.disconnect(); });
    observer.observe(document.body, { childList: true, subtree: true });
  }
}
"""


GUIDE_JS = GUIDE_JS.replace("__PURPOSE_IMAGE__", _image_data("what-this-app-is-for.svg"))
GUIDE_JS = GUIDE_JS.replace("__WORKFLOW_IMAGE__", _image_data("workflow-overview.png"))
GUIDE_JS = GUIDE_JS.replace("__INITIALISATION_IMAGE__", _image_data("initialisation-review-workflow-landscape.png"))
GUIDE_JS = GUIDE_JS.replace("__EXPORT_IMAGE__", _image_data("leap-export-detail.png"))
GUIDE_JS = GUIDE_JS.replace("__WORKBOOK_IMAGE__", _image_data("review-workbook.png"))
GUIDE_JS = GUIDE_JS.replace("__DASHBOARD_IMAGE__", _image_data("dashboard-supply.png"))
# Gradio's Blocks ``js`` option expects one function. The main app already
# supplies that wrapper, so expose only this tour's function body for app.py
# to append inside it.
GUIDE_JS = GUIDE_JS.strip()
GUIDE_JS = GUIDE_JS.removeprefix("() => {\n").removesuffix("\n}")
