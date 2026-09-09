from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class CredentialEncryptionError(ValueError):
    """Raised when credential material cannot be encrypted or decrypted."""


def generate_credential_encryption_key() -> str:
    return Fernet.generate_key().decode("ascii")


def validate_credential_encryption_key(value: str) -> None:
    try:
        Fernet(value.encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise CredentialEncryptionError(
            "The credential encryption key must be a Fernet key."
        ) from exc


class CredentialCipher:
    def __init__(self, key: str) -> None:
        validate_credential_encryption_key(key)
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise CredentialEncryptionError("Credential material cannot be empty.")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
            raise CredentialEncryptionError(
                "Stored credential material could not be decrypted."
            ) from exc
