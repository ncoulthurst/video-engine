"""
Radar Agent — fetches real per-90 stats from FBref via soccerdata,
calculates percentile rank vs positional peers, and returns
AttackingRadar props ready for Remotion.

Falls back to LLM-estimated values if FBref is unavailable.

Usage:
    from agents.radar_agent import build_radar_props
    props = build_radar_props("Florian Wirtz", "Liverpool", "Premier League", "2025/26")
"""

import json
import re
import traceback

from utils.llm_utils import ask_llm

# ── Competition → soccerdata league ID ───────────────────────────────────────

LEAGUE_IDS = {
    "Premier League":  "ENG-Premier League",
    "La Liga":         "ESP-La Liga",
    "Bundesliga":      "GER-Bundesliga",
    "Serie A":         "ITA-Serie A",
    "Ligue 1":         "FRA-Ligue 1",
    "Champions League":"UEFA-Champions League",
}

# ── Season string normalisation ───────────────────────────────────────────────

def _season_code(season_str: str) -> str:
    """
    Convert display season to soccerdata code.
    '2025/26' → '2526', '2024-25' → '2425', '2025/2026' → '2526'
    """
    digits = re.findall(r"\d+", season_str)
    if len(digits) >= 2:
        y1 = digits[0][-2:]   # last 2 digits of start year
        y2 = digits[1][-2:]   # last 2 digits of end year
        return y1 + y2
    return season_str

# ── Position → role mapping ───────────────────────────────────────────────────

def _detect_role(pos_str: str) -> str:
    """
    Map FBref position codes to an internal role key.
    FBref pos examples: 'FW', 'MF', 'MF,FW', 'FW,MF', 'DF', 'GK', 'DF,MF'
    """
    pos = (pos_str or "").upper()
    if "GK" in pos:
        return "gk"
    if pos in ("FW",):
        return "striker"
    if pos in ("FW,MF",):
        return "striker"   # more forward-oriented
    if pos in ("MF,FW",):
        return "cam"
    if pos in ("MF",):
        return "cm"
    if "DF" in pos and "MF" in pos:
        return "dm"
    if pos in ("DF",):
        return "defender"
    return "cm"            # default fallback

# ── Per-role metric definitions ───────────────────────────────────────────────
# Each entry: (display_label, stat_type, raw_col, unit)
# 'raw_col' is the column name after index reset + column flatten.
# Values will be divided by (minutes/90) unless already per-90 in FBref.

