#%%
"""Hugging Face/local entry point for the balance-review web app."""

from __future__ import annotations

import os

# ZeroGPU requires at least one registered GPU function, even for this
# CPU-bound spreadsheet application. This hook is never called and therefore
# does not request GPU time; it only permits the free ZeroGPU runtime to boot.
try:
    import spaces

    @spaces.GPU(duration=1)
    def _zerogpu_startup_hook() -> None:
        return None
except ImportError:
    def _zerogpu_startup_hook() -> None:
        return None

from web_app.app import create_app


demo = create_app()


#%%
if __name__ == "__main__":
    demo.launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )

#%%
