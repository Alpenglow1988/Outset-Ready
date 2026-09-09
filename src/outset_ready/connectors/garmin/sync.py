from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from outset_ready.connectors.garmin.client import (
    GarminAuthenticationRequiredError,
    GarminClient,
    GarminConnectorError,
)
from outset_ready.connectors.garmin.config import GarminSettings
from outset_ready.connectors.garmin.normalise import (
    normalise_activity,
    normalise_daily_observation,
)
from outset_ready.connectors.garmin.raw_store import (
    raw_day_dir,
    save_activities_payload,
    save_daily_payload,
)
from outset_ready.domain import ConnectorSyncStatus
from outset_ready.storage import (
    DEFAULT_OWNER_ID,
    DatabaseTarget,
    connect,
    finish_connector_sync,
    init_db,
    start_connector_sync,
    upsert_activity,
    upsert_daily_observation,
)


OPTIONAL_PAYLOADS = (
    ("body_composition", "fetch_body_composition"),
    ("sleep", "fetch_sleep"),
    ("stress", "fetch_stress"),
    ("hrv", "fetch_hrv"),
)
MINIMUM_ACTIVITY_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class GarminSyncStats:
    start_date: date
    end_date: date
    daily_records: int
    activities_fetched: int
    activity_records: int
    payloads_saved: int
    warnings: tuple[str, ...]


def sync_garmin(
    settings: GarminSettings,
    *,
    days: int = 7,
    end_date: date | None = None,
    activity_page_size: int = 50,
    client_factory: Callable[[GarminSettings], GarminClient] = GarminClient,
    prompt_mfa: Callable[[], str] | None = None,
    token_bundle: str | None = None,
    token_updated: Callable[[str], None] | None = None,
    database_target: DatabaseTarget | None = None,
    owner_email: str = "owner@local",
    user_id: str = DEFAULT_OWNER_ID,
    save_raw_payloads: bool = True,
) -> GarminSyncStats:
    if days < 1:
        raise ValueError("days must be at least 1")
    if activity_page_size < 1:
        raise ValueError("activity_page_size must be at least 1")

    end_date = end_date or date.today()
    start_date = end_date - timedelta(days=days - 1)
    activity_start_date = min(
        start_date,
        end_date - timedelta(days=MINIMUM_ACTIVITY_LOOKBACK_DAYS - 1),
    )
    database_target = database_target or settings.db_path

    init_db(database_target, owner_email=owner_email, user_id=user_id)
    with connect(database_target) as conn:
        sync_id = start_connector_sync(
            conn,
            connector="garmin",
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
        )

    warnings: list[str] = []
    daily_records = 0
    activities_fetched = 0
    activity_records = 0
    payloads_saved = 0
    client = client_factory(settings)

    try:
        client.login(prompt_mfa=prompt_mfa, token_bundle=token_bundle)
        if token_updated is not None:
            token_updated(client.export_token_bundle())

        with connect(database_target) as conn:
            for payload_date in _date_range(start_date, end_date):
                payloads: dict[str, object] = {}
                try:
                    payload = client.fetch_user_summary(payload_date)
                except GarminAuthenticationRequiredError:
                    raise
                except GarminConnectorError as exc:
                    warnings.append(f"{payload_date} user summary: {exc}")
                else:
                    payloads["user_summary"] = payload
                    if save_raw_payloads:
                        save_daily_payload(
                            settings,
                            payload_date,
                            "user_summary",
                            payload,
                        )
                        payloads_saved += 1

                for payload_name, method_name in OPTIONAL_PAYLOADS:
                    fetch = getattr(client, method_name)
                    try:
                        payload = fetch(payload_date)
                    except GarminAuthenticationRequiredError:
                        raise
                    except GarminConnectorError as exc:
                        warnings.append(f"{payload_date} {payload_name}: {exc}")
                        continue
                    if payload is None:
                        continue
                    payloads[payload_name] = payload
                    if save_raw_payloads:
                        save_daily_payload(settings, payload_date, payload_name, payload)
                        payloads_saved += 1

                observation = normalise_daily_observation(
                    payload_date,
                    payloads,
                    source_ref=(
                        str(raw_day_dir(settings, payload_date))
                        if save_raw_payloads
                        else f"garmin:daily:{payload_date.isoformat()}"
                    ),
                )
                upsert_daily_observation(conn, observation, user_id=user_id)
                daily_records += 1

            try:
                activities = client.fetch_activities_since(
                    activity_start_date,
                    page_size=activity_page_size,
                )
            except GarminAuthenticationRequiredError:
                raise
            except GarminConnectorError as exc:
                warnings.append(f"activities: {exc}")
            else:
                activities_fetched = len(activities)
                raw_path = None
                if save_raw_payloads:
                    raw_path = save_activities_payload(
                        settings,
                        activity_start_date,
                        end_date,
                        activities,
                    )
                    payloads_saved += 1
                source_ref = (
                    str(raw_path)
                    if raw_path is not None
                    else (
                        "garmin:activities:"
                        f"{activity_start_date.isoformat()}:{end_date.isoformat()}"
                    )
                )
                for activity in activities:
                    record = normalise_activity(activity, source_ref=source_ref)
                    if record is None:
                        continue
                    if not activity_start_date <= record.recorded_on <= end_date:
                        continue
                    upsert_activity(conn, record, user_id=user_id)
                    activity_records += 1

            if token_updated is not None:
                token_updated(client.export_token_bundle())

            status = (
                ConnectorSyncStatus.COMPLETED_WITH_WARNINGS
                if warnings
                else ConnectorSyncStatus.COMPLETED
            )
            finish_connector_sync(
                conn,
                sync_id,
                status=status,
                daily_records=daily_records,
                activity_records=activity_records,
                warnings=len(warnings),
                user_id=user_id,
            )
    except Exception as exc:
        with connect(database_target) as conn:
            finish_connector_sync(
                conn,
                sync_id,
                status=ConnectorSyncStatus.FAILED,
                daily_records=daily_records,
                activity_records=activity_records,
                warnings=len(warnings),
                error_message=_safe_sync_error(exc),
                user_id=user_id,
            )
        raise

    return GarminSyncStats(
        start_date=start_date,
        end_date=end_date,
        daily_records=daily_records,
        activities_fetched=activities_fetched,
        activity_records=activity_records,
        payloads_saved=payloads_saved,
        warnings=tuple(warnings),
    )


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days + 1
    return [start_date + timedelta(days=offset) for offset in range(days)]


def _safe_sync_error(exc: Exception) -> str:
    if isinstance(exc, GarminAuthenticationRequiredError):
        return "Garmin requires reconnection."
    return "Garmin sync could not finish."
