import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from project_state import append_evidence_record, persist_project_state


ACCEPTANCE_EVIDENCE_FIELDS = (
    "criterion_id",
    "verifier",
    "verifier_type",
    "setup_steps",
    "action_steps",
    "assertions",
    "collected_evidence",
    "verdict",
    "failure_reason",
    "method",
    "command",
    "exit_code",
    "status",
    "timestamp",
    "summary",
    "artifacts",
)
ACCEPTANCE_EVIDENCE_HISTORY_KEY = "acceptance_evidence"
ACCEPTANCE_CONTRACT_FIELDS = (
    "criterion_id",
    "verifier_type",
    "setup_steps",
    "action_steps",
    "assertions",
    "collected_evidence",
    "verdict",
    "failure_reason",
    "timestamp",
)
ACCEPTANCE_VERDICTS = ("passed", "failed", "blocked", "not_executed")
VERIFIER_PLAN_TYPES = (
    "file_artifact",
    "command",
    "python_import",
    "http_single",
    "http_sequence",
    "persistence_restart",
    "runtime_start",
    "responsive_ui",
    "manual_or_unsupported",
)
VERIFIER_PLAN_FIELDS = (
    "criterion_id",
    "verifier_type",
    "setup",
    "required_fixtures",
    "actions",
    "assertions",
    "observable_expected_outcomes",
    "execution_status",
)
GLOBAL_ACCEPTANCE_EVIDENCE_TYPES = {
    "build_and_tests",
    "full_qa",
    "global_pytest",
    "global_qa",
    "latest_full_qa",
    "pytest",
    "qa_result",
    "qa_summary",
}
ISSUE_FIELDS = (
    "id",
    "source",
    "severity",
    "requirement_id",
    "criterion_id",
    "title",
    "evidence",
    "reproduction",
    "owner",
    "status",
    "attempts",
    "verification_method",
)


@dataclass
class Issue:
    id: str
    source: str
    severity: str
    requirement_id: str
    criterion_id: str
    title: str
    evidence: dict[str, Any] = field(default_factory=dict)
    reproduction: list[str] = field(default_factory=list)
    owner: str = "unassigned"
    status: str = "open"
    attempts: int = 0
    verification_method: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


AGENT_REVIEW_VERIFICATION_METHODS = {
    "bugcatcher": "qa_review",
    "sentinel": "security_review",
    "lupa": "code_review",
}


def _agent_issue_severity(agent_id: str, report_text: str) -> str:
    text = (report_text or "").lower()
    for severity in ("blocker", "critical", "high", "medium", "low"):
        if re.search(rf"\b{severity}\b", text):
            return severity
    if agent_id == "sentinel":
        return "high"
    return "medium"


def _agent_issue_title(agent_id: str, report_text: str) -> str:
    for line in (report_text or "").splitlines():
        cleaned = line.strip(" -#*\t")
        if not cleaned or cleaned.upper().startswith("VERDICT:"):
            continue
        return cleaned[:120]
    labels = {"bugcatcher": "QA review finding", "sentinel": "Security review finding", "lupa": "Code review finding"}
    return labels.get(agent_id, "Agent review finding")


def _agent_report_has_findings(report_text: str) -> bool:
    text = report_text or ""
    upper = text.upper()
    if "VERDICT: PASS" in upper:
        return False
    if "VERDICT: FAIL" in upper:
        return True
    return bool(re.search(r"\b(issue|bug|defect|vulnerab|risk|failure|failed|regression)\b", text, flags=re.IGNORECASE))


def _agent_issue_reproduction(report_text: str) -> list[str]:
    steps = []
    capture = False
    for line in (report_text or "").splitlines():
        cleaned = line.strip(" -\t")
        if not cleaned:
            capture = False
            continue
        if re.search(r"\b(repro|reproduction|steps?|command)\b", cleaned, flags=re.IGNORECASE):
            capture = True
        if capture:
            steps.append(cleaned)
    return steps[:10]


def normalize_agent_review_issues(agent_id: str, report_text: str, iteration: int = 1) -> list[dict[str, Any]]:
    agent_key = str(agent_id or "").lower()
    if agent_key not in AGENT_REVIEW_VERIFICATION_METHODS or not _agent_report_has_findings(report_text):
        return []

    issue = Issue(
        id=f"ISSUE-{agent_key.upper()}-{max(1, int(iteration)):03d}-001",
        source=agent_key,
        severity=_agent_issue_severity(agent_key, report_text),
        requirement_id="",
        criterion_id="",
        title=_agent_issue_title(agent_key, report_text),
        evidence={"original_text_report": report_text or ""},
        reproduction=_agent_issue_reproduction(report_text),
        owner="codex",
        status="open",
        attempts=0,
        verification_method=AGENT_REVIEW_VERIFICATION_METHODS[agent_key],
    )
    return [issue.to_dict()]


def append_agent_review_issues(project: dict, agent_id: str, report_text: str, iteration: int = 1) -> list[dict[str, Any]]:
    issues = normalize_agent_review_issues(agent_id, report_text, iteration)
    if issues:
        project.setdefault("issues", []).extend(issues)
        persist_project_state(project)
    return issues


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AcceptanceEvidenceExecutionContract:
    criterion_id: str
    verifier_type: str
    setup_steps: list[str] = field(default_factory=list)
    action_steps: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    collected_evidence: dict[str, Any] = field(default_factory=dict)
    verdict: str = "not_executed"
    failure_reason: str = ""
    timestamp: str = field(default_factory=_utc_timestamp)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verdict"] = _normalize_acceptance_verdict(data.get("verdict"))
        return data


@dataclass
class AcceptanceVerifierPlan:
    criterion_id: str
    verifier_type: str
    setup: list[str] = field(default_factory=list)
    required_fixtures: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    observable_expected_outcomes: list[str] = field(default_factory=list)
    execution_status: str = "not_executed"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["verifier_type"] not in VERIFIER_PLAN_TYPES:
            data["verifier_type"] = "manual_or_unsupported"
        data["execution_status"] = "not_executed"
        return {field_name: data[field_name] for field_name in VERIFIER_PLAN_FIELDS}


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _normalize_acceptance_verdict(verdict: Any, status: Any = None) -> str:
    raw = str(verdict or "").strip().lower()
    if raw in ACCEPTANCE_VERDICTS:
        return raw
    raw_status = str(status or "").strip().lower()
    if raw_status in ("pass", "passed"):
        return "passed"
    if raw_status in ("fail", "failed", "optional_fail"):
        return "failed"
    if raw_status in ("block", "blocked", "blocked_by_credentials"):
        return "blocked"
    return "not_executed"


def _status_from_verdict(verdict: str, raw_status: Any) -> str:
    status = str(raw_status or "").strip().lower()
    if verdict == "passed":
        return "passed"
    if verdict == "failed":
        return status if status in ("failed", "optional_fail") else "failed"
    if verdict == "blocked":
        return status if status in ("blocked", "blocked_by_credentials") else "blocked"
    return status if status and status not in ("pass", "passed", "fail", "failed", "blocked") else "not_verified"


def _acceptance_verifier_type(source: dict[str, Any]) -> str:
    return str(
        source.get("verifier_type")
        or source.get("method")
        or source.get("verification_method")
        or source.get("verifier")
        or source.get("source")
        or "unknown"
    ).strip() or "unknown"


