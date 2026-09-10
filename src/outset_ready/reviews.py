from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from outset_ready.domain import Goal
from outset_ready.weekly import WeeklyRead


SNAPSHOT_VERSION = 1
PROMPT_VERSION = "weekly-review-v1"


@dataclass(frozen=True)
class ReviewSnapshot:
    data: dict[str, Any]
    json: str
    fingerprint: str


@dataclass(frozen=True)
class GeneratedInterpretation:
    what_went_well: str
    main_risk: str
    one_adjustment: str
    encouragement: str
    provider_response_id: str | None = None


@dataclass(frozen=True)
class InterpretationFailure:
    code: str
    public_message: str
    retryable: bool
    exception_type: str
    status_code: int | None = None
    provider_code: str | None = None
    request_id: str | None = None


class WeeklyReviewInterpreter(Protocol):
    provider: str
    model: str

    def interpret(self, snapshot: dict[str, Any]) -> GeneratedInterpretation: ...


class InterpretationOutput(BaseModel):
    what_went_well: str = Field(min_length=1, max_length=600)
    main_risk: str = Field(min_length=1, max_length=600)
    one_adjustment: str = Field(min_length=1, max_length=600)
    encouragement: str = Field(min_length=1, max_length=300)


class OpenAIWeeklyReviewInterpreter:
    provider = "openai"

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ValueError("An OpenAI API key is required.")
        if not model.strip():
            raise ValueError("An OpenAI model is required.")
        self.model = model.strip()
        self._api_key = api_key.strip()

    def interpret(self, snapshot: dict[str, Any]) -> GeneratedInterpretation:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("OpenAI support requires the openai package.") from exc

        client = OpenAI(api_key=self._api_key, timeout=60.0, max_retries=0)
        response = client.responses.parse(
            model=self.model,
            store=False,
            max_output_tokens=1_200,
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(snapshot, sort_keys=True),
                },
            ],
            text_format=InterpretationOutput,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("The model returned no weekly interpretation.")
        return GeneratedInterpretation(
            what_went_well=parsed.what_went_well.strip(),
            main_risk=parsed.main_risk.strip(),
            one_adjustment=parsed.one_adjustment.strip(),
            encouragement=parsed.encouragement.strip(),
            provider_response_id=getattr(response, "id", None),
        )


def classify_interpretation_error(exc: Exception) -> InterpretationFailure:
    exception_type = type(exc).__name__
    status_code = _optional_int(getattr(exc, "status_code", None))
    provider_code = _provider_error_code(exc)
    request_id = _optional_text(getattr(exc, "request_id", None))

    if status_code == 401 or exception_type == "AuthenticationError":
        code = "openai_authentication"
    elif provider_code in {
        "billing_hard_limit_reached",
        "billing_not_active",
        "insufficient_quota",
    }:
        code = "openai_quota"
    elif status_code == 429 or exception_type == "RateLimitError":
        code = "openai_rate_limit"
    elif status_code == 403 or exception_type == "PermissionDeniedError":
        code = "openai_permission"
    elif status_code == 404 or exception_type == "NotFoundError":
        code = "openai_model_access"
    elif status_code == 400 or exception_type in {
        "BadRequestError",
        "UnprocessableEntityError",
    }:
        code = "openai_request_invalid"
    elif "Timeout" in exception_type:
        code = "openai_timeout"
    elif "Connection" in exception_type:
        code = "openai_connection"
    elif status_code is not None and status_code >= 500:
        code = "openai_provider_error"
    else:
        code = "unexpected_error"

    public_message, retryable = _FAILURE_GUIDANCE[code]
    return InterpretationFailure(
        code=code,
        public_message=public_message,
        retryable=retryable,
        exception_type=exception_type,
        status_code=status_code,
        provider_code=provider_code,
        request_id=request_id,
    )


def interpretation_failure_message(failure_code: str | None) -> str:
    return _FAILURE_GUIDANCE.get(
        failure_code or "",
        _FAILURE_GUIDANCE["unexpected_error"],
    )[0]


