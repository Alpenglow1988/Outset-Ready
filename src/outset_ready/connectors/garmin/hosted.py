from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from outset_ready.connectors.garmin.client import (
    GarminAuthenticationRequiredError,
    GarminClient,
)
from outset_ready.connectors.garmin.config import GarminSettings
from outset_ready.connectors.garmin.sync import GarminSyncStats, sync_garmin
from outset_ready.connectors.garmin.tokens import normalise_token_bundle
from outset_ready.credentials import CredentialCipher, CredentialEncryptionError
from outset_ready.domain import ConnectorConnectionStatus
from outset_ready.settings import AppSettings
from outset_ready.storage import (
    connect,
    delete_connector_connection,
    fetch_connector_connection,
    load_connector_credentials,
    mark_connector_connected,
    mark_connector_reconnect_required,
    save_connector_credentials,
    update_connector_credentials,
)


GARMIN_CONNECTOR = "garmin"


class GarminConnectionUnavailable(RuntimeError):
    """Raised when Ready cannot use the stored Garmin connection."""


def save_uploaded_token(
    settings: AppSettings,
    *,
    user_id: str,
    token_payload: str | bytes,
) -> None:
    canonical = normalise_token_bundle(token_payload)
    encrypted = CredentialCipher(settings.credential_encryption_key).encrypt(canonical)
    with connect(settings.database_target) as conn:
        save_connector_credentials(
            conn,
            connector=GARMIN_CONNECTOR,
            encrypted_credentials=encrypted,
            status=ConnectorConnectionStatus.TOKEN_SAVED,
            user_id=user_id,
        )


def sync_hosted_garmin(
    settings: AppSettings,
    *,
    user_id: str,
    days: int = 7,
    end_date: date | None = None,
    client_factory: Callable[[GarminSettings], GarminClient] = GarminClient,
) -> GarminSyncStats:
    cipher = CredentialCipher(settings.credential_encryption_key)
    with connect(settings.database_target) as conn:
        connection = fetch_connector_connection(
            conn,
            connector=GARMIN_CONNECTOR,
            user_id=user_id,
        )
        encrypted = load_connector_credentials(
            conn,
            connector=GARMIN_CONNECTOR,
            user_id=user_id,
        )
    if connection is None or encrypted is None:
        raise GarminConnectionUnavailable("No Garmin connection has been saved.")
    if connection.status is ConnectorConnectionStatus.RECONNECT_REQUIRED:
        raise GarminConnectionUnavailable("Garmin requires a replacement token file.")

    try:
        token_bundle = cipher.decrypt(encrypted)
    except CredentialEncryptionError as exc:
        _require_reconnect(settings, user_id=user_id)
        raise GarminConnectionUnavailable(
            "The saved Garmin connection cannot be read."
        ) from exc

    transient_settings = GarminSettings(
        email=None,
        password=None,
        token_dir=Path("/tmp/outset-ready-garmin-tokens"),
        data_dir=Path("/tmp/outset-ready-garmin-data"),
        db_path=Path("/tmp/outset-ready-unused.sqlite"),
    )

    def persist_refreshed_token(updated_bundle: str) -> None:
        encrypted_bundle = cipher.encrypt(normalise_token_bundle(updated_bundle))
        with connect(settings.database_target) as conn:
            update_connector_credentials(
                conn,
                connector=GARMIN_CONNECTOR,
                encrypted_credentials=encrypted_bundle,
                user_id=user_id,
            )

    try:
        stats = sync_garmin(
            transient_settings,
            days=days,
            end_date=end_date,
            client_factory=client_factory,
            token_bundle=token_bundle,
            token_updated=persist_refreshed_token,
            database_target=settings.database_target,
            owner_email=settings.owner_email,
            user_id=user_id,
            save_raw_payloads=False,
        )
    except GarminAuthenticationRequiredError:
        _require_reconnect(settings, user_id=user_id)
        raise

    with connect(settings.database_target) as conn:
        mark_connector_connected(
            conn,
            connector=GARMIN_CONNECTOR,
            user_id=user_id,
        )
    return stats


def disconnect_hosted_garmin(settings: AppSettings, *, user_id: str) -> None:
    with connect(settings.database_target) as conn:
        delete_connector_connection(
            conn,
            connector=GARMIN_CONNECTOR,
            user_id=user_id,
        )


def _require_reconnect(settings: AppSettings, *, user_id: str) -> None:
    with connect(settings.database_target) as conn:
        mark_connector_reconnect_required(
            conn,
            connector=GARMIN_CONNECTOR,
            user_id=user_id,
        )
