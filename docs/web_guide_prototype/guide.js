const steps = [
  { target: 'target-upload', title: 'Welcome — start with one export', copy: 'The web app needs one LEAP Energy Balance export workbook. The economy and scenario are read from the file, so there is no second metadata form to complete.', image: 'assets/workflow-overview.png', alt: 'One LEAP export flows into the review workbook and dashboard.' },
  { target: 'target-upload', title: 'Prepare the LEAP export', copy: 'In LEAP, set Units to Petajoules and Detail to Level 2 or deeper. Export all years you want to inspect. A Level 1 export is too shallow to compare meaningfully.', image: 'assets/leap-export-detail.png', alt: 'LEAP energy balance detail selection.' },
  { target: 'target-upload', title: 'Add the workbook', copy: 'Drop the workbook into the highlighted area or choose it from your computer. No Python, Git, or LEAP connection is needed once the web app is open.', image: 'assets/workflow-overview.png', alt: 'The export is the starting point for the workflow.' },
  { target: 'target-year', title: 'Choose the review year(s)', copy: 'Enter one year such as 2022, or several separated by commas such as 2022, 2030, 2040. These years control which balance tables are checked and returned.' },
  { target: 'target-run', title: 'Build the review', copy: 'Start the run. The app calculates the diagnostics, creates the three-sheet workbook, renders the dashboard, and prepares downloadable outputs. The full run can take several minutes.' },
  { target: 'target-run', title: 'Read the workbook in order', copy: 'Use LEAP Values to orient yourself, LEAP – Source Error to find disagreements, and Full Expected Source to see the full ninth/ESTO balance table for that year.', image: 'assets/review-workbook.png', alt: 'The balance review workbook in Excel.' },
  { target: 'target-run', title: 'Explore and save the dashboard', copy: 'Use the sector pages to see the whole picture. Historical values compare with ESTO and projections compare with the 9th Outlook. Saved views stay in this browser; download the full archive when you need a durable copy.', image: 'assets/dashboard-supply.png', alt: 'Interactive supply dashboard with comparison series.' }
];
let current = 0;
steps[0] = { target: 'target-upload', title: 'Where this app fits in LEAP initialisation', copy: 'This guided tour covers the review stage of the wider LEAP initialisation process. The sequence is: import the baseline seed and run LEAP, do a quick LEAP review, use this balance review app, inspect the dashboard, then resolve any material issue and repeat. The web app supports steps 2b and 2c; LEAP remains the source of the baseline and the fixes.', image: 'assets/initialisation-review-workflow-landscape.png', alt: 'Major steps in the LEAP initialisation process, including the balance review app.' };
const $ = id => document.getElementById(id);
function positionPopover(target) {
  const box = target.getBoundingClientRect();
  const pop = $('tour-popover');
  const left = Math.min(Math.max(16, box.left), window.innerWidth - pop.offsetWidth - 16);
  const top = box.bottom + 18 + pop.offsetHeight < window.innerHeight ? box.bottom + 18 : Math.max(16, box.top - pop.offsetHeight - 18);
  pop.style.left = `${left}px`; pop.style.top = `${top}px`;
}
function showStep(index) {
  current = Math.max(0, Math.min(index, steps.length - 1));
  document.querySelectorAll('.tour-target').forEach(el => el.classList.remove('is-highlighted'));
  const step = steps[current]; const target = $(step.target);
  $('tour-step').textContent = current + 1; $('tour-title').textContent = step.title; $('tour-copy').textContent = step.copy; $('tour-total').textContent = steps.length;
  const image = $('tour-image'); image.hidden = !step.image; image.src = step.image || ''; image.alt = step.alt || '';
  $('tour-back').style.visibility = current ? 'visible' : 'hidden'; $('tour-next').innerHTML = current === steps.length - 1 ? 'Done <span>✓</span>' : 'Next <span>→</span>';
  target.classList.add('is-highlighted'); target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  setTimeout(() => positionPopover(target), 220);
}
function closeTour() { $('tour-backdrop').hidden = true; $('tour-popover').hidden = true; document.querySelectorAll('.tour-target').forEach(el => el.classList.remove('is-highlighted')); }
function openTour() { $('tour-backdrop').hidden = false; $('tour-popover').hidden = false; showStep(0); }
document.addEventListener('DOMContentLoaded', () => {
  $('start-tour').addEventListener('click', openTour); $('hero-tour').addEventListener('click', openTour); $('close-tour').addEventListener('click', closeTour);
  $('tour-next').addEventListener('click', () => current === steps.length - 1 ? closeTour() : showStep(current + 1)); $('tour-back').addEventListener('click', () => showStep(current - 1)); $('tour-backdrop').addEventListener('click', closeTour);
  window.addEventListener('resize', () => { if (!$('tour-popover').hidden) positionPopover($(steps[current].target)); });
});
