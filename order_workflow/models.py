from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .failure_cause import FailureCause, classify_failure_cause


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
OrderDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20_000)]
PublicId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*_[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")]
OpaqueReference = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,239}$")]
LanguageCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=16, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")]

DEFAULT_EVENT_LIMIT = 200
MAX_EVENT_LIMIT = 500
_PUBLIC_ID_PREFIX = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class UserOrderStatus(str, Enum):
    DRAFT = "draft"
    CLARIFICATION_REQUIRED = "clarification_required"
    BRIEF_READY = "brief_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProductType(str, Enum):
    WEB_APP = "web_app"
    BOT = "bot"
    # A single self-contained .html file: a creative/interactive page (WebGL scene, canvas
    # art, landing page) that ships as one file loaded straight in a browser. Not a smaller
    # web_app -- it has no build step, no npm project and no framework, so every gate the
    # web_app pipeline depends on (`npm run build`, `npm test`, a preview server, a palette
    # measured off DOM computed styles) is either inapplicable or actively wrong for it.
    STATIC_PAGE = "static_page"


# Every ProductType member the pipeline actually has a full clarification/brief/execution
# path for. A ProductType could exist here as a value the *type system* allows before an
# execution adapter is wired up for it -- this is the single, explicit gate that decides
# whether an order of that type is actually accepted end to end.
SUPPORTED_PRODUCT_TYPES: frozenset[ProductType] = frozenset(
    {ProductType.WEB_APP, ProductType.BOT, ProductType.STATIC_PAGE}
)


class QuestionType(str, Enum):
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    SHORT_TEXT = "short_text"
    LONG_TEXT = "long_text"
    BOOLEAN = "boolean"


class ElenaDesignChoice(str, Enum):
    SHOW_ELENA_CONCEPT = "show_elena_concept"
    PROCEED_WITHOUT_CONCEPT = "proceed_without_concept"
    UNDECIDED = "undecided"
    NOT_APPLICABLE = "not_applicable"

    # Compatibility aliases for the Commit 1 public names.
    SHOW_CONCEPT = SHOW_ELENA_CONCEPT
    PROCEED_DIRECTLY = PROCEED_WITHOUT_CONCEPT


class ExecutionMode(str, Enum):
    FAKE = "fake"
    PRODUCTION = "production"


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStage(str, Enum):
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    PLANNING = "planning"
    UI_SHELL = "ui_shell"
    CORE_FEATURE = "core_feature"
    BACKEND_DECISION = "backend_decision"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    REPAIR = "repair"
    PACKAGING = "packaging"
    REVISION = "revision"
    BOT_BUILD = "bot_build"
    STATIC_PAGE_BUILD = "static_page_build"
    COMPLETED = "completed"


class EventKind(str, Enum):
    STATUS = "status"
    ACTIVITY = "activity"
    BLOCKER = "blocker"
    ARTIFACT = "artifact"
    RESULT = "result"
    MILESTONE = "milestone"


class EventLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ArtifactKind(str, Enum):
    PROJECT_BRIEF = "project_brief"
    AGENT_HANDOFF = "agent_handoff"
    PROJECT_SUMMARY = "project_summary"
    TEST_SUMMARY = "test_summary"
    DELIVERY_REPORT = "delivery_report"
    GENERATED_PROJECT = "generated_project"
    PROJECT_DOCUMENTATION = "project_documentation"


class StrictDomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False):
        """Return a fully validated copy; frozen snapshots must not accept unchecked updates."""
        values = self.model_dump(mode="python")
        values.update(update or {})
        return type(self).model_validate(values)

    @classmethod
    def from_dict(cls, value: object):
        return cls.model_validate(value)

    @classmethod
    def from_json(cls, value: str):
        return cls.model_validate_json(value)


def utc_now(clock: Callable[[], datetime] | None = None) -> datetime:
    return _aware_datetime(clock() if clock is not None else datetime.now(timezone.utc))


def new_public_id(prefix: str, id_factory: Callable[[], object] = uuid4) -> str:
    if not _PUBLIC_ID_PREFIX.fullmatch(prefix):
        raise ValueError("public ID prefix is invalid")
    value = str(id_factory()).strip()
    value = re.sub(r"[^A-Za-z0-9_-]", "", value)
    if not value or len(value) > 96 - len(prefix) - 1:
        raise ValueError("ID factory returned an invalid value")
    return f"{prefix}_{value}"


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


