# Deploying the Hugging Face Space

The Space is an output of this repository, not a separate project. Everything
it needs is here; nothing should be hand-edited in the Space checkout, because
the next deploy overwrites it.

## What each file is for

| File | Becomes | Why it exists |
|---|---|---|
| `space_app.py` | `app.py` at the Space root | Entry point. Registers a no-op ZeroGPU hook so the free ZeroGPU runtime will boot a CPU-only app, then calls `create_app()`. |
| `requirements.txt` | `requirements.txt` | Deliberately omits `gradio` and `spaces`: ZeroGPU supplies and pins those. Adds `tomli` for Python 3.10, which has no `tomllib`. |
| `README.md` | `README.md` | Carries the Hugging Face YAML frontmatter (`sdk`, `sdk_version`, `app_file`). The Space will not build without it. |
| `gitattributes` | `.gitattributes` | Marks the four large source tables as Git LFS. Paths point at `runtime/`, which is where this repository puts the prepared closure. |

## Deploy sequence

1. Commit the three source repositories. `refresh_runtime.py` refuses dirty
   sources, so provenance always points at real commits.
2. `python scripts/refresh_runtime.py` — pulls the runtime closure into
   `runtime/` and records the commit and branch of each source repository.
3. Run the app locally against that prepared runtime and confirm it works.
   This is the configuration the Space runs, and it is not the one you get
   while developing against the live sibling checkouts.
4. Copy `web_app/`, `runtime/`, and the four files above into the Space
   checkout, renaming as the table says.
5. Commit and push the Space.

## Before pushing anywhere public

The prepared runtime is roughly 390MB of source tables and mapping results. A
public Space publishes all of it. Confirm every copied file is safe to
redistribute, or keep the Space private.

The Space is a shared target: check `git fetch` and reconcile with
`origin/main` before pushing, rather than force-pushing over whatever landed
there since the last deploy.