def interpretation_failure_retryable(failure_code: str | None) -> bool:
    return _FAILURE_GUIDANCE.get(
        failure_code or "",
        _FAILURE_GUIDANCE["unexpected_error"],
    )[1]


_FAILURE_GUIDANCE = {
    "openai_authentication": (
        "OpenAI rejected the production API key. Replace "
        "OUTSET_READY_OPENAI_API_KEY and create a new production deployment "
        "before trying again.",
        False,
    ),
    "openai_quota": (
        "OpenAI reports no available API quota. Add API billing or raise the "
        "project usage limit before trying again.",
        False,
    ),
    "openai_rate_limit": (
        "OpenAI has rate-limited the request. Wait before trying again.",
        True,
    ),
    "openai_permission": (
        "OpenAI denied this key access to the configured project or model. "
        "Check the key permissions before trying again.",
        False,
    ),
    "openai_model_access": (
        "OpenAI could not access the configured model. Check "
        "OUTSET_READY_OPENAI_MODEL and the API project's model access before "
        "trying again.",
        False,
    ),
    "openai_request_invalid": (
        "OpenAI rejected the interpretation request format. Check the Vercel "
        "function log before retrying; repeated attempts will send the same request.",
        False,
    ),
    "openai_timeout": (
        "OpenAI did not respond within 60 seconds. Try once more.",
        True,
    ),
    "openai_connection": (
        "Ready could not reach OpenAI. Try once more after checking the service status.",
        True,
    ),
    "openai_provider_error": (
        "OpenAI returned a server error. Try once more later.",
        True,
    ),
    "unexpected_error": (
        "Ready hit an unexpected interpretation error. Check the Vercel function "
        "log before trying again.",
        False,
    ),
}


def _provider_error_code(exc: Exception) -> str | None:
    direct = _optional_text(getattr(exc, "code", None))
    if direct:
        return direct
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    nested = body.get("error")
    for candidate in (
        body.get("code"),
        nested.get("code") if isinstance(nested, dict) else None,
    ):
        value = _optional_text(candidate)
        if value:
            return value
    return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:120]
    return None


def build_review_snapshot(
    weekly_read: WeeklyRead,
    goals: list[Goal],
) -> ReviewSnapshot:
    weekly_data = asdict(weekly_read)
    weekly_data["plan"]["planned_sessions"] = weekly_read.plan.planned_sessions
    weekly_data["plan"]["completed_sessions"] = weekly_read.plan.completed_sessions
    weekly_data["plan"]["skipped_sessions"] = weekly_read.plan.skipped_sessions
    payload = _canonicalise(
        {
            "snapshot_version": SNAPSHOT_VERSION,
            "goals": [asdict(goal) for goal in goals],
            "weekly_read": weekly_data,
        }
    )
    snapshot_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ReviewSnapshot(
        data=payload,
        json=snapshot_json,
        fingerprint=hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
    )


def load_review_snapshot(snapshot_json: str) -> dict[str, Any]:
    payload = json.loads(snapshot_json)
    if not isinstance(payload, dict) or payload.get("snapshot_version") != SNAPSHOT_VERSION:
        raise ValueError("Unsupported weekly review snapshot.")
    if not isinstance(payload.get("weekly_read"), dict):
        raise ValueError("Weekly review snapshot is missing its evidence read.")
    if not isinstance(payload.get("goals"), list):
        raise ValueError("Weekly review snapshot is missing its goal context.")
    return payload


def _canonicalise(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonicalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalise(item) for item in value]
    return value


def _system_prompt() -> str:
    return (
        "You are a pragmatic health, fitness and adventure readiness reviewer. "
        "Use only the supplied confirmed weekly snapshot. Do not invent facts, "
        "diagnose a medical condition, declare expedition clearance, or generate "
        "a training programme. Treat Garmin recovery and calorie values as useful "
        "signals rather than exact truth. Treat smart-scale body composition as "
        "directional. Missing optional context means unknown, not zero. Respect the "
        "user's stated goal priority and the deterministic Ready status. Identify "
        "one practical adjustment at most. Use direct, concise British English."
    )
