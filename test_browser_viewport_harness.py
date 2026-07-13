import ast
import subprocess
import sys
from pathlib import Path

import browser_viewport_harness as harness


def test_viewport_parser_and_invalid_values():
    assert harness.parse_viewport("1366x768") == (1366, 768)
    try: harness.parse_viewport("bad")
    except ValueError as exc: assert str(exc) == "invalid_viewport"
    else: assert False


def test_multiline_metrics_source_and_expected_schema():
    source = Path(harness.__file__).read_text(encoding="utf-8")
    ast.parse(source)
    assert "\n() => {" in harness.LAYOUT_METRICS_SCRIPT
    assert harness.METRIC_KEYS.issubset({"viewport_width", "viewport_height", "document_scroll_width", "document_scroll_height", "horizontal_overflow", "main_content_bbox", "clipped_primary_control_count", "offscreen_primary_control_count"})
    assert "python -c" not in source and "shell=True" not in source


def test_smoke_mode_does_not_start_generated_project():
    result = subprocess.run([sys.executable, harness.__file__, "--smoke", "--viewport", "1366x768"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
