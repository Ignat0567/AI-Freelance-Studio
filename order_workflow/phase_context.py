from __future__ import annotations

from .models import LongText, ShortText, StrictDomainModel
from .qa_runner import QAOutcome
from .workspace import ProjectWorkspace, scan_meaningful_generated_artifacts

_MAX_LISTED_FILES = 30


class PhaseContext(StrictDomainModel):
    phase: ShortText
    summary: LongText
    files: tuple[ShortText, ...] = ()
    qa_status: ShortText


def build_phase_context(phase: str, workspace: ProjectWorkspace, qa_outcome: QAOutcome | None) -> PhaseContext:
    files = scan_meaningful_generated_artifacts(workspace)[:_MAX_LISTED_FILES]
    if qa_outcome is None:
        qa_status = "QA was not run for this phase."
    elif qa_outcome.passed:
        qa_status = "QA passed."
    else:
        qa_status = "QA failed."
    summary = (
        f"Phase '{phase}' produced {len(files)} listed file(s) in the workspace. {qa_status}"
        if files
        else f"Phase '{phase}' completed. {qa_status}"
    )
    return PhaseContext(phase=phase, summary=summary, files=files, qa_status=qa_status)
