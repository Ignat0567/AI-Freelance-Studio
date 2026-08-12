from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from enum import Enum
import re
from typing import Annotated

from pydantic import Field, model_validator

from .models import (
    ClarificationAnswer,
    ClarificationQuestion,
    ElenaDesignChoice,
    ProductType,
    QuestionType,
    StrictDomainModel,
    SUPPORTED_PRODUCT_TYPES,
    UserOrder,
    UserOrderStatus,
    utc_now,
)


MAX_CLARIFICATION_ROUNDS = 3
MAX_QUESTIONS_PER_ROUND = 8


class ClarificationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RequirementDimension(str, Enum):
    TARGET_USERS = "target_users"
    CORE_FEATURES = "core_features"
    AUTHENTICATION = "authentication"
    DATA_PERSISTENCE = "data_persistence"
    EXTERNAL_INTEGRATIONS = "external_integrations"
    UI_REQUIREMENT = "ui_requirement"
    DESIGN_PREFERENCE = "design_preference"
    DEPLOYMENT_EXPECTATION = "deployment_expectation"
    INPUT_METHODS = "input_methods"
    OUTPUT_METHODS = "output_methods"
    PRIVACY_MODE = "privacy_mode"
    DOCUMENT_SUPPORT = "document_support"
    LANGUAGE_SUPPORT = "language_support"


_DIMENSION_ORDER = (
    RequirementDimension.TARGET_USERS,
    RequirementDimension.CORE_FEATURES,
    RequirementDimension.AUTHENTICATION,
    RequirementDimension.DOCUMENT_SUPPORT,
    RequirementDimension.LANGUAGE_SUPPORT,
    RequirementDimension.DATA_PERSISTENCE,
    RequirementDimension.PRIVACY_MODE,
    RequirementDimension.EXTERNAL_INTEGRATIONS,
    RequirementDimension.UI_REQUIREMENT,
    RequirementDimension.DESIGN_PREFERENCE,
    RequirementDimension.DEPLOYMENT_EXPECTATION,
    RequirementDimension.INPUT_METHODS,
    RequirementDimension.OUTPUT_METHODS,
)
_QUESTION_DIMENSIONS = {
    "target-users": RequirementDimension.TARGET_USERS,
    "core-features": RequirementDimension.CORE_FEATURES,
    "authentication": RequirementDimension.AUTHENTICATION,
    "document-ocr": RequirementDimension.DOCUMENT_SUPPORT,
    "speech-languages": RequirementDimension.LANGUAGE_SUPPORT,
    "document-persistence": RequirementDimension.DATA_PERSISTENCE,
    "document-processing": RequirementDimension.PRIVACY_MODE,
    "elena-design": RequirementDimension.DESIGN_PREFERENCE,
}
_MANDATORY_DIMENSIONS = frozenset(
    {
        RequirementDimension.TARGET_USERS,
        RequirementDimension.CORE_FEATURES,
        RequirementDimension.LANGUAGE_SUPPORT,
        RequirementDimension.DESIGN_PREFERENCE,
    }
)
class RequirementSignals(StrictDomainModel):
    target_users: str | None = None
    has_material_features: bool = False
    authentication_resolved: bool = False
    data_persistence_resolved: bool = False
    documents_persist: bool | None = None
    external_integrations_resolved: bool = False
    ui_required: bool = True
    input_methods: tuple[str, ...] = ()
    output_methods: tuple[str, ...] = ()
    privacy_mode_resolved: bool = False
    processing_mode: str | None = None
    pdf_documents: bool = False
    ocr_resolved: bool = True
    ocr_supported: bool | None = None
    language_support_resolved: bool = True
    speech_languages: tuple[str, ...] = ()
    grounded_answers: bool = False
    citations: bool = False
    authentication: str | None = None


class GapAnalysis(StrictDomainModel):
    signals: RequirementSignals
    missing_dimensions: tuple[RequirementDimension, ...]


