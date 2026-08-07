import project_state
from quality_profiles import LEVEL_2_BUILD, LEVEL_3_RUNTIME, LEVEL_4_INTERACTION, LEVEL_6_NATIVE_RUNTIME, LEVEL_7_PACKAGED_ARTIFACT, default_quality_settings, derive_target_requirements, ensure_quality_settings, evaluate_quality_completion, target_required_evidence_level


def test_explicit_platform_scope_excludes_other_os_targets_from_boilerplate_text():
    spec = {
        "requested_target_platforms": ["desktop", "Windows", "web"],
        "project_profiles": ["react_frontend", "vite_frontend"],
        "description": (
            "Installer targets are configured through electron-builder for Windows NSIS/MSI, "
            "macOS DMG, and Linux AppImage/deb. Critical scenarios must be tested."
        ),
    }
    targets = derive_target_requirements(spec, "strict_mvp")

    assert targets["desktop_windows"] == "required"
    assert targets["desktop_macos"] == "not_applicable"
    assert targets["desktop_linux"] == "not_applicable"
    assert targets["ios"] == "not_applicable"


def test_unscoped_text_still_falls_back_to_keyword_detection():
    spec = {
        "requested_target_platforms": [],
        "project_profiles": [],
        "description": "Build a cross platform desktop app for windows and linux users.",
    }
    targets = derive_target_requirements(spec, "strict_mvp")

    assert targets["desktop_windows"] == "required"
    assert targets["desktop_linux"] == "required"


def _checks(*extra):
    base = [
        {"name": "required_files", "status": "passed", "evidence": {}},
        {"name": "readme_instructions", "status": "passed", "evidence": {}},
        {"name": "runtime_smoke", "status": "passed", "evidence": {}},
        {"name": "latest_full_qa", "status": "passed", "evidence": {}},
        {"name": "acceptance_criteria", "status": "passed", "evidence": {}},
        {"name": "secret_scan", "status": "passed", "evidence": {}},
        {"name": "open_blocking_issues", "status": "passed", "evidence": {}},
        {"name": "target:backend", "status": "passed", "evidence": {"achieved_evidence_level": LEVEL_3_RUNTIME}},
        {"name": "target:api_service", "status": "passed", "evidence": {"achieved_evidence_level": LEVEL_3_RUNTIME}},
        {"name": "target:web", "status": "passed", "evidence": {"achieved_evidence_level": LEVEL_3_RUNTIME}},
    ]
    by_name = {item["name"]: item for item in base}
    for item in extra:
        by_name[item["name"]] = item
    return list(by_name.values())


def _project(profile="strict_mvp", targets=None):
    project = {
        "project_id": "quality-test",
        "title": "Quality Test",
        "description": "Web app with login roles and workflow persistence.",
        "quality_profile": profile,
        "project_mode": profile,
        "project_spec": {"quality_profile": profile, "project_profiles": ["fastapi"], "requested_target_platforms": []},
        "project_profiles": ["fastapi"],
        "acceptance_criteria": [
            {"id": "AC-PERSIST", "title": "Data persists after restart", "priority": "high", "status": "passed"},
            {"id": "AC-WORKFLOW", "title": "Real E2E workflow passes", "priority": "high", "status": "passed"},
            {"id": "AC-RBAC", "title": "Authentication and RBAC roles pass", "priority": "high", "status": "passed"},
        ],
        "issues": [],
        "logs": [],
    }
    settings = ensure_quality_settings(project)
    if targets is not None:
        settings["target_requirements"] = {target: state for target, state in targets.items()}
        settings["required_targets"] = [target for target, state in targets.items() if state == "required"]
        settings["optional_targets"] = [target for target, state in targets.items() if state == "optional"]
    return project


def test_prototype_can_finish_with_optional_missing_features():
    project = _project("prototype", {"web": "required", "ios": "optional"})
    result = evaluate_quality_completion(project, _checks({"name": "target:web", "status": "passed", "evidence": {}}, {"name": "target:ios", "status": "optional_missing", "evidence": {}}))

    assert result["accepted"] is True
    assert result["final_status"] == "PROTOTYPE_VALIDATED"


def test_prototype_cannot_be_called_strict_mvp():
    project = _project("prototype")
    result = evaluate_quality_completion(project, _checks())

    assert result["quality_profile"] == "prototype"
    assert result["final_status"] != "STRICT_MVP_ACCEPTED"


def test_strict_mvp_blocks_on_mandatory_not_verified():
    project = _project("strict_mvp")
    result = evaluate_quality_completion(project, _checks({"name": "acceptance_criteria", "status": "failed", "evidence": {"failed_mandatory": ["AC-1"]}}))

    assert result["accepted"] is False
    assert "mandatory_criteria_not_verified" in result["blockers"]


def test_strict_mvp_requires_restart_persistence_when_enabled():
    project = _project("strict_mvp")
    project["acceptance_criteria"] = [c for c in project["acceptance_criteria"] if c["id"] != "AC-PERSIST"]
    result = evaluate_quality_completion(project, _checks())

    assert result["accepted"] is False
    assert "restart_persistence_not_verified" in result["blockers"]


