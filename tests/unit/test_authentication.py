"""Session-token authentication and Host header checks."""
import pytest

from ai_engine.server.authentication import (Authenticator, generate_token, is_allowed_host_header,
                                             validate_token_strength)


def test_generated_tokens_are_strong_and_unique():
    tokens = {generate_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


@pytest.mark.parametrize("token", [None, "", "short"])
def test_weak_tokens_rejected(token):
    with pytest.raises(ValueError):
        validate_token_strength(token)


def test_authenticator():
    token = generate_token()
    auth = Authenticator(token)
    assert auth.is_authorized("Bearer " + token)
    assert auth.is_authorized("bearer " + token)
    assert not auth.is_authorized(None)
    assert not auth.is_authorized("")
    assert not auth.is_authorized(token)
    assert not auth.is_authorized("Basic " + token)
    assert not auth.is_authorized("Bearer " + token[:-1])
    assert not auth.is_authorized("Bearer ")


@pytest.mark.parametrize("host, ok", [("127.0.0.1:8765", True), ("localhost:1", True), ("127.0.0.1", True),
                                      ("LOCALHOST:5", True), ("evil.example:8765", False), ("", False),
                                      (None, False), ("[::1]:8765", False), ("127.0.0.1.evil.com", False)])
def test_host_header(host, ok):
    assert is_allowed_host_header(host) is ok