class ClarificationSession(StrictDomainModel):
    order_id: str
    round_number: Annotated[int, Field(ge=0, le=MAX_CLARIFICATION_ROUNDS)] = 0
    asked_question_ids: tuple[str, ...] = ()
    answered_question_ids: tuple[str, ...] = ()
    remaining_dimensions: tuple[RequirementDimension, ...] = ()
    assumptions: tuple[str, ...] = ()
    recommended_defaults_used: tuple[str, ...] = ()
    elena_choice: ElenaDesignChoice = ElenaDesignChoice.UNDECIDED

    @model_validator(mode="after")
    def validate_session(self) -> "ClarificationSession":
        for values in (self.asked_question_ids, self.answered_question_ids, self.remaining_dimensions, self.assumptions, self.recommended_defaults_used):
            if len(values) != len(set(values)):
                raise ValueError("clarification session contains duplicate values")
        if any(item not in self.asked_question_ids for item in self.answered_question_ids):
            raise ValueError("answered question was not asked")
        return self


class ClarificationResult(StrictDomainModel):
    order: UserOrder
    session: ClarificationSession


def _contains(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _answer_map(order: UserOrder) -> dict[str, ClarificationAnswer]:
    return {answer.question_id: answer for answer in order.answers}


def infer_requirement_signals(order: UserOrder) -> RequirementSignals:
    text = " ".join((order.title, order.description, *order.constraints)).casefold()
    answers = _answer_map(order)
    target_users = None
    if _contains(text, r"\b(only me|personal|single[- ]user|for myself)\b"):
        target_users = "Only me"
    elif _contains(text, r"\b(team|employees|staff|coworkers)\b"):
        target_users = "My team"
    elif _contains(text, r"\b(customers|clients|patients)\b"):
        target_users = "My customers"
    elif _contains(text, r"\b(public users|everyone|general public)\b"):
        target_users = "Public users"
    if "target-users" in answers:
        target_users = str(answers["target-users"].value)

    no_ui = _contains(text, r"\b(api|backend)[ -]only\b", r"\bno (user interface|ui|frontend)\b")
    pdf_documents = _contains(text, r"\bpdfs?\b", r"portable document")
    voice_input = _contains(text, r"\bvoice (input|question|recognition|assistant)\b", r"push[- ]to[- ]talk", r"\bspoken question")
    text_input = _contains(text, r"\btext (input|question|chat)\b", r"\btyped? questions?\b", r"voice or text", r"text or voice")
    speech_output = _contains(text, r"spoken aloud", r"hear the answers?", r"speech (output|synthesis)", r"text[- ]to[- ]speech")
    visual_output = _contains(text, r"\banswers?\b", r"\bresults?\b", r"\bstatus\b")
    grounded = _contains(text, r"grounded", r"restricted to .*documents?", r"based (only )?on .*documents?", r"contents? of .*pdf")
    citations = _contains(text, r"page (references?|numbers?|citations?)", r"document .* page", r"\bcitations?\b")
    ocr_answer = answers.get("document-ocr")
    if ocr_answer is not None:
        ocr_supported = bool(ocr_answer.value)
    elif _contains(text, r"\b(no ocr|text[- ]based pdf|selectable text only)\b"):
        ocr_supported = False
    elif _contains(text, r"support .*scanned pdf", r"\bocr required\b"):
        ocr_supported = True
    else:
        ocr_supported = None
    ocr_resolved = not pdf_documents or ocr_supported is not None

    language_answer = answers.get("speech-languages")
    if language_answer is not None and isinstance(language_answer.value, tuple):
        speech_languages = language_answer.value
    else:
        speech_languages = tuple(
            language.title()
            for language in ("english", "russian", "german", "spanish", "french")
            if re.search(rf"\b{language}\b", text)
        )
    language_resolved = not voice_input or bool(speech_languages)

    persistence_answer = answers.get("document-persistence")
    if persistence_answer is not None:
        documents_persist = bool(persistence_answer.value)
    elif _contains(text, r"persist", r"after restart", r"remain available"):
        documents_persist = True
    elif _contains(text, r"temporary upload", r"delete .* reload"):
        documents_persist = False
    else:
        documents_persist = None
    persistence_resolved = documents_persist is not None

    processing_answer = answers.get("document-processing")
    processing_mode = str(processing_answer.value) if processing_answer is not None else None
    if processing_mode is None:
        if _contains(text, r"fully local"):
            processing_mode = "Fully local"
        elif _contains(text, r"retrieved fragments", r"external llm"):
            processing_mode = "Local extraction; retrieved fragments may use an external LLM"
        elif _contains(text, r"cloud[- ]assisted"):
            processing_mode = "Cloud-assisted processing"
    privacy_resolved = processing_mode is not None

    authentication_answer = answers.get("authentication")
    authentication = str(authentication_answer.value) if authentication_answer is not None else None
    auth_resolved = authentication is not None or _contains(text, r"\b(login|sign[- ]in|accounts?|authentication|no auth)\b") or target_users == "Only me"
    integration_resolved = _contains(text, r"\b(llm|openai|ollama|provider|external api|no external integration)\b")
    material_features = len(order.description.strip()) >= 50 and _contains(
        text,
        r"\b(upload|create|manage|book|track|search|ask|answer|dashboard|chat|assistant|process|list|remove)\w*\b",
    )
    return RequirementSignals(
        target_users=target_users,
        has_material_features=material_features,
        authentication_resolved=auth_resolved,
        data_persistence_resolved=persistence_resolved,
        documents_persist=documents_persist,
        external_integrations_resolved=integration_resolved,
        # A Telegram bot has no BROWSER ui in the sense this signal gates (Elena's visual
        # design step) -- it has its own chat "UI" that isn't a design-preview concern.
        ui_required=order.product_type is not ProductType.BOT and not no_ui,
        input_methods=tuple(item for item, present in (("text", text_input), ("voice", voice_input)) if present),
        output_methods=tuple(item for item, present in (("visual", visual_output), ("speech", speech_output)) if present),
        privacy_mode_resolved=privacy_resolved,
        processing_mode=processing_mode,
        pdf_documents=pdf_documents,
        ocr_resolved=ocr_resolved,
        ocr_supported=ocr_supported,
        language_support_resolved=language_resolved,
        speech_languages=speech_languages,
        grounded_answers=grounded,
        citations=citations,
        authentication=authentication,
    )


def detect_requirement_gaps(order: UserOrder) -> GapAnalysis:
    if order.product_type not in SUPPORTED_PRODUCT_TYPES:
        raise ClarificationError("unsupported_product_type")
    signals = infer_requirement_signals(order)
    missing: set[RequirementDimension] = set()
    if signals.target_users is None:
        missing.add(RequirementDimension.TARGET_USERS)
    if not signals.has_material_features:
        missing.add(RequirementDimension.CORE_FEATURES)
    if not signals.authentication_resolved and signals.target_users not in {None, "Only me"}:
        missing.add(RequirementDimension.AUTHENTICATION)
    if signals.pdf_documents and not signals.ocr_resolved:
        missing.add(RequirementDimension.DOCUMENT_SUPPORT)
    if signals.pdf_documents and not signals.data_persistence_resolved:
        missing.add(RequirementDimension.DATA_PERSISTENCE)
    if signals.pdf_documents and not signals.privacy_mode_resolved:
        missing.add(RequirementDimension.PRIVACY_MODE)
    if "voice" in signals.input_methods and not signals.language_support_resolved:
        missing.add(RequirementDimension.LANGUAGE_SUPPORT)
    if signals.ui_required:
        missing.add(RequirementDimension.DESIGN_PREFERENCE)
    return GapAnalysis(
        signals=signals,
        missing_dimensions=tuple(item for item in _DIMENSION_ORDER if item in missing),
    )


def _language_recommendation(order: UserOrder) -> tuple[str, ...]:
    language = order.preferred_language.casefold()
    if language.startswith("ru"):
        return ("Russian", "German")
    if language.startswith("de"):
        return ("German", "Russian")
    return ("English",)


def _question_for(dimension: RequirementDimension, order: UserOrder) -> ClarificationQuestion:
    if dimension is RequirementDimension.TARGET_USERS:
        return ClarificationQuestion(
            id="target-users",
            text="Who will use this application?",
            type=QuestionType.SINGLE_SELECT,
            options=("Only me", "My team", "My customers", "Public users"),
            required=True,
            reason="This determines access and authentication needs.",
            recommended_answer="Only me",
        )
    if dimension is RequirementDimension.CORE_FEATURES:
        return ClarificationQuestion(
            id="core-features",
            text="What are the essential actions the application must support?",
            type=QuestionType.LONG_TEXT,
            required=True,
            reason="These actions define the first usable version and its acceptance criteria.",
            recommended_answer=None,
        )
    if dimension is RequirementDimension.AUTHENTICATION:
        return ClarificationQuestion(
            id="authentication",
            text="Should users sign in?",
            type=QuestionType.SINGLE_SELECT,
            options=("No sign-in", "Simple local sign-in", "User accounts"),
            required=False,
            reason="Authentication changes data ownership and security requirements.",
            recommended_answer="No sign-in",
        )
    if dimension is RequirementDimension.DOCUMENT_SUPPORT:
        return ClarificationQuestion(
            id="document-ocr",
            text="Should scanned PDFs without selectable text be supported?",
            type=QuestionType.BOOLEAN,
            required=False,
            reason="Scanned documents require OCR and a larger processing pipeline.",
            recommended_answer=False,
        )
    if dimension is RequirementDimension.LANGUAGE_SUPPORT:
        return ClarificationQuestion(
            id="speech-languages",
            text="Which languages should voice recognition and speech output support?",
            type=QuestionType.MULTI_SELECT,
            options=("English", "Russian", "German", "Spanish", "French"),
            required=True,
            reason="Browser speech capability and interface behavior vary by language.",
            recommended_answer=_language_recommendation(order),
        )
    if dimension is RequirementDimension.DATA_PERSISTENCE:
        return ClarificationQuestion(
            id="document-persistence",
            text="Should uploaded documents remain available after restart?",
            type=QuestionType.BOOLEAN,
            required=False,
            reason="This determines local storage and document lifecycle behavior.",
            recommended_answer=True,
        )
    if dimension is RequirementDimension.PRIVACY_MODE:
        return ClarificationQuestion(
            id="document-processing",
            text="How should document processing use local and cloud services?",
            type=QuestionType.SINGLE_SELECT,
            options=("Fully local", "Local extraction; retrieved fragments may use an external LLM", "Cloud-assisted processing"),
            required=False,
            reason="This determines provider readiness and what document content may leave the device.",
            recommended_answer="Local extraction; retrieved fragments may use an external LLM",
        )
    if dimension is RequirementDimension.DESIGN_PREFERENCE:
        return ClarificationQuestion(
            id="elena-design",
            text="Should Elena prepare a design concept before implementation?",
            type=QuestionType.SINGLE_SELECT,
            options=("Show a preliminary Elena design concept", "Proceed directly to implementation"),
            required=True,
            reason="This decides whether the visual direction is reviewed before coding starts.",
            recommended_answer="Show a preliminary Elena design concept",
        )
    raise ClarificationError("unsupported_requirement_dimension")


_DEFAULT_ASSUMPTIONS = {
    "target-users": "The first version is for single-user local usage.",
    "core-features": "The first version uses the smallest feature set explicitly described in the order.",
    "authentication": "No sign-in is required for the single-user local first version.",
    "document-ocr": "The first version supports text-based PDFs and does not include OCR.",
    "document-persistence": "Uploaded documents persist locally across normal reloads and application restarts.",
    "document-processing": "PDF extraction and indexing stay local; only retrieved fragments may be sent to the selected external LLM.",
    "elena-design": "Elena will prepare a preliminary structured design concept before implementation.",
}


def _default_assumption(answer: ClarificationAnswer) -> str | None:
    if answer.question_id == "speech-languages" and isinstance(answer.value, tuple):
        languages = " and ".join(answer.value)
        return f"Speech recognition and output support {languages}, with one selected interface language initially."
    return _DEFAULT_ASSUMPTIONS.get(answer.question_id)


def _elena_choice(answer: ClarificationAnswer | None, *, ui_required: bool) -> ElenaDesignChoice:
    if not ui_required:
        return ElenaDesignChoice.NOT_APPLICABLE
    if answer is None:
        return ElenaDesignChoice.UNDECIDED
    if answer.value == "Show a preliminary Elena design concept":
        return ElenaDesignChoice.SHOW_ELENA_CONCEPT
    if answer.value == "Proceed directly to implementation":
        return ElenaDesignChoice.PROCEED_WITHOUT_CONCEPT
    raise ClarificationError("invalid_elena_choice")


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in values if item))


