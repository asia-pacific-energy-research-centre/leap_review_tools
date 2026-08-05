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
#leap-guide-launch {
  position: fixed; right: 1.15rem; bottom: 1.15rem; z-index: 40;
  border: 0; border-radius: 999px; padding: .7rem 1rem;
  background: #e7672a; color: #fff; font-weight: 750;
  box-shadow: 0 8px 24px #17345240; cursor: pointer;
}
#leap-guide-launch span { display: inline-grid; place-items: center; width: 1.25rem;
  height: 1.25rem; margin-right: .35rem; border: 1px solid #fff; border-radius: 50%; }
#leap-guide-backdrop { position: fixed; inset: 0; z-index: 50; background: #102a4666; }
#leap-guide-popover { position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%);
  z-index: 60; width: min(720px, calc(100vw - 2rem)); max-height: calc(100vh - 2rem);
  overflow: auto; padding: 1.45rem; border: 1px solid #cbd8e7; border-radius: 9px;
  background: #fff; color: #173452; box-shadow: 0 22px 70px #0d254c55; }
#leap-guide-popover.guide-has-image { width: min(1450px, calc(100vw - 2rem)); }
#leap-guide-popover.guide-image-tall { width: min(760px, calc(100vw - 2rem)); }
#leap-guide-popover[hidden], #leap-guide-backdrop[hidden] { display: none !important; }
.leap-guide-progress { color: #65788d; font-size: .72rem; letter-spacing: .08em; }
#leap-guide-close { float: right; border: 0; background: transparent; color: #65788d;
  font-size: 1.5rem; cursor: pointer; }
.leap-guide-kicker { margin-top: 1.2rem; color: #e7672a; font-size: .65rem;
  font-weight: 800; letter-spacing: .14em; }
#leap-guide-title { margin: .35rem 0 .55rem; font-size: 1.55rem; line-height: 1.15; }
#leap-guide-copy { margin: 0 0 1rem; color: #65788d; font-size: .95rem; line-height: 1.6; }
#leap-guide-image { display: block; width: 100%; max-height: min(70vh, 760px); object-fit: contain;
  object-position: left center; padding: .45rem; border: 1px solid #cbd8e7;
  border-radius: 5px; background: #f3f6fa; }
#leap-guide-image[hidden] { display: none; }
.leap-guide-actions { display: flex; align-items: center; justify-content: space-between;
  margin-top: 1.1rem; }
.leap-guide-actions button { border: 0; border-radius: 4px; padding: .65rem .9rem;
  background: transparent; color: #173452; cursor: pointer; }
#leap-guide-next { background: #e7672a; color: #fff; font-weight: 700; }
.leap-guide-highlight { position: relative !important; z-index: 55 !important;
  box-shadow: 0 0 0 5px #ff9868, 0 0 0 9px #fff !important; }
"""


GUIDE_JS = """
() => {
  const steps = [
    { target: '#balance-upload', title: 'Where this app fits in LEAP initialisation', copy: 'This guided tour covers the review stage of the wider LEAP initialisation process. The major sequence is: import the baseline seed and run LEAP, do a quick LEAP review, use this balance review app, inspect the dashboard, then resolve any material issue and repeat. The web app supports steps 2b and 2c; LEAP remains the source of the baseline and the fixes.', image: '__INITIALISATION_IMAGE__' },
    { target: '#balance-upload', title: 'Prepare the right export', copy: 'In LEAP, use Petajoules and Detail Level 2 or deeper. A Level 1 export is too shallow to compare meaningfully.', image: '__EXPORT_IMAGE__' },
    { target: '#year-input', title: 'Choose the review year(s)', copy: 'Enter one year such as 2022, or several comma-separated years such as 2022, 2030, 2040. These control the workbook review.', image: '' },
    { target: '#outputs-wanted', title: 'Choose what to build', copy: 'Keep workbook and dashboard selected when you want both outputs. The dashboard is the visual overview; the workbook is the detailed worklist.', image: '' },
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
