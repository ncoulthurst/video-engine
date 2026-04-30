"""
Track B — Formation validator (soft validation, hard counts on critical roles).

Public API:
  - validate_formation(payload: dict) -> tuple[bool, str]
  - ROLE_BANDS, FORMATION_RULES, CRITICAL_ROLES, SOFT_TOL (constants)
  - STOCK_FORMATION_PYTHON: canonical stock node layouts (mirrors stockFormations.ts)

Hard-checks GK + CB counts only. Other roles allowed ±1 substitution tolerance
(inverted full-backs, false 9, etc.). Coordinate band membership uses ±SOFT_TOL.
"""

import math
import itertools
from collections import Counter

SOFT_TOL = 8.0  # ±percentage-point tolerance on band membership

ROLE_BANDS = {
    "GK":  {"y": (2, 8),    "x": [(45, 55)]},
    "CB":  {"y": (15, 28),  "x": [(28, 45), (55, 72)]},
    "FB":  {"y": (22, 40),  "x": [(5, 20), (80, 95)]},
    "CDM": {"y": (32, 48),  "x": [(35, 50), (50, 65)]},
    "CM":  {"y": (40, 58),  "x": [(25, 75)]},
    "AM":  {"y": (55, 72),  "x": [(35, 65)]},
    "W":   {"y": (60, 80),  "x": [(5, 22), (78, 95)]},
    "ST":  {"y": (75, 92),  "x": [(35, 65)]},
}

CRITICAL_ROLES = frozenset({"GK", "CB"})

FORMATION_RULES = {
    "4-2-3-1": {"GK": 1, "CB": 2, "FB": 2, "CDM": 2, "AM": 1, "W": 2, "ST": 1},
    "4-2-2-2": {"GK": 1, "CB": 2, "FB": 2, "CDM": 2, "AM": 2, "ST": 2},
    "4-3-3":   {"GK": 1, "CB": 2, "FB": 2, "CM": 3, "W": 2, "ST": 1},
    "3-5-2":   {"GK": 1, "CB": 3, "FB": 2, "CM": 3, "ST": 2},
    "4-4-2":   {"GK": 1, "CB": 2, "FB": 2, "CM": 2, "W": 2, "ST": 2},
    "4-1-4-1": {"GK": 1, "CB": 2, "FB": 2, "CDM": 1, "CM": 2, "W": 2, "ST": 1},
}


def _in_band_soft(value: float, lo: float, hi: float, tol: float = SOFT_TOL) -> bool:
    return (lo - tol) <= value <= (hi + tol)


def validate_formation(payload: dict) -> tuple[bool, str]:
    """Returns (is_valid, reason). Soft validation on non-critical roles."""
    name = payload.get("formation")
    nodes = payload.get("nodes") or payload.get("players") or []

    if name not in FORMATION_RULES:
        return False, f"unknown formation {name!r}"

    expected = FORMATION_RULES[name]
    counts = Counter(n.get("role") for n in nodes if n.get("role"))

    if sum(counts.values()) != 11:
        return False, f"need 11 players, got {sum(counts.values())}"

    for role in CRITICAL_ROLES:
        if counts.get(role, 0) != expected.get(role, 0):
            return False, (
                f"critical role {role}: expected {expected.get(role, 0)}, "
                f"got {counts.get(role, 0)}"
            )

    non_critical_expected = sum(v for k, v in expected.items() if k not in CRITICAL_ROLES)
    non_critical_actual   = sum(v for k, v in counts.items()   if k not in CRITICAL_ROLES)
    if abs(non_critical_actual - non_critical_expected) > 1:
        return False, "non-critical role count drift > 1"

    for n in nodes:
        role = n.get("role")
        band = ROLE_BANDS.get(role)
        if not band:
            continue
        try:
            ny = float(n.get("y"))
            nx = float(n.get("x"))
        except (TypeError, ValueError):
            return False, f"node missing/invalid x,y for role {role}"
        if not _in_band_soft(ny, band["y"][0], band["y"][1]):
            return False, f"{role} y={ny} outside band {band['y']} ±{SOFT_TOL}"
        if not any(_in_band_soft(nx, lo, hi) for lo, hi in band["x"]):
            return False, f"{role} x={nx} outside any x-band"

    for role in ("CB", "FB", "W"):
        pts = sorted(float(n["x"]) for n in nodes if n.get("role") == role)
        if len(pts) == 2 and abs((pts[0] + pts[1]) - 100) > 8:
            return False, f"{role} not mirrored (sum={pts[0] + pts[1]:.1f})"

    for a, b in itertools.combinations(nodes, 2):
        try:
            d = math.dist((float(a["x"]), float(a["y"])), (float(b["x"]), float(b["y"])))
        except (TypeError, ValueError, KeyError):
            continue
        if d < 6:
            return False, f"nodes {a.get('role')}/{b.get('role')} too close (d={d:.1f})"

    return True, "ok"


# ── Stock canonical layouts (mirrors <REMOTION_PROJECT_PATH>/src/lib/stockFormations.ts)
# Used as fallback when validate_formation fails.

