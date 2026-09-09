import json

from outset_ready.cli import main
from outset_ready.credentials import CredentialCipher


TOKEN = json.dumps(
    {
        "di_token": "access",
        "di_refresh_token": "refresh",
        "di_client_id": "client",
    }
)


def test_generate_encryption_key_command_prints_usable_key(capsys):
    assert main(["generate-encryption-key"]) == 0

    key = capsys.readouterr().out.strip()
    assert CredentialCipher(key).decrypt(CredentialCipher(key).encrypt("value")) == "value"


def test_export_garmin_token_writes_file_without_printing_token(
    monkeypatch,
    tmp_path,
    capsys,
):
    calls = []

    class FakeClient:
        def __init__(self, settings):
            calls.append(settings)

        def login(self, prompt_mfa=None):
            self.prompt_mfa = prompt_mfa

        def export_token_bundle(self):
            return TOKEN

    monkeypatch.setattr(
        "outset_ready.connectors.garmin.client.GarminClient",
        FakeClient,
    )
    monkeypatch.setattr("outset_ready.cli.load_garmin_settings", lambda: "settings")
    output = tmp_path / "garmin-token.json"

    assert main(["export-garmin-token", "--output", str(output)]) == 0

    terminal = capsys.readouterr().out
    assert calls == ["settings"]
    assert json.loads(output.read_text(encoding="utf-8"))["di_refresh_token"] == "refresh"
    assert "refresh" not in terminal
    assert str(output) in terminal
