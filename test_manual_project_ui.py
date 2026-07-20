from pathlib import Path


def test_manual_project_button_is_persistent_in_dashboard():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "Manual Project" in source
    assert "onClick={onNewProject}" in source
    assert "Create a manual project from your own requirements" in source


def test_dashboard_shows_separate_maturity_badges():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    for badge in ["build", "runtime", "core_e2e", "platform_verification", "strict_mvp", "production_readiness"]:
        assert badge in source
    assert "Evidence maturity badges" in source


def test_quality_profile_can_be_selected_in_project_creation():
    source = Path("frontend/src/components/NewProjectModal.jsx").read_text(encoding="utf-8")
    assert "Project Quality" in source
    assert "value={qualityProfile}" in source
    for profile in ["prototype", "strict_mvp", "production_candidate", "production"]:
        assert profile in source


def test_required_and_optional_targets_are_visible_and_submitted():
    modal = Path("frontend/src/components/NewProjectModal.jsx").read_text(encoding="utf-8")
    app = Path("frontend/src/App.jsx").read_text(encoding="utf-8")
    for target in ["backend", "web", "manager_web", "android", "ios", "desktop_windows", "desktop_macos", "desktop_linux", "packaged_installer"]:
        assert target in modal
    assert "Required" in modal
    assert "Optional" in modal
    assert "required_targets" in app
    assert "optional_targets" in app


def test_strict_policy_summary_is_visible_before_generation():
    source = Path("frontend/src/components/NewProjectModal.jsx").read_text(encoding="utf-8")
    for text in ["This project will not be accepted until", "mandatory features pass", "required targets are verified", "required E2E passes", "required security/RBAC checks pass", "mandatory unverified criteria are resolved"]:
        assert text in source


def test_feature_matrix_quality_sections_render():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    for section in ["Maturity Status", "Feature Completeness", "Target Verification", "Test Matrix", "RBAC Matrix", "Security", "Architecture", "Evidence", "Limitations"]:
        assert section in source
    for column in ["Feature", "Mandatory", "Backend", "Customer UI", "Management UI", "Tests", "E2E", "Platform", "Status"]:
        assert column in source


def test_target_statuses_remain_separate_and_show_blocking_reasons():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "targetRows.map" in source
    assert "Exact blocking reasons" in source
    assert "row.target_type || row.target" in source


def test_core_e2e_and_mvp_acceptance_are_distinct_in_ui():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "Current milestone" in source
    assert "Target acceptance" in source
    assert "MVP ACCEPTANCE INCOMPLETE" in source


def test_agent_settings_allow_role_defaults_global_inheritance_and_overrides():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    assert "Agent Role Contracts" in source
    assert "Reset to recommended role defaults" in source
    assert "Inherit global model" in source
    assert "Override generation parameters" in source


def test_quality_modals_remain_scrollable():
    css = Path("frontend/src/index.css").read_text(encoding="utf-8")
    assert ".settings-modal-body" in css and "overflow-y: auto" in css
    assert ".new-project-modal-body" in css and "overflow-y: auto" in css
    assert ".info-modal-container" in css and "max-height: calc(100dvh - 32px)" in css
    assert ".info-inline-body" in css and "min-height: 0" in css and "overflow-y: auto" in css
    source = Path("frontend/src/components/InfoModal.jsx").read_text(encoding="utf-8")
    assert "info-modal-header" in source
    assert "info-modal-footer" in source
