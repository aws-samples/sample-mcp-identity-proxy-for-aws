"""Credential model shared across auth strategies."""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass


@dataclass
class Credential:
    """A credential to inject into an outbound HTTP request.

    Attributes:
        value: The raw secret value (an OAuth access token, API key, JWT, ...).
        header_name: The HTTP header to place the credential in.
        scheme: Optional auth scheme prefix (e.g. ``Bearer``). When ``None`` the
            raw ``value`` is written to the header verbatim (used for API keys in
            headers like ``x-api-key``).
        expires_at: Optional absolute expiry (epoch seconds). ``None`` means the
            credential has no known expiry and the caller should fall back to a
            time-to-live cache and/or 401-triggered refresh.
    """

    value: str
    header_name: str = "Authorization"
    scheme: str | None = "Bearer"
    expires_at: float | None = None

    @property
    def header_value(self) -> str:
        """Render the value for the HTTP header, applying the scheme if set."""
        if self.scheme:
            return f"{self.scheme} {self.value}"
        return self.value

    def is_expired(self, skew_seconds: float = 60.0) -> bool:
        """Return True when the credential is at/near its expiry.

        A safety ``skew_seconds`` window treats a token as expired slightly early
        so a request is never sent with a token that expires mid-flight.
        """
        if self.expires_at is None:
            return False
        return time.time() >= (self.expires_at - skew_seconds)


def jwt_expiry(token: str) -> float | None:
    """Best-effort extraction of the ``exp`` claim from a JWT.

    Returns the expiry as epoch seconds, or ``None`` if the value is not a JWT or
    has no ``exp`` claim. The signature is intentionally NOT verified: we only use
    this to schedule proactive refresh, never for authorization decisions.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        # Restore base64url padding.
        padding = "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(decoded)
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
        return None
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return None
