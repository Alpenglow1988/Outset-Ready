from __future__ import annotations

from datetime import date
from typing import Any, Callable

from outset_ready.connectors.garmin.config import GarminSettings
from outset_ready.connectors.garmin.normalise import extract_activity_date


Garmin: type[Any] | None = None


class GarminConnectorError(RuntimeError):
    """Base error for Garmin connector failures."""


class MissingGarminCredentialsError(GarminConnectorError):
    """Raised when Garmin credentials have not been configured."""


class OptionalGarminEndpointUnavailable(GarminConnectorError):
    """Raised when the installed client lacks an optional endpoint."""


class GarminClient:
    def __init__(self, settings: GarminSettings) -> None:
        self.settings = settings
        self._client: Any | None = None

    def login(self, prompt_mfa: Callable[[], str] | None = None) -> None:
        if not self.settings.email or not self.settings.password:
            raise MissingGarminCredentialsError(
                "Garmin credentials are missing. Set GARMIN_EMAIL and "
                "GARMIN_PASSWORD in .env or the current environment."
            )

        self.settings.token_dir.mkdir(parents=True, exist_ok=True)
        garmin_class = _get_garmin_class()
        kwargs = {"prompt_mfa": prompt_mfa} if prompt_mfa is not None else {}
        self._client = garmin_class(
            self.settings.email,
            self.settings.password,
            **kwargs,
        )
        try:
            self._client.login(tokenstore=str(self.settings.token_dir))
        except Exception as exc:
            raise GarminConnectorError(f"Garmin login failed: {exc}") from exc

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

    def _call(self, method_name: str, endpoint_name: str, *args: Any, **kwargs: Any) -> Any:
        if self._client is None:
            raise GarminConnectorError("Garmin client is not logged in.")
        method = getattr(self._client, method_name, None)
        if method is None:
            raise GarminConnectorError(f"Garmin {endpoint_name} endpoint is unavailable.")
        try:
            return method(*args, **kwargs)
        except Exception as exc:
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