def _is_global_pytest_command(value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        text = " ".join(str(part) for part in value)
    else:
        text = str(value or "")
    lower = re.sub(r"\s+", " ", text.strip().lower())
    if "pytest" not in lower:
        return False
    tokens = lower.split()
    ignored = {"-m", "pytest", "-q", "--quiet", "--disable-warnings"}
    meaningful = []
    for token in tokens:
        if token.startswith("python"):
            continue
        if token in ignored or token.startswith("--maxfail="):
            continue
        meaningful.append(token)
    return not meaningful


def _collected_acceptance_evidence(source: dict[str, Any]) -> dict[str, Any]:
    raw = source.get("collected_evidence")
    if isinstance(raw, dict):
        collected = dict(raw)
    elif raw in (None, "", [], {}):
        collected = {}
    else:
        collected = {"value": raw}

    reserved = set(ACCEPTANCE_CONTRACT_FIELDS) | {
        "verifier",
        "source",
        "method",
        "verification_method",
        "status",
        "timestamp",
    }
    for key, value in source.items():
        if key in reserved:
            continue
        if value in (None, "", [], {}):
            continue
        collected.setdefault(key, value)
    return collected


def acceptance_evidence_is_direct(evidence: dict[str, Any] | None, criterion_id: str | None = None) -> bool:
    if not isinstance(evidence, dict):
        return False
    if criterion_id is not None and str(evidence.get("criterion_id") or "") != str(criterion_id):
        return False

    verdict = _normalize_acceptance_verdict(evidence.get("verdict"), evidence.get("status"))
    status = str(evidence.get("status") or "").lower()
    if verdict == "not_executed" or status in ("not_applicable", "not_verified"):
        return False

    verifier_type = _acceptance_verifier_type(evidence).lower()
    if verifier_type in GLOBAL_ACCEPTANCE_EVIDENCE_TYPES:
        return False
    if any(marker in verifier_type for marker in ("keyword", "text_presence")):
        return False
    if "qa_success" in evidence or "qa_success" in (evidence.get("collected_evidence") or {}):
        return False

    collected = evidence.get("collected_evidence")
    if not isinstance(collected, dict):
        collected = _collected_acceptance_evidence(evidence)
    if "qa_success" in collected:
        return False
    if _is_global_pytest_command(evidence.get("command") or collected.get("command")):
        return False

    has_strategy = bool(verifier_type and verifier_type != "unknown")
    has_observation = bool(
        collected
        or evidence.get("command")
        or evidence.get("exit_code") is not None
        or evidence.get("artifacts")
        or evidence.get("traceback_tail")
    )
    return has_strategy and has_observation


def normalize_acceptance_evidence(criterion_id: str, status: str, evidence: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(evidence or {})
    raw_status = source.get("status", status)
    verdict = _normalize_acceptance_verdict(source.get("verdict"), raw_status)
    normalized_status = _status_from_verdict(verdict, raw_status)
    verifier_type = _acceptance_verifier_type(source)
    collected_evidence = _collected_acceptance_evidence(source)
    action_steps = _as_text_list(source.get("action_steps"))
    if not action_steps and source.get("command"):
        action_steps = [str(source.get("command"))]
    assertions = _as_text_list(source.get("assertions"))
    if not assertions and source.get("summary"):
        assertions = [str(source.get("summary"))]
    failure_reason = str(source.get("failure_reason") or "")
    if not failure_reason and verdict in ("failed", "blocked"):
        failure_reason = str(source.get("summary") or "")
    normalized = {
        "criterion_id": source.get("criterion_id", criterion_id),
        "verifier": source.get("verifier", source.get("source", "unknown")),
        "verifier_type": verifier_type,
        "setup_steps": _as_text_list(source.get("setup_steps")),
        "action_steps": action_steps,
        "assertions": assertions,
        "collected_evidence": collected_evidence,
        "verdict": verdict,
        "failure_reason": failure_reason,
        "method": source.get("method", source.get("verification_method", "")),
        "command": source.get("command"),
        "exit_code": source.get("exit_code"),
        "status": normalized_status,
        "timestamp": source.get("timestamp", _utc_timestamp()),
        "summary": source.get("summary", ""),
        "artifacts": source.get("artifacts", []),
    }
    for key, value in source.items():
        normalized.setdefault(key, value)
    if normalized["verdict"] == "passed" and not acceptance_evidence_is_direct(normalized, str(normalized["criterion_id"])):
        raise ValueError("Passed acceptance evidence requires direct criterion-specific evidence")
    return normalized


def _has_acceptance_evidence(evidence: dict[str, Any]) -> bool:
    if evidence.get("summary"):
        return True
    if evidence.get("command"):
        return True
    if evidence.get("exit_code") is not None:
        return True
    return bool(evidence.get("artifacts"))


def ensure_acceptance_evidence_history(project: dict) -> dict[str, list[dict[str, Any]]]:
    history = project.get(ACCEPTANCE_EVIDENCE_HISTORY_KEY)
    if not isinstance(history, dict):
        history = {}
        project[ACCEPTANCE_EVIDENCE_HISTORY_KEY] = history

    for criterion in project.get("acceptance_criteria", []):
        criterion_id = criterion.get("id")
        if not criterion_id:
            continue
        entries = history.setdefault(criterion_id, [])
        legacy_entries = criterion.get("evidence", [])
        if isinstance(legacy_entries, list) and legacy_entries is not entries:
            for entry in legacy_entries:
                if isinstance(entry, dict) and entry not in entries:
                    entries.append(entry)
        criterion["evidence"] = entries
    return history


IGNORED_QA_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".pytest_cache",
    "__pycache__",
}


def _text(project: dict) -> str:
    parts = [project.get("title", ""), project.get("original_request", "") or project.get("description", "")]
    for msg in project.get("chat_history", []):
        if msg.get("role") in ("user", "assistant"):
            parts.append(msg.get("content", ""))
    return "\n".join(p for p in parts if p)


def _original_request(project: dict) -> str:
    return project.get("original_request") or project.get("description") or project.get("project_spec", {}).get("original_user_request") or "\n".join(
        msg.get("content", "") for msg in project.get("chat_history", []) if msg.get("role") == "user"
    ) or project.get("title", "")


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"(?:\r?\n|[.;])", text or "")
    return [c.strip(" -\t") for c in chunks if len(c.strip()) >= 8]


def _paragraphs(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"(?:\r?\n){2,}", text or "") if chunk.strip()]


def _strip_bullet(line: str) -> str:
    return re.sub(r"^\s*[-*•]\s*", "", line).strip(" .;\t")


def _join_header_bullets(header: str, bullets: list[str]) -> str:
    header = header.strip(" :;.-\t")
    bullet_text = "; ".join(item.strip(" ;.") for item in bullets if item.strip())
    if header and bullet_text:
        return f"{header}: {bullet_text}"
    return header or bullet_text


def _requirement_like(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        token in lower
        for token in (
            "must", "should", "need", "needs", "allow", "can", "create", "build", "implement", "add", "support", "include",
            "долж", "нужно", "нужен", "нужна", "нужны", "хочу", "хотел", "важно", "может", "возможность", "создать",
            "сохран", "появ", "открыть", "изменить", "поменять", "добавить", "удалить", "поиск", "фильтр", "запуск",
            "перезапуск", "windows", "планшет", "компьютер",
        )
    )


def _semantic_requirement_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for paragraph in _paragraphs(text):
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        bullet_indexes = [index for index, line in enumerate(lines) if re.match(r"^\s*[-*•]\s+", line)]
        if bullet_indexes:
            first_bullet = bullet_indexes[0]
            header = " ".join(lines[:first_bullet]).strip()
            bullets = [_strip_bullet(lines[index]) for index in bullet_indexes]
            header_lower = header.lower()
            if any(token in header_lower for token in ("очень важно", "important")):
                header_requirements = re.sub(r"(?i)\bimportant\b\s*:?", "", header)
                header_requirements = re.sub(r"(?i)очень\s+важно\s*:?", "", header_requirements).strip()
                for sentence in _sentences(header_requirements):
                    if _requirement_like(sentence):
                        phrases.append(sentence)
                phrases.extend(item for item in bullets if _requirement_like(item))
            else:
                combined = _join_header_bullets(header, bullets)
                if combined and (_requirement_like(combined) or bullets):
                    phrases.append(combined)
            continue

        for sentence in _sentences(paragraph):
            if _requirement_like(sentence):
                phrases.append(sentence)

    if not phrases:
        phrases = _sentences(text)[:6]
    return _dedupe([phrase.strip(" .;\t") for phrase in phrases if phrase.strip()])[:20]


