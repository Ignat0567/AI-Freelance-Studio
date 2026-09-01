"""Turn a phase prompt into a file-by-file spec a local 14B coder can follow.

The Studio phase prompts already name the gates. A local Ollama model still
needs them restated as concrete files, because it has no tools and a short
attention span. Grok authors that spec through the same `grok.exe` login as
PowerShell (grok.com subscription), never the paid xAI HTTP API. The original
prompt is kept verbatim if the CLI is unavailable so a run never stalls.
"""

from __future__ import annotations

from collections.abc import Callable

GROK_PROMPT_AUTHOR_SYSTEM = (
    "You write implementation briefs for a local coding model (Qwen2.5-Coder 14B) "
    "that cannot use tools and cannot browse the repo. It only sees this brief and "
    "must emit complete source files. Be concrete and exhaustive. Do not write the "
    "source files yourself. Do not omit a QA gate rule from the original prompt."
)

_EXPAND_INSTRUCTIONS = """Rewrite the coding task below as a file-by-file implementation spec for a local 14B coder.

Rules for your output:
- Start with a short architecture paragraph (stack, entry file, how to run).
- Then list EVERY file the coder must write, using this exact shape:

FILE: relative/path/from/project/root
PURPOSE: one sentence
MUST CONTAIN:
- bullet list of exact symbols, CSS tokens, routes, copy, and behaviours
CONSTRAINTS:
- repeat any visual/QA numbers from the original (contrast 4.5:1, 375px, palette hex, etc.)

- Include package.json, index.html, src files, tests, Dockerfile if the original asked for them.
- Repeat the original's forbidden items (no fake tests, no secrets, no backend if it said none).
- Keep paths POSIX (forward slashes).
- Do not wrap the spec in Markdown fences.
- Do not invent product features the original did not ask for.
- Write in the same language as the original prompt's user-facing copy.

Original coding task:
"""

_ONE_FILE_EXPAND_ADDENDUM = (
    "This original task is a SINGLE FILE deliverable. Your spec must list exactly one file:\n"
    "FILE: index.html\n"
    "Do not list package.json, src/, sibling .js/.css, tests, or Dockerfile.\n\n"
)


def is_one_file_static_page_task(text: str) -> bool:
    lowered = (text or "").casefold()
    return "build one self-contained file" in lowered or (
        "exactly one html file" in lowered and "index.html" in lowered
    )


def expansion_user_prompt(original: str) -> str:
    prefix = _EXPAND_INSTRUCTIONS
    if is_one_file_static_page_task(original):
        prefix = _EXPAND_INSTRUCTIONS.replace(
            "Original coding task:\n",
            _ONE_FILE_EXPAND_ADDENDUM + "Original coding task:\n",
        )
    return prefix + original


def expand_coding_prompt(
    phase_prompt: str,
    *,
    ask: Callable[[str, str], str] | None = None,
) -> str:
    """Return a Grok-authored spec, or the original prompt if expansion fails."""
    original = (phase_prompt or "").strip()
    if not original:
        return phase_prompt
    asker = ask or _default_grok_ask
    try:
        expanded = (asker(GROK_PROMPT_AUTHOR_SYSTEM, expansion_user_prompt(original)) or "").strip()
    except Exception:
        return phase_prompt
    if _looks_like_error(expanded) or (len(expanded) < 40 and "FILE:" not in expanded):
        return phase_prompt
    return (
        expanded
        + "\n\n--- ORIGINAL TASK (authoritative if the spec above conflicts) ---\n"
        + original
    )


def _looks_like_error(text: str) -> bool:
    lowered = text.casefold()
    return lowered.startswith("grok cli ") or lowered.startswith("ai provider ") or lowered.startswith("ai service ")


def _default_grok_ask(system_prompt: str, user_prompt: str) -> str:
    import grok_bridge

    return grok_bridge.ask_grok_cli(system_prompt, user_prompt, model=grok_bridge.DEFAULT_GROK_MODEL, timeout=180)
