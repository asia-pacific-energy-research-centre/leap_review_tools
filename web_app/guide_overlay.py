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
    of <span id="leap-guide-total">9</span>
    <button id="leap-guide-close" type="button" aria-label="Close guide">×</button>
  </div>
  <div class="leap-guide-kicker">LEAP BALANCE REVIEW GUIDE</div>
  <h2 id="leap-guide-title"></h2>
  <p id="leap-guide-copy"></p>
  <div id="leap-guide-image-frame" hidden>
    <img id="leap-guide-image" alt="">
    <div id="leap-guide-image-controls" hidden>
      <button id="leap-guide-image-back" type="button" aria-label="Previous guide image">&larr;</button>
      <span><span id="leap-guide-image-step">1 of 1</span> &middot; Click the image to continue</span>
      <button id="leap-guide-image-next" type="button" aria-label="Next guide image">&rarr;</button>
    </div>
  </div>
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
  flex: 0 1 auto; max-height: 38vh; overflow: auto; white-space: pre-line; }
#leap-guide-image-frame { display: flex; flex: 1 1 auto; min-height: 90px;
  flex-direction: column; overflow: hidden; }
#leap-guide-image-frame[hidden] { display: none; }
#leap-guide-image { display: block; width: 100%; flex: 1 1 auto; min-height: 0;
  max-height: 72vh; object-fit: contain;
  object-position: center; padding: .4rem; border: 1px solid #cbd8e7;
  border-radius: 5px; background: #f3f6fa; }
#leap-guide-image.is-clickable { cursor: pointer; }
#leap-guide-image-controls { display: flex; align-items: center; justify-content: center;
  gap: .65rem; margin-top: .3rem; color: #65788d; font-size: .72rem; }