def _feature_phrases(text: str) -> list[str]:
    return _semantic_requirement_phrases(text)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        key = str(item).strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _stable_id(prefix: str, index: int) -> str:
    return f"{prefix}-{index:03d}"


def _requirement_title(description: str) -> str:
    title = str(description or "").strip().splitlines()[0]
    return title[:72] or "Requirement"


def requirements_from_features(features: list[str]) -> list[dict[str, Any]]:
    unique_features = _dedupe([str(feature).strip() for feature in features if str(feature).strip()])
    return [
        {
            "id": _stable_id("REQ", index),
            "parent_id": None,
            "title": _requirement_title(feature),
            "description": feature,
            "priority": "high",
            "source": "user_requirement",
            "dependencies": [],
            "status": "pending",
            "mandatory": True,
        }
        for index, feature in enumerate(unique_features, start=1)
    ]


def _user_requirements(features: list[str]) -> list[dict[str, Any]]:
    return requirements_from_features(features)


def requirement_dependency_errors(requirements: list[dict[str, Any]]) -> list[str]:
    ids = {str(req.get("id")) for req in requirements if isinstance(req, dict) and req.get("id")}
    errors = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        req_id = str(requirement.get("id") or "")
        dependencies = requirement.get("dependencies", [])
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list):
            errors.append(f"{req_id}: dependencies must be a list")
            continue
        for dependency_id in dependencies:
            dependency_id = str(dependency_id)
            if dependency_id == req_id:
                errors.append(f"{req_id}: dependency cannot reference itself")
            elif dependency_id not in ids:
                errors.append(f"{req_id}: unknown dependency {dependency_id}")
    return errors


def requirement_graph(project_spec: dict) -> list[dict[str, Any]]:
    requirements = project_spec.get("requirements", [])
    return requirements if isinstance(requirements, list) else []


def mandatory_user_requirements(project_spec: dict) -> list[dict[str, Any]]:
    requirements = project_spec.get("requirements", [])
    if not isinstance(requirements, list):
        return []
    mandatory = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        if requirement.get("source") != "user_requirement":
            continue
        if requirement.get("mandatory", True) and requirement.get("priority", "high") in ("critical", "high"):
            mandatory.append(requirement)
    return mandatory