ROLE_METRICS = {
    "striker": [
        ("Non-Penalty\nGoals",      "standard",          "goals_minus_pens",   ""),
        ("Expected\nGoals (xG)",    "standard",          "npxg",               ""),
        ("Shots per 90",            "shooting",          "shots",              ""),
        ("xG per\nShot",            "shooting",          "npxg_per_shot",      ""),
        ("Touches in\nPenalty Box", "possession",        "touches_att_pen_area",""),
        ("Dribbles\nCompleted",     "possession",        "take_ons_won",       ""),
        ("Progressive\nCarries",    "possession",        "progressive_carries",""),
        ("Aerial Duels\nWon %",     "misc",              "aerials_won_pct",    "%"),
        ("Shot-Creating\nActions",  "goal_shot_creation","sca",                ""),
    ],
    "cam": [
        ("Non-Penalty\nGoals",      "standard",          "goals_minus_pens",   ""),
        ("Expected\nGoals (xG)",    "standard",          "npxg",               ""),
        ("Expected\nAssists (xA)",  "standard",          "xag",                ""),
        ("Shot-Creating\nActions",  "goal_shot_creation","sca",                ""),
        ("Key Passes",              "passing",           "assisted_shots",     ""),
        ("Dribbles\nCompleted",     "possession",        "take_ons_won",       ""),
        ("Progressive\nCarries",    "possession",        "progressive_carries",""),
        ("Progressive\nPasses",     "passing",           "progressive_passes", ""),
        ("Touches in\nPenalty Box", "possession",        "touches_att_pen_area",""),
    ],
    "winger": [
        ("xG per 90",               "standard",          "npxg",               ""),
        ("Expected\nAssists (xA)",  "standard",          "xag",                ""),
        ("Dribbles\nCompleted",     "possession",        "take_ons_won",       ""),
        ("Progressive\nCarries",    "possession",        "progressive_carries",""),
        ("Key Passes",              "passing",           "assisted_shots",     ""),
        ("Crosses",                 "misc",              "crosses",            ""),
        ("Touches in\nPenalty Box", "possession",        "touches_att_pen_area",""),
        ("Shot-Creating\nActions",  "goal_shot_creation","sca",                ""),
        ("Progressive\nPasses",     "passing",           "progressive_passes", ""),
    ],
    "cm": [
        ("Progressive\nPasses",     "passing",           "progressive_passes", ""),
        ("Key Passes",              "passing",           "assisted_shots",     ""),
        ("Shot-Creating\nActions",  "goal_shot_creation","sca",                ""),
        ("Expected\nAssists (xA)",  "standard",          "xag",                ""),
        ("Progressive\nCarries",    "possession",        "progressive_carries",""),
        ("Dribbles\nCompleted",     "possession",        "take_ons_won",       ""),
        ("Ball\nRecoveries",        "misc",              "ball_recoveries",    ""),
        ("Pressures\nApplied",      "defense",           "pressures",          ""),
        ("xG per 90",               "standard",          "npxg",               ""),
    ],
    "dm": [
        ("Tackles\nWon",            "defense",           "tackles_won",        ""),
        ("Interceptions",           "defense",           "interceptions",      ""),
        ("Pressures\nApplied",      "defense",           "pressures",          ""),
        ("Ball\nRecoveries",        "misc",              "ball_recoveries",    ""),
        ("Progressive\nPasses",     "passing",           "progressive_passes", ""),
        ("Blocks",                  "defense",           "blocks",             ""),
        ("Dribbles\nCompleted",     "possession",        "take_ons_won",       ""),
        ("Key Passes",              "passing",           "assisted_shots",     ""),
        ("Aerial Duels\nWon %",     "misc",              "aerials_won_pct",    "%"),
    ],
    "defender": [
        ("Tackles\nWon",            "defense",           "tackles_won",        ""),
        ("Interceptions",           "defense",           "interceptions",      ""),
        ("Clearances",              "defense",           "clearances",         ""),
        ("Aerial Duels\nWon %",     "misc",              "aerials_won_pct",    "%"),
        ("Progressive\nCarries",    "possession",        "progressive_carries",""),
        ("Progressive\nPasses",     "passing",           "progressive_passes", ""),
        ("Ball\nRecoveries",        "misc",              "ball_recoveries",    ""),
        ("Pressures\nApplied",      "defense",           "pressures",          ""),
        ("Blocks",                  "defense",           "blocks",             ""),
    ],
}
# Metrics that should NOT be divided by minutes/90 (already a percentage/ratio)
RATIO_METRICS = {"npxg_per_shot", "aerials_won_pct", "take_ons_won_pct"}

# Metrics where HIGHER is better (for percentile direction)
# All others assumed: higher = better (default)
LOWER_IS_BETTER = set()  # e.g. "fouls" — add if needed

# ── Column name search ────────────────────────────────────────────────────────

def _flatten_df(df):
    """Flatten MultiIndex columns; reset index so player/team/pos become columns."""
    import pandas as pd
    df = df.copy()
    if hasattr(df.index, "names") and df.index.names != [None]:
        df = df.reset_index()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            "_".join(str(p).strip() for p in parts if p and "Unnamed" not in str(p)
                     and str(p) != "nan").strip("_")
            for parts in df.columns
        ]
    return df


def _find_col(df, *candidates):
    """
    Return the first existing column name from candidates.
    Tries exact match first, then case-insensitive substring.
    """
    for c in candidates:
        if c in df.columns:
            return c
    for c in candidates:
        cl = c.lower()
        matches = [col for col in df.columns if cl in col.lower()]
        if matches:
            return matches[0]
    return None


def _find_player(df, name: str):
    """Find a player row by name (case-insensitive, accent-insensitive)."""
    import unicodedata

    def norm(s):
        s = str(s).lower()
        return "".join(
            c for c in unicodedata.normalize("NFD", s)
            if unicodedata.category(c) != "Mn"
        )

    name_norm = norm(name)
    player_col = _find_col(df, "player", "Player", "name")
    if player_col is None:
        return None
    mask = df[player_col].apply(norm) == name_norm
    if mask.any():
        return df[mask].iloc[0]
    # Partial match fallback
    mask = df[player_col].apply(norm).str.contains(name_norm.split()[0], regex=False)
    if mask.any():
        return df[mask].iloc[0]
    return None


# ── FBref per-request retry — exponential backoff, 3 attempts ─────────────────
# No session-level circuit breaker: a single 403 should not block the whole run.
# If all 3 attempts fail, the caller falls back to LLM with a visible warning.

