"""Where a benchmark run leaves its evidence.

Acceptance is run from a fresh clone with --csv pointing at the main repository, and the
clone is then deleted. Until this was fixed the transcript went to the clone's own
bench/transcripts while only the row survived, so the three rows recorded on 2026-08-23 name
files that exist nowhere -- including the failed run, the only one worth reading.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bench import run_bench
from bench.orders import BENCH_ORDERS_BY_ID


pytestmark = pytest.mark.unit
ORDER = BENCH_ORDERS_BY_ID["b01-profile-card"]


class _FakeHarness:
    """Just enough of the demo runner's transport for run_one to reach the end."""

    def __init__(self) -> None:
        self.logs: list[str] = []

    def section(self, text: str) -> None:
        self.logs.append(text)

    def log(self, text: str) -> None:
        self.logs.append(text)

    def log_event(self, event: dict, *, prefix: str = "  ") -> None:
        self.logs.append(event.get("message", ""))

    def request(self, method: str, path: str, payload: dict | None = None, *, timeout: int = 120) -> dict:
        if path == "/api/orders":
            return {"order": {"id": "order-1"}}
        if path.endswith("/autopilot"):
            return {"brief": {"id": "brief-1", "revision": 1, "core_features": ["one"]}}
        if "/readiness" in path:
            return {"can_run_live": True}
        if path.endswith("/execution"):
            return {"execution": {"status": "succeeded", "events": [], "result": {"outcome": "completed"}}}
        raise AssertionError(f"unexpected request {method} {path}")

    def summarise(self, events: list[dict], execution: dict, brief: dict) -> dict:
        return {"brief_goal": ORDER.title}


@pytest.fixture
def fake_run(monkeypatch):
    monkeypatch.setattr(run_bench, "harness", _FakeHarness())
    monkeypatch.setattr(run_bench, "POLL_SECONDS", 0)
    monkeypatch.setattr(run_bench, "_run_commit", lambda: "abc1234")
    return None


def test_transcript_is_written_beside_the_results_file(tmp_path, fake_run):
    csv_path = tmp_path / "main-repo" / "bench" / "results.csv"

    row = run_bench.run_one(ORDER, csv_path=csv_path)

    written = csv_path.parent / "transcripts" / row["notes"]
    assert written.is_file(), f"{row['notes']} was recorded but written nowhere near {csv_path}"
    assert json.loads(written.read_text(encoding="utf-8"))["bench"]["id"] == ORDER.id


def test_the_row_names_a_transcript_that_exists(tmp_path, fake_run):
    csv_path = tmp_path / "results.csv"

    run_bench.run_one(ORDER, csv_path=csv_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    named = csv_path.parent / "transcripts" / rows[0]["notes"]
    assert named.is_file()


def test_default_csv_still_writes_into_bench_transcripts():
    # The default layout must not move: demo/replay.py and report.py read bench/transcripts.
    assert run_bench._transcript_dir(run_bench.RESULTS_CSV) == run_bench.REPO_ROOT / "bench" / "transcripts"


def test_a_relative_csv_path_does_not_land_transcripts_in_the_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "elsewhere").mkdir()

    resolved = run_bench._transcript_dir(Path("elsewhere/results.csv"))

    assert resolved == (tmp_path / "elsewhere" / "transcripts").resolve()


def test_a_run_that_stops_to_ask_a_human_is_recorded_immediately(tmp_path, monkeypatch):
    """Tonight's run: a transient 401 blocked the first order at 21:35 and the harness sat on
    it, because `awaiting_user` is not one of the statuses the poll loop calls terminal. Left
    alone it would have waited an hour, three times over."""

    class _AwaitingHarness(_FakeHarness):
        def request(self, method: str, path: str, payload: dict | None = None, *, timeout: int = 120) -> dict:
            if path.endswith("/execution") and method == "GET":
                return {
                    "execution": {
                        "status": "awaiting_user",
                        "events": [{"id": "e1", "level": "warning", "message": "The coding CLI's login has expired.", "stage": "requirements"}],
                    }
                }
            return super().request(method, path, payload)

    monkeypatch.setattr(run_bench, "harness", _AwaitingHarness())
    monkeypatch.setattr(run_bench, "POLL_SECONDS", 0)
    monkeypatch.setattr(run_bench, "_run_commit", lambda: "abc1234")
    csv_path = tmp_path / "results.csv"

    row = run_bench.run_one(ORDER, csv_path=csv_path)

    assert row["outcome"] == "not_started"
    assert row["failure_cause"] == "environment"
    assert "login has expired" in row["notes"]
    assert csv_path.is_file()


def test_the_start_call_outlives_the_preflight_it_waits_on():
    """2026-08-28: the runner timed out after 120s on the POST that starts an execution,
    killed the backend and recorded nothing -- no workspace, no CSV row, no transcript. That
    call is synchronous through preflight, whose login probe alone may retry a 401 three times
    over 80 seconds of sleeps with a 90-second ceiling each: ~350s inside one HTTP call."""
    from demo.run_end_to_end_demo import EXECUTION_START_TIMEOUT_SECONDS
    from order_workflow.preflight import AUTH_PROBE_TIMEOUT_SECONDS

    assert EXECUTION_START_TIMEOUT_SECONDS >= 3 * AUTH_PROBE_TIMEOUT_SECONDS + 80
