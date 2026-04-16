"""Tests for src/auth.py."""

import json
import pytest
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path

from src.auth import get_credentials, AuthError, SCOPES


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def _make_valid_creds():
    creds = MagicMock()
    creds.valid = True
    creds.expired = False
    creds.refresh_token = "refresh_token_value"
    creds.to_json.return_value = json.dumps({"token": "fake"})
    return creds


def _make_expired_creds():
    creds = MagicMock()
    creds.valid = False
    creds.expired = True
    creds.refresh_token = "refresh_token_value"
    creds.to_json.return_value = json.dumps({"token": "refreshed"})
    return creds


# -------------------------------------------------------------------------
# Test: valid token loaded from file
# -------------------------------------------------------------------------

def test_returns_valid_credentials_from_file(tmp_path):
    token_file = tmp_path / "token.json"
    creds = _make_valid_creds()

    with patch("src.auth.Path") as mock_path_cls, \
         patch("src.auth.Credentials.from_authorized_user_file", return_value=creds):

        mock_path_cls.return_value.exists.return_value = True
        mock_path_cls.return_value.parent.mkdir = MagicMock()

        result = get_credentials("credentials.json", str(token_file))

    assert result is creds


# -------------------------------------------------------------------------
# Test: expired token is silently refreshed
# -------------------------------------------------------------------------

def test_expired_token_is_refreshed(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"token": "old"}))

    creds = _make_expired_creds()

    with patch("src.auth.Credentials.from_authorized_user_file", return_value=creds), \
         patch("src.auth.Request") as mock_request, \
         patch("builtins.open", mock_open()):

        result = get_credentials("credentials.json", str(token_file))

    creds.refresh.assert_called_once()
    assert result is creds


# -------------------------------------------------------------------------
# Test: RefreshError raises AuthError
# -------------------------------------------------------------------------

def test_refresh_error_raises_auth_error(tmp_path):
    from google.auth.exceptions import RefreshError

    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"token": "old"}))

    creds = _make_expired_creds()
    creds.refresh.side_effect = RefreshError("Token has been revoked")

    with patch("src.auth.Credentials.from_authorized_user_file", return_value=creds), \
         patch("src.auth.Request"):

        with pytest.raises(AuthError, match="Token refresh failed"):
            get_credentials("credentials.json", str(token_file))


# -------------------------------------------------------------------------
# Test: missing credentials.json raises AuthError
# -------------------------------------------------------------------------

def test_missing_credentials_raises_auth_error(tmp_path):
    # No token.json AND no credentials.json
    with patch("src.auth.Path") as mock_path_cls:
        # token path does not exist
        token_mock = MagicMock()
        token_mock.exists.return_value = False
        # credentials path does not exist
        creds_mock = MagicMock()
        creds_mock.exists.return_value = False

        def path_side_effect(p):
            if "token" in str(p):
                return token_mock
            return creds_mock

        mock_path_cls.side_effect = path_side_effect

        with pytest.raises(AuthError, match="credentials.json not found"):
            get_credentials("credentials.json", "token.json")


# -------------------------------------------------------------------------
# Test: missing token triggers InstalledAppFlow
# -------------------------------------------------------------------------

def test_missing_token_triggers_flow(tmp_path):
    token_file = tmp_path / "token.json"
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text(json.dumps({"installed": {}}))

    new_creds = MagicMock()
    new_creds.to_json.return_value = json.dumps({"token": "new"})

    with patch("src.auth.InstalledAppFlow.from_client_secrets_file") as mock_flow, \
         patch("builtins.open", mock_open()):
        mock_flow.return_value.run_local_server.return_value = new_creds
        result = get_credentials(str(creds_file), str(token_file))

    mock_flow.assert_called_once_with(str(creds_file), SCOPES)
    assert result is new_creds
