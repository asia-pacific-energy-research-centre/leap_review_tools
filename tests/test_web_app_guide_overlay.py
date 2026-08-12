"""Focused checks for the web app guided-tour content."""

import re

from web_app.guide_overlay import GUIDE_HTML, GUIDE_JS


def _guide_copy_blocks() -> list[str]:
    """Return the user-facing copy strings from the JavaScript step list."""
    return re.findall(r"copy: '([^']*)'", GUIDE_JS)


def test_guide_starts_with_text_diagram_and_four_step_cards() -> None:
    assert 'of <span id="leap-guide-total">9</span>' in GUIDE_HTML
    assert "title: 'What this app is for'" in GUIDE_JS
    assert "title: 'Where this app fits in LEAP initialisation'" in GUIDE_JS
    assert "title: 'Review the baseline seed in four steps'" in GUIDE_JS

    first_step = GUIDE_JS.split("const steps = [", 1)[1].split("},", 1)[0]
    assert "image: ''" in first_step
    assert "Use this diagram to decide how the app fits into your work." in GUIDE_JS
    assert "1. Import the baseline seed into LEAP" in GUIDE_JS
    assert "4. Correct material issues in LEAP" in GUIDE_JS


def test_guide_copy_uses_plain_punctuation() -> None:
    copy_blocks = _guide_copy_blocks()
    assert len(copy_blocks) == 9
    assert all("—" not in copy for copy in copy_blocks)
    assert all(";" not in copy for copy in copy_blocks)


def test_export_card_explains_why_the_export_is_needed() -> None:
    assert "title: 'Create the export used by this app'" in GUIDE_JS
    assert "This is the input the app reads to create the dashboard" in GUIDE_JS


def test_export_card_keeps_fuel_guidance_in_a_two_image_carousel() -> None:
    assert "Under Columns, choose Fuels." in GUIDE_JS
    assert "Do not change the fuel groupings in your model or select Fuel Groupings here" in GUIDE_JS
    assert "Open Columns and select Fuels instead of Fuel Groupings." in GUIDE_JS
    assert "currentImages.length > 1" in GUIDE_JS
    assert "Click the image to continue" in GUIDE_HTML