def orphan_mandatory_requirements(project_spec: dict, acceptance_criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linked_requirement_ids = set()
    for criterion in acceptance_criteria:
        ids = criterion.get("requirement_ids", [])
        if isinstance(ids, str):
            ids = [ids]
        if isinstance(ids, list):
            linked_requirement_ids.update(str(req_id) for req_id in ids if req_id)
    return [req for req in mandatory_user_requirements(project_spec) if req.get("id") not in linked_requirement_ids]


def _credential_record(name: str, description: str, source: str = "user_request", required: bool = True, blocks_completion: bool | None = None) -> dict[str, Any]:
    if blocks_completion is None:
        blocks_completion = required and name not in {"TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD"}
    return {
        "name": name,
        "description": description,
        "source": source,
        "required": bool(required),
        "configured": False,
        "externally_verifiable": True,
        "blocks_completion": bool(blocks_completion),
    }


def _wants_smtp_credentials(lower: str) -> bool:
    if "smtp" in lower:
        return True
    email_terms = ("email", "e-mail", "mail", "электронн", "почт")
    send_terms = ("send", "deliver", "notify", "notification", "newsletter", "отправ", "рассыл", "уведом")
    return any(email in lower for email in email_terms) and any(send in lower for send in send_terms)


def _has_credential_fallback(lower: str, name: str) -> bool:
    if name == "DATABASE_URL":
        return "database_url" in lower and "sqlite" in lower and "fallback" in lower
    if name == "STRIPE_API_KEY":
        return (
            "stripe_api_key" in lower
            and any(term in lower for term in ("mock payment", "disabled stripe", "stripe mode"))
            and any(term in lower for term in ("not provided", "without external credentials", "without real credentials"))
        )
    if name == "SMTP_PASSWORD":
        return (
            "smtp" in lower
            and any(term in lower for term in ("console/log notification", "console notification", "log notification"))
            and any(term in lower for term in ("not provided", "without external credentials", "without real credentials"))
        )
    return False


def _credential_requirements(text: str) -> list[dict[str, Any]]:
    lower = (text or "").lower()
    credentials = []
    candidates = [
        ("TELEGRAM_BOT_TOKEN", lambda value: "telegram" in value and "bot" in value, "Telegram Bot API token"),
        ("OPENAI_API_KEY", lambda value: "openai" in value, "OpenAI API key"),
        ("ANTHROPIC_API_KEY", lambda value: "anthropic" in value or "claude" in value, "Anthropic API key"),
        ("STRIPE_API_KEY", lambda value: "stripe" in value or "payment" in value, "Stripe API key"),
        ("DISCORD_TOKEN", lambda value: "discord" in value and "bot" in value, "Discord bot token"),
        ("DATABASE_URL", lambda value: any(token in value for token in ("postgres", "mysql", "database url", "database_url")), "Database connection string"),
        ("SMTP_PASSWORD", _wants_smtp_credentials, "SMTP credentials"),
    ]
    for env_name, matcher, description in candidates:
        if matcher(lower):
            credentials.append(
                _credential_record(
                    env_name,
                    description,
                    blocks_completion=not _has_credential_fallback(lower, env_name),
                )
            )
    if any(k in lower for k in ("api key", "apikey", "token", "secret", "oauth")) and not credentials:
        credentials.append(_credential_record("EXTERNAL_SERVICE_CREDENTIAL", "Credential required by requested external service"))
    return credentials


def detect_requirement_gaps(text: str, credentials: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    credentials = credentials or _credential_requirements(text)
    lower = (text or "").lower()
    sentences = _sentences(text)
    gaps: list[dict[str, Any]] = []

    def add(category: str, severity: str, summary: str, evidence: str, suggested_question: str):
        gap_id = f"GAP-{len(gaps) + 1:03d}"
        gaps.append({
            "id": gap_id,
            "category": category,
            "severity": severity,
            "summary": summary,
            "evidence": evidence,
            "suggested_question": suggested_question,
            "status": "unresolved",
        })

    ambiguity_terms = (
        "modern", "user-friendly", "user friendly", "beautiful", "intuitive", "fast",
        "scalable", "secure", "robust", "etc", "and so on", "as needed", "nice",
    )
    for sentence in sentences:
        sentence_lower = sentence.lower()
        term = next((term for term in ambiguity_terms if term in sentence_lower), None)
        if term:
            add(
                "ambiguity",
                "medium",
                "Requirement uses a subjective or open-ended term without measurable acceptance detail.",
                sentence,
                f"What concrete behavior or measurable acceptance criteria should define '{term}'?",
            )
            break

    contradiction_pairs = (
        ("use database", "without database"),
        ("with database", "no database"),
        ("online", "offline only"),
        ("must use react", "must not use react"),
        ("must use fastapi", "must not use fastapi"),
        ("store user data", "do not store user data"),
    )
    for required, forbidden in contradiction_pairs:
        if required in lower and forbidden in lower:
            add(
                "contradiction",
                "high",
                "Requirement contains mutually incompatible instructions.",
                f"Both '{required}' and '{forbidden}' were requested.",
                "Which of these conflicting instructions should take precedence?",
            )
            break

    for credential in credentials:
        if not credential.get("blocks_completion", True):
            continue
        name = credential.get("name", "EXTERNAL_SERVICE_CREDENTIAL")
        add(
            "missing_credential",
            "blocker",
            "External credential is required before full implementation or live verification can be completed.",
            name,
            f"Can you provide or confirm the placeholder/environment variable strategy for {name}?",
        )

    services = _external_services(text, credentials)
    if services and not any(k in lower for k in ("endpoint", "webhook", "model", "scope", "bot token", "api key", "sandbox", "test mode", "callback url")):
        add(
            "missing_external_service_detail",
            "high",
            "External service is named without integration details needed for implementation and QA.",
            ", ".join(services),
            "Which exact service endpoint, mode, scopes, callback URLs, and test/sandbox behavior should be used?",
        )

    impossible_verification_patterns = (
        "100% uptime", "guarantee uptime", "prove users will", "verify users will",
        "rank #1", "rank first", "real payment", "production payment", "millions of users",
    )
    for pattern in impossible_verification_patterns:
        if pattern in lower:
            add(
                "impossible_verification_method",
                "high",
                "Requested verification depends on production-scale, third-party, or subjective outcomes that cannot be proven locally before delivery.",
                pattern,
                "What local, automated, or sandbox acceptance check should replace this verification requirement?",
            )
            break

    return gaps


def _read_project_files(project_path: str | None) -> tuple[set[str], dict[str, str]]:
    files: set[str] = set()
    texts: dict[str, str] = {}
    if project_path and os.path.isdir(project_path):
        for root, dirs, names in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in IGNORED_QA_DIRS]
            for name in names:
                rel = os.path.relpath(os.path.join(root, name), project_path).replace(os.sep, "/")
                rel_lower = rel.lower()
                files.add(rel_lower)
                if rel_lower.endswith((".py", ".txt", ".md", ".toml", ".json", ".html", ".js", ".jsx", ".ts", ".tsx")):
                    texts[rel_lower] = _safe_read(project_path, rel)
    return files, texts


def _has_dependency(texts: dict[str, str], dependency: str) -> bool:
    dep = re.escape(dependency.lower())
    for rel, text in texts.items():
        lower = text.lower()
        if rel.endswith("requirements.txt") and re.search(rf"(?m)^\s*{dep}(?:\s|$|[<>=~!\[])", lower):
            return True
        if rel.endswith(("pyproject.toml", "package.json")) and re.search(rf"['\"]?{dep}['\"]?\s*[:=]", lower):
            return True
        if rel.endswith("pyproject.toml") and re.search(rf"(?m)^\s*{dep}(?:\s|$|[<>=~!\[])", lower):
            return True
    return False


def _negative_framework_request(blob: str, framework: str) -> bool:
    return bool(re.search(rf"\b(no|not|without|must not|do not)\b[^.\n]{{0,60}}\b{re.escape(framework)}\b", blob) or re.search(rf"\b(не|без|нельзя)\b[^.\n]{{0,60}}\b{re.escape(framework)}\b", blob))


def detect_project_profiles(project_spec: dict | None = None, project_path: str | None = None, text: str = "") -> list[str]:
    blob = "\n".join(
        str(x)
        for x in [
            text,
            (project_spec or {}).get("original_user_request", ""),
            (project_spec or {}).get("project_goal", ""),
            " ".join((project_spec or {}).get("required_features", [])),
            " ".join((project_spec or {}).get("technology_requirements", [])),
        ]
    ).lower()
    declared_profiles = {str(profile) for profile in (project_spec or {}).get("project_profiles", []) if profile}

    files, texts = _read_project_files(project_path)
    py_text = "\n".join(text for rel, text in texts.items() if rel.endswith(".py"))
    docs_text = "\n".join(text for rel, text in texts.items() if rel.endswith((".md", ".txt")))

    profiles = []
    has_file = lambda name: name.lower() in files
    has_suffix = lambda suffix: any(f.endswith(suffix) for f in files)

    fastapi_dependency = _has_dependency(texts, "fastapi")
    uvicorn_dependency = _has_dependency(texts, "uvicorn")
    fastapi_import = bool(re.search(r"\b(from\s+fastapi\s+import|import\s+fastapi)\b", py_text))
    fastapi_app = bool(re.search(r"\bfastapi\s*\(", py_text))
    uvicorn_usage = uvicorn_dependency or "uvicorn" in py_text.lower() or "uvicorn" in docs_text.lower()
    root_asgi_entrypoint = (has_file("main.py") or has_file("app.py")) and fastapi_app
    fastapi_file_score = sum(
        weight
        for present, weight in (
            (fastapi_dependency, 2),
            (fastapi_import, 2),
            (fastapi_app, 3),
            (uvicorn_usage, 1),
            (root_asgi_entrypoint, 1),
        )
        if present
    )
    fastapi_text_evidence = "fastapi" in blob and any(token in blob for token in ("api", "rest", "uvicorn", "asgi", "backend", "web")) and not _negative_framework_request(blob, "fastapi")
    declared_fastapi = "fastapi" in declared_profiles and not _negative_framework_request(blob, "fastapi")
    if fastapi_file_score >= 4 or declared_fastapi or (not project_path and fastapi_text_evidence) or (project_path and not files and fastapi_text_evidence):
        profiles.append("fastapi")
        profiles.append("REST_API")
    if any(k in blob for k in ("telegram", "aiogram")) or has_file("bot.py") or any(f.startswith("handlers/") for f in files):
        profiles.append("telegram_bot")
    if any(k in blob for k in ("react", "jsx")) or any(f.endswith((".jsx", ".tsx")) for f in files):
        profiles.append("react_frontend")
    if any(k in blob for k in ("vite",)) or has_file("vite.config.js") or has_file("vite.config.ts"):
        profiles.append("vite_frontend")
    if any(k in blob for k in ("node", "express", "npm")) or has_file("package.json"):
        profiles.append("node_backend" if "express" in blob else "node_project")
    if any(k in blob for k in ("static website", "landing page", "html")) or has_file("index.html") or has_suffix(".html"):
        profiles.append("static_website")
    if any(k in blob for k in ("database", "sqlite", "postgres", "mysql", "sqlalchemy")):
        profiles.append("database_application")
    if any(k in blob for k in ("ai ", "llm", "openai", "anthropic", "nvidia", "model")):
        profiles.append("AI_application")
    if any(k in blob for k in ("cli", "command line", "terminal")):
        profiles.append("python_cli")
    python_file_evidence = has_file("main.py") or has_file("app.py") or has_file("bot.py") or has_file("requirements.txt") or (has_suffix(".py") and "static_website" not in profiles)
    if any(k in blob for k in ("python", "fastapi", "django", "flask", "aiogram")) or python_file_evidence:
        profiles.append("python_application")
    for profile in declared_profiles:
        if profile != "generic" and profile not in profiles:
            profiles.append(profile)
    if not profiles:
        profiles.append("generic")
    return _dedupe(profiles)


def _safe_read(project_path: str | None, rel: str) -> str:
    if not project_path:
        return ""
    try:
        path = Path(project_path) / rel
        if path.is_file() and path.stat().st_size < 250_000:
            return path.read_text(encoding="utf-8", errors="replace").lower()
    except Exception:
        return ""
    return ""


def build_project_spec(project: dict, existing_path: str | None = None) -> dict[str, Any]:
    source_text = _text(project)
    original = _original_request(project)
    if project.get("recovery_status") in {"native", "fully_recovered"} and not original.strip():
        raise ValueError("recovery_state_error: original request is required for contract migration")
    features = _feature_phrases(source_text)
    credentials = _credential_requirements(source_text)
    requirement_gaps = detect_requirement_gaps(source_text, credentials)
    profiles = detect_project_profiles(text=source_text, project_path=existing_path)

    technology = []
    lower = source_text.lower()
    for token, label in (
        ("fastapi", "FastAPI"),
        ("react", "React"),
        ("vite", "Vite"),
        ("telegram", "Telegram Bot API"),
        ("aiogram", "aiogram"),
        ("sqlite", "SQLite"),
        ("postgres", "PostgreSQL"),
        ("docker", "Docker"),
    ):
        if token in lower:
            technology.append(label)

    expected_entrypoint = "main.py"
    if "telegram_bot" in profiles:
        expected_entrypoint = "bot.py"
    elif "vite_frontend" in profiles or "react_frontend" in profiles:
        expected_entrypoint = "package.json"

    install_method = "Document exact dependency installation in README.md"
    run_method = "Document exact run command in README.md"
    test_method = "Run configured automated tests when present"
    if "fastapi" in profiles:
        install_method = "python -m pip install -r requirements.txt"
        run_method = "python -m uvicorn main:app --host 127.0.0.1 --port <free-port>"
        test_method = "python -m pytest -q"
    elif "telegram_bot" in profiles:
        install_method = "python -m pip install -r requirements.txt"
        run_method = "python bot.py"
        test_method = "python -m pytest -q when tests exist; otherwise run import/smoke checks"
    elif "vite_frontend" in profiles:
        install_method = "npm install"
        run_method = "npm run dev or npm run preview"
        test_method = "npm run build and configured npm test when present"

    spec = {
        "original_user_request": original,
        "project_goal": project.get("title") or (features[0] if features else "Generated project"),
        "project_type": profiles[0],
        "project_profiles": profiles,
        "intended_users": _infer_users(source_text),
        "requirements": _user_requirements(features),
        "required_features": features,
        "explicit_constraints": _infer_constraints(source_text),
        "inferred_requirements": _inferred_requirements(profiles, credentials),
        "technology_requirements": _dedupe(technology),
        "external_services": _external_services(source_text, credentials),
        "required_credentials": credentials,
        "requirement_gaps": requirement_gaps,
        "expected_entrypoint": expected_entrypoint,
        "installation_method": install_method,
        "run_method": run_method,
        "test_method": test_method,
        "delivery_artifacts": _delivery_artifacts(profiles, credentials),
        "risks": _risks(profiles, credentials),
        "unknowns": _unknowns(source_text, credentials),
        "assumptions": _assumptions(profiles),
    }
    ticket_blob = f"{original} {source_text}".lower()
    if "ticket" in ticket_blob and ("priority" in ticket_blob or "status" in ticket_blob):
        units = [
            ("browser_ticket_runtime", "Browser-based ticket tracking application runs for internal staff."),
            ("ticket_fields", "Ticket records store client name, contact phone or email, optional company, and problem description."),
            ("priority_values", "Ticket priority accepts low, normal, high, and urgent."),
            ("status_values", "Ticket status accepts new, in progress, waiting for client, completed, and closed."),
            ("ticket_lifecycle", "Users can create, list, open, and edit ticket records."),
            ("ticket_comments", "Users can add and view ticket comments."),
            ("confirmed_deletion", "Ticket deletion requires explicit confirmation."),
            ("dashboard_metrics", "Dashboard shows new, in-progress, urgent, and closed-today counts."),
            ("ticket_search", "Search finds tickets by client, contact, and problem description."),
            ("ticket_filters", "Ticket list filters by status and priority."),
            ("persistent_storage", "Ticket data persists across application restart."),
            ("local_admin_scope", "One configured local administrator can use the application without registration, roles, tenants, or external identity."),
            ("dark_product_quality", "Interface has a modern dark visual design."),
            ("desktop_usability", "Interface remains usable at desktop viewport sizes."),
            ("tablet_usability", "Interface remains usable at tablet viewport sizes."),
            ("primary_controls", "Primary user controls perform their intended actions."),
            ("automated_tests", "Automated tests cover the ticket workflow."),
            ("windows_documentation", "Windows installation and run instructions are documented."),
        ]
        spec["requirements"] = [
            {"id": _stable_id("REQ", index + 1), "title": intent.replace("_", " "), "description": text,
             "semantic_intent": intent, "source_trace": original, "mandatory": True, "priority": "high", "source": "user_requirement", "dependencies": [], "status": "pending"}
            for index, (intent, text) in enumerate(units)
        ]
    return spec


def _infer_users(text: str) -> list[str]:
    lower = text.lower()
    users = []
    if "admin" in lower:
        users.append("administrator")
    if "telegram" in lower:
        users.append("telegram users")
    if "api" in lower:
        users.append("API consumers")
    if "dashboard" in lower or "frontend" in lower or "website" in lower:
        users.append("web users")
    return users or ["end users"]


def _infer_constraints(text: str) -> list[str]:
    constraints = []
    for sentence in _sentences(text):
        lower = sentence.lower()
        if any(k in lower for k in ("must", "must not", "no ", "without", "only", "долж", "нельзя", "только", "без ")):
            constraints.append(sentence)
    return _dedupe(constraints)[:10]


def _inferred_requirements(profiles: list[str], credentials: list[dict[str, Any]]) -> list[str]:
    reqs = ["Root README.md with install, run, and test instructions", "No placeholder or TODO-only implementation"]
    if any(p in profiles for p in ("python_application", "fastapi", "telegram_bot")):
        reqs.append("Python dependency file must match imports")
    if any(p in profiles for p in ("node_project", "react_frontend", "vite_frontend")):
        reqs.append("Node dependency file and build script must be valid")
    if credentials:
        reqs.append("Credential requirements must be documented in .env.example without real secrets")
    return reqs


def _external_services(text: str, credentials: list[dict[str, Any]]) -> list[str]:
    services = []
    lower = text.lower()
    for token, label in (("telegram", "Telegram"), ("openai", "OpenAI"), ("anthropic", "Anthropic"), ("stripe", "Stripe"), ("discord", "Discord")):
        if token in lower:
            services.append(label)
    services.extend(c["description"] for c in credentials if c.get("description"))
    return _dedupe(services)


def _delivery_artifacts(profiles: list[str], credentials: list[dict[str, Any]]) -> list[str]:
    artifacts = ["README.md"]
    if any(p in profiles for p in ("python_application", "fastapi", "telegram_bot")):
        artifacts.append("requirements.txt")
    if any(p in profiles for p in ("node_project", "react_frontend", "vite_frontend")):
        artifacts.append("package.json")
    if credentials:
        artifacts.extend([".env.example", ".gitignore"])
    if "static_website" in profiles:
        artifacts.append("index.html or frontend/index.html")
    return _dedupe(artifacts)


def _risks(profiles: list[str], credentials: list[dict[str, Any]]) -> list[str]:
    risks = []
    if any(credential.get("required") and credential.get("blocks_completion", True) for credential in credentials):
        risks.append("Project depends on external credentials that cannot be validated without user input")
    if "telegram_bot" in profiles:
        risks.append("Telegram network behavior cannot be fully smoke-tested without a real bot token")
    if "AI_application" in profiles:
        risks.append("AI provider behavior may vary by model/provider availability")
    return risks or ["User-specific acceptance behavior still requires project-specific QA evidence"]


def _unknowns(text: str, credentials: list[dict[str, Any]]) -> list[str]:
    unknowns = []
    if not text.strip():
        unknowns.append("No detailed original request was provided")
    if credentials:
        unknowns.append("Real production credentials are not available during generation")
    return unknowns


def _assumptions(profiles: list[str]) -> list[str]:
    assumptions = ["Generated project should run locally from its project root"]
    if "fastapi" in profiles:
        assumptions.append("FastAPI app exposes an ASGI app object named app")
    if "vite_frontend" in profiles:
        assumptions.append("Vite build is the production frontend readiness check")
    return assumptions


def _acceptance_statement(requirement_text: str) -> dict[str, str]:
    text = str(requirement_text or "").strip()
    lower = text.lower()

    patterns: list[tuple[bool, str, str, str]] = [
        (
            ("после создания" in lower and "спис" in lower) or ("appear" in lower and "list" in lower),
            "After creation, the new request appears in the common request list.",
            "A newly created client request is visible in the shared request list without manual data repair.",
            "The request list contains the new request after creation.",
        ),
        (
            ("веб-программ" in lower or "web" in lower) and ("уч" in lower and "заяв" in lower or "request tracking" in lower),
            "The web application supports client request tracking.",
            "The delivered browser-based application lets company staff track client requests in one system.",
            "Client requests can be managed through the delivered web application.",
        ),
        (
            ("открыть в браузере" in lower) or ("browser" in lower and "system" in lower),
            "The application is usable through a browser.",
            "Users can open the application in a browser and use it for the internal request workflow.",
            "The application serves a browser-accessible interface.",
        ),
        (
            (("созда" in lower and "заяв" in lower and "после создания" not in lower) or ("create" in lower and any(token in lower for token in ("request", "ticket")))) ,
            "User can create a new client request.",
            "A staff user can enter required client request data and receive a persisted request record.",
            "Creating a client request returns a saved record with the submitted client data.",
        ),
        (
            ("статус" in lower and any(token in lower for token in ("новая", "в работе", "закрыта", "workflow"))),
            "Request statuses include the requested workflow states.",
            "Client requests support the requested workflow statuses such as new, in progress, waiting for client, completed, and closed where applicable.",
            "Request records can use the required status values.",
        ),
        (
            ("перезапуск" in lower or "restart" in lower) and ("исчез" in lower or "persist" in lower or "data" in lower),
            "Application data persists after restart.",
            "Saved client request data remains available after the application process is stopped and started again.",
            "Data created before restart can be retrieved after restart.",
        ),
        (
            ("сохран" in lower and "заяв" in lower) or ("store" in lower and any(token in lower for token in ("field", "request", "ticket"))),
            "Client request records store all required fields.",
            "A client request stores client identity, contact, company, problem description, priority, and status where applicable.",
            "Saved request records expose the required fields with their submitted values.",
        ),
        (
            any(token in lower for token in ("открыть", "изменить", "поменять", "комментар", "удалить")) and "заяв" in lower,
            "User can open an existing request and update its data.",
            "A user can open an existing client request, change request data or status, add comments, and delete it with confirmation when supported.",
            "Existing request operations change the stored request state and are observable through the UI or API.",
        ),
        (
            ("главной" in lower and "сколько" in lower) or ("summary" in lower and "count" in lower),
            "The home page displays current request summary counts.",
            "The application shows counts for new, in-progress, urgent, and closed-today requests where applicable.",
            "Summary counters reflect the current stored request data.",
        ),
        (
            "поиск" in lower or "search" in lower,
            "User can search requests by client, contact, or request text.",
            "A user can search the request list using client, phone, email, company, description, or comment text where applicable.",
            "Search results include matching requests and exclude non-matching requests.",
        ),
        (
            "фильтр" in lower or "filter" in lower,
            "User can filter requests by status and priority.",
            "The request list can be narrowed by status and priority values.",
            "Filtered results contain only requests matching the selected status or priority.",
        ),
        (
            ("планшет" in lower or "tablet" in lower) and ("компьютер" in lower or "desktop" in lower),
            "The application layout remains usable on desktop and tablet viewport sizes.",
            "Core screens and controls remain readable and usable on ordinary desktop and tablet viewport sizes.",
            "The UI remains usable on desktop and tablet widths without hiding required actions.",
        ),
        (
            ("современно" in lower or "тёмн" in lower or "dark theme" in lower) and ("интерфейс" in lower or "interface" in lower),
            "The interface uses a modern, tidy visual design.",
            "The application interface is visually tidy and supports the requested dark-theme direction where applicable.",
            "The UI presents the workflow in a modern, readable layout.",
        ),
        (
            ("система пользователей" in lower and "не нужна" in lower) or ("администратор" in lower and "слож" in lower) or ("single" in lower and "administrator" in lower),
            "A single local administrator can use the application without a complex user system.",
            "The first version supports use by one local administrator without requiring a full user-management subsystem.",
            "The application can be used locally without complex user-account setup.",
        ),
        (
            "реально запуск" in lower or ("start" in lower and any(token in lower for token in ("runtime", "application", "app"))),
            "The application starts successfully with the documented run command.",
            "The delivered application can be started locally using the documented command without hidden manual edits.",
            "The documented run command starts the application successfully.",
        ),
        (
            "windows" in lower and ("установ" in lower or "install" in lower or "запуст" in lower or "run" in lower),
            "Windows installation and run instructions are documented.",
            "The README explains how to install dependencies and start the project on Windows.",
            "Windows setup and run instructions are present and specific to the delivered project.",
        ),
        (
            "кноп" in lower or "button" in lower,
            "Primary interface buttons perform their intended actions.",
            "Main user-facing controls invoke real application behavior rather than inert placeholders.",
            "Clicking primary controls changes application state or displays the expected result.",
        ),
        (
            "автомат" in lower and "тест" in lower or "automated test" in lower,
            "Automated tests cover the main application functions.",
            "The delivered project includes automated tests for the main create, read, update, and persistence behavior where applicable.",
            "Automated tests execute and assert the main application functions.",
        ),
        (
            "настоящ" in lower and "логик" in lower or "real logic" in lower,
            "The interface is backed by real application logic.",
            "User-facing screens are connected to real application state and persistence rather than static mockups.",
            "UI actions are backed by implemented application logic.",
        ),
        (
            ("простым для локального запуска" in lower) or ("local startup" in lower and "future" in lower),
            "The selected technology stack remains simple for local startup and future maintenance.",
            "The delivered project uses a technology approach that can be started locally and extended without unnecessary infrastructure.",
            "The project can be installed and run locally with the documented stack.",
        ),
    ]
    for matched, title, description, expected in patterns:
        if matched:
            return {"title": title, "description": description, "expected": expected}

    cleaned = re.sub(r"^(?:feature\s*:\s*|иметь возможность\s*:?\s*|have the ability to\s*)", "", text, flags=re.IGNORECASE).strip(" .;:")
    if not cleaned:
        cleaned = "requested behavior"
    title = cleaned[:1].upper() + cleaned[1:]
    if not title.endswith(('.', '!', '?')):
        title += "."
    return {
        "title": title[:120],
        "description": f"Requested behavior is implemented as an observable, testable outcome: {text}",
        "expected": "The delivered project provides observable behavior matching this requirement.",
    }


def _criterion_semantic_text(criterion: dict[str, Any]) -> str:
    parts = [
        criterion.get("title", ""),
        criterion.get("description", ""),
        criterion.get("expected_result", ""),
        criterion.get("trace", ""),
    ]
    return "\n".join(str(part) for part in parts if str(part).strip())


def _manual_verifier_plan(criterion_id: str) -> dict[str, Any]:
    return AcceptanceVerifierPlan(criterion_id=criterion_id, verifier_type="manual_or_unsupported").to_dict()


def _semantic_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_]+", text.lower())


