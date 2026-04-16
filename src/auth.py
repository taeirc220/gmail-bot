"""
auth.py — Google OAuth2 authentication for the Gmail Automation Bot.

On first run (token.json missing), opens the browser for OAuth consent.
On subsequent runs, loads token.json and silently refreshes if expired.
Raises AuthError if refresh fails — caller is responsible for notifying user.
"""

import logging
import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


class AuthError(Exception):
    """Raised when authentication cannot be recovered automatically."""


def get_credentials(credentials_path: str, token_path: str) -> Credentials:
    """
    Load or create valid credentials.

    - If token.json exists and is valid: return as-is.
    - If token.json exists but is expired: silently refresh and save.
    - If token.json is missing: run InstalledAppFlow (opens browser).
    - If refresh fails: raise AuthError.
    """
    creds: Credentials | None = None

    if Path(token_path).exists():
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as exc:
            logger.warning("Failed to load token.json, will re-authenticate: %s", exc)
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            logger.info("Token expired — refreshing silently")
            creds.refresh(Request())
            _save_token(creds, token_path)
            logger.info("Token refreshed successfully")
            return creds
        except RefreshError as exc:
            raise AuthError(
                f"Token refresh failed. Re-authentication required. ({exc})"
            ) from exc

    logger.info("No valid token found — starting OAuth2 flow (browser will open)")
    if not Path(credentials_path).exists():
        raise AuthError(
            f"credentials.json not found at {credentials_path!r}. "
            "Download it from Google Cloud Console and place it in the project root."
        )

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds, token_path)
    logger.info("OAuth2 flow complete — token.json saved")
    return creds


def _save_token(creds: Credentials, token_path: str) -> None:
    Path(token_path).parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    logger.debug("Token saved to %s", token_path)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from dotenv import load_dotenv

    load_dotenv()
    creds_path = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    token_path = os.getenv("GMAIL_TOKEN_PATH", "token.json")

    print("Starting Gmail OAuth2 setup...")
    try:
        creds = get_credentials(creds_path, token_path)
        print(f"Authentication successful. token.json saved to {token_path}")
    except AuthError as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)
