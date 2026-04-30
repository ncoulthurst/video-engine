"""
football_data_api.py — Fetch real match lineups from football-data.org

API key is read from FOOTBALL_DATA_API_KEY in the .env file.
Free tier: 10 requests/min, covers PL, CL, Bundesliga, La Liga, Serie A, Ligue 1.

Usage:
    from utils.football_data_api import fetch_lineup_for_tag
    data = fetch_lineup_for_tag("Liverpool 4-3-3 vs Crystal Palace, 05 Oct 2024")
    # Returns TeamLineup-compatible dict, or None on failure.
"""

import os
import re
import time
import requests
from datetime import datetime, timedelta

# ── Competition codes ─────────────────────────────────────────────────────────

COMPETITION_CODES = {
    "premier league":      "PL",
    "la liga":             "PD",
    "bundesliga":          "BL1",
    "serie a":             "SA",
    "ligue 1":             "FL1",
    "champions league":    "CL",
    "uefa champions league": "CL",
    "fa cup":              "FAC",
    "eredivisie":          "DED",
    "championship":        "ELC",
}

# ── Position → pitch x/y coords ──────────────────────────────────────────────
# y: 0=keeper end (bottom of component), 100=attacker end (top)
# Positions are for a team attacking upward (toward y=100).

POSITION_COORDS = {
    # Goalkeeper
    "Goalkeeper":           {"positionLabel": "GK",  "y": 7},

    # Defenders
    "Centre-Back":          {"positionLabel": "CB",  "y": 22},
    "Left-Back":            {"positionLabel": "LB",  "y": 24, "xHint": "left"},
    "Right-Back":           {"positionLabel": "RB",  "y": 24, "xHint": "right"},
    "Left Wing-Back":       {"positionLabel": "LWB", "y": 32, "xHint": "left"},
    "Right Wing-Back":      {"positionLabel": "RWB", "y": 32, "xHint": "right"},
    "Sweeper":              {"positionLabel": "SW",  "y": 14},

    # Midfielders
    "Defensive Midfield":   {"positionLabel": "DM",  "y": 38},
    "Central Midfield":     {"positionLabel": "CM",  "y": 48},
    "Attacking Midfield":   {"positionLabel": "AM",  "y": 60},
    "Left Midfield":        {"positionLabel": "LM",  "y": 48, "xHint": "left"},
    "Right Midfield":       {"positionLabel": "RM",  "y": 48, "xHint": "right"},

    # Forwards
    "Left Winger":          {"positionLabel": "LW",  "y": 72, "xHint": "left"},
    "Right Winger":         {"positionLabel": "RW",  "y": 72, "xHint": "right"},
    "Second Striker":       {"positionLabel": "SS",  "y": 76},
    "Centre-Forward":       {"positionLabel": "ST",  "y": 82},
}

DEFAULT_COORD = {"positionLabel": "?", "y": 50}

# ── Horizontal spread helpers ─────────────────────────────────────────────────

def _assign_x_positions(players_by_pos: list[dict]) -> list[dict]:
    """
    For each position group (same y), spread players evenly across x=15..85.
    Players with xHint='left' anchor left, xHint='right' anchor right,
    the rest share the middle.
    """
    from collections import defaultdict
    groups: dict[float, list[dict]] = defaultdict(list)
    for p in players_by_pos:
        groups[p["_y"]].append(p)

    result = []
    for y, group in groups.items():
        n = len(group)
        if n == 1:
            # Check hint
            hint = group[0].get("_xHint", "")
            if hint == "left":
                group[0]["x"] = 18
            elif hint == "right":
                group[0]["x"] = 82
            else:
                group[0]["x"] = 50
        else:
            # Sort: left-hinted first, right-hinted last, rest by insertion order
            left   = [p for p in group if p.get("_xHint") == "left"]
            right  = [p for p in group if p.get("_xHint") == "right"]
            middle = [p for p in group if not p.get("_xHint")]
            ordered = left + middle + right
            step = 70 / max(n - 1, 1)
            for i, p in enumerate(ordered):
                p["x"] = round(15 + i * step)
        for p in group:
            p["y"] = p.pop("_y")
            p.pop("_xHint", None)
            result.append(p)
    return result


# ── API helpers ───────────────────────────────────────────────────────────────

def _api_key() -> str | None:
    # Try env first (already loaded by llm_utils), then .env directly
    key = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
    if not key:
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("FOOTBALL_DATA_API_KEY="):
                        key = line.split("=", 1)[1].strip()
                        break
    return key or None


def _get(url: str, params: dict = None) -> dict | None:
    key = _api_key()
    if not key:
        print("    [FD API] No FOOTBALL_DATA_API_KEY found — skipping API lookup")
        return None
    headers = {"X-Auth-Token": key}
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=10)
        if r.status_code == 429:
            print("    [FD API] Rate limited — waiting 12s")
            time.sleep(12)
            r = requests.get(url, headers=headers, params=params or {}, timeout=10)
        if r.status_code != 200:
            print(f"    [FD API] HTTP {r.status_code} for {url}")
            return None
        return r.json()
    except Exception as e:
        print(f"    [FD API] Request failed: {e}")
        return None


BASE = "https://api.football-data.org/v4"


