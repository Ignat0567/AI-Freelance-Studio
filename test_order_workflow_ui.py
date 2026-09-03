from pathlib import Path


ROOT = Path("frontend/src/features/order-workflow")


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_create_project_entry_is_visible_in_dashboard():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "Create Project" in source
    assert "create-project" in source
    assert "OrderWorkflowPage" in source
    assert "CORE_NAV_IDS" in source
    assert "show_experimental" in source


def test_pdf_voice_assistant_demo_fill_is_form_only():
    state = _read("orderWorkflowState.js")
    create = _read("OrderCreatePanel.jsx")
    execution = _read("ExecutionDashboard.jsx")
    assert "PDF Voice Assistant" in state
    assert "Create a browser-based voice assistant" in state
    assert "Use PDF Voice Assistant example" in create
    assert "PDF Voice Assistant" not in execution


def test_new_order_screen_states_supported_profile_and_no_unsupported_options():
    source = _read("OrderCreatePanel.jsx")
    profile = source.lower()
    # Every product type the selector offers has to appear in the stated profile: a screen
    # that offers a third option while promising two teaches the client not to read it.
    assert "small browser-based web applications" in profile
    assert "websites (one html file)" in profile
    assert "coming later" in profile
    assert "telegram bot" in profile
    assert "Small web application" in source
    assert ">Website<" in source
    assert 'option value="static_page"' in source
    assert 'option value="bot" disabled' in source
    assert "Use bakery website example" in source
    assert "Harbour Bakery" in _read("orderWorkflowState.js")
    assert "desktop" not in profile
    assert "mobile application" not in profile


def test_clarification_supports_required_input_types_and_defaults():
    source = _read("ClarificationPanel.jsx")
    for input_type in ["single_select", "multi_select", "boolean", "long_text"]:
        assert input_type in source
    assert "short_text" not in source or "return <input" in source
    assert "Recommended:" in source
    assert "Use recommended defaults" in source
    assert "Visible assumptions" in source
    assert "reason" in source


def test_brief_screen_renders_structured_sections_and_approval_gate():
    source = _read("ProjectBriefPanel.jsx")
    for section in [
        "Goal",
        "Target users",
        "Core features",
        "Non-goals",
        "Assumptions",
        "Technical constraints",
        "UI requirements",
        "Recommended stack",
        "Acceptance criteria",
        "Elena design choice",
    ]:
        assert section in source
    assert "Approve brief" in source
    assert "Request changes" in source
    assert "Alex to Codex handoff is ready" in source


def test_execution_dashboard_is_live_first_and_cancellable():
    source = _read("ExecutionDashboard.jsx")
    assert "Start live build" in source
    assert "Run simulation" in source
    assert "Prepare production dry-run" in source
    assert "Live execution locked" in source
    assert "Cancel execution" in source
    assert "Grok" in source
    assert "Ollama" in source
    assert "This run" in source
    assert "Who writes the project files" in source
    assert "claude_code" in source
    assert "openrouter" in source
    assert "Provider connection" in source
    assert "Writing project files" in source
    assert "ow-execution-split" in source
    assert "CodingWorkbench" in source
    for agent in ["active_agent", "stage", "progress", "events", "blockers", "Execution Readiness"]:
        assert agent in source


def test_result_screen_shows_simulated_artifacts_and_verification_summary():
    source = _read("ExecutionResultPanel.jsx")
    assert "Simulated artifact" in source
    assert "test_summary" in source
    assert "Repair attempts" in source
    assert "Create another order" in source
    assert "Revise brief" in source
    assert "Delivery folder" in source
    assert "Open folder" in source
    assert "delivery_report.md" in source
    assert "delivery_screenshot.png" in source
    assert "Retry this workspace" in source
    assert "CodingWorkbench" in source
    assert "session limit" in source
    assert "europe/berlin" in source.lower() or "europe\\/berlin" in source.lower()


def test_api_client_uses_relative_routes_and_no_renderer_token_storage():
    api = _read("orderWorkflowApi.js")
    page = _read("OrderWorkflowPage.jsx")
    for route in [
        "/api/orders",
        "/answers",
        "/defaults",
        "/brief",
        "/brief/approve",
        "/readiness",
        "/execution",
        "/execution/cancel",
        "/events",
        "/artifacts",
        "/result",
        "/open-workspace",
    ]:
        assert route in api
    combined = api + page
    assert "X-FreelancerStudio-Token" not in combined
    assert "backend_token" not in combined
    assert "localStorage.setItem(STORAGE_KEY" in page


