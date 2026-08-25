---
title: LEAP Balance Review
emoji: 📊
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 5.44.1
app_file: app.py
short_description: Review LEAP balance exports and compare dashboards.
pinned: true
---

# LEAP Balance Review

This Gradio Space turns a LEAP Energy Balance export workbook into an
internally diagnosed balance-review workbook and an interactive Common ESTO
comparison dashboard.

Inputs are processed during the current run. The app uses the configured ESTO
and 9th-edition source tables automatically; users provide the LEAP balance
export and review year(s).

Dashboard snapshots are kept in the visitor's browser rather than shared
server storage. Download the full ZIP when a durable copy of a dashboard is
needed.

This Space uses a self-contained `hf_bundle/` snapshot prepared from the
`leap_initialisation`, `leap_mappings`, and `leap_dashboard` repositories.
See `web_app/README.md` and the source repository's HF Space developer guide
for the refresh and release procedure. The shared portable-release backend
still supports an ESTO override for desktop workflows, but this web interface
does not expose that option.