def _find_match(team_name: str, opposition: str, match_date: str) -> dict | None:
    """
    Search for a match around the given date. Returns the raw match object or None.
    match_date: e.g. '05 Oct 2024' or '2024-10-05'
    """
    # Normalise date to YYYY-MM-DD
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(match_date.strip(), fmt)
            break
        except ValueError:
            continue
    else:
        print(f"    [FD API] Could not parse date: {match_date!r}")
        return None

    date_from = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    date_to   = (dt + timedelta(days=1)).strftime("%Y-%m-%d")

    data = _get(f"{BASE}/matches", {"dateFrom": date_from, "dateTo": date_to})
    if not data:
        return None

    team_lower = team_name.lower()
    opp_lower  = opposition.lower() if opposition else ""

    for match in data.get("matches", []):
        home = match.get("homeTeam", {}).get("name", "").lower()
        away = match.get("awayTeam", {}).get("name", "").lower()
        short_home = match.get("homeTeam", {}).get("shortName", "").lower()
        short_away = match.get("awayTeam", {}).get("shortName", "").lower()

        team_match = (team_lower in home or team_lower in short_home or
                      home in team_lower or short_home in team_lower)
        opp_match  = not opp_lower or (
                      opp_lower in away or opp_lower in short_away or
                      away in opp_lower or short_away in opp_lower or
                      opp_lower in home or opp_lower in short_home or
                      home in opp_lower or short_home in opp_lower)

        if team_match and opp_match:
            return match

    print(f"    [FD API] No match found for {team_name} vs {opposition} on {match_date}")
    return None


def _build_lineup(match: dict, team_name: str) -> dict | None:
    """
    Given a raw match object, build a TeamLineup-compatible dict for team_name.
    """
    home     = match.get("homeTeam", {})
    away     = match.get("awayTeam", {})
    home_name = (home.get("name") or "").lower()
    team_lower = team_name.lower()

    is_home = (team_lower in home_name or home_name in team_lower)
    our_side  = home if is_home else away
    opp_side  = away if is_home else home

    # lineups live under match["lineups"]["homeTeam"] / ["awayTeam"] in v4
    lineups = match.get("lineups", {})
    side_key = "homeTeam" if is_home else "awayTeam"
    side_lineup = lineups.get(side_key, {})

    starters = side_lineup.get("startXI", [])
    if not starters:
        print(f"    [FD API] No lineup data in match object — may not be available yet")
        return None

    formation = side_lineup.get("formation", "4-3-3")

    players_raw = []
    for entry in starters:
        p = entry.get("player", entry)  # v4 wraps in 'player' key
        pos_str = p.get("position", "") or ""
        coord   = POSITION_COORDS.get(pos_str, DEFAULT_COORD)
        players_raw.append({
            "name":          p.get("name", "Unknown"),
            "number":        p.get("shirtNumber") or 0,
            "positionLabel": coord["positionLabel"],
            "_y":            coord["y"],
            "_xHint":        coord.get("xHint", ""),
            "isCaptain":     p.get("captain", False),
            "appearFrame":   0,
        })

    players = _assign_x_positions(players_raw)

    # Match date pretty print
    raw_date = match.get("utcDate", "")
    try:
        pretty_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:
        pretty_date = raw_date[:10]

    return {
        "teamName":  our_side.get("name", team_name),
        "formation": formation,
        "opposition": opp_side.get("name", ""),
        "date":      pretty_date,
        "players":   players,
        "_source":   "footballdata",  # Track B provenance stamp
    }


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_lineup_for_tag(tag_text: str) -> dict | None:
    """
    Parse a TEAM LINEUP tag and fetch the real lineup from football-data.org.
    Tag format: "Liverpool 4-3-3 vs Crystal Palace, 05 Oct 2024"
    Returns a partial TeamLineup dict (teamName, formation, opposition, date, players)
    or None if lookup fails.
    """
    # Extract team, formation, opposition, date
    # Pattern: "TeamName Formation vs Opposition, Date"
    # Formation is optional
    m = re.match(
        r"^(.+?)\s+(\d[-\d]+)\s+vs\s+(.+?),\s*(.+)$",
        tag_text.strip(), re.IGNORECASE
    )
    if m:
        team_name  = m.group(1).strip()
        opposition = m.group(3).strip()
        match_date = m.group(4).strip()
    else:
        # Try without formation: "Liverpool vs Crystal Palace, 05 Oct 2024"
        m2 = re.match(r"^(.+?)\s+vs\s+(.+?),\s*(.+)$", tag_text.strip(), re.IGNORECASE)
        if not m2:
            print(f"    [FD API] Could not parse tag: {tag_text!r}")
            return None
        team_name  = m2.group(1).strip()
        opposition = m2.group(2).strip()
        match_date = m2.group(3).strip()

    print(f"    [FD API] Looking up: {team_name} vs {opposition} on {match_date}")

    match = _find_match(team_name, opposition, match_date)
    if not match:
        return None

    # Fetch full match detail (includes lineups)
    match_id   = match.get("id")
    full_match = _get(f"{BASE}/matches/{match_id}")
    if not full_match:
        return None

    return _build_lineup(full_match, team_name)