STOCK_FORMATION_PYTHON = {
    "4-2-3-1": [
        {"role": "GK",  "x": 50, "y": 5,  "name": ""},
        {"role": "CB",  "x": 36, "y": 22, "name": ""},
        {"role": "CB",  "x": 64, "y": 22, "name": ""},
        {"role": "FB",  "x": 12, "y": 30, "name": ""},
        {"role": "FB",  "x": 88, "y": 30, "name": ""},
        {"role": "CDM", "x": 40, "y": 42, "name": ""},
        {"role": "CDM", "x": 60, "y": 42, "name": ""},
        {"role": "AM",  "x": 50, "y": 62, "name": ""},
        {"role": "W",   "x": 14, "y": 70, "name": ""},
        {"role": "W",   "x": 86, "y": 70, "name": ""},
        {"role": "ST",  "x": 50, "y": 84, "name": ""},
    ],
    "4-2-2-2": [
        {"role": "GK",  "x": 50, "y": 5,  "name": ""},
        {"role": "CB",  "x": 36, "y": 22, "name": ""},
        {"role": "CB",  "x": 64, "y": 22, "name": ""},
        {"role": "FB",  "x": 12, "y": 30, "name": ""},
        {"role": "FB",  "x": 88, "y": 30, "name": ""},
        {"role": "CDM", "x": 40, "y": 42, "name": ""},
        {"role": "CDM", "x": 60, "y": 42, "name": ""},
        {"role": "AM",  "x": 38, "y": 64, "name": ""},
        {"role": "AM",  "x": 62, "y": 64, "name": ""},
        {"role": "ST",  "x": 42, "y": 84, "name": ""},
        {"role": "ST",  "x": 58, "y": 84, "name": ""},
    ],
    "4-3-3": [
        {"role": "GK",  "x": 50, "y": 5,  "name": ""},
        {"role": "CB",  "x": 36, "y": 22, "name": ""},
        {"role": "CB",  "x": 64, "y": 22, "name": ""},
        {"role": "FB",  "x": 12, "y": 30, "name": ""},
        {"role": "FB",  "x": 88, "y": 30, "name": ""},
        {"role": "CM",  "x": 30, "y": 48, "name": ""},
        {"role": "CM",  "x": 50, "y": 48, "name": ""},
        {"role": "CM",  "x": 70, "y": 48, "name": ""},
        {"role": "W",   "x": 14, "y": 72, "name": ""},
        {"role": "W",   "x": 86, "y": 72, "name": ""},
        {"role": "ST",  "x": 50, "y": 84, "name": ""},
    ],
    "3-5-2": [
        {"role": "GK",  "x": 50, "y": 5,  "name": ""},
        {"role": "CB",  "x": 32, "y": 22, "name": ""},
        {"role": "CB",  "x": 50, "y": 22, "name": ""},
        {"role": "CB",  "x": 68, "y": 22, "name": ""},
        {"role": "FB",  "x": 10, "y": 38, "name": ""},
        {"role": "FB",  "x": 90, "y": 38, "name": ""},
        {"role": "CM",  "x": 32, "y": 50, "name": ""},
        {"role": "CM",  "x": 50, "y": 50, "name": ""},
        {"role": "CM",  "x": 68, "y": 50, "name": ""},
        {"role": "ST",  "x": 42, "y": 84, "name": ""},
        {"role": "ST",  "x": 58, "y": 84, "name": ""},
    ],
    "4-4-2": [
        {"role": "GK",  "x": 50, "y": 5,  "name": ""},
        {"role": "CB",  "x": 36, "y": 22, "name": ""},
        {"role": "CB",  "x": 64, "y": 22, "name": ""},
        {"role": "FB",  "x": 12, "y": 30, "name": ""},
        {"role": "FB",  "x": 88, "y": 30, "name": ""},
        {"role": "CM",  "x": 36, "y": 50, "name": ""},
        {"role": "CM",  "x": 64, "y": 50, "name": ""},
        {"role": "W",   "x": 14, "y": 68, "name": ""},
        {"role": "W",   "x": 86, "y": 68, "name": ""},
        {"role": "ST",  "x": 42, "y": 84, "name": ""},
        {"role": "ST",  "x": 58, "y": 84, "name": ""},
    ],
    "4-1-4-1": [
        {"role": "GK",  "x": 50, "y": 5,  "name": ""},
        {"role": "CB",  "x": 36, "y": 22, "name": ""},
        {"role": "CB",  "x": 64, "y": 22, "name": ""},
        {"role": "FB",  "x": 12, "y": 30, "name": ""},
        {"role": "FB",  "x": 88, "y": 30, "name": ""},
        {"role": "CDM", "x": 50, "y": 40, "name": ""},
        {"role": "CM",  "x": 32, "y": 56, "name": ""},
        {"role": "CM",  "x": 68, "y": 56, "name": ""},
        {"role": "W",   "x": 14, "y": 70, "name": ""},
        {"role": "W",   "x": 86, "y": 70, "name": ""},
        {"role": "ST",  "x": 50, "y": 84, "name": ""},
    ],
}
