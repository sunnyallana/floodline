"""Coordinator tools for Floodline Field: issue single-use invites, list devices, withdraw a device.

A phone can report only after redeeming an invite (``supabase/migrations/*_device_enrolment.sql``). One invite
enrols one phone, once. So the number of identities anyone can report under is the number of invites a
coordinator has issued, and each identity carries the label of the invite it came from (who it was handed to).

Codes are 16 characters from a 31-symbol alphabet with no look-alikes (no I, L, O, 0 or 1): about 79 bits, far
beyond guessing over the network. Only their SHA-256 is stored, so a database leak does not leak usable codes.
The plaintext is printed once, here, with a link that opens the app with the code filled in.

Runs on the analysis machine with ``SUPABASE_URL`` and ``SUPABASE_SERVICE_KEY``, like ``floodline ingest``.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from floodline.log import get_logger

log = get_logger(__name__)

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_LENGTH = 16
APP_URL = "https://floodline.sunnyshabanali.dev/field/"


def normalise(code: str) -> str:
    """Case, spaces and dashes do not count (as ``private.normalise_invite`` in the database)."""
    return re.sub(r"[^0-9A-Za-z]", "", code).upper()


def display(code: str) -> str:
    c = normalise(code)
    return "-".join(c[i : i + 4] for i in range(0, len(c), 4))


def code_hash(code: str) -> str:
    return hashlib.sha256(normalise(code).encode("utf-8")).hexdigest()


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(INVITE_LENGTH))


def invite_link(code: str, app_url: str = APP_URL) -> str:
    return f"{app_url}#invite={display(code)}"


class AdminStore(Protocol):
    def team(self, code: str) -> dict[str, Any] | None: ...
    def insert_invites(self, rows: list[dict[str, Any]]) -> None: ...
    def devices(self) -> list[dict[str, Any]]: ...
    def set_device_active(self, device_id: str, active: bool) -> int: ...


@dataclass
class SupabaseAdmin:
    """PostgREST with the service-role key: the only role that can see invites, devices and teams."""

    url: str
    service_key: str
    timeout_s: float = 30.0

    @classmethod
    def from_env(cls) -> SupabaseAdmin:
        from floodline.stages.ingest import SupabaseStore

        s = SupabaseStore.from_env()
        return cls(s.url, s.service_key)

    def _session(self):
        import requests

        s = requests.Session()
        s.headers.update({"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}"})
        return s

    def team(self, code: str) -> dict[str, Any] | None:
        r = self._session().get(
            f"{self.url}/rest/v1/teams",
            params={"code": f"eq.{code}", "select": "code,name,active"},
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

    def insert_invites(self, rows: list[dict[str, Any]]) -> None:
        r = self._session().post(
            f"{self.url}/rest/v1/invites", json=rows, headers={"Prefer": "return=minimal"}, timeout=self.timeout_s
        )
        r.raise_for_status()

    def devices(self) -> list[dict[str, Any]]:
        r = self._session().get(
            f"{self.url}/rest/v1/devices", params={"select": "*", "order": "enrolled_at.asc"}, timeout=self.timeout_s
        )
        r.raise_for_status()
        return list(r.json())

    def set_device_active(self, device_id: str, active: bool) -> int:
        r = self._session().patch(
            f"{self.url}/rest/v1/devices",
            params={"id": f"eq.{device_id}"},
            json={"active": active},
            headers={"Prefer": "return=representation"},
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        return len(r.json())


def create_invites(
    store: AdminStore, team: str, count: int, label: str = "", days: int = 30, now: datetime | None = None
) -> list[str]:
    """Issue ``count`` invites for an active team; returns the plaintext codes, which are not stored anywhere."""
    if not 1 <= count <= 500:
        raise ValueError("issue between 1 and 500 invites at a time")
    t = store.team(team)
    if not t or not t.get("active"):
        raise ValueError(f"no active team {team!r}")
    expires = ((now or datetime.now(UTC)) + timedelta(days=days)).isoformat()
    codes = [new_code() for _ in range(count)]
    store.insert_invites(
        [{"code_hash": code_hash(c), "team_code": team, "label": label, "expires_at": expires} for c in codes]
    )
    log.info("issued %d invites for %s (%s), valid %d days", count, t.get("name", team), label or "no label", days)
    return codes


def set_active(store: AdminStore, device_id: str, active: bool) -> None:
    if store.set_device_active(device_id, active) != 1:
        raise ValueError(f"no device {device_id}")
    log.info("device %s %s", device_id, "restored" if active else "withdrawn")


def revoked_ids(devices: list[dict[str, Any]]) -> set[str]:
    return {str(d["id"]) for d in devices if not d.get("active", True)}