def _criterion_is_incomplete(criterion: dict[str, Any], semantic_text: str) -> bool:
    if not str(criterion.get("id") or "").strip():
        return True
    if not semantic_text.strip():
        return True
    tokens = _semantic_tokens(semantic_text)
    if len(tokens) < 4:
        return True
    vague = {"good", "nice", "better", "modern", "works", "done", "complete", "fast", "secure", "robust"}
    return bool(tokens) and set(tokens).issubset(vague)


def _plan(
    criterion_id: str,
    verifier_type: str,
    setup: list[str],
    required_fixtures: list[str],
    actions: list[str],
    assertions: list[str],
    observable_expected_outcomes: list[str],
) -> dict[str, Any]:
    return AcceptanceVerifierPlan(
        criterion_id=criterion_id,
        verifier_type=verifier_type,
        setup=setup,
        required_fixtures=required_fixtures,
        actions=actions,
        assertions=assertions,
        observable_expected_outcomes=observable_expected_outcomes,
    ).to_dict()


def plan_acceptance_verifier(criterion: dict[str, Any]) -> dict[str, Any]:
    criterion_id = str(criterion.get("id") or "").strip()
    semantic_text = _criterion_semantic_text(criterion)
    lower = semantic_text.lower()
    method = str(criterion.get("verification_method") or "").strip()
    if _criterion_is_incomplete(criterion, semantic_text):
        return _manual_verifier_plan(criterion_id)

    if any(token in lower for token in ("persist", "restart", "stopped and started", "after restart", "data remains")):
        return _plan(
            criterion_id,
            "persistence_restart",
            ["start application with persistent storage"],
            ["request or record payload with a unique identifier", "documented restart command or runtime adapter"],
            ["create record", "stop application", "restart application", "retrieve or list records"],
            ["record exists before restart", "same record is observable after restart"],
            ["post-restart read response contains the pre-restart identifier and data"],
        )

    if ("after creation" in lower and "list" in lower) or ("new request" in lower and "appears" in lower and "list" in lower):
        return _plan(
            criterion_id,
            "http_sequence",
            ["start application"],
            ["request payload with a unique client or request identifier"],
            ["create request", "list requests"],
            ["create succeeds", "created identifier appears in list"],
            ["create response reports success and returns or preserves the created identifier", "list response contains the created request"],
        )

    if "search" in lower and any(token in lower for token in ("request", "ticket", "record", "item", "client", "contact", "text")):
        return _plan(
            criterion_id,
            "http_sequence",
            ["start application"],
            ["two request records with distinct searchable values"],
            ["create target request", "create control request", "search requests"],
            ["search succeeds", "target record appears", "control record is excluded"],
            ["search response contains only records matching the search term"],
        )

    if "filter" in lower and any(token in lower for token in ("request", "ticket", "record", "item", "status", "priority")):
        return _plan(
            criterion_id,
            "http_sequence",
            ["start application"],
            ["two request records with distinct filter values"],
            ["create target request", "create control request", "filter requests"],
            ["filter succeeds", "target record appears", "control record is excluded"],
            ["filtered response contains only records matching the requested field value"],
        )

    if any(token in lower for token in ("delete", "remove")) and any(token in lower for token in ("request", "ticket", "record", "item")):
        return _plan(
            criterion_id,
            "http_sequence",
            ["start application"],
            ["existing request record"],
            ["create or seed request", "delete request", "read or list request"],
            ["delete succeeds", "deleted record is no longer observable"],
            ["post-delete read fails or list response excludes the deleted identifier and marker"],
        )

    if "status" in lower and any(token in lower for token in ("change", "update", "set", "поменять", "изменить")):
        return _plan(
            criterion_id,
            "http_sequence",
            ["start application"],
            ["existing request record", "new status value"],
            ["create or seed request", "change request status", "read request"],
            ["status change succeeds", "changed status is observable after update"],
            ["read response contains the changed status for the same request identifier"],
        )

    if any(token in lower for token in ("update", "change", "edit", "open an existing")) and any(token in lower for token in ("request", "ticket", "status", "data")):
        return _plan(
            criterion_id,
            "http_sequence",
            ["start application"],
            ["existing request record", "updated request values"],
            ["create or seed request", "update request", "fetch or list request"],
            ["update succeeds", "updated fields or status are observable after update"],
            ["read response contains the changed values for the same request identifier"],
        )

    if any(token in lower for token in ("read", "open", "view")) and any(token in lower for token in ("existing request", "existing ticket", "request", "ticket", "record", "item")):
        return _plan(
            criterion_id,
            "http_sequence",
            ["start application"],
            ["existing request record"],
            ["create or seed request", "read request"],
            ["read succeeds", "read response contains the same identifier or marker"],
            ["read response returns the created record by its identifier"],
        )

    if "create" in lower and any(token in lower for token in ("request", "ticket", "record", "item")):
        return _plan(
            criterion_id,
            "http_sequence",
            ["start application"],
            ["request payload with a unique client or request identifier"],
            ["create request", "read or list request"],
            ["create succeeds", "created identifier or marker is observable after create"],
            ["created record can be retrieved or appears in the collection response"],
        )

    if any(token in lower for token in ("tablet", "responsive", "viewport", "desktop")):
        return _plan(
            criterion_id,
            "responsive_ui",
            ["start application", "open the primary user interface"],
            ["desktop viewport size", "tablet viewport size", "core screen route or page"],
            ["render core screen at desktop width", "render core screen at tablet width"],
            ["required controls remain visible", "primary workflow remains usable without hidden required actions"],
            ["desktop and tablet captures expose the required controls and content"],
        )

    if method == "runtime_smoke" or "starts successfully" in lower or "start successfully" in lower or "documented run command" in lower:
        return _plan(
            criterion_id,
            "runtime_start",
            ["prepare documented runtime command"],
            ["documented run command", "expected local port, health endpoint, or landing page"],
            ["start application", "probe observable endpoint or page", "stop owned process"],
            ["process starts", "observable endpoint or page responds", "owned process stops cleanly"],
            ["runtime probe returns a successful response before shutdown"],
        )

    if method in ("file_check", "static_scan", "secret_scan", "file_and_secret_check", "static_asset_check") or any(token in lower for token in ("readme", "artifact", "file exists", "documented safely")):
        return _plan(
            criterion_id,
            "file_artifact",
            [],
            ["declared artifact paths and required documentation content"],
            ["inspect required artifact paths", "inspect required file content when specified"],
            ["required files exist", "required observable file content is present"],
            ["filesystem inspection reports the required artifact paths and content"],
        )

    if method == "command" or any(token in lower for token in ("build succeeds", "test command", "exit with code", "automated tests")):
        return _plan(
            criterion_id,
            "command",
            ["prepare project dependencies required by the command"],
            ["deterministic command", "expected exit code"],
            ["run configured command"],
            ["command exits with the expected code", "command output contains no blocking error"],
            ["captured exit code and output match the command expectation"],
        )

    if method == "python_import" or "imports successfully" in lower or "can be imported" in lower:
        return _plan(
            criterion_id,
            "python_import",
            ["prepare Python import path"],
            ["target module name"],
            ["import target module in an isolated Python process"],
            ["module imports without exception"],
            ["import process exits successfully and reports the imported module"],
        )

    if any(token in lower for token in ("health endpoint", "landing page", "browser-accessible", "home page", "summary counts")):
        return _plan(
            criterion_id,
            "http_single",
            ["start application"],
            ["target URL or route", "expected response status and response content"],
            ["request target URL once"],
            ["response status is successful", "response exposes the expected content"],
            ["single HTTP response contains the expected status and content"],
        )

    return _manual_verifier_plan(criterion_id)


