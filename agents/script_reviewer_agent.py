"""
Script Reviewer Agent
Validates a storyboard (list of scene dicts) before the pipeline renders anything.
Returns a structured report of issues so the user can fix them in the editor first.

Called from server.py /review-storyboard endpoint and also from orchestrator.py
after script_agent runs (for script_draft.md validation).
"""

import re
from pathlib import Path

# ── Known tag formats ─────────────────────────────────────────────────────────

VALID_TRANSITIONS = {"letterbox", "push", "grain", "paper", "dataline", "flash", "none", "evolve"}

# Tags that must have a season like YYYY/YY
SEASON_TAGS = {"TOP SCORERS", "TOP ASSISTS", "PLAYER STATS", "STANDINGS TABLE"}

# Acts in order: index → expected mandatory opening transition
ACT_TRANSITIONS = {
    1: "letterbox",
    2: "push",
    3: "letterbox",
    4: "grain",
    5: "paper",
}

# ── Year extraction helpers ────────────────────────────────────────────────────

def _extract_years(text):
    """Return list of 4-digit years mentioned in text, in order."""
    return [int(y) for y in re.findall(r'\b(19\d{2}|20[0-2]\d)\b', text)]


def _content_year(scene):
    """Best year estimate for a scene (from content + label)."""
    years = _extract_years(scene.get("content", "") + " " + scene.get("label", ""))
    return years[0] if years else None


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_transitions(scenes):
    """Each act must open with the correct TRANSITION type."""
    issues = []
    # Group scenes by act
    from collections import defaultdict
    act_scenes = defaultdict(list)
    for s in scenes:
        act_scenes[s.get("actIndex", 0)].append(s)

    for act_idx, required_type in ACT_TRANSITIONS.items():
        act = act_scenes.get(act_idx, [])
        if not act:
            continue
        first = act[0]
        if first.get("template", "").upper() != "TRANSITION":
            issues.append({
                "scene_id": first["id"],
                "tag": first.get("template", ""),
                "problem": f"ACT {act_idx} should open with [TRANSITION: {required_type}] — first scene is {first.get('template') or first.get('type')} instead",
                "severity": "warning",
            })
        elif first.get("content", "").lower().strip() != required_type:
            issues.append({
                "scene_id": first["id"],
                "tag": "TRANSITION",
                "problem": f"ACT {act_idx} transition should be '{required_type}', got '{first.get('content', '').strip()}'",
                "severity": "warning",
            })
    return issues


def _check_player_trio(scenes, entity=""):
    """PLAYER TRIO must appear exactly once in ACT 3, with 3 named individual players."""
    issues = []
    trios = [s for s in scenes if s.get("template", "").upper() == "PLAYER TRIO"]

    if not trios:
        issues.append({
            "scene_id": None,
            "tag": "PLAYER TRIO",
            "problem": "No PLAYER TRIO found — mandatory once in ACT 3 (peak)",
            "severity": "error",
        })
        return issues

    if len(trios) > 1:
        for t in trios[1:]:
            issues.append({
                "scene_id": t["id"],
                "tag": "PLAYER TRIO",
                "problem": "Duplicate PLAYER TRIO — use exactly once per documentary",
                "severity": "warning",
            })

    for trio in trios:
        content = trio.get("content", "")
        # Normalise "vs." → "vs"
        norm = re.sub(r'\bvs\.\s*', 'vs ', content, flags=re.IGNORECASE)
        parts = [p.strip() for p in norm.split(' vs ')]
        if len(parts) != 3:
            issues.append({
                "scene_id": trio["id"],
                "tag": "PLAYER TRIO",
                "problem": f"PLAYER TRIO needs exactly 3 players separated by 'vs' — got: '{content}'",
                "severity": "error",
            })
            continue
        # Check each player name has ≥2 words and no group abbreviations (MSN, BBC, etc.)
        # Exception: well-known mononymous footballers are fine with one name
        MONONYMS = {
            "pelé", "pele", "ronaldinho", "kaká", "kaka", "neymar", "rivaldo",
            "romário", "romario", "zico", "garrincha", "bebeto", "socrates",
            "sócrates", "adriano", "robinho", "júnior", "cafu", "hulk",
        }
        players = [parts[0].split(',')[-1].strip(), parts[1], parts[2]]
        for p in players:
            clean = re.sub(r'[^A-Za-z\s]', '', p).strip()
            words = clean.split()
            if len(words) < 2 and clean.lower() not in MONONYMS:
                issues.append({
                    "scene_id": trio["id"],
                    "tag": "PLAYER TRIO",
                    "problem": f"PLAYER TRIO player '{p}' looks like a group abbreviation or single name — use full names (e.g. 'Lionel Messi')",
                    "severity": "error",
                })
            elif any(w.isupper() and 2 <= len(w) <= 4 for w in words):
                issues.append({
                    "scene_id": trio["id"],
                    "tag": "PLAYER TRIO",
                    "problem": f"PLAYER TRIO contains group abbreviation '{p}' — name each player individually",
                    "severity": "error",
                })

        # Verify trio is in ACT 3
        if trio.get("actIndex", -1) != 3:
            issues.append({
                "scene_id": trio["id"],
                "tag": "PLAYER TRIO",
                "problem": f"PLAYER TRIO is in '{trio.get('act')}' — it belongs in ACT 3 (PEAK)",
                "severity": "warning",
            })

    return issues


