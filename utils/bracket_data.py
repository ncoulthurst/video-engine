"""
Known tournament bracket data for the TOURNAMENT BRACKET tag.

Each tournament is stored as a list of Match dicts matching the schema in
`remotiontest/src/TournamentBracket.tsx`:

    { "round":  "R16" | "QF" | "SF" | "Final",
      "teamA":  str,
      "teamB":  str,
      "scoreA": int,
      "scoreB": int,
      "winner": str }

Matches for a given round must be provided in the order required by the bracket
layout in TournamentBracket.tsx (R16: 8 matches — first four L-side, last four
R-side; QF: 4 matches — first two L-side, last two R-side; SF: 2 matches —
L then R; Final: 1 match).
"""

from __future__ import annotations

from typing import Any


# ── 2002 FIFA World Cup (South Korea / Japan) ─────────────────────────────────
_WC_2002: list[dict[str, Any]] = [
    # R16 — L side
    {"round": "R16", "teamA": "Germany",    "teamB": "Paraguay",    "scoreA": 1, "scoreB": 0, "winner": "Germany"},
    {"round": "R16", "teamA": "England",    "teamB": "Denmark",     "scoreA": 3, "scoreB": 0, "winner": "England"},
    {"round": "R16", "teamA": "Senegal",    "teamB": "Sweden",      "scoreA": 2, "scoreB": 1, "winner": "Senegal"},
    {"round": "R16", "teamA": "Spain",      "teamB": "Ireland",     "scoreA": 1, "scoreB": 1, "winner": "Spain"},
    # R16 — R side
    {"round": "R16", "teamA": "USA",        "teamB": "Mexico",      "scoreA": 2, "scoreB": 0, "winner": "USA"},
    {"round": "R16", "teamA": "Brazil",     "teamB": "Belgium",     "scoreA": 2, "scoreB": 0, "winner": "Brazil"},
    {"round": "R16", "teamA": "Japan",      "teamB": "Turkey",      "scoreA": 0, "scoreB": 1, "winner": "Turkey"},
    {"round": "R16", "teamA": "South Korea","teamB": "Italy",       "scoreA": 2, "scoreB": 1, "winner": "South Korea"},
    # QF — L side
    {"round": "QF",  "teamA": "Germany",    "teamB": "USA",         "scoreA": 1, "scoreB": 0, "winner": "Germany"},
    {"round": "QF",  "teamA": "England",    "teamB": "Brazil",      "scoreA": 1, "scoreB": 2, "winner": "Brazil"},
    # QF — R side
    {"round": "QF",  "teamA": "Senegal",    "teamB": "Turkey",      "scoreA": 0, "scoreB": 1, "winner": "Turkey"},
    {"round": "QF",  "teamA": "Spain",      "teamB": "South Korea", "scoreA": 0, "scoreB": 0, "winner": "South Korea"},
    # SF
    {"round": "SF",  "teamA": "Germany",    "teamB": "South Korea", "scoreA": 1, "scoreB": 0, "winner": "Germany"},
    {"round": "SF",  "teamA": "Brazil",     "teamB": "Turkey",      "scoreA": 1, "scoreB": 0, "winner": "Brazil"},
    # Final
    {"round": "Final","teamA":"Germany",    "teamB": "Brazil",      "scoreA": 0, "scoreB": 2, "winner": "Brazil"},
]


# ── 2022 FIFA World Cup (Qatar) ───────────────────────────────────────────────
_WC_2022: list[dict[str, Any]] = [
    # R16 — L side
    {"round": "R16", "teamA": "Netherlands","teamB": "USA",        "scoreA": 3, "scoreB": 1, "winner": "Netherlands"},
    {"round": "R16", "teamA": "Argentina", "teamB": "Australia",   "scoreA": 2, "scoreB": 1, "winner": "Argentina"},
    {"round": "R16", "teamA": "France",    "teamB": "Poland",      "scoreA": 3, "scoreB": 1, "winner": "France"},
    {"round": "R16", "teamA": "England",   "teamB": "Senegal",     "scoreA": 3, "scoreB": 0, "winner": "England"},
    # R16 — R side
    {"round": "R16", "teamA": "Japan",     "teamB": "Croatia",     "scoreA": 1, "scoreB": 1, "winner": "Croatia"},
    {"round": "R16", "teamA": "Brazil",    "teamB": "South Korea", "scoreA": 4, "scoreB": 1, "winner": "Brazil"},
    {"round": "R16", "teamA": "Morocco",   "teamB": "Spain",       "scoreA": 0, "scoreB": 0, "winner": "Morocco"},
    {"round": "R16", "teamA": "Portugal",  "teamB": "Switzerland", "scoreA": 6, "scoreB": 1, "winner": "Portugal"},
    # QF — L side
    {"round": "QF",  "teamA": "Netherlands","teamB": "Argentina",  "scoreA": 2, "scoreB": 2, "winner": "Argentina"},
    {"round": "QF",  "teamA": "France",    "teamB": "England",     "scoreA": 2, "scoreB": 1, "winner": "France"},
    # QF — R side
    {"round": "QF",  "teamA": "Croatia",   "teamB": "Brazil",      "scoreA": 1, "scoreB": 1, "winner": "Croatia"},
    {"round": "QF",  "teamA": "Morocco",   "teamB": "Portugal",    "scoreA": 1, "scoreB": 0, "winner": "Morocco"},
    # SF
    {"round": "SF",  "teamA": "Argentina", "teamB": "Croatia",     "scoreA": 3, "scoreB": 0, "winner": "Argentina"},
    {"round": "SF",  "teamA": "France",    "teamB": "Morocco",     "scoreA": 2, "scoreB": 0, "winner": "France"},
    # Final
    {"round": "Final","teamA":"Argentina", "teamB": "France",      "scoreA": 3, "scoreB": 3, "winner": "Argentina"},
]