class ClarificationQuestion(StrictDomainModel):
    id: ShortText
    text: ShortText
    type: QuestionType
    options: tuple[ShortText, ...] = ()
    required: bool = True
    reason: ShortText
    recommended_answer: str | tuple[str, ...] | bool | None = None

    @model_validator(mode="after")
    def validate_input_contract(self) -> "ClarificationQuestion":
        selectable = self.type in {QuestionType.SINGLE_SELECT, QuestionType.MULTI_SELECT}
        if selectable and len(self.options) < 2:
            raise ValueError("select questions require at least two options")
        if not selectable and self.options:
            raise ValueError("text and boolean questions cannot define options")
        recommendation = self.recommended_answer
        if recommendation is None:
            return self
        if self.type is QuestionType.SINGLE_SELECT:
            if not isinstance(recommendation, str) or recommendation not in self.options:
                raise ValueError("recommended answer must be one of the options")
        elif self.type is QuestionType.MULTI_SELECT:
            if not isinstance(recommendation, tuple) or not recommendation or any(item not in self.options for item in recommendation):
                raise ValueError("recommended answers must be selected options")
        elif self.type is QuestionType.BOOLEAN:
            if type(recommendation) is not bool:
                raise ValueError("recommended answer must be boolean")
        elif not isinstance(recommendation, str) or not recommendation.strip():
            raise ValueError("recommended answer must be text")
        return self