def _check_player_radar(scenes):
    """PLAYER RADAR must appear exactly once."""
    radars = [s for s in scenes if s.get("template", "").upper() == "PLAYER RADAR"]
    issues = []
    if not radars:
        issues.append({
            "scene_id": None,
            "tag": "PLAYER RADAR",
            "problem": "No PLAYER RADAR found — mandatory once in ACT 3 (peak) for the player's best season",
            "severity": "warning",
        })
    for r in radars:
        content = r.get("content", "")
        parts = [p.strip() for p in content.split(",")]
        if len(parts) < 4:
            issues.append({
                "scene_id": r["id"],
                "tag": "PLAYER RADAR",
                "problem": f"PLAYER RADAR needs 4 parts: Player, Club, Competition, Season — got: '{content}'",
                "severity": "error",
            })
    return issues


def _check_season_tags(scenes):
    """TOP SCORERS, TOP ASSISTS, PLAYER STATS must have a valid season."""
    issues = []
    for s in scenes:
        tpl = s.get("template", "").upper()
        if tpl not in SEASON_TAGS:
            continue
        content = s.get("content", "")
        if not re.search(r'\d{4}[/\-]\d{2}', content):
            issues.append({
                "scene_id": s["id"],
                "tag": tpl,
                "problem": f"[{tpl}] requires a season (YYYY/YY) — got: '{content}'",
                "severity": "error",
            })
        # TOP SCORERS / TOP ASSISTS must not contain player names (just competition + season)
        if tpl in ("TOP SCORERS", "TOP ASSISTS"):
            # If more than 4 words before the year it likely has a player name
            before_year = re.split(r'\d{4}', content)[0].strip()
            if len(before_year.split()) > 4:
                issues.append({
                    "scene_id": s["id"],
                    "tag": tpl,
                    "problem": f"[{tpl}] content '{content}' looks like it includes player names — format should be 'Competition YYYY/YY' only",
                    "severity": "warning",
                })
    return issues


def _check_transition_tag_format(scenes):
    """TRANSITION content must be a known type."""
    issues = []
    for s in scenes:
        if s.get("template", "").upper() != "TRANSITION":
            continue
        val = s.get("content", "").lower().strip()
        if val not in VALID_TRANSITIONS:
            issues.append({
                "scene_id": s["id"],
                "tag": "TRANSITION",
                "problem": f"Unknown transition type '{val}' — valid: {', '.join(sorted(VALID_TRANSITIONS))}",
                "severity": "error",
            })
    return issues


def _check_hero_big_stat(scenes):
    """HERO BIG STAT needs 4 comma-separated parts: Stat, Unit, Label, Context."""
    issues = []
    for s in scenes:
        if s.get("template", "").upper() != "HERO BIG STAT":
            continue
        parts = [p.strip() for p in s.get("content", "").split(",")]
        if len(parts) < 4:
            issues.append({
                "scene_id": s["id"],
                "tag": "HERO BIG STAT",
                "problem": f"Needs 4 parts: Stat, Unit, Label, Context — got {len(parts)}: '{s.get('content')}'",
                "severity": "error",
            })
    return issues


