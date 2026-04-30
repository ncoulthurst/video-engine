"""
format_utils — Format profile selection and scaling.

compute_format_profile(context, entity, blueprint) → profile dict
  Selects the best-fit format profile from format_profiles.json based on:
    - entity count   (number of named subjects)
    - time span      (year range in context)
    - event density  (number of named events / facts)

  Returns a profile dict with:
    acts, clips_per_act, graphics_per_act, complexity_score, format_name
"""

import json
import re
from pathlib import Path

_PROFILES_PATH = Path(__file__).parent.parent / "templates" / "format_profiles.json"


def _load_profiles():
    if _PROFILES_PATH.exists():
        return json.loads(_PROFILES_PATH.read_text())
    return {}


def compute_format_profile(context="", entity="", blueprint=None, format_override=None):
    """Select and return a format profile scaled to this video's complexity.

    Args:
        context:   Raw Director's Brief / context.md text
        entity:    Primary subject (player, team, concept)
        blueprint: Blueprint dict (optional) — used to count acts and events

    Returns:
        dict with keys: acts, clips_per_act, graphics_per_act,
                        complexity_score, format_name
    """
    profiles = _load_profiles()
    if not profiles:
        return _default_profile()

    # Explicit user override — skip all heuristics
    if format_override and format_override in profiles:
        profile = dict(profiles[format_override])
        profile["format_name"] = format_override
        if blueprint:
            actual_acts = len(blueprint.get("acts", []))
            declared_acts = profile.get("acts", 5)
            if actual_acts and actual_acts != declared_acts:
                ratio = actual_acts / declared_acts
                profile["clips_per_act"]    = max(2, round(profile.get("clips_per_act", 6) * ratio))
                profile["graphics_per_act"] = max(2, round(profile.get("graphics_per_act", 6) * ratio))
                profile["acts"] = actual_acts
        return profile

    combined = (context + " " + entity).lower()

    # ── Heuristic signals ────────────────────────────────────────────────────

    # Entity count: number of distinct proper nouns (rough proxy)
    named = re.findall(r'\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b', context)
    entity_count = len(set(named))

    # Time span: year range found in context
    years = [int(y) for y in re.findall(r'\b(19[5-9]\d|20[0-3]\d)\b', context)]
    time_span = (max(years) - min(years)) if len(years) >= 2 else 0

    # Event density: bullet points or numbered facts in context
    events = re.findall(r'(?:^|\n)\s*[-•*\d]+[\.\)]\s+\S', context)
    event_density = len(events)

    # ── Format selection ─────────────────────────────────────────────────────
    # Detect explicit format keywords in the context or entity string
    format_name = _detect_format_keyword(combined)

    if not format_name:
        # Score-based fallback
        if time_span >= 15 and entity_count >= 10:
            format_name = "documentary"
        elif event_density >= 12 and time_span < 10:
            format_name = "breakdown"
        elif time_span < 5 and entity_count < 6:
            format_name = "news-style"
        elif any(kw in combined for kw in ("history of", "evolution of", "rise of", "why ")) and time_span < 5:
            # Only classify as short-form explainer when time span is narrow.
            # Long thematic docs ("Why Brazil Stopped...") span decades → documentary.
            format_name = "explainer"
        elif time_span >= 8:
            format_name = "story"
        else:
            format_name = "documentary"

    profile = dict(profiles.get(format_name, profiles.get("documentary", {})))
    profile["format_name"] = format_name

    # ── Scale budgets to blueprint act count ─────────────────────────────────
    if blueprint:
        actual_acts = len(blueprint.get("acts", []))
        declared_acts = profile.get("acts", 5)
        if actual_acts and actual_acts != declared_acts:
            # Pro-rate clips/graphics budgets to actual act count
            ratio = actual_acts / declared_acts
            profile["clips_per_act"]    = max(2, round(profile.get("clips_per_act", 6) * ratio))
            profile["graphics_per_act"] = max(2, round(profile.get("graphics_per_act", 6) * ratio))
            profile["acts"] = actual_acts

    return profile


def _detect_format_keyword(text):
    """Return a profile name if an explicit format keyword is in the text."""
    mapping = {
        "explainer":  ["explainer", "how football", "why football", "history of", "evolution of"],
        "breakdown":  ["breakdown", "tactical", "analysis", "deep dive"],
        "news-style": ["news", "short-form", "quick take"],
        "story":      ["story", "rise and fall", "journey"],
    }
    for profile_name, keywords in mapping.items():
        if any(kw in text for kw in keywords):
            return profile_name
    return None


def _default_profile():
    return {
        "acts": 5,
        "clips_per_act": 6,
        "graphics_per_act": 6,
        "complexity_score": 1.0,
        "format_name": "documentary",
    }
