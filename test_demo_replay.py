"""The replay has to be watchable and it has to be unmistakably a recording.

The second is the one worth guarding with tests. A demo that could be mistaken for a live
run would be worth less than the run it stands in for -- this project's entire claim is that
its evidence is real, and a replay that blurred that would put the claim in question rather
than support it.
"""

from __future__ import annotations

import json

import pytest

from demo.replay import (
    DEFAULT_SPEED,
    MAX_GAP_SECONDS,
    banner,
    original_duration,
    pick_transcript,
    replay,
    replay_schedule,
)


pytestmark = pytest.mark.unit


def _event(offset_seconds: int, message: str = "working", **extra) -> dict:
    minute, second = divmod(offset_seconds, 60)
    hour, minute = divmod(minute, 60)
    return {
        "created_at": f"2026-08-18T{7 + hour:02d}:{minute:02d}:{second:02d}.000000Z",
        "message": message,
        "stage": "ui_shell",
        "agent": "Codex",
        **extra,
    }


def _transcript(tmp_path, events, *, status="succeeded", name="run.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps({
            "summary": {"brief_goal": "A personal reading journal that lives in one browser."},
            "events": events,
            "execution": {"status": status, "result": {"duration_seconds": 1256.1, "usage": {"total_cost_usd": 5.46}}},
        }),
        encoding="utf-8",
    )
    return path


# --- pacing ---------------------------------------------------------------------------


def test_the_first_event_plays_immediately():
    schedule = replay_schedule([_event(0), _event(10)])

    assert schedule[0][0] == 0.0


def test_gaps_are_scaled_by_speed():
    schedule = replay_schedule([_event(0), _event(100)], speed=10, max_gap=1000)

    assert schedule[1][0] == pytest.approx(10.0)


def test_a_long_coding_call_is_capped_rather_than_merely_scaled():
    """An 11-minute CLI call scaled by speed alone is still 33 seconds of one unchanging
    line. Capping separately keeps the rhythm without the dead air."""
    schedule = replay_schedule([_event(0), _event(660)], speed=DEFAULT_SPEED, max_gap=MAX_GAP_SECONDS)

    assert schedule[1][0] == MAX_GAP_SECONDS


def test_real_time_is_available_for_anyone_who_wants_the_true_pacing():
    schedule = replay_schedule([_event(0), _event(30)], speed=1, max_gap=10_000)

    assert schedule[1][0] == pytest.approx(30.0)


def test_out_of_order_timestamps_never_produce_a_negative_delay():
    """Events come from several threads and two can land out of order by milliseconds; a
    negative delay would be passed straight to sleep()."""
    schedule = replay_schedule([_event(10), _event(4)])

    assert all(delay >= 0 for delay, _ in schedule)


def test_an_event_without_a_usable_timestamp_does_not_stall_the_replay():
    schedule = replay_schedule([_event(0), {"message": "no timestamp"}, {"created_at": "not a date", "message": "bad"}])

    assert [delay for delay, _ in schedule] == [0.0, 0.0, 0.0]


def test_speed_must_be_positive():
    with pytest.raises(ValueError):
        replay_schedule([_event(0)], speed=0)


def test_original_duration_reports_the_real_run_length():
    assert original_duration([_event(0), _event(1256)]) == pytest.approx(1256.0)


# --- honesty --------------------------------------------------------------------------


def test_the_banner_says_it_is_a_recording_before_anything_else(tmp_path):
    lines = banner(tmp_path / "bench-b06.json", [_event(0), _event(1256)], {"brief_goal": "A reading journal"})

    assert "RECORDING" in lines[0].upper()
    assert "NOT A LIVE RUN" in lines[0].upper()


def test_the_banner_names_the_source_and_when_it_really_happened(tmp_path):
    lines = "\n".join(banner(tmp_path / "bench-b06.json", [_event(0), _event(1256)], {}))

    assert "bench-b06.json" in lines
    assert "2026-08-18" in lines
    assert "21 minutes" in lines  # 1256s, honestly stated rather than the replay's own length