import time as _time

def _fetch_stat_with_retry(fbref, stat_type: str, max_attempts: int = 3) -> object | None:
    """
    Fetch one FBref stat table with exponential back-off.
    Returns flattened DataFrame or None on persistent failure.
    Raises RuntimeError immediately on a 403 (no point retrying a permanent block).
    """
    import pandas as pd
    delays = [2, 4, 8]
    for attempt in range(max_attempts):
        try:
            df = fbref.read_player_season_stats(stat_type=stat_type)
            return _flatten_df(df)
        except Exception as e:
            msg = str(e)
            is_403 = any(x in msg for x in ("403", "Forbidden", "Could not download"))
            if is_403:
                print(f"    [Radar] ⚠ FBref 403 on '{stat_type}' — permanent block, aborting FBref fetch.")
                raise RuntimeError(f"FBref blocked (403)")
            if attempt < max_attempts - 1:
                wait = delays[attempt]
                print(f"    [Radar] ✗ {stat_type} attempt {attempt+1} failed ({msg[:60]}), retrying in {wait}s…")
                _time.sleep(wait)
    return None


# ── Main FBref fetch ──────────────────────────────────────────────────────────

def _fetch_fbref(player_name: str, competition: str, season_str: str):
    """
    Fetch all needed FBref stat tables via soccerdata.
    Returns (tables, player_row_dict, minutes, role) or raises.
    Each stat table is fetched independently with retry — one failing
    does not abort the whole request.
    """
    import soccerdata as sd
    import pandas as pd

    league = LEAGUE_IDS.get(competition, "ENG-Premier League")
    season = _season_code(season_str)

    print(f"    [Radar] Fetching FBref: {league} {season}")
    fbref = sd.FBref(leagues=league, seasons=season)

    # Stat types we need across all roles
    stat_types = ["standard", "shooting", "passing",
                  "goal_shot_creation", "possession", "defense", "misc"]

    tables = {}
    for st in stat_types:
        try:
            result = _fetch_stat_with_retry(fbref, st)
            if result is not None:
                tables[st] = result
                print(f"    [Radar] ✓ {st}: {len(tables[st])} rows")
            else:
                print(f"    [Radar] ✗ Could not fetch {st} (no data returned)")
                tables[st] = None
        except RuntimeError as e:
            if "403" in str(e):
                print(f"    [Radar] ⚠ 403 confirmed — skipping remaining stat types, will use LLM fallback.")
                for remaining in stat_types[stat_types.index(st):]:
                    tables[remaining] = None
                break
            tables[st] = None

    std = tables.get("standard")
    if std is None:
        raise RuntimeError("Standard stats table unavailable")

    # Find our player in the standard table
    row = _find_player(std, player_name)
    if row is None:
        raise ValueError(f"Player '{player_name}' not found in FBref data")

    print(f"    [Radar] Found player: {row.get('player', row.get('Player', '?'))}")

    # Extract minutes played
    min_col = _find_col(std, "minutes", "min", "MP", "mins_per_90")
    minutes = float(row[min_col]) if min_col and not pd.isna(row.get(min_col, float("nan"))) else None
    if not minutes:
        # Try computing from 90s played
        nineties_col = _find_col(std, "minutes_90s", "90s")
        if nineties_col:
            nineties_val = float(row.get(nineties_col, 0) or 0)
            minutes = nineties_val * 90
    if not minutes or minutes < 1:
        # Track B: no silent default. Caller's data_gate will reject the payload.
        raise ValueError(f"Player {player_name!r}: minutes data missing — refusing to fabricate")

    # Detect position
    pos_col = _find_col(std, "pos", "Pos", "position")
    pos_str = str(row[pos_col]) if pos_col else "MF"
    role = _detect_role(pos_str)
    print(f"    [Radar] Position: {pos_str} → role: {role}")

    return tables, row, minutes, role


def _per90(row, df, col_name: str, minutes: float) -> float | None:
    """Extract a per-90 value from a row, computing if raw count."""
    import pandas as pd
    val = row.get(col_name)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    val = float(val)
    if col_name in RATIO_METRICS:
        return round(val, 2)
    return round((val / minutes) * 90, 2)


