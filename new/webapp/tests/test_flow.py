"""End-to-end sanity test for the web backend."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow importing the webapp.app package without installation.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

os.environ.setdefault("GM_PASSWORD", "admin")

from webapp.app.main import app  # noqa: E402


def test_admin_login_and_create_game():
    c = TestClient(app)

    r = c.post("/api/admin/login", data={"password": "admin"})
    assert r.status_code == 200, r.text

    r = c.post("/api/admin/games")
    assert r.status_code == 200
    gid = r.json()["gid"]

    r = c.get(f"/api/admin/games/{gid}")
    assert r.status_code == 200
    data = r.json()
    assert data["phase"] == "lobby"
    assert len(data["teams"]) == 3


def test_full_lobby_flow_three_teams():
    """Join enough players for all 3 teams, pick captain/radist, set pool,
    hit ready. The game should auto-start."""
    c = TestClient(app)
    c.post("/api/admin/login", data={"password": "admin"})
    gid = c.post("/api/admin/games").json()["gid"]

    pool = ["Артиллерия", "Радиовышка", "Прыгун", "Факел",
            "Тишина", "Бурав", "Провокатор", "Паук"]

    tokens = {}
    for letter in ("A", "B", "C"):
        # Captain + radist + 2 crew (enough to own 8 ships split).
        for idx, (role, nick) in enumerate([
            ("captain", f"cap_{letter}"),
            ("radist",  f"rad_{letter}"),
            ("crew",    f"crew1_{letter}"),
            ("crew",    f"crew2_{letter}"),
        ]):
            r = c.post(f"/api/play/{gid}/join", json={"name": nick})
            assert r.status_code == 200, r.text
            tok = r.json()["token"]
            tokens[nick] = tok
            r = c.post(f"/api/play/{gid}/pick_team?token={tok}&team={letter}")
            assert r.status_code == 200, r.text
            r = c.post(f"/api/play/{gid}/claim_role?token={tok}&role={role}")
            assert r.status_code == 200, r.text

        cap_tok = tokens[f"cap_{letter}"]
        r = c.post(f"/api/play/{gid}/pool?token={cap_tok}",
                   json={"pool": pool})
        assert r.status_code == 200, r.text
        r = c.post(f"/api/play/{gid}/ready?token={cap_tok}",
                   json={"ready": True})
        assert r.status_code == 200, r.text

    # All 3 captains ready → game auto-starts.
    r = c.get(f"/api/admin/games/{gid}")
    assert r.status_code == 200
    data = r.json()
    assert data["phase"] == "planning", data["phase"]
    assert data["turn"] == 0
    # 24 ships total (8 per team × 3).
    assert len(data["ships"]) == 24
    # Each team has captain + radist set + pool filled.
    for t in data["teams"]:
        assert t["captain_pid"]
        assert t["radist_pid"]
        assert len(t["pool"]) == 8


def test_radist_intel_auto_and_explicit_relay():
    """Radist should auto-see whatever the crew sees; captain stays blind
    until the radist explicitly relays a sighting."""
    c = TestClient(app)
    c.post("/api/admin/login", data={"password": "admin"})
    gid = c.post("/api/admin/games").json()["gid"]

    pool = ["Радиовышка"] * 8  # radios see an entire Z plane each — easy sight.

    tokens = {}
    for letter in ("A", "B", "C"):
        for role, nick in [
            ("captain", f"C_{letter}"),
            ("radist",  f"R_{letter}"),
            ("crew",    f"E_{letter}"),
        ]:
            tok = c.post(f"/api/play/{gid}/join", json={"name": nick}).json()["token"]
            tokens[(letter, role, nick)] = tok
            c.post(f"/api/play/{gid}/pick_team?token={tok}&team={letter}")
            c.post(f"/api/play/{gid}/claim_role?token={tok}&role={role}")
        cap = [t for (l, r, _), t in tokens.items() if l == letter and r == "captain"][0]
        c.post(f"/api/play/{gid}/pool?token={cap}", json={"pool": pool})
        c.post(f"/api/play/{gid}/ready?token={cap}", json={"ready": True})

    # All radio ships scan their Z plane — plenty of sightings show up
    # for the radist immediately.
    cap_a = [t for (l, r, _), t in tokens.items() if l == "A" and r == "captain"][0]
    rad_a = [t for (l, r, _), t in tokens.items() if l == "A" and r == "radist"][0]

    cap_state = c.get(f"/api/play/{gid}/state?token={cap_a}").json()
    rad_state = c.get(f"/api/play/{gid}/state?token={rad_a}").json()

    # Captain starts with empty intel; radist has auto intel.
    assert isinstance(cap_state.get("intel", {}), dict)
    assert cap_state["intel"] == {}
    # Radist should have auto-populated intel (RADIO ships scan whole Z).
    assert rad_state["intel"], "Radist should see at least some sightings"

    # Radist relays one sighting explicitly → captain now sees it.
    enemy_id = next(iter(rad_state["intel"].keys()))
    r = c.post(f"/api/play/{gid}/share_intel?token={rad_a}",
               json={"enemy_ship_id": enemy_id})
    assert r.status_code == 200, r.text

    cap_state2 = c.get(f"/api/play/{gid}/state?token={cap_a}").json()
    assert enemy_id in cap_state2["intel"]
