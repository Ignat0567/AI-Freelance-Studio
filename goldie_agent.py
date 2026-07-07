import json
from ddgs import DDGS
from ai_utils import ask_studio_ai_with_history

FINANCIAL_ADVISOR_PROMPT = (
    "You are Goldie, an elite senior financial analyst and strategic advisor. "
    "You provide expert financial guidance on freelance projects, budgets, pricing, "
    "cost estimation, ROI analysis, and market research. "
    "You can search the web for current financial data, exchange rates, market trends, "
    "and competitor pricing. "
    "Always respond with clear, actionable financial advice. "
    "When asked about a project, analyze its budget, scope, timeline, and provide "
    "concrete recommendations for pricing, cost optimization, and profitability. "
    "Be concise but thorough. Use numbers and percentages where appropriate."
)

def web_search(query: str, max_results: int = 5) -> list:
    """Search the web using DuckDuckGo (no API key required)."""
    try:
        with DDGS() as ddgs:
            results = []
            for i, r in enumerate(ddgs.text(query, max_results=max_results)):
                results.append({
                    "title": r.get("title", "")[:100],
                    "snippet": r.get("body", "")[:200],
                    "url": r.get("href", "")
                })
                if len(results) >= max_results:
                    break
            return results if results else [{"title": "No results", "snippet": f"No results for '{query}'", "url": ""}]
    except Exception as e:
        return [{"title": "Search error", "snippet": str(e)[:200], "url": ""}]

def chat_with_goldie(
    message: str,
    chat_history: list,
    provider: str = "nvidia",
    model_name: str = "nvidia/nemotron-4-340b-instruct",
    temperature: float = 0.3,
) -> dict:
    """Process a chat message through Goldie's financial advisor brain."""
    try:
        ai_reply = ask_studio_ai_with_history(
            provider=provider,
            model_name=model_name,
            system_prompt=FINANCIAL_ADVISOR_PROMPT,
            chat_history=chat_history + [{"role": "user", "content": message}],
            temperature=temperature,
        )
        return {"reply": ai_reply, "sources": []}
    except Exception as e:
        return {"reply": f"Goldie encountered an error: {str(e)}", "sources": []}

def analyze_project_finances(
    project_title: str,
    project_description: str,
    project_budget: str,
    provider: str = "nvidia",
    model_name: str = "nvidia/nemotron-4-340b-instruct",
) -> dict:
    """Perform deep financial analysis on a project and return structured advice."""
    analysis_prompt = FINANCIAL_ADVISOR_PROMPT + (
        "\n\nPerform a complete financial analysis of the following project. "
        "Provide your analysis in a structured format covering: "
        "1. Budget Assessment — is the budget realistic? "
        "2. Cost Breakdown — estimated costs (development, testing, deployment, maintenance) "
        "3. Pricing Strategy — recommended hourly rate or fixed price "
        "4. Profitability Forecast — expected margin and break-even "
        "5. Risk Factors — financial risks to watch "
        "6. Market Comparison — how this compares to similar projects "
        "Format your response with clear sections."
    )

    context = (
        f"Project Title: {project_title}\n"
        f"Description: {project_description}\n"
        f"Budget: {project_budget}\n"
    )

    try:
        ai_reply = ask_studio_ai_with_history(
            provider=provider,
            model_name=model_name,
            system_prompt=analysis_prompt,
            chat_history=[{"role": "user", "content": context}],
            temperature=0.3,
        )
        return {"analysis": ai_reply}
    except Exception as e:
        return {"analysis": f"Analysis failed: {str(e)}"}

def search_financial_data(query: str, max_results: int = 5) -> dict:
    """Search the web specifically for financial/market data."""
    results = web_search(query, max_results=max_results)
    return {"query": query, "results": results}
