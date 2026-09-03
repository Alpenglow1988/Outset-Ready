from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from outset_ready.connectors.garmin.config import GarminSettings


JsonPayload = dict[str, Any] | list[Any]


def raw_day_dir(settings: GarminSettings, payload_date: date) -> Path:
    return settings.raw_dir / payload_date.isoformat()


def save_daily_payload(
    settings: GarminSettings,
    payload_date: date,
    payload_name: str,
    payload: JsonPayload,
) -> Path:
    path = raw_day_dir(settings, payload_date) / f"{payload_name}.json"
    _save_json(path, payload)
    return path


def save_activities_payload(
    settings: GarminSettings,
    start_date: date,
    end_date: date,
    payload: JsonPayload,
) -> Path:
    path = settings.raw_dir / "activities" / (
        f"{start_date.isoformat()}_to_{end_date.isoformat()}.json"
    )
    _save_json(path, payload)
    return path


def _save_json(path: Path, payload: JsonPayload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, sort_keys=True)
        file.write("\n")