def generate_acceptance_criteria(project_spec: dict) -> list[dict[str, Any]]:
    criteria = []

    def add(title: str, description: str, priority: str, source: str, method: str, expected: str, trace: str = "", requirement_ids: list[str] | None = None):
        criterion = {
            "id": _stable_id("AC", len(criteria) + 1),
            "title": title,
            "description": description,
            "priority": priority,
            "source": source,
            "verification_method": method,
            "expected_result": expected,
            "requirement_ids": requirement_ids or [],
            "status": "pending",
            "evidence": [],
            "trace": trace,
        }
        criterion["verifier_plan"] = plan_acceptance_verifier(criterion)
        criteria.append(criterion)

    add("Project has delivery documentation", "Root documentation explains installation, run, and test commands.", "high", "system_safety", "file_check", "README.md exists and contains install/run/test guidance")
    add("Project has no placeholder implementation", "Required functionality is implemented, not replaced by TODO/pass/stub code.", "high", "system_safety", "static_scan", "No blocking placeholder patterns in source files")

    requirements = mandatory_user_requirements(project_spec)
    if not requirements:
        requirements = _user_requirements(project_spec.get("required_features", []))
    for requirement in requirements:
        feature = str(requirement.get("description", ""))
        statement = _acceptance_statement(feature)
        add(
            statement["title"],
            statement["description"],
            "high",
            "user_requirement",
            "feature_trace_static_or_smoke",
            statement["expected"],
            trace=feature,
            requirement_ids=[requirement["id"]],
        )

    profiles = project_spec.get("project_profiles", [])
    if "fastapi" in profiles:
        add("FastAPI application imports", "FastAPI app module imports successfully.", "high", "project_profile", "python_import", "Expected ASGI app can be imported")
        add("FastAPI runtime starts", "Application starts with python -m uvicorn on a free local port.", "high", "project_profile", "runtime_smoke", "Process stays up long enough to serve HTTP")
    if "telegram_bot" in profiles:
        add("Telegram bot configuration is safe", "Bot documents token requirements without committing a real token.", "critical", "project_profile", "file_and_secret_check", ".env.example documents BOT_TOKEN and contains no real-looking token")
        add("Telegram command handlers smoke test", "Core bot commands and callback data are importable and safe.", "high", "project_profile", "telegram_smoke", "/start/key commands import without exception; callback_data <= 64 bytes")
    if "vite_frontend" in profiles or "react_frontend" in profiles:
        add("Frontend production build succeeds", "Frontend builds in production mode using configured npm scripts.", "high", "project_profile", "command", "npm install and npm run build exit with code 0")
    if "static_website" in profiles:
        add("Static website entry page exists", "Static website has an entry HTML file and local references resolve.", "high", "project_profile", "static_asset_check", "index.html exists and referenced local files are present")
    if project_spec.get("required_credentials"):
        add("Credentials are documented safely", "All required credentials are represented in docs/env examples without real secrets.", "critical", "inferred_requirement", "secret_scan", ".env.example exists, .gitignore excludes .env, and no real secrets are committed")

    return criteria


