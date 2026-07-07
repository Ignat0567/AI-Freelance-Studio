import json
import re
from ai_utils import ask_studio_ai_with_history


GOLDIE_PROPOSAL_PROMPT = (
    "You are Goldie, a senior sales agent and proposal specialist. A freelancer wants to apply "
    "for this job on Upwork. Analyze the job description and generate:\n\n"
    "1. **summary** — 2-3 sentence understanding of the project\n"
    "2. **clarifying_questions** — 3-7 specific questions to ask the client to clarify requirements "
    "(budget, timeline, tech stack preferences, access details, design references)\n"
    "3. **proposal** — a tailored 2-3 paragraph proposal draft that:\n"
    "   - Shows understanding of the project\n"
    "   - Highlights relevant skills\n"
    "   - Proposes an approach\n"
    "   - Asks 1-2 smart questions\n"
    "   - Is professional but friendly\n"
    "4. **suggested_tech_stack** — languages, frameworks, tools needed\n"
    "5. **estimated_timeline** — realistic delivery estimate in days\n"
    "6. **estimated_budget** — suggested bid range in USD\n\n"
    "JOB DESCRIPTION:\n{job_description}\n\n"
    "Return ONLY valid JSON with these exact keys:\n"
    '{"summary": "...", "clarifying_questions": ["...", "..."], "proposal": "...", '
    '"suggested_tech_stack": ["...", "..."], "estimated_timeline": "...", "estimated_budget": "..."}'
)

MAYA_REFINE_PROMPT = (
    "You are Maya, a senior business analyst. The freelancer has received answers from the client "
    "regarding the job. Based on the original job description and the client's answers, generate:\n\n"
    "1. **refined_spec** — a complete, structured project specification that can be directly used "
    "by a developer to build the project. Include all clarified details.\n"
    "2. **remaining_questions** — any remaining ambiguities (or empty list if sufficient)\n"
    "3. **ready_for_development** — true/false if the spec is detailed enough to start coding\n\n"
    "ORIGINAL JOB:\n{original_job}\n\n"
    "CLIENT ANSWERS:\n{client_answers}\n\n"
    "Return ONLY valid JSON:\n"
    '{"refined_spec": "...", "remaining_questions": ["...", "..."], "ready_for_development": true|false}'
)


def generate_proposal(job_description: str, provider: str, model: str, temperature: float) -> dict:
    if not job_description or len(job_description.strip()) < 20:
        return {"error": "Job description too short. Please paste the full job posting."}

    try:
        raw = ask_studio_ai_with_history(
            provider=provider, model_name=model,
            system_prompt="You are Goldie, a senior sales agent. Return ONLY valid JSON.",
            chat_history=[{"role": "user", "content": GOLDIE_PROPOSAL_PROMPT.format(job_description=job_description[:4000])}],
            temperature=temperature,
        )
    except Exception as e:
        return {"error": f"AI call failed: {e}"}

    result = _parse_proposal_json(raw)
    if result and "summary" in result:
        return result

    return {"error": "Failed to parse proposal. AI returned invalid format.", "raw": raw[:500]}


def refine_spec(original_job: str, client_answers: str, provider: str, model: str, temperature: float) -> dict:
    if not client_answers or len(client_answers.strip()) < 10:
        return {"error": "Client answers too short. Paste the full client response."}

    try:
        raw = ask_studio_ai_with_history(
            provider=provider, model_name=model,
            system_prompt="You are Maya, a business analyst. Return ONLY valid JSON.",
            chat_history=[{"role": "user", "content": MAYA_REFINE_PROMPT.format(
                original_job=original_job[:3000], client_answers=client_answers[:3000]
            )}],
            temperature=temperature,
        )
    except Exception as e:
        return {"error": f"AI call failed: {e}"}

    result = _parse_proposal_json(raw)
    if result and "refined_spec" in result:
        return result

    return {"error": "Failed to parse spec. AI returned invalid format.", "raw": raw[:500]}


def _parse_proposal_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.split("```", 1)[1].rsplit("```", 1)[0].strip()

    for strategy in [
        lambda: json.loads(cleaned),
        lambda: json.loads(cleaned.replace('\n', '\\n').replace('\r', '\\r')),
        lambda: json.loads(re.sub(r'[\x00-\x1F]+', ' ', cleaned)),
    ]:
        try:
            parsed = strategy()
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    m = re.search(r'\{.*"summary".*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None
