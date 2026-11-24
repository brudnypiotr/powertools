"""The Graph Explorer token: pasted or caught in the window, kept encrypted (vault), valid for about an hour."""

from __future__ import annotations

import base64
import json
import time

from ..vault import SecretFile


def claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


class TokenStore(SecretFile):
    def check(self, secret: str) -> None:
        if secret.count(".") != 2:
            raise ValueError("this does not look like an access token (copy it from the 'Access token' tab)")

    def get(self) -> str:
        return self._secret if self.expires_in() > 60 else ""

    def expires_in(self) -> float:
        """Seconds until the token expires (0 when missing or expired)."""
        return max(0.0, claims(self._secret).get("exp", 0) - time.time()) if self._secret else 0

    def scopes(self) -> list[str]:
        return sorted(claims(self._secret).get("scp", "").split()) if self._secret else []