def test_recovery_polling_duplicate_start_and_sanitized_errors_are_present():
    page = _read("OrderWorkflowPage.jsx")
    state = _read("orderWorkflowState.js")
    assert "studio_order_workflow_last_order_id_v1" in page
    assert "This order could not be reloaded after the backend restarted" in page
    assert "in-memory order is no longer available" not in page
    assert "setInterval" in page
    assert "clearInterval" in page
    assert "startInFlight" in page
    assert "cleanError" in page
    assert "authorization:" in state
    assert "x-freelancerstudio-token" in state
    assert "token\\s*" in state


def test_frontend_execution_start_is_approval_gated():
    page = _read("OrderWorkflowPage.jsx")
    dashboard = _read("ExecutionDashboard.jsx")
    assert "canStartExecution" in page
    assert "approval?.approved" in page
    assert "handoff_ready" in page
    assert "Approve the current brief and Elena design preview before starting execution" in page
    assert "Approval required" in dashboard
    assert "disabled={pending || !canStart}" in dashboard


def test_execution_readiness_panel_guides_settings_without_live_execution():
    dashboard = _read("ExecutionDashboard.jsx")
    page = _read("OrderWorkflowPage.jsx")
    api = _read("orderWorkflowApi.js")
    css = _read("OrderWorkflow.css")

    for expected in [
        "Execution Readiness",
        "Production dry-run",
        "Open Settings from the sidebar",
        "AI provider",
        "Model",
        "Workspace",
        "Live execution opt-in",
        "production_live_ready",
        "Live execution locked",
        "grok login",
        "ollama serve",
        "ollama pull qwen2.5-coder:14b",
        "Start Docker Desktop",
        "Environment checklist",
        "Provider quota",
        "Retry this workspace",
    ]:
        assert expected in dashboard
    assert "can_prepare_dry_run" in page
    assert "orderWorkflowApi.getReadiness" in page
    assert "startExecution(state.order.id, mode, false)" in page
    assert "startExecution(state.order.id, 'production', true, choice)" in page
    assert "mode = 'production'" in api
    assert "X-FreelancerStudio-Token" not in dashboard + page + api
    assert "ow-readiness" in css


def test_design_preview_ui_and_api_are_wired_generically():
    brief = _read("ProjectBriefPanel.jsx")
    page = _read("OrderWorkflowPage.jsx")
    api = _read("orderWorkflowApi.js")
    css = _read("OrderWorkflow.css")

    for expected in [
        "Elena Design Preview",
        "layout_type",
        "User flows",
        "Empty states",
        "Error states",
        "Accessibility notes",
        "Implementation notes for Codex",
        "Approve preview",
        "Regenerate preview",
    ]:
        assert expected in brief
    for route in ["/design-preview", "/design-preview/revise", "/design-preview/approve"]:
        assert route in api
    assert "approveDesignPreview" in page
    assert "reviseDesignPreview" in page
    assert "generateDesignPreview" in page
    assert "left PDF library panel" not in brief
    assert "ow-design-preview" in css


def test_autopilot_trigger_is_wired_end_to_end():
    api = _read("orderWorkflowApi.js")
    create = _read("OrderCreatePanel.jsx")
    page = _read("OrderWorkflowPage.jsx")

    assert "/autopilot" in api
    assert "runAutopilot" in api
    assert "Create & run automatically" in create
    assert "onAutoSubmit" in create
    assert "submitOrderAutomatically" in page
    assert "runAutopilot" in page
    assert "onAutoSubmit={submitOrderAutomatically}" in page


def test_client_proposal_trigger_is_wired_end_to_end():
    api = _read("orderWorkflowApi.js")
    brief = _read("ProjectBriefPanel.jsx")
    page = _read("OrderWorkflowPage.jsx")
    css = _read("OrderWorkflow.css")

    assert "/proposal" in api
    assert "generateProposal" in api
    assert "Generate proposal" in brief
    assert "Download as .md" in brief
    assert "proposal_markdown" in brief
    assert "onGenerateProposal" in page
    assert "generateProposal" in page
    assert "ow-proposal-text" in css


def test_theme_accessibility_and_reduced_motion_hooks_exist():
    css = _read("OrderWorkflow.css")
    jsx = _read("OrderWorkflowPage.jsx") + _read("ExecutionDashboard.jsx")
    assert "var(--fs-" in css
    assert "prefers-reduced-motion" in css
    assert "aria-live" in jsx
    assert "role=\"alert\"" in jsx
    assert "aria-label=\"Order workflow steps\"" in jsx