def test_production_candidate_requires_architecture_security_and_packaging_gates():
    project = _project("production_candidate")
    result = evaluate_quality_completion(project, _checks())

    assert result["accepted"] is False
    assert "architecture_review_not_passed" in result["blockers"]
    assert "expanded_security_baseline_not_passed" in result["blockers"]
    assert "packaged_artifact_not_verified" in result["blockers"]


def test_required_target_missing_blocks_completion():
    project = _project("strict_mvp", {"android": "required"})
    result = evaluate_quality_completion(project, _checks())

    assert result["accepted"] is False
    assert "required_target_android_missing" in result["blockers"]


def test_optional_target_missing_does_not_block_completion():
    project = _project("strict_mvp", {"ios": "optional"})
    result = evaluate_quality_completion(project, _checks({"name": "target:ios", "status": "optional_missing", "evidence": {}}))

    assert result["accepted"] is True
    assert result["final_status"] == "STRICT_MVP_ACCEPTED"


def test_web_export_cannot_pass_android_target():
    project = _project("strict_mvp", {"android": "required"})
    result = evaluate_quality_completion(project, _checks({"name": "target:android", "status": "not_verified", "evidence": {"required_evidence_level": LEVEL_6_NATIVE_RUNTIME, "achieved_evidence_level": LEVEL_2_BUILD, "reason": "Expo web export only"}}))

    assert result["accepted"] is False
    assert "required_target_android_missing" in result["blockers"]


def test_android_runtime_cannot_pass_ios_target():
    project = _project("strict_mvp", {"ios": "required"})
    result = evaluate_quality_completion(project, _checks({"name": "target:ios", "status": "not_verified", "evidence": {"required_evidence_level": LEVEL_6_NATIVE_RUNTIME, "achieved_evidence_level": LEVEL_6_NATIVE_RUNTIME, "reason": "android runtime evidence belongs to another target"}}))

    assert result["accepted"] is False
    assert "required_target_ios_missing" in result["blockers"]


def test_browser_runtime_cannot_pass_desktop_shell_target():
    project = _project("strict_mvp", {"desktop_windows": "required"})
    result = evaluate_quality_completion(project, _checks({"name": "target:desktop_windows", "status": "passed", "evidence": {"required_evidence_level": LEVEL_6_NATIVE_RUNTIME, "achieved_evidence_level": LEVEL_3_RUNTIME}}))

    assert result["accepted"] is False
    assert any("required LEVEL_6_NATIVE_RUNTIME, achieved LEVEL_3_RUNTIME" in blocker for blocker in result["blockers"])


def test_quality_profiles_map_required_levels():
    assert target_required_evidence_level("prototype", "android") == LEVEL_2_BUILD
    assert target_required_evidence_level("strict_mvp", "android") == LEVEL_6_NATIVE_RUNTIME
    assert target_required_evidence_level("strict_mvp", "manager_web") == LEVEL_4_INTERACTION
    assert target_required_evidence_level("production_candidate", "packaged_installer") == LEVEL_7_PACKAGED_ARTIFACT


def test_required_target_level_mismatch_blocks_strict_mvp():
    project = _project("strict_mvp", {"android": "required"})
    result = evaluate_quality_completion(project, _checks({"name": "target:android", "status": "passed", "evidence": {"required_evidence_level": LEVEL_6_NATIVE_RUNTIME, "achieved_evidence_level": LEVEL_2_BUILD}}))

    assert result["accepted"] is False
    assert any("required LEVEL_6_NATIVE_RUNTIME, achieved LEVEL_2_BUILD" in blocker for blocker in result["blockers"])


def test_legacy_mvp_migration_is_conservative():
    settings = default_quality_settings({"project_mode": "mvp", "project_profiles": []})

    assert settings["quality_profile"] == "strict_mvp"
    assert "Legacy mode 'mvp'" in settings["migration_notice"]


def test_final_status_vocabulary_reflects_actual_maturity():
    prototype = evaluate_quality_completion(_project("prototype"), _checks())
    strict = evaluate_quality_completion(_project("strict_mvp"), _checks())
    blocked = evaluate_quality_completion(_project("strict_mvp"), _checks({"name": "runtime_smoke", "status": "failed", "evidence": {}}))

    assert prototype["final_status"] == "PROTOTYPE_VALIDATED"
    assert strict["final_status"] == "STRICT_MVP_ACCEPTED"
    assert blocked["final_status"] == "BLOCKED"


def test_quality_settings_persist_after_restart(tmp_path):
    (tmp_path / "README.md").write_text("# Demo", encoding="utf-8")
    project = _project("production_candidate", {"backend": "required", "ios": "optional"})
    project.update({"target_path": str(tmp_path), "status": "created"})
    project_state.persist_project_state(project)

    state, error = project_state.load_project_state(str(tmp_path))

    assert error == ""
    assert state["quality_profile"] == "production_candidate"
    assert state["quality_settings"]["target_requirements"]["backend"] == "required"
    assert state["quality_settings"]["target_requirements"]["ios"] == "optional"