def _check_team_lineup(scenes):
    """TEAM LINEUP should have format: Team N-N-N vs Opposition, DD Mon YYYY."""
    issues = []
    for s in scenes:
        if s.get("template", "").upper() != "TEAM LINEUP":
            continue
        content = s.get("content", "")
        # Must contain a formation (digit-digit-digit pattern) and "vs"
        has_formation = bool(re.search(r'\d-\d-\d', content))
        has_vs = bool(re.search(r'\bvs\b', content, re.IGNORECASE))
        if not has_formation:
            issues.append({
                "scene_id": s["id"],
                "tag": "TEAM LINEUP",
                "problem": f"TEAM LINEUP missing formation (e.g. 4-3-3) in: '{content}'",
                "severity": "warning",
            })
        if not has_vs:
            issues.append({
                "scene_id": s["id"],
                "tag": "TEAM LINEUP",
                "problem": f"TEAM LINEUP missing 'vs' separator in: '{content}'",
                "severity": "warning",
            })
    return issues


def _check_hero_stat_bars(scenes):
    """HERO STAT BARS needs exactly 4 comma-separated parts."""
    issues = []
    for s in scenes:
        if s.get("template", "").upper() != "HERO STAT BARS":
            continue
        parts = [p.strip() for p in s.get("content", "").split(",")]
        if len(parts) < 4:
            issues.append({
                "scene_id": s["id"],
                "tag": "HERO STAT BARS",
                "problem": f"Needs 4 parts: Title, Subtitle, Team A, Team B — got {len(parts)}: '{s.get('content')}'",
                "severity": "error",
            })
    return issues


def _check_career_timeline(scenes):
    """CAREER TIMELINE content should be 'Player Name' or 'Player Name - Focus: Club'."""
    issues = []
    bad_words = {"ban", "suspension", "controversy", "disciplin", "incident", "international career"}
    for s in scenes:
        if s.get("template", "").upper() != "CAREER TIMELINE":
            continue
        content = s.get("content", "").lower()
        for word in bad_words:
            if word in content:
                issues.append({
                    "scene_id": s["id"],
                    "tag": "CAREER TIMELINE",
                    "problem": f"CAREER TIMELINE content '{s.get('content')}' contains non-club text '{word}' — should be 'Player Name - Focus: Club'",
                    "severity": "error",
                })
                break
    return issues


def _check_chronological_order(scenes):
    """Within each act, narration/clip/graphic scenes should be broadly chronological."""
    issues = []
    from collections import defaultdict
    act_scenes = defaultdict(list)
    for s in scenes:
        if s.get("type") in ("transition",):
            continue
        act_idx = s.get("actIndex", 0)
        year = _content_year(s)
        if year:
            act_scenes[act_idx].append((s["id"], year, s.get("content", "")[:60]))

    for act_idx, items in act_scenes.items():
        for i in range(1, len(items)):
            prev_id, prev_year, prev_content = items[i - 1]
            curr_id, curr_year, curr_content = items[i]
            # Allow up to 3 years of "flashback" before flagging
            if curr_year < prev_year - 3:
                issues.append({
                    "scene_id": curr_id,
                    "tag": "CHRONOLOGY",
                    "problem": f"Possible out-of-order: '{curr_content}' ({curr_year}) appears after '{prev_content}' ({prev_year}) in the same act",
                    "severity": "warning",
                })
    return issues


def _check_mandatory_graphics(scenes):
    """ACT 3 (peak) must contain PLAYER TRIO, PLAYER RADAR, and HERO STAT BARS."""
    issues = []
    act3 = [s for s in scenes if s.get("actIndex") == 3]
    act3_templates = {s.get("template", "").upper() for s in act3}

    required = {
        "PLAYER TRIO": "mandatory in ACT 3 for peer comparison",
        "PLAYER RADAR": "mandatory in ACT 3 for peak season analysis",
        "HERO STAT BARS": "mandatory in ACT 3 for head-to-head stat comparison",
    }
    for tpl, reason in required.items():
        if tpl not in act3_templates:
            issues.append({
                "scene_id": None,
                "tag": tpl,
                "problem": f"[{tpl}] missing from ACT 3 — {reason}",
                "severity": "warning",
            })
    return issues


