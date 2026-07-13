"""Windows-safe, multiline browser viewport primitives."""
from __future__ import annotations

import argparse
from pathlib import Path

LAYOUT_METRICS_SCRIPT = """
() => {
  const root = document.documentElement;
  const body = document.body;
  const main = document.querySelector('main, [role="main"]');
  const controls = Array.from(document.querySelectorAll('button, a, input, select, textarea'));
  const box = main ? main.getBoundingClientRect() : null;
  const offscreen = controls.filter(el => {
    const rect = el.getBoundingClientRect();
    return rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth;
  }).length;
  return {
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
    document_scroll_width: Math.max(root.scrollWidth, body ? body.scrollWidth : 0),
    document_scroll_height: Math.max(root.scrollHeight, body ? body.scrollHeight : 0),
    horizontal_overflow: Math.max(0, Math.max(root.scrollWidth, body ? body.scrollWidth : 0) - window.innerWidth),
    main_content_bbox: box ? {x: box.x, y: box.y, width: box.width, height: box.height} : null,
    clipped_primary_control_count: 0,
    offscreen_primary_control_count: offscreen
  };
}
"""

METRIC_KEYS = {"viewport_width", "viewport_height", "document_scroll_width", "document_scroll_height", "horizontal_overflow", "main_content_bbox", "clipped_primary_control_count", "offscreen_primary_control_count"}


def parse_viewport(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x", 1))
    except (TypeError, ValueError):
        raise ValueError("invalid_viewport") from None
    if width <= 0 or height <= 0:
        raise ValueError("invalid_viewport")
    return width, height


def smoke(viewport: tuple[int, int]) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("browser_import_error") from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
            page = context.new_page()
            page.set_content("<nav>Navigation</nav><main><button>Primary</button></main>")
            metrics = page.evaluate(LAYOUT_METRICS_SCRIPT)
            if not isinstance(metrics, dict) or not METRIC_KEYS.issubset(metrics):
                raise RuntimeError("layout_metrics_schema_error")
            context.close(); browser.close()
            return metrics
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("browser_launch_error") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--viewport", default="1366x768")
    args = parser.parse_args()
    viewport = parse_viewport(args.viewport)
    if args.smoke:
        smoke(viewport)


if __name__ == "__main__":
    main()
