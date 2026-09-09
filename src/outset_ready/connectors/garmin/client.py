from __future__ import annotations

from datetime import date
from typing import Any, Callable

from outset_ready.connectors.garmin.config import GarminSettings
from outset_ready.connectors.garmin.normalise import (
    extract_activity_date,
    extract_scheduled_workout_date,
)
from outset_ready.connectors.garmin.tokens import normalise_token_bundle


Garmin: type[Any] | None = None


class GarminConnectorError(RuntimeError):
    """Base error for Garmin connector failures."""


class MissingGarminCredentialsError(GarminConnectorError):
    """Raised when Garmin credentials have not been configured."""


class GarminAuthenticationRequiredError(GarminConnectorError):
    """Raised when Garmin rejects reusable token material."""


class OptionalGarminEndpointUnavailable(GarminConnectorError):
    """Raised when the installed client lacks an optional endpoint."""


class GarminClient:
    def __init__(self, settings: GarminSettings) -> None:
        self.settings = settings
        self._client: Any | None = None

    def login(
        self,
        prompt_mfa: Callable[[], str] | None = None,
        *,
        token_bundle: str | None = None,
    ) -> None:
        if token_bundle is None and (
            not self.settings.email or not self.settings.password
        ):
            raise MissingGarminCredentialsError(
                "Garmin credentials are missing. Set GARMIN_EMAIL and "
                "GARMIN_PASSWORD in .env or the current environment."
            )

        garmin_class = _get_garmin_class()
        if token_bundle is not None:
            self._client = garmin_class()
            tokenstore = normalise_token_bundle(token_bundle)
        else:
            self.settings.token_dir.mkdir(parents=True, exist_ok=True)
            kwargs = {"prompt_mfa": prompt_mfa} if prompt_mfa is not None else {}
            self._client = garmin_class(
                self.settings.email,
                self.settings.password,
                **kwargs,
            )
            tokenstore = str(self.settings.token_dir)
        try:
            self._client.login(tokenstore=tokenstore)
        except Exception as exc:
            if _is_authentication_failure(exc):
                raise GarminAuthenticationRequiredError(
                    "Garmin rejected the saved connection. Reconnect Garmin."
                ) from exc
            if token_bundle is not None:
                raise GarminConnectorError(
                    "Garmin could not verify the saved connection."
                ) from exc
            raise GarminConnectorError(f"Garmin login failed: {exc}") from exc

    def export_token_bundle(self) -> str:
        if self._client is None:
            raise GarminConnectorError("Garmin client is not logged in.")
        internal_client = getattr(self._client, "client", None)
        dumps = getattr(internal_client, "dumps", None)
        if dumps is None:
            raise GarminConnectorError(
                "The installed Garmin client cannot export reusable tokens."
            )
        try:
            return normalise_token_bundle(dumps())
        except Exception as exc:
            raise GarminConnectorError("Garmin token export failed.") from exc

    def fetch_user_summary(self, payload_date: date) -> dict[str, Any]:
        response = self._call("get_user_summary", "user summary", payload_date.isoformat())
        if not isinstance(response, dict):
            raise GarminConnectorError("Garmin user summary was not an object.")
        return response

    def fetch_body_composition(self, payload_date: date) -> dict[str, Any]:
        day = payload_date.isoformat()
        return self._call_optional(
            "get_body_composition",
            "body composition",
            day,
            day,
        )

    def fetch_sleep(self, payload_date: date) -> dict[str, Any]:
        return self._call_optional("get_sleep_data", "sleep", payload_date.isoformat())

    def fetch_stress(self, payload_date: date) -> dict[str, Any]:
        return self._call_optional("get_stress_data", "stress", payload_date.isoformat())

    def fetch_hrv(self, payload_date: date) -> dict[str, Any] | None:
        return self._call_optional("get_hrv_data", "HRV", payload_date.isoformat())

    def fetch_activities_since(
        self,
        start_date: date,
        *,
        page_size: int = 50,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        activities: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for page_number in range(max_pages):
            page = _coerce_activity_list(
                self._call(
                    "get_activities",
                    "activities",
                    start=page_number * page_size,
                    limit=page_size,
                )
            )
            if not page:
                break

            oldest_date: date | None = None
            new_records = 0
            for activity in page:
                activity_date = _activity_date(activity)
                if activity_date is not None:
                    oldest_date = (
                        activity_date
                        if oldest_date is None
                        else min(oldest_date, activity_date)
                    )
                identity = _activity_identity(activity)
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
                activities.append(activity)
                new_records += 1

            if oldest_date is not None and oldest_date < start_date:
                break
            if len(page) < page_size or new_records == 0:
                break

        return activities

    def fetch_scheduled_workouts(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        if end_date < start_date:
            raise ValueError("The plan end date cannot precede its start date.")

        workouts: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        month = start_date.replace(day=1)
        final_month = end_date.replace(day=1)
        while month <= final_month:
            response = self._call(
                "get_scheduled_workouts",
                "scheduled workouts",
                month.year,
                month.month,
            )
            for workout in _coerce_scheduled_workout_list(response):
                scheduled_on = _scheduled_workout_date(workout)
                if scheduled_on is None or not start_date <= scheduled_on <= end_date:
                    continue
                identity = _scheduled_workout_identity(workout, scheduled_on)
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
                workouts.append(workout)

            month = (
                date(month.year + 1, 1, 1)
                if month.month == 12
                else date(month.year, month.month + 1, 1)
            )
        return workouts

    def _call(self, method_name: str, endpoint_name: str, *args: Any, **kwargs: Any) -> Any:
        if self._client is None:
            raise GarminConnectorError("Garmin client is not logged in.")
        method = getattr(self._client, method_name, None)
        if method is None:
            raise GarminConnectorError(f"Garmin {endpoint_name} endpoint is unavailable.")
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            if _is_authentication_failure(exc):
                raise GarminAuthenticationRequiredError(
                    "Garmin rejected the saved connection. Reconnect Garmin."
                ) from exc
            raise GarminConnectorError(f"Garmin {endpoint_name} fetch failed: {exc}") from exc

    def _call_optional(self, method_name: str, endpoint_name: str, *args: str) -> Any:
        if self._client is None:
            raise GarminConnectorError("Garmin client is not logged in.")
        if getattr(self._client, method_name, None) is None:
            raise OptionalGarminEndpointUnavailable(
                f"Garmin {endpoint_name} endpoint is unavailable in this client version."
            )
        return self._call(method_name, endpoint_name, *args)


def _get_garmin_class() -> type[Any]:
    global Garmin
    if Garmin is None:
        try:
            from garminconnect import Garmin as imported_garmin
        except ImportError as exc:
            raise GarminConnectorError(
                "The garminconnect package is not installed. Install Outset Ready first."
            ) from exc
        Garmin = imported_garmin
    return Garmin


def _coerce_activity_list(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        for key in ("activities", "activityList", "data", "results"):
            value = response.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_scheduled_workout_list(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        for key in (
            "calendarItems",
            "scheduledWorkouts",
            "workouts",
            "items",
            "data",
            "results",
        ):
            value = response.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _activity_date(activity: dict[str, Any]) -> date | None:
    value = extract_activity_date(activity)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _activity_identity(activity: dict[str, Any]) -> str:
    for key in ("activityId", "activity_id", "id"):
        value = activity.get(key)
        if value is not None:
            return f"{key}:{value}"
    return repr(sorted(activity.items()))


def _scheduled_workout_date(workout: dict[str, Any]) -> date | None:
    value = extract_scheduled_workout_date(workout)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _scheduled_workout_identity(workout: dict[str, Any], scheduled_on: date) -> str:
    for key in ("scheduledWorkoutId", "calendarItemId", "calendarId", "id"):
        value = workout.get(key)
        if value is not None:
            return f"{key}:{value}"
    for key in ("workoutId", "workout_id"):
        value = workout.get(key)
        if value is not None:
            return f"{key}:{value}:{scheduled_on.isoformat()}"
    return f"fallback:{scheduled_on.isoformat()}:{repr(sorted(workout.items()))}"


def _is_authentication_failure(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).casefold()
        if any(
            marker in message
            for marker in (
                "401",
                "403",
                "unauthorized",
                "authentication failed",
                "token rejected",
                "missing tokens",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False
