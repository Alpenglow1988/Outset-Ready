import json

import pytest

from outset_ready.connectors.garmin.tokens import (
    MAX_TOKEN_BUNDLE_BYTES,
    GarminTokenBundleError,
    normalise_token_bundle,
    write_token_bundle,
)


VALID_TOKEN = {
    "di_token": "access",
    "di_refresh_token": "refresh",
    "di_client_id": "client",
}


def test_token_bundle_keeps_only_reusable_fields():
    raw = json.dumps({**VALID_TOKEN, "unexpected": "discarded"})

    parsed = json.loads(normalise_token_bundle(raw))

    assert parsed == VALID_TOKEN


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b"{}",
        b'{"di_token":"access"}',
        b"x" * (MAX_TOKEN_BUNDLE_BYTES + 1),
    ],
)
def test_token_bundle_rejects_invalid_payloads(payload):
    with pytest.raises(GarminTokenBundleError):
        normalise_token_bundle(payload)


def test_token_export_uses_owner_only_file_permissions(tmp_path):
    output = write_token_bundle(
        tmp_path / "garmin-token.json",
        json.dumps(VALID_TOKEN),
    )

    assert json.loads(output.read_text(encoding="utf-8")) == VALID_TOKEN
    assert output.stat().st_mode & 0o777 == 0o600