def generate_qa_plan(project_spec: dict, profiles: list[str], acceptance_criteria: list[dict[str, Any]]) -> dict[str, Any]:
    levels = []

    def level(level_id: str, title: str, checks: list[str]):
        levels.append({"level": level_id, "title": title, "checks": checks, "evidence": []})

    level("A", "Delivery readiness", [
        "README/install/run/test instructions",
        "dependency files",
        "expected entrypoint",
        ".env.example and .gitignore when credentials are required",
        "secret scan and placeholder scan",
    ])
    level("B", "Static checks", ["Python syntax/import/compileall when Python is present", "Node build/type checks when configured"])
    level("C", "Dependencies", ["Python requirements verification", "Node npm install/build when package.json exists"])
    level("D", "Automated tests", ["pytest/unittest/npm test/vitest/jest when configured", "capture command, cwd, exit code, output, duration"])
    level("E", "Runtime smoke tests", ["start runnable projects on a free port", "verify expected endpoint/page", "stop only owned process"])

    profile_checks = []
    if "telegram_bot" in profiles:
        profile_checks.extend(["Telegram imports", "handler registration", "/start", "callback length", "database behavior", "token safety"])
    if "fastapi" in profiles:
        profile_checks.extend(["FastAPI import", "application startup", "important endpoints", "invalid input behavior"])
    if "vite_frontend" in profiles or "react_frontend" in profiles:
        profile_checks.extend(["npm install", "production build", "expected frontend files"])
    if "static_website" in profiles:
        profile_checks.extend(["entry HTML", "referenced local resources", "broken internal paths"])
    if not profile_checks:
        profile_checks.append("generic project smoke/readiness checks")
    level("F", "Profile-specific tests", profile_checks)

    return {
        "profiles": profiles,
        "levels": levels,
        "acceptance_criteria_ids": [c["id"] for c in acceptance_criteria],
        "orphan_mandatory_requirement_ids": [r["id"] for r in orphan_mandatory_requirements(project_spec, acceptance_criteria)],
        "expected_entrypoint": project_spec.get("expected_entrypoint", ""),
        "install_command": project_spec.get("installation_method", ""),
        "run_command": project_spec.get("run_method", ""),
        "test_command": project_spec.get("test_method", ""),
    }