# ── Euro 2024 (Germany) ───────────────────────────────────────────────────────
_EURO_2024: list[dict[str, Any]] = [
    # R16 — L side
    {"round": "R16", "teamA": "Switzerland","teamB": "Italy",      "scoreA": 2, "scoreB": 0, "winner": "Switzerland"},
    {"round": "R16", "teamA": "Germany",   "teamB": "Denmark",     "scoreA": 2, "scoreB": 0, "winner": "Germany"},
    {"round": "R16", "teamA": "Spain",     "teamB": "Georgia",     "scoreA": 4, "scoreB": 1, "winner": "Spain"},
    {"round": "R16", "teamA": "France",    "teamB": "Belgium",     "scoreA": 1, "scoreB": 0, "winner": "France"},
    # R16 — R side
    {"round": "R16", "teamA": "Portugal",  "teamB": "Slovenia",    "scoreA": 0, "scoreB": 0, "winner": "Portugal"},
    {"round": "R16", "teamA": "Romania",   "teamB": "Netherlands", "scoreA": 0, "scoreB": 3, "winner": "Netherlands"},
    {"round": "R16", "teamA": "Austria",   "teamB": "Turkey",      "scoreA": 1, "scoreB": 2, "winner": "Turkey"},
    {"round": "R16", "teamA": "England",   "teamB": "Slovakia",    "scoreA": 2, "scoreB": 1, "winner": "England"},
    # QF — L side
    {"round": "QF",  "teamA": "Spain",     "teamB": "Germany",     "scoreA": 2, "scoreB": 1, "winner": "Spain"},
    {"round": "QF",  "teamA": "Portugal",  "teamB": "France",      "scoreA": 0, "scoreB": 0, "winner": "France"},
    # QF — R side
    {"round": "QF",  "teamA": "England",   "teamB": "Switzerland", "scoreA": 1, "scoreB": 1, "winner": "England"},
    {"round": "QF",  "teamA": "Netherlands","teamB": "Turkey",     "scoreA": 2, "scoreB": 1, "winner": "Netherlands"},
    # SF
    {"round": "SF",  "teamA": "Spain",     "teamB": "France",      "scoreA": 2, "scoreB": 1, "winner": "Spain"},
    {"round": "SF",  "teamA": "Netherlands","teamB": "England",    "scoreA": 1, "scoreB": 2, "winner": "England"},
    # Final
    {"round": "Final","teamA":"Spain",     "teamB": "England",     "scoreA": 2, "scoreB": 1, "winner": "Spain"},
]


# Tournament name variations → canonical bracket.
# Keys are normalised to lower-case and collapsed whitespace before lookup.
_BRACKETS: dict[str, list[dict[str, Any]]] = {
    "fifa world cup 2002":   _WC_2002,
    "world cup 2002":        _WC_2002,
    "2002 fifa world cup":   _WC_2002,
    "2002 world cup":        _WC_2002,
    "wc 2002":               _WC_2002,

    "fifa world cup 2022":   _WC_2022,
    "world cup 2022":        _WC_2022,
    "2022 fifa world cup":   _WC_2022,
    "2022 world cup":        _WC_2022,
    "wc 2022":               _WC_2022,

    "euro 2024":             _EURO_2024,
    "uefa euro 2024":        _EURO_2024,
    "european championship 2024": _EURO_2024,
    "2024 euro":             _EURO_2024,
}


def _normalise(name: str) -> str:
    return " ".join(name.lower().split())


def lookup_bracket(tournament: str) -> list[dict[str, Any]] | None:
    """Return the match list for `tournament` if we know it, else None."""
    return _BRACKETS.get(_normalise(tournament))


def known_tournaments() -> list[str]:
    """Canonical display names for tournaments we have data for."""
    return ["FIFA World Cup 2002", "FIFA World Cup 2022", "Euro 2024"]
