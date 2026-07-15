import re as _re
import urllib.parse
import warnings
import xml.etree.ElementTree as ET

import requests

import config_storage
import secret_store

PLATFORMS = {
    "upwork": {"name": "Upwork", "base_url": "https://upwork.com"},
    "freelancer": {"name": "Freelancer", "base_url": "https://freelancer.com"},
    "habr": {"name": "Habr Freelance", "base_url": "https://habr.com"},
}

AVAILABLE_PLATFORMS = [
    {"name": "Upwork", "code": "upwork", "url": "https://upwork.com"},
    {"name": "Freelancer", "code": "freelancer", "url": "https://freelancer.com"},
    {"name": "Habr Freelance", "code": "habr", "url": "https://habr.com"},
]

REGISTER_URLS = {
    "freelancer": "https://developers.freelancer.com/",
    "upwork": "https://www.upwork.com/developer/keys/apply",
    "habr": None,
}

RSS_FEEDS = {
    "upwork": "https://www.upwork.com/ab/feed/jobs/rss?q={q}&sort=recency",
    "freelancer": "https://www.freelancer.com/jobs/rss?q={q}",
    "habr": "https://habr.com/tasks.rss?q={q}",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

MOCK_UPWORK = [
    {"title": "Build production ready Flask API endpoint", "budget": "$300", "description": "Looking for developer to deploy secure web server with PostgreSQL and Redis caching."},
    {"title": "Fullstack Next.js e-commerce dashboard", "budget": "$1,200", "description": "Build admin panel with real-time analytics, order management, and Stripe integration."},
    {"title": "Python data pipeline automation", "budget": "$500", "description": "Automate ETL processes from multiple CSV/JSON sources into PostgreSQL database."},
    {"title": "React Native mobile app for delivery service", "budget": "$2,500", "description": "Cross-platform app with GPS tracking, push notifications, and payment gateway."},
    {"title": "Docker + CI/CD infrastructure setup", "budget": "$800", "description": "Set up GitHub Actions pipeline, Docker Compose staging environment, and AWS deployment."},
]

MOCK_FREELANCER = [
    {"title": "React dashboard optimization using Tailwind CSS", "budget": "$120", "description": "Refactor complex layout structures and improve Core Web Vitals scores for a SaaS product."},
    {"title": "Telegram bot for customer support", "budget": "$250", "description": "Build a smart bot integrated with Zendesk API for automated ticket creation and FAQ responses."},
    {"title": "Vue.js PWA with offline support", "budget": "$900", "description": "Convert existing Vue 3 SPA into a Progressive Web App with service workers and indexed DB."},
    {"title": "Web scraping service with rotating proxies", "budget": "$400", "description": "Python scraper that extracts product data from e-commerce sites using Playwright and proxy rotation."},
]

MOCK_HABR = [
    {"title": "Разработка API на FastAPI для финтех-стартапа", "budget": "180 000 руб.", "description": "Проектирование REST API, интеграция с банковскими сервисами через OpenAPI спецификацию."},
    {"title": "Настройка Kubernetes кластера", "budget": "250 000 руб.", "description": "Миграция монолитного приложения в микросервисную архитектуру на K8s с GitOps."},
]

MOCK_FALLBACKS = {
    "upwork": (MOCK_UPWORK, "https://upwork.com/jobs"),
    "freelancer": (MOCK_FREELANCER, "https://freelancer.com/jobs"),
    "habr": (MOCK_HABR, "https://habr.com/tasks"),
}


def _load_config():
    return config_storage.load_studio_keys()


def _client_secret(config: dict, secret_name: str) -> str:
    secret = secret_store.get_secret(secret_name, config)
    if secret_store.has_legacy_secret(secret_name, config) and not secret_store.has_env_secret(secret_name):
        warnings.warn(secret_store.legacy_secret_warning(secret_name)["message"], RuntimeWarning, stacklevel=2)
    return secret


def _keyword_match(keyword: str, text: str) -> bool:
    if not keyword:
        return True
    return keyword.lower() in text.lower()


def _search_freelancer_api(keyword):
    """Search Freelancer.com via REST API. Requires configured client_id + client_secret."""
    config = _load_config()
    client_id = config.get("freelancer_client_id", "")
    client_secret = _client_secret(config, "freelancer_client_secret")
    if not client_id or not client_secret:
        return None  # signal: no keys configured

    try:
        # OAuth2 client_credentials grant
        token_resp = requests.post(
            "https://www.freelancer.com/api/v1/auth/token",
            json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=10,
        )
        if token_resp.status_code != 200:
            return []
        access_token = token_resp.json().get("access_token", "")

        # Search projects
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(
            f"https://www.freelancer.com/api/projects/0.1/projects/search?query={urllib.parse.quote(keyword)}&limit=15",
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        for p in data.get("result", []):
            budget = p.get("budget", {})
            results.append({
                "title": p.get("title", "").strip(),
                "platform": "Freelancer",
                "budget": f"${budget.get('minimum', '?')}" if budget else "Negotiable",
                "description": (p.get("description", "") or "")[:300],
                "url": f"https://www.freelancer.com/projects/{p.get('id', '')}",
            })
        return results
    except Exception as e:
        print(f"[Freelancer API ERROR]: {e}")
        return []


def _search_upwork_api(keyword):
    """Search Upwork via GraphQL API. Requires configured client_id + client_secret."""
    config = _load_config()
    client_id = config.get("upwork_client_id", "")
    client_secret = _client_secret(config, "upwork_client_secret")
    if not client_id or not client_secret:
        return None  # signal: no keys configured

    try:
        # OAuth2 token
        token_resp = requests.post(
            "https://www.upwork.com/api/v3/oauth2/token",
            json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=10,
        )
        if token_resp.status_code != 200:
            return []
        access_token = token_resp.json().get("access_token", "")

        # GraphQL search
        query = """{
          jobSearch(searchParams: {query: "%s"}) {
            jobs {
              title description budget { amount currency }
              url jobType
            }
          }
        }""" % keyword.replace('"', '\\"')

        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        resp = requests.post(
            "https://www.upwork.com/api/v3/graphql",
            json={"query": query},
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        for job in data.get("data", {}).get("jobSearch", {}).get("jobs", []):
            budget = job.get("budget", {})
            amount = budget.get("amount", "")
            results.append({
                "title": job.get("title", "").strip(),
                "platform": "Upwork",
                "budget": f"${amount}" if amount else "Negotiable",
                "description": (job.get("description", "") or "")[:300],
                "url": job.get("url", ""),
            })
        return results
    except Exception as e:
        print(f"[Upwork API ERROR]: {e}")
        return []


def _parse_rss_feed(platform_code: str, keyword: str) -> list:
    """Try to fetch and parse RSS feed. Returns list of job dicts or empty list on failure."""
    encoded = urllib.parse.quote(keyword)
    rss_url = RSS_FEEDS[platform_code].format(q=encoded)
    try:
        resp = requests.get(rss_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item"):
            title_el = item.find("title")
            link_el = item.find("link")
            desc_el = item.find("description")
            title = title_el.text if title_el is not None else ""
            link = link_el.text if link_el is not None else ""
            desc = desc_el.text if desc_el is not None else ""
            if not title:
                continue
            budget = "Negotiable"
            if platform_code in ("upwork", "freelancer"):
                m = _re.search(r'\$[\d,]+(\.\d+)?', desc or title)
                if m:
                    budget = m.group(0)
            items.append({
                "title": title.strip(),
                "platform": PLATFORMS[platform_code]["name"],
                "budget": budget,
                "description": (desc[:300] + "...") if desc and len(desc) > 300 else (desc or ""),
                "url": link,
            })
        return items if items else []
    except Exception as err:
        print(f"[RSS ERROR] {platform_code}: {err}")
        return []


API_SEARCHERS = {
    "freelancer": _search_freelancer_api,
    "upwork": _search_upwork_api,
}


def search_freelance_jobs(search_query: str) -> list:
    clean_query = search_query.strip().lower()
    if not clean_query:
        return []

    target_platform = None
    keyword = search_query

    tokens = clean_query.split(maxsplit=1)
    prefix = tokens[0]

    for key in PLATFORMS:
        if key.startswith(prefix):
            target_platform = key
            keyword = tokens[1] if len(tokens) > 1 else ""
            break

    results = []
    platforms_to_check = [target_platform] if target_platform else list(PLATFORMS.keys())

    for code in platforms_to_check:
        found = False

        # 1) Try real API (Freelancer / Upwork only)
        searcher = API_SEARCHERS.get(code)
        if searcher:
            api_results = searcher(keyword)
            if api_results is None:
                pass  # keys not configured, fall through
            elif api_results:
                results.extend(api_results)
                found = True
            # empty list = API error, still try RSS

        # 2) Try RSS feed (Habr only works; Upwork/Freelancer RSS are dead)
        if not found:
            rss_items = _parse_rss_feed(code, keyword)
            if rss_items:
                results.extend(rss_items)
                found = True

        # 3) Fallback to mock data
        if not found:
            mock_list, fallback_url = MOCK_FALLBACKS[code]
            for job in mock_list:
                if _keyword_match(keyword, job["title"] + job["description"]):
                    results.append({
                        **job,
                        "platform": PLATFORMS[code]["name"],
                        "url": fallback_url,
                        "mock": True,
                    })

    return results
