from outset_ready.auth import csrf_token_matches, hash_password, verify_password


def test_password_hash_round_trip_does_not_store_plaintext():
    encoded = hash_password("a-long-test-password")

    assert "a-long-test-password" not in encoded
    assert verify_password("a-long-test-password", encoded)
    assert not verify_password("another-password", encoded)


def test_short_password_is_rejected():
    try:
        hash_password("too-short")
    except ValueError as exc:
        assert "at least 12" in str(exc)
    else:
        raise AssertionError("Expected a short password to be rejected.")


def test_malformed_password_hash_fails_closed():
    assert not verify_password("password", "not-a-password-hash")


def test_csrf_comparison_fails_closed():
    assert csrf_token_matches("token", "token")
    assert not csrf_token_matches("token", "different")
    assert not csrf_token_matches(None, "token")
