from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints

from backend_security import StrictRequestModel


class StrictApiModel(StrictRequestModel):
    model_config = ConfigDict(extra="forbid")


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]


class CreateOrderRequest(StrictApiModel):
    title: ShortText
    description: LongText
    product_type: ShortText
    preferred_language: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=16)] = "en"
    constraints: tuple[ShortText, ...] = ()
    attachments: tuple[ShortText, ...] = ()


class AnswerItem(StrictApiModel):
    question_id: ShortText
    value: Any


class AnswersRequest(StrictApiModel):
    answers: Annotated[tuple[AnswerItem, ...], Field(min_length=1, max_length=20)]


class DefaultsRequest(StrictApiModel):
    use_recommended_defaults: Literal[True] = True


class RevisionOperationRequest(StrictApiModel):
    kind: Literal[
        "add_requirement",
        "remove_requirement",
        "change_assumption",
        "change_acceptance_criterion",
        "change_elena_choice",
    ]
    value: str | None = None
    previous_value: str | None = None
    elena_choice: Literal["show_elena_concept", "proceed_without_concept", "undecided", "not_applicable"] | None = None


class ReviseBriefRequest(StrictApiModel):
    operations: Annotated[tuple[RevisionOperationRequest, ...], Field(min_length=1, max_length=20)]


class ApproveBriefRequest(StrictApiModel):
    revision: Annotated[int, Field(ge=1)]
    fingerprint: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None


class StartExecutionRequest(StrictApiModel):
    mode: Literal["fake", "production"] = "fake"


class NextAction(StrictApiModel):
    code: str
    message: str


class ErrorResponse(StrictApiModel):
    code: str
    message: str