#leap-guide-image-controls[hidden] { display: none; }
#leap-guide-image-controls button { border: 1px solid #cbd8e7; border-radius: 999px;
  width: 1.65rem; height: 1.65rem; padding: 0; background: #fff; color: #173452;
  cursor: pointer; }
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
    { target: '#app-hero', title: 'What this app is for', copy: 'Two jobs, one tool. Any LEAP model can be checked against ESTO history and the 9th Outlook at any point in modelling by using the dashboard. A model being initialised from the baseline seed can also have its energy balance reviewed against the source data it should match. Use the dashboard to review trends across years. Use the workbook to list every disagreeing cell in one year. Upload several exports for dashboards across economies and scenarios. Upload one export when you also want the workbook.', image: '' },
    { target: '#balance-upload', title: 'Where this app fits in LEAP initialisation', copy: 'Use this diagram to decide how the app fits into your work. The app supports review steps 2b and 2c. It reports differences but does not change the model. Make corrections in LEAP, run LEAP again, then upload a new export for another review.', image: '__INITIALISATION_IMAGE__' },
    { target: '#balance-upload', title: 'Review the baseline seed in four steps', copy: 'This review works best in this order:\\n1. Import the baseline seed into LEAP, run the model, and complete a quick check in LEAP.\\n2. Upload one export and build the workbook first. Review one important year in detail. Most baseline seed problems found in one year will affect other years too, so fix them before moving on.\\n3. Inspect the dashboard for unexpected trends across years. Return to the workbook when you need the exact differences within one year.\\n4. Correct material issues in LEAP, run the model again, create a new export, and repeat the review.', image: '' },
    { target: '#balance-upload', title: 'Create the export used by this app', copy: 'Create a LEAP Energy Balance export in Petajoules at Detail Level 2 or deeper. Under Columns, choose Fuels. Do not change the fuel groupings in your model or select Fuel Groupings here because the app expects the individual fuels from the model. This is the input the app reads to create the dashboard and, when you upload only one export, the review workbook. A Level 1 export does not contain enough detail. Upload several exports when you want dashboards for several economies or scenarios.', images: [{ src: '__EXPORT_IMAGE__', alt: 'Set the LEAP Energy Balance export to Petajoules and Detail Level 2 or deeper.' }, { src: '__EXPORT_FUELS_IMAGE__', alt: 'Open Columns and select Fuels instead of Fuel Groupings.' }] },
    { target: '#year-input', title: 'Choose the review year(s)', copy: 'Enter one year such as 2022, or several comma-separated years such as 2022, 2030, 2040. These control the workbook review.', image: '' },
    { target: '#outputs-wanted', title: 'Choose what to build', copy: 'Keep workbook and dashboard selected when you want both outputs. The dashboard is the visual overview. The workbook is the detailed worklist. With several exports uploaded, the workbook fades out because it covers a single economy and scenario. Choose which economy and scenario to render just above.', image: '' },
    { target: '#run-button', title: 'Start the review', copy: 'Run the workflow. Diagnostics, workbooks, dashboard pages, and archives appear in Results when processing finishes. The run can take several minutes.', image: '' },
    { target: '#workbook-note, #results-card', title: 'Read the workbook in order', copy: 'Use LEAP Values to orient yourself, LEAP – Source Error to find disagreements, and Full Expected Source to see the full ninth/ESTO balance table for that year.', image: '__WORKBOOK_IMAGE__' },
    { target: '#saved-reviews, #results-card', title: 'Explore and save the dashboard', copy: 'Open the dashboard link to see the whole picture. Saved reviews stay in this browser. Download the complete archive when you need a durable copy.', image: '__DASHBOARD_IMAGE__' }
  ];
  const installGuide = () => {
    const $ = (selector) => document.querySelector(selector);
    const launch = $('#leap-guide-launch'); const popover = $('#leap-guide-popover');
    const backdrop = $('#leap-guide-backdrop'); const imageFrame = $('#leap-guide-image-frame');
    const image = $('#leap-guide-image'); const imageControls = $('#leap-guide-image-controls');
    if (!launch || !popover || !backdrop) return false;
    if (launch.dataset.guideBound === '1') return true;
    launch.dataset.guideBound = '1';
    let current = 0; let currentImage = 0; let currentImages = [];
    const updateImageSizing = () => {
      popover.classList.remove('guide-has-image', 'guide-image-tall');
      if (imageFrame.hidden || !image.naturalWidth || !image.naturalHeight) return;
      popover.classList.add('guide-has-image');
      if (image.naturalHeight > image.naturalWidth) popover.classList.add('guide-image-tall');
    };
    image.addEventListener('load', updateImageSizing);
    const showImage = (index) => {
      if (!currentImages.length) {
        imageFrame.hidden = true; imageControls.hidden = true; image.src = ''; image.alt = '';
        image.classList.remove('is-clickable'); image.removeAttribute('role'); image.removeAttribute('tabindex');
        updateImageSizing(); return;
      }
      currentImage = (index + currentImages.length) % currentImages.length;
      const item = currentImages[currentImage];
      imageFrame.hidden = false; image.src = item.src; image.alt = item.alt;
      imageControls.hidden = currentImages.length < 2;
      $('#leap-guide-image-step').textContent = `${currentImage + 1} of ${currentImages.length}`;
      image.classList.toggle('is-clickable', currentImages.length > 1);
      if (currentImages.length > 1) { image.setAttribute('role', 'button'); image.setAttribute('tabindex', '0'); }
      else { image.removeAttribute('role'); image.removeAttribute('tabindex'); }
      updateImageSizing();
    };
    const resolveTarget = (selector) => selector.split(',').map((part) => $(part.trim())).find(Boolean) || $('#upload-card');
  const show = (index) => {
    current = Math.max(0, Math.min(index, steps.length - 1));
    document.querySelectorAll('.leap-guide-highlight').forEach((node) => node.classList.remove('leap-guide-highlight'));
    const step = steps[current]; const target = resolveTarget(step.target);
    $('#leap-guide-step').textContent = String(current + 1); $('#leap-guide-total').textContent = String(steps.length);
    $('#leap-guide-title').textContent = step.title; $('#leap-guide-copy').textContent = step.copy;
    currentImages = step.images || (step.image ? [{ src: step.image, alt: step.title }] : []);
    showImage(0);
    $('#leap-guide-back').style.visibility = current ? 'visible' : 'hidden';
    $('#leap-guide-next').innerHTML = current === steps.length - 1 ? 'Done <span>✓</span>' : 'Next <span>→</span>';
    target.classList.add('leap-guide-highlight'); target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  const close = () => { popover.hidden = true; backdrop.hidden = true; document.querySelectorAll('.leap-guide-highlight').forEach((node) => node.classList.remove('leap-guide-highlight')); };
    launch.addEventListener('click', () => { popover.hidden = false; backdrop.hidden = false; show(0); });
    $('#leap-guide-close').addEventListener('click', close); backdrop.addEventListener('click', close);
    image.addEventListener('click', () => { if (currentImages.length > 1) showImage(currentImage + 1); });
    image.addEventListener('keydown', (event) => {
      if (currentImages.length > 1 && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault(); showImage(currentImage + 1);
      }
    });
    $('#leap-guide-image-back').addEventListener('click', () => showImage(currentImage - 1));
    $('#leap-guide-image-next').addEventListener('click', () => showImage(currentImage + 1));
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
GUIDE_JS = GUIDE_JS.replace("__EXPORT_FUELS_IMAGE__", _image_data("leap-export-fuels.png"))
GUIDE_JS = GUIDE_JS.replace("__WORKBOOK_IMAGE__", _image_data("review-workbook.png"))
GUIDE_JS = GUIDE_JS.replace("__DASHBOARD_IMAGE__", _image_data("dashboard-supply.png"))
# Gradio's Blocks ``js`` option expects one function. The main app already
# supplies that wrapper, so expose only this tour's function body for app.py
# to append inside it.
GUIDE_JS = GUIDE_JS.strip()
GUIDE_JS = GUIDE_JS.removeprefix("() => {\n").removesuffix("\n}")
