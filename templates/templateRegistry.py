"""
Track E — single source of truth for template-level metadata.

Consumed by:
  - server.py (Track A): resolve_template, ShouldRenderGate, BANNED_AUTOGEN
  - tests / debug tooling

Three exports only:
  - ACT_TEMPLATE_WHITELIST: dict[int, dict[str, list[str]]]
  - TEMPLATE_DATA_KIND:     dict[str, str]
  - BANNED_AUTOGEN:         frozenset[str]
"""

ACT_TEMPLATE_WHITELIST = {
    1: {
        "MUST_VISUALISE":   ["HeroIntro", "ArticleHeadline", "ScoutReport"],
        "SHOULD_VISUALISE": ["WorldPan"],
    },
    2: {
        "MUST_VISUALISE":   ["HeroStatBars", "PlayerStats", "AttackingRadar", "CareerTimeline", "PortraitStatHero", "PortraitWithBars"],
        "SHOULD_VISUALISE": ["PlayerTrio", "HeroComparisonRadar"],
    },
    3: {
        "MUST_VISUALISE":   ["HeroTactical", "TeamLineup", "HeroBigStat"],
        "SHOULD_VISUALISE": ["CountdownReveal", "PlayerTrio", "PortraitStatHero"],
    },
    4: {
        "MUST_VISUALISE":   ["HeroStatBars", "HeroAwardsList", "HeroLeagueGraph", "PortraitStatHero", "PortraitWithBars"],
        "SHOULD_VISUALISE": ["HeroComparisonRadar", "PlayerTrio"],
    },
    5: {
        "MUST_VISUALISE":   ["ArticleHeadline", "HeroQuote", "QuoteCard"],
        "SHOULD_VISUALISE": ["WorldPan"],
    },
}

TEMPLATE_DATA_KIND = {
    "HeroStatBars":       "stat",
    "HeroLeagueGraph":    "stat",
    "PlayerStats":           "stat",
    "AttackingRadar":        "stat",
    "HeroBigStat":        "stat",
    "HeroAwardsList":     "timeline",
    "CareerTimeline":        "timeline",
    "HeroSeasonTimeline": "timeline",
    "HeroTactical":       "formation",
    "TeamLineup":            "formation",
    "CountdownReveal":       "ranking",
    "TopScorersTable":       "ranking",
    "PlayerTrio":            "entity",
    "ScoutReport":           "entity",
    "HeroComparisonRadar":"comparison",
    "ArticleHeadline":       "copy",
    "WorldPan":              "copy",
    "HeroQuote":          "copy",
    "QuoteCard":             "copy",
    "HeroIntro":          "copy",
    # Track E hybrid templates (Phase 6 — portrait + stat composites)
    "PortraitStatHero":      "stat",
    "PortraitWithBars":      "stat",
}

BANNED_AUTOGEN = frozenset({"TournamentBracket", "TopScorersTable"})