def ensure_project_spec_bundle(project: dict, project_path: str | None = None) -> dict[str, Any]:
    spec = build_project_spec(project, existing_path=project_path)
    profiles = detect_project_profiles(spec, project_path=project_path)
    spec["project_profiles"] = profiles
    spec["project_type"] = profiles[0] if profiles else "generic"
    criteria = generate_acceptance_criteria(spec)
    qa_plan = generate_qa_plan(spec, profiles, criteria)
    bundle = {
        "project_spec": spec,
        "project_profiles": profiles,
        "acceptance_criteria": criteria,
        "qa_plan": qa_plan,
        "traceability": {"orphan_mandatory_requirement_ids": qa_plan["orphan_mandatory_requirement_ids"]},
    }
    project.update(bundle)
    ensure_acceptance_evidence_history(project)
    return bundle


def acceptance_summary(project: dict) -> str:
    criteria = project.get("acceptance_criteria", [])
    lines = []
    for c in criteria:
        reqs = ",".join(c.get("requirement_ids", [])) or "none"
        lines.append(f"{c.get('id')}: {c.get('title')} | REQs: {reqs} | Verify: {c.get('verification_method')} | Expected: {c.get('expected_result')}")
    return "\n".join(lines)


PRODUCT_JUDGE_INPUT_FIELDS = (
    "original_request",
    "requirement_graph",
    "acceptance_criteria",
    "evidence",
    "open_issues",
    "qa_summary",
    "runtime_evidence",
    "security_review_summaries",
    "known_limitations",
)


def _open_issues(project: dict) -> list[dict[str, Any]]:
    return [issue for issue in project.get("issues", []) if isinstance(issue, dict) and str(issue.get("status", "open")).lower() == "open"]


def _review_summaries(project: dict) -> dict[str, list[dict[str, Any]]]:
    summaries = {"bugcatcher": [], "sentinel": [], "lupa": []}
    for agent_id in summaries:
        prefix = f"{agent_id}_review_v"
        for key in sorted(k for k in project if k.startswith(prefix)):
            value = project.get(key)
            if value:
                try:
                    iteration = int(key.rsplit("v", 1)[1])
                except (IndexError, ValueError):
                    iteration = None
                summaries[agent_id].append({"iteration": iteration, "summary": str(value)})
    return summaries


def _qa_summary(project: dict, qa_result: dict[str, Any] | None) -> dict[str, Any]:
    source = qa_result or project.get("qa_result") or project.get("latest_qa_result") or {}
    return {
        "success": bool(source.get("success")),
        "rounds_completed": source.get("rounds_completed"),
        "total_errors": source.get("total_errors"),
        "errors": source.get("errors", []),
        "needs_credentials": bool(source.get("needs_credentials")),
        "needs_human_input": bool(source.get("needs_human_input")),
        "policy_groups": source.get("policy_groups", []),
        "repair_history": source.get("repair_history", []),
    }


def build_product_judge_input(project: dict, qa_result: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = project.get("project_spec", {}) if isinstance(project.get("project_spec"), dict) else {}
    report = project.get("final_delivery_report", {}) if isinstance(project.get("final_delivery_report"), dict) else {}
    ensure_acceptance_evidence_history(project)
    bundle = {
        "original_request": spec.get("original_user_request") or _original_request(project),
        "requirement_graph": requirement_graph(spec),
        "acceptance_criteria": project.get("acceptance_criteria", []),
        "evidence": project.get(ACCEPTANCE_EVIDENCE_HISTORY_KEY, {}),
        "open_issues": _open_issues(project),
        "qa_summary": _qa_summary(project, qa_result),
        "runtime_evidence": report.get("runtime_verification", {}),
        "security_review_summaries": _review_summaries(project),
        "known_limitations": report.get("known_limitations", spec.get("risks", [])),
    }
    return {field: bundle[field] for field in PRODUCT_JUDGE_INPUT_FIELDS}


def record_acceptance_evidence(project: dict, criterion_id: str, status: str, evidence: dict[str, Any]) -> None:
    history = ensure_acceptance_evidence_history(project)
    for criterion in project.get("acceptance_criteria", []):
        if criterion.get("id") == criterion_id:
            criterion["status"] = status
            entry = normalize_acceptance_evidence(criterion_id, status, evidence)
            history.setdefault(criterion_id, []).append(entry)
            criterion["evidence"] = history[criterion_id]
            try:
                append_evidence_record(project, criterion, entry)
            except Exception as exc:
                project.setdefault("persistence_errors", []).append(f"acceptance_evidence:{criterion_id}:{exc}")
            return