def test_the_banner_says_nothing_is_running_or_being_charged(tmp_path):
    lines = " ".join(banner(tmp_path / "r.json", [_event(0)], {})).casefold()

    assert "no provider" in lines
    assert "no cost" in lines


def test_the_replay_repeats_the_disclaimer_at_the_end(tmp_path, capsys):
    path = _transcript(tmp_path, [_event(0, "Preparing phased execution"), _event(5, "QA passed.")])

    assert replay(path, sleeper=lambda _seconds: None) == 0

    output = capsys.readouterr().out
    assert output.count("recording") + output.count("RECORDING") >= 2
    assert "END OF RECORDING" in output


def test_the_replay_shows_the_real_events_and_the_real_outcome(tmp_path, capsys):
    path = _transcript(tmp_path, [_event(0, "Preparing phased execution"), _event(5, "QA passed.")])

    replay(path, sleeper=lambda _seconds: None)

    output = capsys.readouterr().out
    assert "Preparing phased execution" in output
    assert "QA passed." in output
    assert "succeeded" in output
    assert "$5.46" in output


def test_a_transcript_with_no_events_is_reported_rather_than_replayed_as_nothing(tmp_path, capsys):
    path = _transcript(tmp_path, [])

    assert replay(path, sleeper=lambda _seconds: None) == 1
    assert "no events" in capsys.readouterr().out


# --- choosing what to show ------------------------------------------------------------


def test_a_failed_run_is_never_the_default_choice(tmp_path):
    failed = _transcript(tmp_path, [_event(0)], status="failed", name="failed.json")
    succeeded = _transcript(tmp_path, [_event(0)], status="succeeded", name="ok.json")

    assert pick_transcript([failed, succeeded]) == succeeded


def test_an_unreadable_transcript_is_skipped_rather_than_crashing_the_demo(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    good = _transcript(tmp_path, [_event(0)], name="ok.json")

    assert pick_transcript([broken, good]) == good


def test_no_successful_transcript_returns_nothing_rather_than_a_failure_reel(tmp_path):
    failed = _transcript(tmp_path, [_event(0)], status="failed", name="failed.json")

    assert pick_transcript([failed]) is None


def test_a_watched_run_shows_what_the_gate_found_not_only_that_it_failed(capsys):
    """The live runners printed the message and dropped the details, so watching a run told
    you less than replaying its recording did. A failing gate's findings are the whole
    reason anyone is watching."""
    from demo.run_end_to_end_demo import log_event

    log_event(
        {
            "stage": "ui_shell",
            "message": "QA failed; asking Codex to fix (attempt 1 of 2)",
            "details": ("VISUAL CHECK FAILED:\n- Tap targets under 24x24px at phone width.\n- Text extends 132px outside the viewport.",),
        }
    )

    printed = capsys.readouterr().out
    assert "QA failed; asking Codex to fix" in printed
    assert "Tap targets under 24x24px at phone width." in printed
    assert "Text extends 132px outside the viewport." in printed


def test_an_event_without_details_prints_one_line(capsys):
    from demo.run_end_to_end_demo import log_event

    log_event({"stage": "ui_shell", "message": "QA passed."})

    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_a_long_detail_keeps_its_verdict_rather_than_its_header(capsys):
    """A gate's detail opens with the shell command that ran it and closes with what it
    found. Printing the first lines showed the container invocation and dropped the
    findings -- which is what a live run printed on 2026-08-26 before this."""
    from demo.run_end_to_end_demo import log_event

    detail = chr(10).join(["Command failed: npm install ...", "Exit code: 1", "Stderr:", "", "Stdout:", "Palette: 4/4"] + [f"- finding {n}" for n in range(1, 9)])

    log_event({"stage": "ui_shell", "message": "QA failed", "details": (detail,)})

    printed = capsys.readouterr().out
    assert "- finding 8" in printed
    assert "earlier line(s)" in printed
