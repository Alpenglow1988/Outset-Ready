from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any


MAX_TOKEN_BUNDLE_BYTES = 16_384
REQUIRED_TOKEN_FIELDS = ("di_token", "di_refresh_token", "di_client_id")


class GarminTokenBundleError(ValueError):
    """Raised when a Garmin token bundle is unsafe or incomplete."""


def normalise_token_bundle(payload: str | bytes) -> str:
    raw = _decode_payload(payload)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GarminTokenBundleError("The Garmin token file is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise GarminTokenBundleError("The Garmin token file must contain one JSON object.")

    clean: dict[str, str] = {}
    for field in REQUIRED_TOKEN_FIELDS:
        value = parsed.get(field)
        if not isinstance(value, str) or not value.strip():
            raise GarminTokenBundleError(
                "The Garmin token file does not contain all reusable token fields."
            )
        clean[field] = value
    return json.dumps(clean, separators=(",", ":"), sort_keys=True)


def write_token_bundle(path: str | Path, payload: str) -> Path:
    output_path = Path(path).expanduser()
    canonical = normalise_token_bundle(payload)
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        output_path.parent.chmod(0o700)
    except OSError:
        pass

    temporary_path = output_path.with_name(
        f".{output_path.name}.{secrets.token_hex(8)}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary_path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
            token_file.write(canonical)
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(output_path)
    except Exception:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise
    return output_path


def _decode_payload(payload: str | bytes) -> str:
    if isinstance(payload, bytes):
        if len(payload) > MAX_TOKEN_BUNDLE_BYTES:
            raise GarminTokenBundleError("The Garmin token file is too large.")
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GarminTokenBundleError(
                "The Garmin token file must use UTF-8 text."
            ) from exc
    if len(payload.encode("utf-8")) > MAX_TOKEN_BUNDLE_BYTES:
        raise GarminTokenBundleError("The Garmin token file is too large.")
    return payload