class ClarificationAnswer(StrictDomainModel):
    question_id: ShortText
    value: str | tuple[str, ...] | bool

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str | tuple[str, ...] | bool):
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized or len(normalized) > 20_000:
                raise ValueError("answer text is invalid")
            return normalized
        if isinstance(value, tuple):
            if not value or len(value) > 30:
                raise ValueError("answer selection is invalid")
            normalized = tuple(item.strip() for item in value)
            if any(not item or len(item) > 240 for item in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError("answer selection is invalid")
            return normalized
        if type(value) is not bool:
            raise ValueError("answer value is invalid")
        return value


class UserOrder(StrictDomainModel):
    id: PublicId
    title: ShortText
    description: OrderDescription
    product_type: ProductType = ProductType.WEB_APP
    preferred_language: LanguageCode = "en"
    constraints: tuple[ShortText, ...] = ()
    attachments: tuple[OpaqueReference, ...] = ()
    status: UserOrderStatus = UserOrderStatus.DRAFT
    clarification_round: Annotated[int, Field(ge=0, le=5)] = 0
    questions: tuple[ClarificationQuestion, ...] = ()
    answers: tuple[ClarificationAnswer, ...] = ()
    brief_id: PublicId | None = None
    execution_id: PublicId | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_datetime(value)

    @model_validator(mode="after")
    def validate_order(self) -> "UserOrder":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if len(self.constraints) > 50 or len(self.attachments) > 20:
            raise ValueError("order contains too many items")
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError("an order cannot contain duplicate questions")
        if len({answer.question_id for answer in self.answers}) != len(self.answers):
            raise ValueError("an order cannot contain duplicate answers")
        questions = {question.id: question for question in self.questions}
        for answer in self.answers:
            question = questions.get(answer.question_id)
            if question is None:
                raise ValueError("answer does not match an order question")
            self._validate_answer(question, answer)
        return self

    @staticmethod
    def _validate_answer(question: ClarificationQuestion, answer: ClarificationAnswer) -> None:
        value = answer.value
        if question.type is QuestionType.SINGLE_SELECT:
            valid = isinstance(value, str) and value in question.options
        elif question.type is QuestionType.MULTI_SELECT:
            valid = isinstance(value, tuple) and all(item in question.options for item in value)
        elif question.type in {QuestionType.SHORT_TEXT, QuestionType.LONG_TEXT}:
            valid = isinstance(value, str)
        else:
            valid = type(value) is bool
        if not valid:
            raise ValueError("answer does not satisfy its question contract")


class RecommendedStack(StrictDomainModel):
    frontend: ShortText = "React + Vite"
    backend: ShortText = "FastAPI"
    storage: ShortText = "SQLite"


class ThemePalette(StrictDomainModel):
    background: ShortText
    surface: ShortText
    text: ShortText
    accent: ShortText


class ElenaDesignConcept(StrictDomainModel):
    visual_direction: ShortText
    layout: LongText
    screens: tuple[ShortText, ...]
    components: tuple[ShortText, ...]
    light_theme: ThemePalette
    dark_theme: ThemePalette
    accessibility_notes: tuple[ShortText, ...] = ()


class ProjectBrief(StrictDomainModel):
    id: PublicId
    order_id: PublicId
    revision: Annotated[int, Field(ge=1)] = 1
    product_type: ProductType = ProductType.WEB_APP
    goal: LongText
    target_users: tuple[ShortText, ...]
    core_features: tuple[ShortText, ...]
    assumptions: tuple[ShortText, ...] = ()
    non_goals: tuple[ShortText, ...] = ()
    technical_constraints: tuple[ShortText, ...] = ()
    ui_requirements: tuple[ShortText, ...] = ()
    acceptance_criteria: tuple[ShortText, ...]
    open_questions: tuple[ShortText, ...] = ()
    recommended_stack: RecommendedStack = Field(default_factory=RecommendedStack)
    elena_design_choice: ElenaDesignChoice
    elena_design_concept: ElenaDesignConcept | None = None
    approved_at: datetime | None = None
    approved_revision: Annotated[int, Field(ge=1)] | None = None
    approval_fingerprint: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "approved_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware_datetime(value)

    @model_validator(mode="after")
    def validate_brief(self) -> "ProjectBrief":
        if not self.target_users or not self.core_features or not self.acceptance_criteria:
            raise ValueError("brief requires users, features, and acceptance criteria")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.approved_at is not None and self.approved_at < self.created_at:
            raise ValueError("approved_at cannot precede created_at")
        approval_fields = (self.approved_at, self.approved_revision, self.approval_fingerprint)
        if any(item is not None for item in approval_fields) and not all(item is not None for item in approval_fields):
            raise ValueError("approval metadata must be complete")
        if self.approved_revision is not None and self.approved_revision != self.revision:
            raise ValueError("approval must bind the current brief revision")
        if self.elena_design_concept is not None and self.elena_design_choice is not ElenaDesignChoice.SHOW_ELENA_CONCEPT:
            raise ValueError("Elena concept is only valid when selected")
        return self


class AgentHandoff(StrictDomainModel):
    id: PublicId
    order_id: PublicId
    brief_id: PublicId
    source_agent: ShortText
    target_agent: ShortText
    goal: LongText
    context_summary: LongText
    requirements: tuple[ShortText, ...]
    constraints: tuple[ShortText, ...] = ()
    acceptance_criteria: tuple[ShortText, ...]
    artifacts: tuple[OpaqueReference, ...] = ()
    design_preview_id: PublicId | None = None
    design_preview_summary: tuple[ShortText, ...] = ()
    open_questions: tuple[ShortText, ...] = ()
    requested_action: Literal["implement", "revise", "verify"] = "implement"
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_datetime(value)

    @model_validator(mode="after")
    def validate_handoff(self) -> "AgentHandoff":
        if not self.requirements or not self.acceptance_criteria:
            raise ValueError("handoff requires requirements and acceptance criteria")
        return self


class ExecutionEvent(StrictDomainModel):
    id: PublicId
    execution_id: PublicId
    kind: EventKind
    level: EventLevel = EventLevel.INFO
    message: ShortText
    stage: ExecutionStage | None = None
    agent: ShortText | None = None
    progress: Annotated[int, Field(ge=0, le=100)] | None = None
    # LongText, unlike message: details carry a gate's verbatim output (a compiler error, a
    # visual-check finding), and the 240-char ShortText cap silently cut every one of them
    # down to its first line -- typically just the command that failed, which is the one
    # part of the text that carries no information. The repair prompt always had the full
    # text; only the record a human reads was truncated.
    details: tuple[LongText, ...] = ()
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_datetime(value)


class ExecutionBlocker(StrictDomainModel):
    code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
    message: ShortText
    action: ShortText
    action_required: bool = True


class ExecutionArtifact(StrictDomainModel):
    id: PublicId
    execution_id: PublicId
    kind: ArtifactKind
    name: ShortText
    summary: LongText
    reference: OpaqueReference | None = None
    simulated: bool = False
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_datetime(value)


class TestSummary(StrictDomainModel):
    passed: Annotated[int, Field(ge=0)] = 0
    failed: Annotated[int, Field(ge=0)] = 0
    skipped: Annotated[int, Field(ge=0)] = 0
    repair_attempts: Annotated[int, Field(ge=0)] = 0


class TokenUsage(StrictDomainModel):
    """Token/cost accounting for a single coding-agent CLI invocation, as reported by
    the CLI's own JSON output (Claude Code's `-p --output-format json`). Not an estimate --
    these are the exact figures the provider billed for that call."""

    total_cost_usd: Annotated[float, Field(ge=0)] = 0.0
    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0
    cache_read_input_tokens: Annotated[int, Field(ge=0)] = 0
    cache_creation_input_tokens: Annotated[int, Field(ge=0)] = 0


class ExecutionResult(StrictDomainModel):
    success: bool
    summary: LongText
    # "succeeded" | "failed" | "cancelled" | "qa_failed" | "docker_unavailable" |
    # "awaiting_user" -- the last one is not a finish at all: the run paused at a phase
    # boundary to ask the client something, and resumes from its checkpoints once answered.
    outcome: ShortText | None = None
    # Populated only alongside outcome="awaiting_user".
    questions: tuple[ClarificationQuestion, ...] = ()
    artifact_ids: tuple[PublicId, ...] = ()
    test_summary: TestSummary = Field(default_factory=TestSummary)
    warnings: tuple[ShortText, ...] = ()
    errors: tuple[ShortText, ...] = ()
    duration_seconds: Annotated[float, Field(ge=0)] | None = None
    final_stage: ExecutionStage | None = None
    usage: TokenUsage | None = None
    rate_limit_message: ShortText | None = None
    # Which *kind* of thing went wrong, derived from errors/outcome below. A dead provider
    # token and a gate the generated code could not pass are both "a failed run" and carry
    # opposite information about quality; a yield figure that mixes them measures nothing.
    failure_cause: FailureCause | None = None
    completed_at: datetime

    @model_validator(mode="before")
    @classmethod
    def derive_failure_cause(cls, data: Any) -> Any:
        """Filled in here rather than at each producer, so a new failure path cannot forget
        it. Not a computed field: these models round-trip through model_dump/model_validate
        for persistence, and extra="forbid" would reject a computed field on the way back."""
        if not isinstance(data, dict) or data.get("failure_cause") or data.get("success", True):
            return data
        return {**data, "failure_cause": classify_failure_cause(data.get("errors") or (), outcome=data.get("outcome"))}

    @field_validator("completed_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_datetime(value)


class ProjectExecution(StrictDomainModel):
    id: PublicId
    order_id: PublicId
    brief_id: PublicId
    handoff_id: PublicId
    approval_fingerprint: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None
    mode: ExecutionMode
    live: bool = False
    revised_from: PublicId | None = None
    status: ExecutionStatus = ExecutionStatus.QUEUED
    stage: ExecutionStage = ExecutionStage.REQUIREMENTS
    active_agent: ShortText | None = None
    progress: Annotated[int, Field(ge=0, le=100)] = 0
    current_activity: ShortText = "Queued for execution"
    blockers: tuple[ExecutionBlocker, ...] = ()
    # Set while status is AWAITING_USER: the run paused at a phase boundary to ask the
    # client something. Answering them resumes the same execution from its checkpoints.
    pending_questions: tuple[ClarificationQuestion, ...] = ()
    midbuild_answers: tuple[ClarificationAnswer, ...] = ()
    # Kept on the snapshot so a retry re-sends what the client asked for rather than
    # silently reverting to the unedited prompt.
    prompt_additions: LongText | None = None
    events: tuple[ExecutionEvent, ...] = ()
    artifacts: tuple[ExecutionArtifact, ...] = ()
    # Live coding pane: rolling assistant output and the file paths detected so far.
    # Kept off the activity timeline so token streaming cannot flood the event log.
    coding_transcript: Annotated[str, StringConstraints(max_length=24_000)] = ""
    coding_files: tuple[ShortText, ...] = ()
    result: ExecutionResult | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("created_at", "updated_at", "started_at", "finished_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware_datetime(value)

    @model_validator(mode="after")
    def validate_execution(self) -> "ProjectExecution":
        if len(self.events) > MAX_EVENT_LIMIT:
            raise ValueError("execution contains too many events")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        if self.finished_at is not None and self.started_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        terminal = self.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
        if terminal != (self.finished_at is not None):
            raise ValueError("terminal execution must have finished_at")
        if self.status is ExecutionStatus.SUCCEEDED and (self.result is None or not self.result.success):
            raise ValueError("successful execution requires a successful result")
        if self.status is ExecutionStatus.SUCCEEDED and (self.stage is not ExecutionStage.COMPLETED or self.progress != 100):
            raise ValueError("successful execution must complete every stage")
        if self.status in {ExecutionStatus.FAILED, ExecutionStatus.CANCELLED} and self.result is not None and self.result.success:
            raise ValueError("unsuccessful execution cannot contain a successful result")
        if self.result is not None and not terminal:
            raise ValueError("only terminal execution can contain a result")
        if any(event.execution_id != self.id for event in self.events):
            raise ValueError("event belongs to another execution")
        if any(artifact.execution_id != self.id for artifact in self.artifacts):
            raise ValueError("artifact belongs to another execution")
        artifact_ids = {artifact.id for artifact in self.artifacts}
        if self.result is not None and any(item not in artifact_ids for item in self.result.artifact_ids):
            raise ValueError("result references an unknown artifact")
        return self


def append_bounded_event(
    events: Iterable[ExecutionEvent],
    event: ExecutionEvent,
    *,
    limit: int = DEFAULT_EVENT_LIMIT,
) -> tuple[ExecutionEvent, ...]:
    if not 1 <= limit <= MAX_EVENT_LIMIT:
        raise ValueError(f"event limit must be between 1 and {MAX_EVENT_LIMIT}")
    current = tuple(events)
    if current and any(item.execution_id != event.execution_id for item in current):
        raise ValueError("events must belong to the same execution")
    return (current + (event,))[-limit:]
