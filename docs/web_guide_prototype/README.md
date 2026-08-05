# Static web guide prototype

This folder is a backend-free documentation prototype for the LEAP Balance
Review web app. It is intentionally isolated from `web_app/app.py` so that
front-end work and the running app on port `7861` are not affected.

## Preview

From this folder, serve the files with any static web server, for example:

```powershell
python -m http.server 7862
```

Then open `http://127.0.0.1:7862/`.

The **Guided tour** buttons demonstrate the intended in-app help overlay. The
overlay highlights the upload, year, optional ESTO, and run controls, and all
guide content is plain HTML/CSS/JavaScript with no backend calls.

## Source material

The narrative is adapted from `docs/docx/LEAP Review Tools - user guide.docx`.
The four images in `assets/` were extracted from that Word document:

- `workflow-overview.png` — the high-level export/review/dashboard flow;
- `leap-export-detail.png` — LEAP export detail selection;
- `review-workbook.png` — the review workbook;
- `dashboard-supply.png` — a dashboard page.

The current prototype intentionally reflects the web app's latest three-sheet
workbook. The older Word guide describes a fifth “Missing Combinations” sheet;
that text should not be copied into the production web guide unchanged.

## Later integration

When the documentation is approved, the content can be moved into the actual
Gradio app as a Help button and HTML/JavaScript overlay. Keep this prototype
read-only and separate until that integration is explicitly scheduled.