def _check_hero_form_run(scenes):
    """For title races: if one HERO FORM RUN is present, both teams should be present."""
    issues = []
    form_runs = [s for s in scenes if s.get("template", "").upper() == "HERO FORM RUN"]
    if len(form_runs) == 1:
        issues.append({
            "scene_id": form_runs[0]["id"],
            "tag": "HERO FORM RUN",
            "problem": "Only one HERO FORM RUN found — for a title race, emit one per team (both teams' form runs must appear)",
            "severity": "warning",
        })
    return issues


def _check_hero_intro(scenes):
    """HERO INTRO must be the first scene (scene index 0)."""
    issues = []
    if not scenes:
        return issues
    first = scenes[0]
    if first.get("template", "").upper() != "HERO INTRO":
        issues.append({
            "scene_id": first["id"],
            "tag": "HERO INTRO",
            "problem": f"First scene must be HERO INTRO — got '{first.get('template') or first.get('type')}'",
            "severity": "error",
        })
    # Also check for any duplicate HERO INTRO beyond position 0
    for s in scenes[1:]:
        if s.get("template", "").upper() == "HERO INTRO":
            issues.append({
                "scene_id": s["id"],
                "tag": "HERO INTRO",
                "problem": "Duplicate HERO INTRO — should only appear once at the start",
                "severity": "error",
            })
    return issues


def _check_deprecated_tags(scenes):
    """Flag any deprecated tags that should no longer be used."""
    issues = []
    deprecated = {
        "TROPHY": "Use [HERO BIG STAT: ...] for title wins instead",
        "HERO CHAPTER": "Use [TRANSITION: letterbox] for act breaks instead",
        "QUOTE CARD": "Use [HERO QUOTE: ...] instead",
    }
    for s in scenes:
        tpl = s.get("template", "").upper()
        if tpl in deprecated:
            issues.append({
                "scene_id": s["id"],
                "tag": tpl,
                "problem": f"Deprecated tag [{tpl}] — {deprecated[tpl]}",
                "severity": "warning",
            })
    return issues


def _check_clip_single_density(scenes):
    """Each act should have at least 2 CLIP SINGLE scenes."""
    issues = []
    from collections import defaultdict
    act_clips = defaultdict(int)
    act_names = {}
    for s in scenes:
        act_idx = s.get("actIndex", 0)
        act_names[act_idx] = s.get("act", f"ACT {act_idx}")
        if s.get("template", "").upper() == "CLIP SINGLE" or (s.get("type") == "clip" and s.get("template", "").upper() == "CLIP SINGLE"):
            act_clips[act_idx] += 1
        elif s.get("type") == "clip":
            act_clips[act_idx] += 1

    for act_idx in range(6):
        if act_idx not in act_names:
            continue
        count = act_clips.get(act_idx, 0)
        if count < 2:
            issues.append({
                "scene_id": None,
                "tag": "CLIP SINGLE",
                "problem": f"{act_names[act_idx]} has only {count} clip(s) — aim for at least 2 CLIP SINGLE per act",
                "severity": "info",
            })
    return issues


# ── Main entry point ──────────────────────────────────────────────────────────

