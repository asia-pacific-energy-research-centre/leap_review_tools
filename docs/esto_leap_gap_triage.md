# Repeatable ESTO–LEAP gap triage

`scripts/esto_leap_gap_triage.py` turns one or more generated dashboards into a
stable investigation queue. It is designed for the repeated cycle: generate,
review, classify, fix, rerun, and move to the next active case.

## Comparison contract

- Compare the plotted `ESTO Historical` and `LEAP Target` traces at the selected
  base year (2022 by default).
- Include a graph when the absolute difference is at least 20 PJ, or when it is
  at least 5 PJ and at least 25% of the absolute ESTO value.
- Keep missing-source coverage cases separate from numerical differences.
- Deduplicate aggregate graphs only when the economy, page, and plotted values
  are identical. Detailed line charts are never value-deduplicated.
- Exclude known dummy-run cases by the economy-independent graph identity
  `page_key + chart_key`. This excludes the exact known graph in every economy
  without hiding other graphs from the same broad sector.

## Stable case registry

Every active case receives `ELG-<economy>-<hash>`. The ID is derived from the
economy and exact graph identity, so it survives reranking and reruns. Pass the
previous `case_registry.csv` back with `--previous-registry` to carry forward:

- status;
- owner;
- issue type and root cause;
- fix reference; and
- reviewer notes.

Cases that disappear are retained with `current_run_state=not_reproduced`.
Priorities are impact bands: P0 is at least 100 PJ, P1 is at least 20 PJ, and P2
is any other case that satisfies the comparison contract. Priority is triage
order, not proof that the graph is wrong.

## Run from current LEAP exports

The `--export` form renders dashboards using the same backend as the specified
local web app and then builds the queue. Each argument must select one exact
workbook so stale exports in the same economy folder cannot be mixed in.

```powershell
C:\Users\Work\miniconda3\python.exe scripts\esto_leap_gap_triage.py `
  --web-app-root C:\Users\Work\github\leap_review_web_app `
  --export "01_AUS=C:\path\to\current_AUS_Target.xlsx" `
  --export "20_USA=C:\path\to\current_USA_Target.xlsx" `
  --baseline-cases C:\path\to\dummy_review\selected_2022_differences.csv `
  --plotly-bundle C:\path\to\dummy_review\assets\plotly.min.js `
  --previous-registry C:\path\to\prior_run\case_registry.csv `
  --output-directory C:\path\to\new_triage_run
```

For a faster re-triage when dashboards already exist, replace `--export` and
`--web-app-root` with one or more `--dashboard ECONOMY=PATH` arguments. `PATH`
is the economy dashboard directory containing `chart_bundles` and
`supporting_files/chart_manifest.csv`.

## Review loop

1. Open `index.html` and review cases in P0, P1, P2 order.
2. Record the decision in `case_registry.csv`. Recommended status values are
   `open`, `investigating`, `confirmed_issue`, `accepted_difference`, and
   `fixed_pending_rerun`.
3. Put the diagnosis in `issue_type`, `root_cause`, and `reviewer_notes`; record
   the commit, pull request, or mapping change in `fix_reference`.
4. Rerun with the edited registry passed as `--previous-registry`.
5. Confirm fixed cases move to `not_reproduced`; continue with the next active
   case. Review `coverage_gaps.csv` separately because absence is not a numeric
   disagreement.

The output also retains `excluded_baseline_cases.csv` and
`all_large_candidates.csv`, making both the exclusion decision and threshold
calculation auditable.
