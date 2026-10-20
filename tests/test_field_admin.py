import hashlib
from datetime import UTC, datetime

import pytest

from floodline.stages import field_admin as fa


class FakeAdmin:
    def __init__(self):
        self.teams = {"FL-TEAM-0001": {"code": "FL-TEAM-0001", "name": "Larkana boats", "active": True},
                      "OLD-TEAM-0001": {"code": "OLD-TEAM-0001", "name": "Retired", "active": False}}  # fmt: skip
        self.invites, self.rows = [], [{"id": "d1", "active": True}]

    def team(self, code):
        return self.teams.get(code)

    def insert_invites(self, rows):
        self.invites += rows

    def devices(self):
        return self.rows

    def set_device_active(self, device_id, active):
        hit = [d for d in self.rows if d["id"] == device_id]
        for d in hit:
            d["active"] = active
        return len(hit)


def test_codes_are_long_unambiguous_and_stored_only_as_hashes():
    store = FakeAdmin()
    codes = fa.create_invites(
        store, "FL-TEAM-0001", 50, "Rescue 1122, boat 3", days=7, now=datetime(2026, 10, 21, tzinfo=UTC)
    )
    assert len(set(codes)) == 50
    assert all(len(c) == fa.INVITE_LENGTH and set(c) <= set(fa.ALPHABET) for c in codes)
    assert not set("ILO01") & set(fa.ALPHABET)  # no look-alikes to mistype
    stored = {r["code_hash"] for r in store.invites}
    assert all(c not in str(store.invites) for c in codes)  # plaintext never leaves this function's caller
    assert stored == {hashlib.sha256(c.encode()).hexdigest() for c in codes}
    assert {r["expires_at"][:10] for r in store.invites} == {"2026-10-28"}
    assert {r["label"] for r in store.invites} == {"Rescue 1122, boat 3"}


def test_hash_ignores_case_spaces_and_dashes_like_the_app_and_the_database():
    assert fa.code_hash("abcd-2345 efgh-6789") == fa.code_hash("ABCD2345EFGH6789")
    assert fa.display("abcd2345efgh6789") == "ABCD-2345-EFGH-6789"
    assert fa.invite_link("ABCD2345EFGH6789").endswith("/field/#invite=ABCD-2345-EFGH-6789")


def test_refuses_retired_or_unknown_teams_and_silly_counts():
    store = FakeAdmin()
    for team in ("OLD-TEAM-0001", "NOPE-0000"):
        with pytest.raises(ValueError, match="no active team"):
            fa.create_invites(store, team, 1)
    with pytest.raises(ValueError):
        fa.create_invites(store, "FL-TEAM-0001", 0)
    assert store.invites == []


def test_withdraw_and_restore_a_device():
    store = FakeAdmin()
    fa.set_active(store, "d1", active=False)
    assert fa.revoked_ids(store.devices()) == {"d1"}
    fa.set_active(store, "d1", active=True)
    assert fa.revoked_ids(store.devices()) == set()
    with pytest.raises(ValueError, match="no device"):
        fa.set_active(store, "missing", active=False)