class AlexClarificationService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        max_rounds: int = MAX_CLARIFICATION_ROUNDS,
        max_questions_per_round: int = MAX_QUESTIONS_PER_ROUND,
    ) -> None:
        if not 1 <= max_rounds <= MAX_CLARIFICATION_ROUNDS:
            raise ValueError("max_rounds is invalid")
        if not 1 <= max_questions_per_round <= MAX_QUESTIONS_PER_ROUND:
            raise ValueError("max_questions_per_round is invalid")
        self._clock = clock
        self._max_rounds = max_rounds
        self._max_questions = max_questions_per_round

    def begin(self, order: UserOrder) -> ClarificationResult:
        if order.status is not UserOrderStatus.DRAFT:
            raise ClarificationError("clarification_invalid_order_state")
        analysis = detect_requirement_gaps(order)
        questions = tuple(_question_for(item, order) for item in analysis.missing_dimensions[: self._max_questions])
        status = UserOrderStatus.CLARIFICATION_REQUIRED if questions else UserOrderStatus.BRIEF_READY
        now = utc_now(self._clock)
        updated = order.model_copy(
            update={
                "status": status,
                "clarification_round": 1 if questions else 0,
                "questions": questions,
                "updated_at": now,
            }
        )
        choice = ElenaDesignChoice.NOT_APPLICABLE if not analysis.signals.ui_required else ElenaDesignChoice.UNDECIDED
        session = ClarificationSession(
            order_id=order.id,
            round_number=1 if questions else 0,
            asked_question_ids=tuple(question.id for question in questions),
            remaining_dimensions=analysis.missing_dimensions,
            elena_choice=choice,
        )
        return ClarificationResult(order=updated, session=session)

    def apply_answers(
        self,
        order: UserOrder,
        session: ClarificationSession,
        answers: Iterable[ClarificationAnswer],
    ) -> ClarificationResult:
        self._validate_pair(order, session)
        questions = {question.id: question for question in order.questions}
        existing = _answer_map(order)
        combined = dict(existing)
        for answer in tuple(answers):
            question = questions.get(answer.question_id)
            if question is None:
                raise ClarificationError("unknown_question_id")
            prior = existing.get(answer.question_id)
            if prior is not None:
                if prior == answer:
                    continue
                raise ClarificationError("duplicate_answer_conflict")
            try:
                UserOrder._validate_answer(question, answer)
            except ValueError:
                raise ClarificationError("invalid_answer") from None
            combined[answer.question_id] = answer

        answered_ids = _unique((*session.answered_question_ids, *combined.keys()))
        remaining = tuple(
            dimension
            for dimension in session.remaining_dimensions
            if not any(_QUESTION_DIMENSIONS.get(question_id) is dimension for question_id in answered_ids)
        )
        signals = infer_requirement_signals(order.model_copy(update={"answers": tuple(combined.values())}))
        choice = _elena_choice(combined.get("elena-design"), ui_required=signals.ui_required)
        status = UserOrderStatus.BRIEF_READY if not remaining else UserOrderStatus.CLARIFICATION_REQUIRED
        updated = order.model_copy(
            update={
                "answers": tuple(combined.values()),
                "status": status,
                "updated_at": utc_now(self._clock),
            }
        )
        updated_session = session.model_copy(
            update={
                "answered_question_ids": answered_ids,
                "remaining_dimensions": remaining,
                "elena_choice": choice,
            }
        )
        return ClarificationResult(order=updated, session=updated_session)

    def use_recommended_defaults(self, order: UserOrder, session: ClarificationSession) -> ClarificationResult:
        self._validate_pair(order, session)
        existing = _answer_map(order)
        defaults = tuple(
            ClarificationAnswer(question_id=question.id, value=question.recommended_answer)
            for question in order.questions
            if question.id not in existing and question.recommended_answer is not None
        )
        result = self.apply_answers(order, session, defaults)
        unasked_dimensions = tuple(
            item
            for item in result.session.remaining_dimensions
            if _question_for(item, result.order).id not in result.session.asked_question_ids
        )
        if unasked_dimensions:
            added = tuple(_question_for(item, result.order) for item in unasked_dimensions)
            expanded_order = result.order.model_copy(update={"questions": (*result.order.questions, *added)})
            expanded_session = result.session.model_copy(
                update={"asked_question_ids": _unique((*result.session.asked_question_ids, *(item.id for item in added)))}
            )
            added_defaults = tuple(
                ClarificationAnswer(question_id=item.id, value=item.recommended_answer)
                for item in added
                if item.recommended_answer is not None
            )
            result = self.apply_answers(expanded_order, expanded_session, added_defaults)
            defaults = (*defaults, *added_defaults)
        used = _unique((*session.recommended_defaults_used, *(answer.question_id for answer in defaults)))
        assumptions = list(result.session.assumptions)
        assumptions.extend(
            assumption
            for answer in defaults
            if (assumption := _default_assumption(answer)) is not None
        )
        signals = infer_requirement_signals(result.order)
        if signals.pdf_documents:
            assumptions.extend(
                (
                    "The first version supports one or several text-based PDF documents.",
                    "Questions use text and push-to-talk input with browser speech recognition where available.",
                    "Answers are restricted to uploaded documents and include document names and page citations.",
                    "Answers use browser speech synthesis and provide a stop-speech action.",
                )
            )
        updated_session = result.session.model_copy(
            update={
                "recommended_defaults_used": used,
                "assumptions": _unique(assumptions),
                "remaining_dimensions": result.session.remaining_dimensions,
            }
        )
        updated_order = result.order.model_copy(
            update={
                "status": UserOrderStatus.BRIEF_READY
                if not updated_session.remaining_dimensions
                else UserOrderStatus.CLARIFICATION_REQUIRED
            }
        )
        return ClarificationResult(order=updated_order, session=updated_session)

    def advance_round(self, order: UserOrder, session: ClarificationSession) -> ClarificationResult:
        self._validate_pair(order, session)
        if order.status is UserOrderStatus.BRIEF_READY:
            return ClarificationResult(order=order, session=session)
        if session.round_number >= self._max_rounds:
            return self._finish_at_round_limit(order, session)
        analysis = detect_requirement_gaps(order)
        already_asked = set(session.asked_question_ids)
        new_dimensions = tuple(
            item for item in analysis.missing_dimensions if _question_for(item, order).id not in already_asked
        )
        new_questions = tuple(_question_for(item, order) for item in new_dimensions[: self._max_questions])
        updated = order.model_copy(
            update={
                "questions": (*order.questions, *new_questions),
                "clarification_round": session.round_number + 1,
                "updated_at": utc_now(self._clock),
            }
        )
        updated_session = session.model_copy(
            update={
                "round_number": session.round_number + 1,
                "asked_question_ids": _unique((*session.asked_question_ids, *(item.id for item in new_questions))),
                "remaining_dimensions": tuple(
                    item for item in _DIMENSION_ORDER if item in set((*session.remaining_dimensions, *new_dimensions))
                ),
            }
        )
        return ClarificationResult(order=updated, session=updated_session)

    def _finish_at_round_limit(self, order: UserOrder, session: ClarificationSession) -> ClarificationResult:
        unanswered = {question.id: question for question in order.questions if question.id not in session.answered_question_ids}
        required = tuple(question for question in unanswered.values() if question.required)
        optional = tuple(question for question in unanswered.values() if not question.required)
        unasked_optional = tuple(
            _question_for(item, order)
            for item in session.remaining_dimensions
            if item not in _MANDATORY_DIMENSIONS and _question_for(item, order).id not in session.asked_question_ids
        )
        assumptions = _unique(
            (
                *session.assumptions,
                *(_DEFAULT_ASSUMPTIONS[item.id] for item in (*optional, *unasked_optional) if item.id in _DEFAULT_ASSUMPTIONS),
            )
        )
        remaining_set = {_QUESTION_DIMENSIONS[item.id] for item in required if item.id in _QUESTION_DIMENSIONS}
        remaining_set.update(item for item in session.remaining_dimensions if item in _MANDATORY_DIMENSIONS)
        remaining = tuple(item for item in _DIMENSION_ORDER if item in remaining_set)
        status = UserOrderStatus.CLARIFICATION_REQUIRED if remaining else UserOrderStatus.BRIEF_READY
        return ClarificationResult(
            order=order.model_copy(update={"status": status, "updated_at": utc_now(self._clock)}),
            session=session.model_copy(update={"remaining_dimensions": remaining, "assumptions": assumptions}),
        )

    @staticmethod
    def _validate_pair(order: UserOrder, session: ClarificationSession) -> None:
        if order.id != session.order_id:
            raise ClarificationError("clarification_order_mismatch")
        if order.status not in {UserOrderStatus.CLARIFICATION_REQUIRED, UserOrderStatus.BRIEF_READY}:
            raise ClarificationError("clarification_invalid_order_state")
