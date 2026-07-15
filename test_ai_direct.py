import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent

def force_import_local_module(module_name, filename):
    full_path = PROJECT_ROOT / filename
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.external
@pytest.mark.skipif(os.environ.get("RUN_EXTERNAL_AI_TESTS") != "1", reason="Set RUN_EXTERNAL_AI_TESTS=1 to run external AI tests")
def test_ask_studio_ai_with_history_external():
    ai_utils = force_import_local_module("ai_utils", "ai_utils.py")
    ask_studio_ai_with_history = ai_utils.ask_studio_ai_with_history

    t0 = time.time()
    result = ask_studio_ai_with_history(
        provider="nvidia",
        model_name="meta/llama-3.3-70b-instruct",
        system_prompt="Reply with just OK",
        chat_history=[],
        temperature=0.2,
    )
    elapsed = time.time() - t0

    assert result, f"AI service returned empty response after {elapsed:.1f}s"