def _percentile_rank(tables, stat_type: str, col: str,
                     player_val: float, role: str, minutes: float) -> int:
    """
    Calculate the player's percentile rank vs positional peers
    (min 450 minutes played).
    """
    import pandas as pd
    import numpy as np

    df = tables.get(stat_type)
    if df is None or col not in df.columns:
        return 50  # unknown → neutral

    std = tables.get("standard")

    # Build a minutes Series indexed by player name
    min_series = None
    if std is not None:
        min_col = _find_col(std, "minutes", "min")
        player_col_std = _find_col(std, "player", "Player")
        if min_col and player_col_std:
            min_series = std.set_index(player_col_std)[min_col].apply(
                lambda x: float(x) if not pd.isna(x) else 0
            )

    # Position filter
    pos_col_df = _find_col(df, "pos", "Pos", "position")
    role_pos_map = {
        "striker":  ["FW", "FW,MF"],
        "cam":      ["MF,FW", "MF", "FW,MF"],
        "winger":   ["MF,FW", "FW,MF", "FW"],
        "cm":       ["MF", "MF,DF", "DF,MF"],
        "dm":       ["MF", "MF,DF", "DF,MF"],
        "defender": ["DF", "DF,MF", "MF,DF"],
        "gk":       ["GK"],
    }
    allowed_pos = role_pos_map.get(role, [])

    peer_vals = []
    player_col = _find_col(df, "player", "Player")
    for _, r in df.iterrows():
        # Position filter
        if pos_col_df:
            rpos = str(r.get(pos_col_df, "")).upper()
            if allowed_pos and not any(p in rpos for p in allowed_pos):
                continue
        # Minutes filter
        mins = 0.0
        if min_series is not None and player_col and r.get(player_col) in min_series:
            mins = float(min_series.get(r[player_col], 0) or 0)
        if mins < 450:
            continue
        raw = r.get(col)
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            continue
        v = float(raw)
        if col not in RATIO_METRICS:
            v = (v / mins) * 90 if mins > 0 else 0
        peer_vals.append(v)

    if not peer_vals or player_val is None:
        return 50

    pct = int(round(100 * sum(1 for v in peer_vals if v <= player_val) / len(peer_vals)))
    if col in LOWER_IS_BETTER:
        pct = 100 - pct
    return min(99, max(1, pct))


# ── LLM fallback ──────────────────────────────────────────────────────────────

def _llm_radar_props(player_name: str, club: str,
                     competition: str, season: str) -> dict:
    """
    Use the LLM to estimate radar props when FBref is unavailable.
    Returns AttackingRadar-compatible dict.
    """
    print(f"    [Radar] Using LLM fallback for {player_name}")
    prompt = f"""You are a football statistics expert.

Generate realistic per-90 statistics and percentile rankings for {player_name} ({club}, {competition} {season}).
Determine the player's position and select the most relevant 9 metrics for that role.

For a striker, use: Non-Penalty Goals, xG, Shots, xG/Shot, Touches in Box, Dribbles Completed, Progressive Carries, Aerial Duels Won %, Shot-Creating Actions
For a CAM/10, use: Non-Penalty Goals, xG, xA, Shot-Creating Actions, Key Passes, Dribbles Completed, Progressive Carries, Progressive Passes, Touches in Box
For a winger, use: xG, xA, Dribbles, Progressive Carries, Key Passes, Crosses, Touches in Box, SCA, Progressive Passes
For a CM, use: Progressive Passes, Key Passes, SCA, xA, Progressive Carries, Dribbles, Ball Recoveries, Pressures, xG

Return JSON only:
{{
  "entityName": "{player_name}",
  "competition": "{competition}",
  "season": "{season}",
  "nineties": 0,
  "accentColor": "#XXXXXX",
  "metrics": [
    {{
      "label": "Metric\\nName",
      "value": 0.00,
      "percentile": 0,
      "unit": ""
    }}
  ]
}}

Rules:
- Use accurate, realistic values you are highly confident about
- percentile = rank vs positional peers in the same league (0-100)
- accentColor = player's club primary colour
- nineties = approximate 90s played this season
- Return EXACTLY 9 metrics
"""
    try:
        res = ask_llm(prompt, expect_json=True)
        data = json.loads(res)
        if isinstance(data, dict) and "metrics" in data:
            return data
    except Exception as e:
        print(f"    [Radar] LLM fallback failed: {e}")

    # Hard fallback — blank structure
    return {
        "entityName": player_name,
        "competition": competition,
        "season": season,
        "nineties": 20,
        "accentColor": "#C8102E",
        "metrics": [],
    }


# ── Public API ────────────────────────────────────────────────────────────────

