import sys, os, json, importlib.util, time
os.chdir("E:\\Python\\OpenCode\\FreelancerStudio")

def force_import_local_module(module_name, filename):
    full_path = os.path.join(os.getcwd(), filename)
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

ai_utils = force_import_local_module("ai_utils", "ai_utils.py")
ask_studio_ai_with_history = ai_utils.ask_studio_ai_with_history

print("Testing ask_studio_ai_with_history...")
t0 = time.time()
result = ask_studio_ai_with_history(
    provider="nvidia",
    model_name="meta/llama-3.3-70b-instruct",
    system_prompt="Reply with just OK",
    chat_history=[],
    temperature=0.2,
)
elapsed = time.time() - t0
print(f"Result ({elapsed:.1f}s): {result[:100] if result else 'EMPTY'}")