def review_storyboard(scenes, entity=""):
    """
    Run all checks against a storyboard scene list.
    Returns { pass: bool, issues: [...], summary: str }
    Each issue: { scene_id, tag, problem, severity }
    severity: 'error' | 'warning' | 'info'
    """
    if not scenes:
        return {"pass": False, "issues": [{"scene_id": None, "tag": "", "problem": "Storyboard is empty", "severity": "error"}], "summary": "0 errors, 0 warnings"}

    all_issues = []
    all_issues += _check_hero_intro(scenes)
    all_issues += _check_transitions(scenes)
    all_issues += _check_player_trio(scenes, entity)
    all_issues += _check_player_radar(scenes)
    all_issues += _check_mandatory_graphics(scenes)
    all_issues += _check_season_tags(scenes)
    all_issues += _check_transition_tag_format(scenes)
    all_issues += _check_hero_big_stat(scenes)
    all_issues += _check_team_lineup(scenes)
    all_issues += _check_hero_stat_bars(scenes)
    all_issues += _check_career_timeline(scenes)
    all_issues += _check_chronological_order(scenes)
    all_issues += _check_hero_form_run(scenes)
    all_issues += _check_deprecated_tags(scenes)
    all_issues += _check_clip_single_density(scenes)

    errors   = [i for i in all_issues if i["severity"] == "error"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]
    infos    = [i for i in all_issues if i["severity"] == "info"]

    passed = len(errors) == 0
    summary = f"{len(errors)} error{'s' if len(errors) != 1 else ''}, {len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
    if infos:
        summary += f", {len(infos)} suggestion{'s' if len(infos) != 1 else ''}"

    return {
        "pass": passed,
        "issues": all_issues,
        "summary": summary,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "info_count": len(infos),
    }


def review_script_draft(script_text, entity=""):
    """
    Parse a script_draft.md into pseudo-scenes and run storyboard review.
    Used by orchestrator.py after script_agent runs.
    Returns same structure as review_storyboard().
    """
    scenes = []
    scene_id = 0
    act_map = {
        "cold open": 0, "act 1": 1, "origins": 1,
        "act 2": 2, "rise": 2,
        "act 3": 3, "peak": 3,
        "act 4": 4, "defining": 4,
        "act 5": 5, "redemption": 5, "legacy": 5,
    }
    current_act = "COLD OPEN"
    current_act_idx = 0

    tag_re = re.compile(r'\[([A-Z][A-Z\s]+?)(?:\s*:\s*(.+?))?\]')

    for line in script_text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Detect act headers
        lower = line.lower().strip("# -")
        for kw, idx in act_map.items():
            if kw in lower:
                current_act_idx = idx
                current_act = line.strip("# ").strip()
                break

        # Extract tags
        for m in tag_re.finditer(line):
            tag_name = m.group(1).strip().upper()
            content  = (m.group(2) or "").strip()
            scene_id += 1
            scene_type = "transition" if tag_name == "TRANSITION" else \
                         "clip"       if "CLIP" in tag_name else "graphic"
            scenes.append({
                "id": f"draft_{scene_id}",
                "act": current_act,
                "actIndex": current_act_idx,
                "type": scene_type,
                "template": tag_name,
                "content": content,
                "label": "",
                "duration": 8,
            })
        # Also capture plain narration lines (no tags)
        elif_narr = tag_re.sub("", line).strip()
        if elif_narr and len(elif_narr) > 20 and not line.startswith("#"):
            scene_id += 1
            scenes.append({
                "id": f"draft_{scene_id}",
                "act": current_act,
                "actIndex": current_act_idx,
                "type": "narration",
                "template": "",
                "content": elif_narr[:120],
                "label": "",
                "duration": 12,
            })

    return review_storyboard(scenes, entity)


if __name__ == "__main__":
    # Quick smoke test
    import json
    test_scenes = [
        {"id": "s1", "act": "COLD OPEN", "actIndex": 0, "type": "graphic", "template": "HERO INTRO", "content": "Test Video", "label": "", "duration": 8},
        {"id": "s2", "act": "ACT 1 — ORIGINS", "actIndex": 1, "type": "transition", "template": "TRANSITION", "content": "letterbox", "label": "", "duration": 2},
        {"id": "s3", "act": "ACT 3 — PEAK", "actIndex": 3, "type": "transition", "template": "TRANSITION", "content": "letterbox", "label": "", "duration": 2},
        {"id": "s4", "act": "ACT 3 — PEAK", "actIndex": 3, "type": "graphic", "template": "PLAYER TRIO", "content": "the debate, Luis Suárez vs Lionel Messi vs Cristiano Ronaldo", "label": "", "duration": 12},
        {"id": "s5", "act": "ACT 3 — PEAK", "actIndex": 3, "type": "graphic", "template": "PLAYER RADAR", "content": "Luis Suárez, Barcelona, La Liga, 2015/16", "label": "", "duration": 10},
    ]
    result = review_storyboard(test_scenes, "Luis Suárez")
    print(json.dumps(result, indent=2))