def build_radar_props(player_name: str, club: str,
                      competition: str, season: str) -> dict:
    """
    Build a complete AttackingRadar props dict.
    Tries FBref via soccerdata, falls back to LLM.

    Returns a dict compatible with AttackingRadarPropsSchema.
    """
    from utils.remotion_renderer import TEAM_CONFIG, FALLBACK_CONFIG

    club_cfg    = TEAM_CONFIG.get(club, FALLBACK_CONFIG)
    accent      = club_cfg["color"]

    try:
        tables, player_row, minutes, role = _fetch_fbref(player_name, competition, season)

        metric_defs = ROLE_METRICS.get(role, ROLE_METRICS["cam"])
        metrics = []

        for label, stat_type, col, unit in metric_defs:
            df = tables.get(stat_type)
            if df is None:
                continue

            # Try to find the column with flexible matching
            actual_col = _find_col(df, col, col.replace("_", " "))
            if actual_col is None:
                print(f"    [Radar] Column '{col}' not found in {stat_type}, skipping")
                continue

            # Get raw value from player row
            # We need the player row from THIS stat table (not just standard)
            row_in_table = _find_player(df, player_name)
            if row_in_table is None:
                print(f"    [Radar] Player not found in {stat_type} table, skipping {col}")
                continue

            # Special handling for npg (non-penalty goals = goals - pens_made)
            if col == "goals_minus_pens":
                goals_col = _find_col(tables["standard"], "goals")
                pens_col  = _find_col(tables["standard"], "pens_made", "pk")
                std_row   = _find_player(tables["standard"], player_name)
                if std_row is not None and goals_col and pens_col:
                    import pandas as pd
                    g = float(std_row.get(goals_col) or 0)
                    p = float(std_row.get(pens_col)  or 0)
                    raw_val = g - p
                    val_p90 = round((raw_val / minutes) * 90, 2)
                else:
                    continue
                # Calc percentile using goals - pens in standard table
                pct = 50
            else:
                val_p90 = _per90(row_in_table, df, actual_col, minutes)
                if val_p90 is None:
                    continue
                pct = _percentile_rank(
                    tables, stat_type, actual_col, val_p90, role, minutes
                )

            # For ratio metrics, recalc percentile directly
            if col == "goals_minus_pens":
                pct = _percentile_rank(
                    tables, "standard",
                    _find_col(tables["standard"], "goals") or "goals",
                    val_p90, role, minutes
                )

            metrics.append({
                "label":      label,
                "value":      val_p90,
                "percentile": pct,
                "unit":       unit,
            })

        # Nineties played
        nineties_col = _find_col(tables["standard"], "minutes_90s", "90s", "min")
        nineties_val = 0
        std_player_row = _find_player(tables["standard"], player_name)
        if std_player_row is not None and nineties_col:
            import pandas as pd
            raw = std_player_row.get(nineties_col, 0)
            if not pd.isna(raw):
                nineties_val = round(float(raw), 1)
                if nineties_col in ("minutes", "min"):
                    nineties_val = round(float(raw) / 90, 1)

        print(f"    [Radar] ✓ Built {len(metrics)} metrics for {player_name} (role: {role})")

        return {
            "entityName":     player_name,
            "competition":    competition,
            "season":         season,
            "matchType":      "All Matches",
            "nineties":       nineties_val or 20,
            "accentColor":    accent,
            "bgColor":        "#f0ece4",
            "lightMode":      True,
            "introFrames":    40,
            "revealInterval": 50,
            "metrics":        metrics,
            "_source":        "fbref",  # Track B provenance stamp
        }

    except Exception as e:
        print(f"    [Radar] FBref pipeline failed: {e}")
        traceback.print_exc()
        # Fall back to LLM — emit a prominent warning so the user knows stats are estimated
        print(f"    [Radar] ⚠ WARNING: Using ESTIMATED stats (FBref unavailable) — verify radar values before publishing")
        props = _llm_radar_props(player_name, club, competition, season)
        props.setdefault("accentColor",    accent)
        props["bgColor"]   = "#f0ece4"
        props["lightMode"] = True
        props.setdefault("introFrames",    40)
        props.setdefault("revealInterval", 50)
        # Strip " per 90" / " per game" suffixes from metric units — value already is per-90
        for m in props.get("metrics", []):
            if isinstance(m.get("unit"), str):
                m["unit"] = re.sub(r'\s*(per\s*90|per\s*game|p90)\s*$', '', m["unit"], flags=re.IGNORECASE).strip()
        props.setdefault("matchType",      "All Matches")
        props["_source"] = "llm"  # Track B provenance stamp — data_gate will reject
        return props
