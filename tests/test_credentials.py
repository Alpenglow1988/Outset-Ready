import pytest

from outset_ready.credentials import (
    CredentialCipher,
    CredentialEncryptionError,
    generate_credential_encryption_key,
)


def test_credential_cipher_round_trip_and_wrong_key_failure():
    first = CredentialCipher(generate_credential_encryption_key())
    second = CredentialCipher(generate_credential_encryption_key())

    encrypted = first.encrypt("private-token-material")

    assert encrypted != "private-token-material"
    assert first.decrypt(encrypted) == "private-token-material"
    with pytest.raises(CredentialEncryptionError):
        second.decrypt(encrypted)


def test_credential_cipher_rejects_empty_plaintext():
    cipher = CredentialCipher(generate_credential_encryption_key())

    with pytest.raises(CredentialEncryptionError, match="cannot be empty"):
        cipher.encrypt("")
