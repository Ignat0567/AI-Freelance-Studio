import argparse
import html
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UI_TARGETS = [
    "test_info_modal_scroll_playwright.py",
    "test_settings_scroll_playwright.py",
    "test_theme_playwright.py",
    "test_check_all_playwright.py",
    "test_component_actions_playwright.py",
    "test_official_download_onboarding.py::test_external_url_and_navigation_policies_reject_untrusted_inputs_in_node",
    "test_official_download_onboarding.py::test_electron_renderer_has_deny_by_default_security_boundaries",
    "test_official_download_onboarding.py::test_requirement_registry_exposes_no_installer_callbacks_or_commands",
    "test_requirements_checker.py::test_user_facing_registry_has_fixed_order_categories_and_actions",
    "test_requirements_checker.py::test_legacy_install_dispatch_is_disabled",
]
PERSISTENCE_ISOLATION_TARGETS = [
    "test_final_delivery_audit.py::test_persistence_restart_verifier_passes_with_persistent_store",
    "test_final_delivery_audit.py::test_persistence_restart_verifier_fails_for_in_memory_store",
]


def _run(command, environment, log_path):
    started = time.monotonic()
    completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    output = completed.stdout + completed.stderr
    log_path.write_text(output, encoding="utf-8")
    print(output, end="")
    return completed.returncode, round(time.monotonic() - started, 3)


def _junit_summary(path):
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    cases = []
    for case in root.iter("testcase"):
        status = "passed"
        if case.find("failure") is not None:
            status = "failed"
        elif case.find("error") is not None:
            status = "error"
        elif case.find("skipped") is not None:
            status = "skipped"
        cases.append({"name": case.get("name", ""), "classname": case.get("classname", ""), "status": status})
    return {
        "tests": int(suite.get("tests", len(cases))),
        "failures": int(suite.get("failures", 0)),
        "errors": int(suite.get("errors", 0)),
        "skipped": int(suite.get("skipped", 0)),
        "time_seconds": float(suite.get("time", 0)),
        "cases": cases,
    }


def _write_html(report, path):
    rows = []
    for run in report["runs"]:
        rows.append(
            f"<tr><td>{html.escape(run['name'])}</td><td>{run['summary']['tests']}</td>"
            f"<td>{run['summary']['failures']}</td><td>{run['summary']['errors']}</td>"
            f"<td>{run['summary']['skipped']}</td><td>{run['duration_seconds']:.3f}s</td></tr>"
        )
    path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>UI Regression Report</title>"
        "<style>body{font:14px system-ui;margin:32px;color:#172033}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ccd3df;padding:8px;text-align:left}.passed{color:#08783e}.failed{color:#b42318}"
        "code{background:#eef2f7;padding:2px 5px}</style></head><body>"
        f"<h1>FreelancerStudio UI Regression</h1><p class='{report['status']}'><b>Status:</b> {report['status']}</p>"
        f"<p><b>Generated:</b> {html.escape(report['generated_at'])}</p>"
        f"<p><b>Offline:</b> {str(report['offline']).lower()} | <b>Evidence mode:</b> {str(report['evidence_mode']).lower()}</p>"
        "<table><thead><tr><th>Run</th><th>Tests</th><th>Failures</th><th>Errors</th><th>Skipped</th><th>Duration</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "<h2>SQLite Isolation Check</h2>"
        f"<p>Status: <b>{html.escape(report['persistence_isolation']['status'])}</b>. "
        "These restart verifiers run outside the browser suite and use temporary project fixtures; the UI suite does not access their storage.</p>"
        "</body></html>",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="Run the deterministic FreelancerStudio UI regression suite.")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--evidence", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeat < 2:
        parser.error("--repeat must be at least 2 for the Stage 7 completion gate")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / "artifacts" / "ui_regression" / stamp).resolve()
    output.mkdir(parents=True, exist_ok=True)

    build = subprocess.run(
        ["npm.cmd" if os.name == "nt" else "npm", "run", "build:vite"],
        cwd=ROOT / "frontend",
        text=True,
    )
    if build.returncode:
        return build.returncode

    environment = os.environ.copy()
    environment["UI_REGRESSION_SKIP_BUILD"] = "1"
    environment["UI_REGRESSION_EVIDENCE"] = "1" if args.evidence else "0"
    runs = []
    success = True
    for index in range(1, args.repeat + 1):
        run_dir = output / f"run-{index}"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_env = environment.copy()
        run_env["UI_REGRESSION_ARTIFACT_DIR"] = str(run_dir)
        junit = run_dir / "junit.xml"
        command = [sys.executable, "-m", "pytest", "-q", *UI_TARGETS, f"--junitxml={junit}"]
        returncode, duration = _run(command, run_env, run_dir / "pytest.log")
        summary = _junit_summary(junit)
        runs.append({"name": f"run-{index}", "returncode": returncode, "duration_seconds": duration, "summary": summary})
        success = success and returncode == 0

    sqlite_dir = output / "sqlite-isolation"
    sqlite_dir.mkdir(parents=True, exist_ok=True)
    sqlite_code, sqlite_duration = _run(
        [sys.executable, "-m", "pytest", "-q", *PERSISTENCE_ISOLATION_TARGETS],
        environment,
        sqlite_dir / "pytest.log",
    )
    success = success and sqlite_code == 0
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if success else "failed",
        "offline": True,
        "evidence_mode": args.evidence,
        "targets": UI_TARGETS,
        "runs": runs,
        "persistence_isolation": {
            "status": "passed" if sqlite_code == 0 else "failed",
            "returncode": sqlite_code,
            "duration_seconds": sqlite_duration,
            "targets": PERSISTENCE_ISOLATION_TARGETS,
            "observed_transients": [
                "SQLite positive restart verifier failed once during Stage 6 full QA and passed immediately in isolation and subsequent full QA.",
                "In-memory negative restart verifier failed once during Stage 7 full QA and passed immediately in isolation.",
            ],
            "relationship_to_ui_suite": "independent temporary project process; no browser, Studio runtime, or UI fixture is shared",
        },
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_html(report, output / "report.html")
    print(f"UI regression report: {output / 'report.html'}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
