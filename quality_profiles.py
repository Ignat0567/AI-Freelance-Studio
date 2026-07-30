"""Explicit FreelancerStudio project quality profiles and completion policy."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


QUALITY_PROFILES = ("prototype", "strict_mvp", "production_candidate", "production")
LEGACY_PROFILE_ALIASES = {"mvp": "strict_mvp"}
LEVEL_1_STRUCTURAL = "LEVEL_1_STRUCTURAL"
LEVEL_2_BUILD = "LEVEL_2_BUILD"
LEVEL_3_RUNTIME = "LEVEL_3_RUNTIME"
LEVEL_4_INTERACTION = "LEVEL_4_INTERACTION"
LEVEL_5_E2E = "LEVEL_5_E2E"
LEVEL_6_NATIVE_RUNTIME = "LEVEL_6_NATIVE_RUNTIME"
LEVEL_7_PACKAGED_ARTIFACT = "LEVEL_7_PACKAGED_ARTIFACT"
LEVEL_8_INSTALLED_APPLICATION = "LEVEL_8_INSTALLED_APPLICATION"
EVIDENCE_LEVELS = (
    LEVEL_1_STRUCTURAL,
    LEVEL_2_BUILD,
    LEVEL_3_RUNTIME,
    LEVEL_4_INTERACTION,
    LEVEL_5_E2E,
    LEVEL_6_NATIVE_RUNTIME,
    LEVEL_7_PACKAGED_ARTIFACT,
    LEVEL_8_INSTALLED_APPLICATION,
)
_EVIDENCE_LEVEL_RANK = {level: index + 1 for index, level in enumerate(EVIDENCE_LEVELS)}
_LEGACY_EVIDENCE_LEVEL_ALIASES = {
    "LEVEL_1_HTTP_HTML_STRUCTURAL_EVIDENCE": LEVEL_1_STRUCTURAL,
    "LEVEL_2_REAL_RUNTIME": LEVEL_3_RUNTIME,
    "LEVEL_2_REAL_UI_RUNTIME": LEVEL_3_RUNTIME,
    "LEVEL_2_REAL_BROWSER_EVIDENCE": LEVEL_3_RUNTIME,
    "LEVEL_3_REAL_INTERACTION": LEVEL_4_INTERACTION,
    "LEVEL_4_LAYOUT_AND_DISPLAY_EVIDENCE": LEVEL_4_INTERACTION,
    "LEVEL_5_PACKAGED_DELIVERY": LEVEL_7_PACKAGED_ARTIFACT,
    "direct": LEVEL_3_RUNTIME,
    "unverified": "",
}
TARGET_STATES = ("required", "optional", "not_applicable")
SUPPORTED_TARGETS = (
    "backend",
    "web",
    "manager_web",
    "android",
    "ios",
    "desktop_windows",
    "desktop_macos",
    "desktop_linux",
    "api_service",
    "telegram_bot",
    "packaged_installer",
)
ACCEPTED_FINAL_STATUS = {
    "prototype": "PROTOTYPE_VALIDATED",
    "strict_mvp": "STRICT_MVP_ACCEPTED",
    "production_candidate": "PRODUCTION_CANDIDATE_ACCEPTED",
    "production": "PRODUCTION_RELEASE_ACCEPTED",
}
MILESTONE_STATUSES = (
    "SOURCE_GENERATED",
    "BUILD_PASSED",
    "RUNTIME_VERIFIED",
    "CORE_E2E_PASSED",
    "PROTOTYPE_VALIDATED",
    "STRICT_MVP_ACCEPTED",
    "PRODUCTION_CANDIDATE_ACCEPTED",
    "PRODUCTION_RELEASE_ACCEPTED",
    "BLOCKED",
    "FAILED",
)


def normalize_evidence_level(value: Any) -> str:
    raw = str(value or "").strip()
    return _LEGACY_EVIDENCE_LEVEL_ALIASES.get(raw, raw if raw in _EVIDENCE_LEVEL_RANK else "")


def evidence_level_rank(value: Any) -> int:
    return _EVIDENCE_LEVEL_RANK.get(normalize_evidence_level(value), 0)


def evidence_level_satisfies(achieved: Any, required: Any) -> bool:
    required_level = normalize_evidence_level(required)
    if not required_level:
        return True
    return evidence_level_rank(achieved) >= evidence_level_rank(required_level)


def target_required_evidence_level(quality_profile: str, target: str) -> str:
    profile = normalize_quality_profile(quality_profile)[0]
    target = str(target or "").lower()
    if target in {"android", "ios", "desktop_windows", "desktop_macos", "desktop_linux"}:
        if profile == "prototype":
            return LEVEL_2_BUILD
        return LEVEL_6_NATIVE_RUNTIME
    if target == "packaged_installer":
        return LEVEL_7_PACKAGED_ARTIFACT if profile in {"production_candidate", "production"} else LEVEL_2_BUILD
    if target == "manager_web":
        return LEVEL_4_INTERACTION if profile != "prototype" else LEVEL_3_RUNTIME
    if target in {"backend", "api_service", "web", "telegram_bot"}:
        return LEVEL_3_RUNTIME
    return LEVEL_1_STRUCTURAL


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class ProjectQualitySettings:
    quality_profile: str = "strict_mvp"
    required_targets: list[str] = field(default_factory=list)
    optional_targets: list[str] = field(default_factory=list)
    target_requirements: dict[str, str] = field(default_factory=dict)
    block_on_mandatory_not_verified: bool = True
    require_restart_persistence: bool = True
    require_real_e2e: bool = True
    require_rbac_matrix: bool = True
    require_security_baseline: bool = True
    require_architecture_review: bool = False
    require_product_judge: bool = True
    require_native_runtime: bool = False
    require_packaged_artifact: bool = False
    architecture_policy: dict[str, Any] = field(default_factory=dict)
    migration_notice: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def architecture_policy_for_profile(quality_profile: str) -> dict[str, Any]:
    profile = normalize_quality_profile(quality_profile)[0]
    defaults = {
        "max_recommended_file_lines": 800,
        "max_blocking_file_lines": 0,
        "separation_of_concerns_required": False,
        "domain_modules_required": False,
        "migrations_required": False,
        "typed_schema_required": False,
        "repository_service_layer_preferred": False,
        "no_placeholder_main_ui": True,
        "no_hardcoded_demo_data_in_production_paths": False,
        "no_single_file_multiplatform_application": False,
    }
    if profile == "strict_mvp":
        defaults.update({
            "max_recommended_file_lines": 650,
            "max_blocking_file_lines": 1200,
            "separation_of_concerns_required": True,
            "domain_modules_required": True,
            "typed_schema_required": True,
            "repository_service_layer_preferred": True,
            "no_hardcoded_demo_data_in_production_paths": True,
            "no_single_file_multiplatform_application": True,
        })
    elif profile in {"production_candidate", "production"}:
        defaults.update({
            "max_recommended_file_lines": 500,
            "max_blocking_file_lines": 900,
            "separation_of_concerns_required": True,
            "domain_modules_required": True,
            "migrations_required": True,
            "typed_schema_required": True,
            "repository_service_layer_preferred": True,
            "no_hardcoded_demo_data_in_production_paths": True,
            "no_single_file_multiplatform_application": True,
        })
    return defaults


def normalize_quality_profile(value: Any, legacy_mode: Any = None) -> tuple[str, str]:
    raw = str(value or "").strip().lower()
    if raw in QUALITY_PROFILES:
        return raw, ""
    legacy = str(legacy_mode or raw or "").strip().lower()
    if legacy in LEGACY_PROFILE_ALIASES:
        return LEGACY_PROFILE_ALIASES[legacy], "Legacy mode 'mvp' was conservatively interpreted as strict_mvp."
    if legacy == "prototype":
        return "prototype", ""
    return "strict_mvp", "Missing quality_profile was conservatively interpreted as strict_mvp."


def _text(project_or_spec: dict[str, Any]) -> str:
    parts = [
        project_or_spec.get("title", ""),
        project_or_spec.get("description", ""),
        project_or_spec.get("original_request", ""),
        project_or_spec.get("original_user_request", ""),
        project_or_spec.get("project_goal", ""),
        " ".join(str(item) for item in project_or_spec.get("requested_target_platforms", []) if item),
        " ".join(str(item) for item in project_or_spec.get("project_profiles", []) if item),
        " ".join(str(item) for item in project_or_spec.get("required_features", []) if item),
    ]
    return "\n".join(part for part in parts if part).lower()


def derive_target_requirements(project_or_spec: dict[str, Any], quality_profile: str) -> dict[str, str]:
    text = _text(project_or_spec)
    profiles = {str(item).lower() for item in project_or_spec.get("project_profiles", []) if item}
    requested = {str(item).lower() for item in project_or_spec.get("requested_target_platforms", []) if item}
    targets = {target: "not_applicable" for target in SUPPORTED_TARGETS}

    def set_target(name: str, state: str = "required") -> None:
        targets[name] = state

    if any(token in profiles for token in ("fastapi", "rest_api", "node_backend")) or any(token in text for token in ("backend", "api", "server")):
        set_target("backend")
        set_target("api_service")
    if any(token in profiles for token in ("react_frontend", "vite_frontend", "static_website")) or any(token in text for token in ("web", "website", "dashboard", "frontend")):
        set_target("web")
    if "manager" in text or "admin dashboard" in text or "manager_web" in text:
        set_target("manager_web")
    if "telegram_bot" in profiles or "telegram" in text:
        set_target("telegram_bot")
    # An explicit requested_target_platforms scope is authoritative for OS/device targets.
    # Free-text scanning over the full spec must not override a narrower platform scope the
    # user explicitly asked for -- specs routinely mention other OSes in generic cross-platform
    # boilerplate (e.g. electron-builder's mac/linux installer targets, or the word "scenarios"
    # containing the substring "ios") even for single-platform projects.
    os_platform_targets = {
        "android": "android",
        "ios": "ios",
        "iphone": "ios",
        "windows": "desktop_windows",
        "macos": "desktop_macos",
        "mac": "desktop_macos",
        "linux": "desktop_linux",
    }
    explicit_os_targets = {os_platform_targets[token] for token in requested if token in os_platform_targets}
    if explicit_os_targets:
        for target_name in explicit_os_targets:
            set_target(target_name)
    else:
        if "android" in text:
            set_target("android")
        if re.search(r"\bios\b", text) or "iphone" in text:
            set_target("ios")
        if "windows" in text:
            set_target("desktop_windows")
        if "macos" in text or re.search(r"\bmac\b", text):
            set_target("desktop_macos")
        if "linux" in text:
            set_target("desktop_linux")
    if any(token in text for token in ("installer", "package", "packaged artifact")):
        set_target("packaged_installer", "required" if quality_profile in {"production_candidate", "production"} else "optional")

    if quality_profile == "prototype":
        for name, state in list(targets.items()):
            if state == "required" and name not in {"backend", "web", "api_service", "telegram_bot"}:
                targets[name] = "optional"

    return targets


def default_quality_settings(project_or_spec: dict[str, Any] | None = None, profile: Any = None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    source = project_or_spec or {}
    previous = previous or {}
    selected, notice = normalize_quality_profile(profile or source.get("quality_profile") or previous.get("quality_profile"), source.get("project_mode") or previous.get("project_mode"))
    now = utc_now()
    targets = derive_target_requirements(source, selected)
    required = [name for name, state in targets.items() if state == "required"]
    optional = [name for name, state in targets.items() if state == "optional"]
    settings = ProjectQualitySettings(
        quality_profile=selected,
        required_targets=required,
        optional_targets=optional,
        target_requirements=targets,
        block_on_mandatory_not_verified=selected != "prototype",
        require_restart_persistence=selected != "prototype",
        require_real_e2e=selected != "prototype",
        require_rbac_matrix=selected != "prototype",
        require_security_baseline=selected != "prototype",
        require_architecture_review=selected in {"production_candidate", "production"},
        require_product_judge=selected != "prototype",
        require_native_runtime=any(targets.get(name) == "required" for name in ("android", "ios")),
        require_packaged_artifact=selected in {"production_candidate", "production"} or targets.get("packaged_installer") == "required",
        architecture_policy=architecture_policy_for_profile(selected),
        migration_notice=notice or str(previous.get("migration_notice") or ""),
        created_at=str(previous.get("created_at") or now),
        updated_at=now,
    ).to_dict()
    for key, value in previous.items():
        if key in settings and key not in {"updated_at", "quality_profile", "migration_notice"}:
            settings[key] = value
    settings["quality_profile"] = selected
    settings["migration_notice"] = notice or settings.get("migration_notice", "")
    return settings


def ensure_quality_settings(project: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = project.get("project_spec") if isinstance(project.get("project_spec"), dict) else {}
    source = {**spec, **{key: project.get(key) for key in ("title", "description", "original_request", "project_mode", "quality_profile")}}
    existing = project.get("quality_settings") if isinstance(project.get("quality_settings"), dict) else previous
    settings = default_quality_settings(source, project.get("quality_profile") or spec.get("quality_profile"), existing)
    project["quality_profile"] = settings["quality_profile"]
    project["quality_settings"] = settings
    if spec is not None:
        spec["quality_profile"] = settings["quality_profile"]
        spec["quality_settings"] = settings
    return settings


def target_check_name(target: str) -> str:
    return f"target:{target}"


def _check(checks: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for check in checks:
        if check.get("name") == name:
            return check
    return None


def _check_status(checks: list[dict[str, Any]], name: str) -> str:
    check = _check(checks, name)
    if check:
        return str(check.get("status") or "").lower()
    return ""


def _check_achieved_level(check: dict[str, Any] | None) -> str:
    if not check:
        return ""
    evidence = check.get("evidence") if isinstance(check.get("evidence"), dict) else {}
    return normalize_evidence_level(
        check.get("achieved_evidence_level")
        or evidence.get("achieved_evidence_level")
        or evidence.get("evidence_level")
    )


def _has_acceptance_status(project: dict[str, Any], needle: str) -> bool:
    for criterion in project.get("acceptance_criteria", []):
        if not isinstance(criterion, dict):
            continue
        text = " ".join(str(criterion.get(key, "")) for key in ("title", "description", "verification_method")).lower()
        if needle in text and str(criterion.get("status") or "").lower() == "passed":
            return True
    return False


def evaluate_quality_completion(project: dict[str, Any], checks: list[dict[str, Any]], *, blocked_by_credentials: bool = False) -> dict[str, Any]:
    settings = ensure_quality_settings(project)
    profile = settings["quality_profile"]
    blockers: list[str] = []
    warnings: list[str] = []
    failed_checks = {str(check.get("name")): check for check in checks if check.get("status") == "failed"}
    runtime_ok = _check_status(checks, "runtime_smoke") == "passed"
    qa_ok = _check_status(checks, "latest_full_qa") == "passed"
    files_ok = _check_status(checks, "required_files") == "passed"
    docs_ok = _check_status(checks, "readme_instructions") == "passed"
    acceptance_ok = _check_status(checks, "acceptance_criteria") == "passed"
    security_ok = _check_status(checks, "secret_scan") == "passed" and _check_status(checks, "open_blocking_issues") == "passed"

    if blocked_by_credentials:
        blockers.append("required_credentials_missing")
    if not files_ok:
        blockers.append("required_source_or_artifact_missing")
    if not runtime_ok:
        blockers.append("runtime_not_verified")
    if not docs_ok:
        blockers.append("startup_or_deployment_instructions_not_verified")

    if profile == "prototype":
        architecture_failed = failed_checks.get("architecture_review")
        architecture_evidence = architecture_failed.get("evidence", {}) if architecture_failed else {}
        if any(str(item.get("severity", "")).lower() == "critical" for item in architecture_evidence.get("blocking_findings", []) if isinstance(item, dict)):
            blockers.append("critical_architecture_issue_open")
        if failed_checks.get("open_blocking_issues"):
            blockers.append("critical_blocking_issue_open")
        if not qa_ok:
            warnings.append("automated_test_coverage_limited")
        final_status = "PROTOTYPE_VALIDATED" if not blockers else "BLOCKED"
        return {"accepted": not blockers, "final_status": final_status, "blockers": blockers, "warnings": warnings, "quality_profile": profile}

    if not qa_ok:
        blockers.append("full_qa_not_passed")
    if _check_status(checks, "architecture_review") == "failed":
        blockers.append("architecture_review_not_passed")
    if _check_status(checks, "feature_completeness_matrix") == "failed":
        blockers.append("mandatory_feature_incomplete")
    if _check_status(checks, "mandatory_test_matrices") == "failed":
        blockers.append("mandatory_test_matrix_incomplete")
    if settings.get("block_on_mandatory_not_verified") and not acceptance_ok:
        blockers.append("mandatory_criteria_not_verified")
    if settings.get("require_security_baseline") and not security_ok:
        blockers.append("security_baseline_not_passed")
    if settings.get("require_restart_persistence") and not _has_acceptance_status(project, "persist"):
        blockers.append("restart_persistence_not_verified")
    if settings.get("require_real_e2e") and not _has_acceptance_status(project, "workflow") and not _has_acceptance_status(project, "e2e"):
        blockers.append("real_e2e_not_verified")
    if settings.get("require_rbac_matrix") and any(token in _text(project) for token in ("auth", "login", "role", "rbac", "manager", "admin")) and not (_has_acceptance_status(project, "rbac") or _has_acceptance_status(project, "role") or _has_acceptance_status(project, "auth")):
        blockers.append("authentication_rbac_not_verified")
    for target in settings.get("required_targets", []):
        required_level = target_required_evidence_level(profile, target)
        check = _check(checks, target_check_name(target))
        status = str((check or {}).get("status") or "").lower()
        achieved_level = _check_achieved_level(check)
        if status in {"", "failed", "not_applicable", "not_verified"}:
            blockers.append(f"required_target_{target}_missing")
        elif not evidence_level_satisfies(achieved_level, required_level):
            blockers.append(f"required_target_{target}_not_verified: required {required_level}, achieved {achieved_level or 'none'}")

    if profile in {"production_candidate", "production"}:
        if settings.get("require_architecture_review") and _check_status(checks, "architecture_review") != "passed":
            blockers.append("architecture_review_not_passed")
        if settings.get("require_security_baseline") and _check_status(checks, "expanded_security_baseline") != "passed":
            blockers.append("expanded_security_baseline_not_passed")
        if settings.get("require_packaged_artifact") and _check_status(checks, "packaged_artifact") != "passed":
            blockers.append("packaged_artifact_not_verified")
    if profile == "production":
        for gate in ("production_deployment", "operational_monitoring", "backup_recovery", "secrets_management", "performance_validation", "release_artifact_validation", "approved_audits"):
            if _check_status(checks, gate) != "passed":
                blockers.append(f"{gate}_not_verified")

    unique_blockers = list(dict.fromkeys(blockers))
    return {
        "accepted": not unique_blockers,
        "final_status": ACCEPTED_FINAL_STATUS[profile] if not unique_blockers else "BLOCKED",
        "blockers": unique_blockers,
        "warnings": warnings,
        "quality_profile": profile,
    }


def milestone_from_checks(checks: list[dict[str, Any]], accepted_status: str) -> str:
    if accepted_status not in {"BLOCKED", "FAILED"}:
        return accepted_status
    if _check_status(checks, "runtime_smoke") == "passed" and _check_status(checks, "acceptance_criteria") == "passed":
        return "CORE_E2E_PASSED"
    if _check_status(checks, "runtime_smoke") == "passed":
        return "RUNTIME_VERIFIED"
    if _check_status(checks, "latest_full_qa") == "passed":
        return "BUILD_PASSED"
    if _check_status(checks, "required_files") == "passed":
        return "SOURCE_GENERATED"
    return accepted_status


def evidence_maturity_badges(project: dict[str, Any], checks: list[dict[str, Any]], final_status: str) -> dict[str, dict[str, Any]]:
    settings = ensure_quality_settings(project)
    targets = project.get("target_verification_status") if isinstance(project.get("target_verification_status"), dict) else {}
    required_targets = [target for target in settings.get("required_targets", [])]
    platform_ok = all((targets.get(target) or {}).get("verdict") == "passed" for target in required_targets)
    qa_ok = _check_status(checks, "latest_full_qa") == "passed"
    runtime_ok = _check_status(checks, "runtime_smoke") == "passed"
    core_ok = runtime_ok and _check_status(checks, "acceptance_criteria") == "passed"
    strict_ok = final_status == "STRICT_MVP_ACCEPTED"
    production_ready = final_status in {"PRODUCTION_CANDIDATE_ACCEPTED", "PRODUCTION_RELEASE_ACCEPTED"}
    return {
        "build": {"label": "Build", "status": "passed" if qa_ok else "not_verified", "source_check": "latest_full_qa"},
        "runtime": {"label": "Runtime", "status": "passed" if runtime_ok else "not_verified", "source_check": "runtime_smoke"},
        "core_e2e": {"label": "Core E2E", "status": "passed" if core_ok else "not_verified", "source_checks": ["runtime_smoke", "acceptance_criteria"]},
        "platform_verification": {"label": "Platform verification", "status": "passed" if platform_ok else "not_verified", "required_targets": required_targets},
        "strict_mvp": {"label": "Strict MVP", "status": "passed" if strict_ok else "incomplete", "accepted_status": "STRICT_MVP_ACCEPTED"},
        "production_readiness": {"label": "Production readiness", "status": "passed" if production_ready else "incomplete", "accepted_statuses": ["PRODUCTION_CANDIDATE_ACCEPTED", "PRODUCTION_RELEASE_ACCEPTED"]},
    }
