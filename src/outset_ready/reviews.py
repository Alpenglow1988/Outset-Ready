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

        client = OpenAI(api_key=self._api_key, timeout=30.0, max_retries=1)
        response = client.responses.parse(
            model=self.model,
            store=False,
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
