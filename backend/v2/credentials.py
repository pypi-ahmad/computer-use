"""Process-local credential sessions; secrets are never persisted or serialized."""
from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr


class CredentialSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    providers: list[str]
    expires_at: float


@dataclass
class ProviderCredential:
    method: Literal["api_key", "oauth"]
    api_key: SecretStr | None = None
    oauth_credentials: Any | None = None
    quota_project_id: str | None = None

    def get_secret_value(self) -> str:
        return self.api_key.get_secret_value() if self.api_key else ""


class CredentialVault:
    def __init__(self, *, max_ttl_seconds: int = 28_800, clock: Callable[[], float] = time.time) -> None:
        self._max_ttl = max_ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[float, dict[str, ProviderCredential]]] = {}
        self._lock = threading.RLock()

    def create(self, credentials: Mapping[str, str], *, ttl_seconds: int = 28_800) -> CredentialSession:
        ttl = max(1, min(ttl_seconds, self._max_ttl))
        clean = {
            provider.upper(): ProviderCredential(method="api_key", api_key=SecretStr(value.strip()))
            for provider, value in credentials.items()
            if value.strip()
        }
        if not clean:
            raise ValueError("At least one non-empty credential is required")
        entry_id = secrets.token_urlsafe(24)
        expires_at = self._clock() + ttl
        with self._lock:
            self._entries[entry_id] = (expires_at, clean)
        return CredentialSession(id=entry_id, providers=sorted(clean), expires_at=expires_at)

    def create_empty(self, *, ttl_seconds: int = 28_800) -> CredentialSession:
        ttl = max(1, min(ttl_seconds, self._max_ttl))
        entry_id = secrets.token_urlsafe(24)
        expires_at = self._clock() + ttl
        with self._lock:
            self._entries[entry_id] = (expires_at, {})
        return CredentialSession(id=entry_id, providers=[], expires_at=expires_at)

    def put_google_oauth(
        self,
        entry_id: str,
        credentials: Any,
        *,
        quota_project_id: str | None = None,
    ) -> None:
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None or entry[0] <= self._clock():
                raise KeyError(entry_id)
            entry[1]["GOOGLE"] = ProviderCredential(
                method="oauth",
                oauth_credentials=credentials,
                quota_project_id=quota_project_id,
            )

    def resolve(self, entry_id: str, provider: str) -> ProviderCredential | None:
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                return None
            expires_at, credentials = entry
            if expires_at <= self._clock():
                self._entries.pop(entry_id, None)
                credentials.clear()
                return None
            return credentials.get(provider.upper())

    def status(self, entry_id: str) -> CredentialSession | None:
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                return None
            expires_at, credentials = entry
            if expires_at <= self._clock():
                self._entries.pop(entry_id, None)
                credentials.clear()
                return None
            return CredentialSession(id=entry_id, providers=sorted(credentials), expires_at=expires_at)

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            entry = self._entries.pop(entry_id, None)
            if entry is None:
                return False
            entry[1].clear()
            return True


credential_vault = CredentialVault()
