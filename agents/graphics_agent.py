"""
Graphics Agent — scans the script for infographic tags and renders each one
as a 1920×1080 MP4 via Remotion.

Supported tags
──────────────
[STANDINGS TABLE: Premier League 2013/14 - Top 6 Final Standings]
[TOP SCORERS: Premier League 2013/14]
[TOP ASSISTS: Premier League 2013/14]
[PLAYER STATS: Luis Suárez 2013/14]
[MATCH RESULT: Liverpool 5-1 Arsenal, 09 Feb 2014]
[TRANSFER: Luis Suárez from Liverpool to Barcelona, 2014, £75m]
[TROPHY: Premier League 2013/14 Manchester City]
[CAREER TIMELINE: Luis Suárez]
[SEASON COMPARISON: Luis Suárez vs Lionel Messi, La Liga 2015/16]
[TEAM LINEUP: Liverpool 4-3-3 vs Arsenal, 09 Feb 2014]
[DISCIPLINARY RECORD: Luis Suárez]
[QUOTE CARD: "I apologise to Giorgio Chiellini" — Luis Suárez, FIFA World Cup 2014]
[PLAYER RADAR: Florian Wirtz, Liverpool, Premier League, 2025/26]
"""

import json
import os
import re
import requests
from bs4 import BeautifulSoup

from utils.llm_utils import ask_llm
from utils.football_data_api import fetch_lineup_for_tag
from agents.motion_agent import generate_motion_graphic
from utils.remotion_renderer import (
    render_standings,
    render_top_scorers,
    render_player_stats,
    render_match_result,
    render_transfer,
    render_trophy,
    render_career_timeline,
    render_season_comparison,
    render_team_lineup,
    render_disciplinary_record,
    render_quote_card,
    render_hero_statbars,
    render_hero_formrun,
    render_hero_tactical,
    render_hero_bigstat,
    render_hero_leaguegraph,
    render_hero_transfer_record,
    render_hero_intro,
    render_hero_outro,
    render_hero_quote,
    render_hero_chapter,
    render_hero_concept,
    render_hero_scatter,
    render_hero_clip_single,
    render_attacking_radar,
    render_player_trio,
    render_hero_shot_map,
    render_hero_match_timeline,
    render_hero_awards_list,
    render_hero_comparison_radar,
    render_hero_season_timeline,
    render_tournament_bracket,
    REMOTION_DIR,
    TEAM_CONFIG,
    FALLBACK_CONFIG,
)
from utils.bracket_data import lookup_bracket, known_tournaments

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YTEngine/1.0)"}

# ── Available player images ────────────────────────────────────────────────────
# Scan the Remotion public dir for player image files (jpg/png/webp) so LLMs
# can pick exact slugs instead of hallucinating filenames.

def _get_available_player_images() -> list[str]:
    """Return path-relative slugs (no extension) so SmartImg can resolve them.
    Files in public/players/ are returned as 'players/name' so staticFile() finds them.
    Files in public/ root are returned as just 'name'.
    """
    img_exts = {".jpg", ".jpeg", ".png", ".webp"}
    paths = []
    seen = set()
    dirs = [
        (os.path.join(REMOTION_DIR, "public", "players"), "players"),
        (os.path.join(REMOTION_DIR, "public"),            ""),
    ]
    for scan_dir, prefix in dirs:
        try:
            for f in os.listdir(scan_dir):
                name, ext = os.path.splitext(f)
                if ext.lower() in img_exts and not f.startswith(".") and not name.endswith(":Zone"):
                    if name not in seen:
                        seen.add(name)
                        paths.append(f"{prefix}/{name}" if prefix else name)
        except Exception:
            pass
    return sorted(paths)

_PLAYER_IMAGES: list[str] = _get_available_player_images()
_DOC_ENTITY: str = ""  # set per-run by generate_graphics from context.md title

def _image_slug_base(value: str) -> str:
    return re.sub(r"\.(jpe?g|png|webp|gif|svg)$", "", (value or "").split("/")[-1], flags=re.IGNORECASE).lower()

def _name_tokens(value: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split() if len(t) > 2]

def _canonical_player_image_slug(value: str) -> str:
    """Return a known local image slug for a raw LLM/image value when possible."""
    if not value:
        return ""
    raw = value.strip().replace("\\", "/").lower()
    raw_base = _image_slug_base(raw)
    raw_tokens = _name_tokens(raw)
    for slug in _PLAYER_IMAGES:
        slug_lower = slug.lower()
        slug_base = _image_slug_base(slug_lower)
        if raw == slug_lower or raw == slug_base or raw_base == slug_base:
            return slug
    for slug in _PLAYER_IMAGES:
        slug_base = _image_slug_base(slug)
        if raw_tokens and all(tok in slug_base for tok in raw_tokens):
            return slug
    for slug in _PLAYER_IMAGES:
        slug_base = _image_slug_base(slug)
        if any(tok in slug_base for tok in raw_tokens):
            return slug
    return ""

def _resolve_player_image(player_name: str) -> str:
    """Return a slug for player_name via fuzzy match against manually-sourced images only."""
    if not player_name:
        return ""

    canonical = _canonical_player_image_slug(player_name)
    if canonical:
        return canonical

    name_lower = player_name.lower()
    parts = _name_tokens(name_lower)
    for slug in _PLAYER_IMAGES:
        slug_base = _image_slug_base(slug)
        if parts and all(p in slug_base for p in parts):
            return slug
    for slug in _PLAYER_IMAGES:
        slug_base = _image_slug_base(slug)
        if any(p in slug_base for p in parts):
            return slug
    return ""

def _fill_player_image(data: dict, player_name: str, prop: str = "playerImage") -> None:
    """If data[prop] is empty, attempt auto-resolution and fill in-place."""
    if not data.get(prop):
        resolved = _resolve_player_image(player_name)
        if resolved:
            data[prop] = resolved

def _prefer_player_image(data: dict, prop: str, *candidate_names: str) -> None:
    """Normalize an existing image value or backfill from the best available player name.
    Falls back to _DOC_ENTITY so the documentary subject is always tried last."""
    if not isinstance(data, dict):
        return
    current = data.get(prop)
    canonical = _canonical_player_image_slug(str(current)) if current else ""
    if canonical:
        data[prop] = canonical
        return
    all_candidates = list(candidate_names)
    if _DOC_ENTITY and _DOC_ENTITY not in all_candidates:
        all_candidates.append(_DOC_ENTITY)
    for name in all_candidates:
        resolved = _resolve_player_image(name)
        if resolved:
            data[prop] = resolved
            return

def _player_from_context(tag_text: str) -> str:
    """Extract the primary player name from a tag text string using common separators."""
    normalized = tag_text
    return re.split(r"[,|\-]", normalized.strip())[0].strip()

def _highlighted_transfer_player(data: dict) -> str:
    transfers = data.get("transfers") if isinstance(data, dict) else None
    if not isinstance(transfers, list):
        return ""
    for transfer in transfers:
        if isinstance(transfer, dict) and transfer.get("highlight") and transfer.get("player"):
            return str(transfer["player"])
    for transfer in transfers:
        if isinstance(transfer, dict) and transfer.get("player"):
            return str(transfer["player"])
    return ""

# -- Tag regexes ---------------------------------------------------------------

SEASON_RE = re.compile(r"(\d{4})[/\-–](\d{2,4})")

TAGS = {
    "standings":   re.compile(r"\[STANDINGS TABLE:\s*(.+?)\]",    re.IGNORECASE),
    "scorers":     re.compile(r"\[TOP SCORERS:\s*(.+?)\]",         re.IGNORECASE),
    "assists":     re.compile(r"\[TOP ASSISTS:\s*(.+?)\]",         re.IGNORECASE),
    "player":      re.compile(r"\[PLAYER STATS:\s*(.+?)\]",        re.IGNORECASE),
    "match":       re.compile(r"\[MATCH RESULT:\s*(.+?)\]",        re.IGNORECASE),
    "transfer":    re.compile(r"\[TRANSFER:\s*(.+?)\]",            re.IGNORECASE),
    "trophy":      re.compile(r"\[TROPHY:\s*(.+?)\]",              re.IGNORECASE),
    "timeline":    re.compile(r"\[CAREER TIMELINE:\s*(.+?)\]",     re.IGNORECASE),
    "comparison":  re.compile(r"\[SEASON COMPARISON:\s*(.+?)\]",   re.IGNORECASE),
    "lineup":      re.compile(r"\[TEAM LINEUP:\s*(.+?)\]",         re.IGNORECASE),
    "disciplinary":re.compile(r"\[DISCIPLINARY RECORD:\s*(.+?)\]", re.IGNORECASE),
    "quote":       re.compile(r"\[QUOTE CARD:\s*(.+?)\]",          re.IGNORECASE),
    "hero_bars":    re.compile(r"\[HERO STAT BARS:\s*(.+?)\]",    re.IGNORECASE),
    "hero_form":    re.compile(r"\[HERO FORM RUN:\s*(.+?)\]",     re.IGNORECASE),
    "hero_tactical":re.compile(r"\[HERO TACTICAL:\s*(.+?)\]",     re.IGNORECASE),
    "hero_bigstat": re.compile(r"\[HERO BIG STAT:\s*(.+?)\]",     re.IGNORECASE),
    "hero_graph":   re.compile(r"\[HERO LEAGUE GRAPH:\s*(.+?)\]", re.IGNORECASE),
    "hero_transfer":re.compile(r"\[HERO TRANSFER RECORD:\s*(.+?)\]", re.IGNORECASE),
    "hero_intro":   re.compile(r"\[HERO INTRO:\s*(.+?)\]",        re.IGNORECASE),
    "hero_quote":   re.compile(r"\[HERO QUOTE:\s*(.+?)\]",        re.IGNORECASE),
    "hero_chapter": re.compile(r"\[HERO CHAPTER:\s*(.+?)\]",      re.IGNORECASE),
    "hero_concept": re.compile(r"\[HERO CONCEPT:\s*(.+?)\]",      re.IGNORECASE),
    "hero_scatter":      re.compile(r"\[HERO SCATTER:\s*(.+?)\]",          re.IGNORECASE),
    "hero_shot_map":     re.compile(r"\[HERO SHOT MAP:\s*(.+?)\]",         re.IGNORECASE),
    "hero_match_tl":     re.compile(r"\[HERO MATCH TIMELINE:\s*(.+?)\]",   re.IGNORECASE),
    "hero_awards_list":  re.compile(r"\[HERO AWARDS LIST:\s*(.+?)\]",      re.IGNORECASE),
    "hero_comp_radar":   re.compile(r"\[HERO COMPARISON RADAR:\s*(.+?)\]", re.IGNORECASE),
    "player_radar":    re.compile(r"\[PLAYER RADAR:\s*(.+?)\]",         re.IGNORECASE),
    "player_trio":  re.compile(r"\[PLAYER TRIO:\s*(.+?)\]",         re.IGNORECASE),
    "season_timeline": re.compile(r"\[HERO SEASON TIMELINE:\s*(.+?)\]", re.IGNORECASE),
    "tournament_bracket": re.compile(r"\[TOURNAMENT BRACKET:\s*(.+?)\]", re.IGNORECASE),
}


# ── Season string helpers ──────────────────────────────────────────────────────

def _parse_season(tag_text: str) -> tuple[str, str]:
    m = SEASON_RE.search(tag_text)
    if not m:
        raise ValueError(f"Could not parse season from tag: {tag_text!r}")
    start_year = m.group(1)
    end_part   = m.group(2)
    end_year   = end_part if len(end_part) == 4 else start_year[:2] + end_part
    display    = f"{start_year}–{end_year[2:]}"
    wiki_title = f"{start_year}–{end_year}_Premier_League"
    return wiki_title, display


def _safe_season(display: str) -> str:
    return display.replace("–", "-").replace("/", "-")


# ── Wikipedia scraping ────────────────────────────────────────────────────────

def _scrape_wikipedia_standings(wiki_title: str) -> list[dict] | None:
    url = f"https://en.wikipedia.org/wiki/{requests.utils.quote(wiki_title)}"
    print(f"    [Graphics] Fetching Wikipedia: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for table in soup.find_all("table", class_="wikitable"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if "Pts" not in headers or "GD" not in headers:
                continue
            teams = []
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 9:
                    continue
                pos_text = cells[0].get_text(strip=True).rstrip("*")
                try:
                    pos = int(pos_text)
                except ValueError:
                    continue
                name = re.sub(r"[\*†‡§].*$", "", cells[1].get_text(strip=True)).strip()
                def _int(idx):
                    try:
                        return int(cells[idx].get_text(strip=True).lstrip("−").replace("−", "-"))
                    except Exception:
                        return 0
                gf = _int(6)
                ga = _int(7)
                teams.append({"pos": pos, "name": name, "p": _int(2), "w": _int(3),
                               "d": _int(4), "l": _int(5), "gd": gf - ga, "pts": _int(8)})
            if teams:
                teams.sort(key=lambda t: t["pos"])
                return teams[:6]
    except Exception as e:
        print(f"    [Graphics] Scrape error: {e}")
    return None


def _scrape_wikipedia_scorers(wiki_title: str, stat: str = "goals") -> list[dict] | None:
    """Try to scrape a top-scorers or top-assists table from the PL season Wikipedia page."""
    url = f"https://en.wikipedia.org/wiki/{requests.utils.quote(wiki_title)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        # Look for a wikitable containing "Goals" or "Assists" column
        key = "Goals" if stat == "goals" else "Assists"
        for table in soup.find_all("table", class_="wikitable"):
            ths = [th.get_text(strip=True) for th in table.find_all("th")]
            if key not in ths and "Player" not in ths:
                continue
            players = []
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 3:
                    continue
                # Try to find Name / Club / Goals columns heuristically
                texts = [c.get_text(strip=True) for c in cells]
                try:
                    # Rows often: Pos | Player | Club | Goals | Apps
                    pos = int(texts[0].rstrip("."))
                    name = texts[1]
                    club = texts[2]
                    goals = int(texts[3]) if texts[3].isdigit() else 0
                    apps  = int(texts[4]) if len(texts) > 4 and texts[4].isdigit() else 0
                    players.append({"pos": pos, "name": name, "club": club,
                                    "goals": goals if stat == "goals" else 0,
                                    "assists": goals if stat == "assists" else 0,
                                    "apps": apps})
                except (ValueError, IndexError):
                    continue
            if players:
                return players[:6]
    except Exception as e:
        print(f"    [Graphics] Scorers scrape error: {e}")
    return None


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _llm_standings(season: str) -> list[dict] | None:
    print(f"    [Graphics] LLM fallback standings for {season}")
    prompt = f"""Provide the final top-6 standings for the {season} Premier League season.
Only include facts you are highly confident about. Return JSON:
{{"teams":[{{"pos":1,"name":"Team","p":38,"w":0,"d":0,"l":0,"gd":0,"pts":0}}]}}"""
    try:
        return json.loads(ask_llm(prompt, expect_json=True)).get("teams", [])[:6]
    except Exception as e:
        print(f"    [Graphics] LLM standings failed: {e}")
        return None


def _llm_scorers(season: str, competition: str, stat: str) -> list[dict] | None:
    label = "goal scorers" if stat == "goals" else "assist providers"
    print(f"    [Graphics] LLM {label} for {season}")
    prompt = f"""Provide the top 5 {label} for the {season} {competition} season.
Return JSON: {{"players":[{{"pos":1,"name":"Player Name","club":"Club","goals":0,"assists":0,"apps":0}}]}}"""
    try:
        return json.loads(ask_llm(prompt, expect_json=True)).get("players", [])[:6]
    except Exception as e:
        print(f"    [Graphics] LLM scorers failed: {e}")
        return None


def _llm_player_stats(player_name: str, season: str) -> dict | None:
    print(f"    [Graphics] LLM player stats for {player_name} {season}")
    prompt = f"""Provide 6 key Premier League stats for {player_name} in the {season} season.
Return JSON:
{{"playerName":"{player_name}","club":"Club Name","badgeSlug":"club-slug.svg","clubColor":"#RRGGBB",
"stats":[{{"label":"Goals","value":0,"sub":"in 38 games"}}]}}"""
    try:
        return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e:
        print(f"    [Graphics] LLM player stats failed: {e}")
        return None


def _llm_match(tag_text: str) -> dict | None:
    print(f"    [Graphics] LLM match result for: {tag_text!r}")
    prompt = f"""Parse this match result tag and return all data as JSON.
Tag: "{tag_text}"
Return JSON: {{"homeTeam":"","awayTeam":"","homeBadgeSlug":"premier-league.svg",
"awayBadgeSlug":"premier-league.svg","homeColor":"#111111","awayColor":"#111111",
"homeScore":0,"awayScore":0,"date":"","competition":"","venue":"","scorers":[]}}

RULES:
- competition: the actual competition this match was played in (e.g. "La Liga", "Champions League", "Premier League", "World Cup"). Do NOT default to Premier League.
- venue: the home team's actual stadium (e.g. "Camp Nou" for Barcelona, "Wanda Metropolitano" for Atletico Madrid, "Anfield" for Liverpool, "Santiago Bernabéu" for Real Madrid). Must match the home team, not the narrator's team.
- scorers: list of {{"name":"","minute":"","team":"home or away"}} for known scorers only. Leave empty if unknown."""
    try:
        return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e:
        print(f"    [Graphics] LLM match failed: {e}")
        return None


def _llm_transfer(tag_text: str) -> dict | None:
    print(f"    [Graphics] LLM transfer for: {tag_text!r}")
    prompt = f"""Parse this transfer tag and return all data as JSON.
Tag: "{tag_text}"
Return JSON: {{"playerName":"","fromClub":"","toClub":"","fromBadgeSlug":"premier-league.svg",
"toBadgeSlug":"premier-league.svg","fromColor":"#111111","toColor":"#111111",
"fee":"","year":"","nationality":""}}"""
    try:
        return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e:
        print(f"    [Graphics] LLM transfer failed: {e}")
        return None


def _llm_trophy(tag_text: str) -> dict | None:
    print(f"    [Graphics] LLM trophy for: {tag_text!r}")
    prompt = f"""Parse this trophy tag and return data as JSON.
Tag: "{tag_text}"
Return JSON: {{"trophyName":"","trophyYear":"","clubName":"","badgeSlug":"premier-league.svg",
"clubColor":"#111111","subtext":"Champions","trophyCount":1}}"""
    try:
        return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e:
        print(f"    [Graphics] LLM trophy failed: {e}")
        return None


def _llm_timeline(tag_text: str) -> dict | None:
    print(f"    [Graphics] LLM career timeline for: {tag_text}")
    player_name = tag_text.split("-")[0].strip()
    # Extract Focus club if present
    focus_club = ""
    if "focus:" in tag_text.lower():
        focus_club = tag_text.lower().split("focus:")[-1].strip().title()

    # Build badge hints from TEAM_CONFIG
    badge_hints = "\n".join(f'  "{c}": "{v["badgeSlug"]}"' for c, v in list(TEAM_CONFIG.items())[:20])

    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""

    prompt = f"""Generate a complete career timeline for {player_name} listing every major club in chronological order.
Tag context: "{tag_text}"
Focus club to highlight: "{focus_club}" (set activeIndex to its 0-based index in events, or -1 if no focus)

Return JSON:
{{
  "playerName":"{player_name}",
  "subjectImage":"",
  "dateline":"",
  "source":"",
  "accentColor":"",
  "events":[
    {{"year":"YYYY–YY","club":"Club Name","badgeSlug":"badge-file.svg","clubColor":"#XXXXXX","detail":"X goals in Y apps","isHighlight":false}}
  ],
  "activeIndex":-1
}}

RULES:
- Include every professional club in strict chronological order (loan spells included)
- 5–8 events total
- Set activeIndex to the 0-based index of the focus club, -1 if no focus
- Use the correct badgeSlug from these known clubs (use premier-league.svg as fallback):
{badge_hints}
- clubColor must be the club's authentic primary color (hex)
- subjectImage: exact slug of the subject from the available list, or leave empty.
- dateline: small-caps editorial line, e.g. "CAREER · 2005–2022" — derive year range from events.
- source: short attribution like "stats · transfermarkt" or "fbref"; leave empty if unknown.
- accentColor: hex of the focus club's primary colour (or the subject's most-associated club).
{images_hint}"""
    try:
        data = json.loads(ask_llm(prompt, expect_json=True))
        _prefer_player_image(data, "subjectImage", player_name, _player_from_context(tag_text))
        return data
    except Exception as e:
        print(f"    [Graphics] LLM timeline failed: {e}")
        return None


def _llm_comparison(player_a: str, player_b: str, season: str, competition: str) -> dict | None:
    print(f"    [Graphics] LLM season comparison: {player_a} vs {player_b}, {competition} {season}")
    prompt = f"""Compare {player_a} and {player_b} during the {season} {competition} season.
Return JSON exactly matching this structure:
{{
  "playerA": {{"name": "{player_a}", "club": "<club name>", "color": "<hex primary club colour>"}},
  "playerB": {{"name": "{player_b}", "club": "<club name>", "color": "<hex primary club colour>"}},
  "season": "{season}",
  "competition": "{competition}",
  "stats": [
    {{"label": "Goals",      "valueA": 0, "valueB": 0}},
    {{"label": "Assists",    "valueA": 0, "valueB": 0}},
    {{"label": "Key Passes", "valueA": 0, "valueB": 0}},
    {{"label": "Shots",      "valueA": 0, "valueB": 0}},
    {{"label": "Dribbles",   "valueA": 0, "valueB": 0}}
  ]
}}
Use accurate historical stats. Include 5 stats. valueA is {player_a}, valueB is {player_b}."""
    try:
        data = json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e:
        print(f"    [Graphics] LLM comparison failed: {e}")
        return None

    for slot, name in [("playerA", player_a), ("playerB", player_b)]:
        resolved = _resolve_player_image(name)
        if resolved:
            data.setdefault(slot, {})["image"] = resolved

    return data


def _llm_disciplinary(player_name: str) -> dict | None:
    print(f"    [Graphics] LLM disciplinary record for: {player_name}")
    prompt = f"""Generate the disciplinary record for {player_name} — confirmed, publicly documented incidents ONLY.
Return JSON: {{"playerName":"{player_name}","badgeSlug":"premier-league.svg",
"incidents":[{{"date":"Month YYYY","incident":"Description","ban":"X matches",
"club":"Club Name","badgeSlug":"premier-league.svg","clubColor":"#111111",
"severity":"serious"}}]}}
severity must be one of: "minor" (caution/yellow card), "warning" (standard ban), "serious" (biting/racism/extreme).
RULES:
- Only include incidents that resulted in a formal suspension or caused major documented controversy (violent conduct, racism bans, biting, match-fixing etc.)
- Do NOT include vague yellows, elbowing, or any incident you are not highly confident about
- Dates and ban lengths must be accurate — if uncertain, omit the incident
- 3–6 incidents maximum — only the most significant, most documented ones"""
    try:
        return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e:
        print(f"    [Graphics] LLM disciplinary failed: {e}")
        return None


def _llm_quote(tag_text: str) -> dict | None:
    print(f"    [Graphics] LLM quote card for: {tag_text!r}")
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this quote card tag and return structured data.
Tag: "{tag_text}"
Format is typically: "Quote text" - Attribution, Context
Return JSON: {{"quote":"","attribution":"","context":"","playerImage":"","accentColor":"#C8102E"}}
Set playerImage to the speaker's exact slug from the available list when possible.
{images_hint}
accentColor should match the speaker's club colour if known."""
    try:
        data = json.loads(ask_llm(prompt, expect_json=True))
        _prefer_player_image(data, "playerImage", data.get("attribution", ""), data.get("context", ""), _player_from_context(tag_text))
        return data
    except Exception as e:
        print(f"    [Graphics] LLM quote failed: {e}")
        return None

def _llm_lineup(tag_text: str) -> dict | None:
    print(f"    [Graphics] LLM lineup for: {tag_text!r}")
    prompt = f"""Generate a starting XI lineup for this match.
Tag: "{tag_text}"
Return JSON: {{"teamName":"","formation":"4-3-3","badgeSlug":"premier-league.svg",
"teamColor":"#111111","opposition":"","date":"",
"players":[{{"name":"Goalkeeper","number":1,"x":50,"y":8,"isCaptain":false}}]}}
x/y are percentages (0–100) of pitch width/height. Keeper at y≈8, defenders y≈22–26,
midfielders y≈42–56, forwards y≈66–76. Spread horizontally by position."""
    try:
        return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e:
        print(f"    [Graphics] LLM lineup failed: {e}")
        return None


# ── hero LLM helpers ──────────────────────────────────────────────────────

def _llm_hero_bars(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this head-to-head stat bars tag and return data as JSON.
Tag: "{tag_text}"
Return JSON: {{"title":"","subtitle":"","sideImage":"","teamA":{{"name":"","color":""}},"teamB":{{"name":"","color":""}},"stats":[{{"label":"","valueA":0,"valueB":0,"maxValue":100,"suffix":""}}]}}
Set 'sideImage' to the exact slug of the most relevant player from the available list. Leave empty string if no match.
{images_hint}"""
    try: return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e: print(f"    [Graphics] LLM hero bars failed: {e}"); return None

def _llm_hero_form(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this form run tag and return data as JSON.
Tag: "{tag_text}"
Return JSON: {{"teamName":"","teamColor":"","label":"last 10 matches","sideImage":"","results":[{{"result":"W","opponent":"","score":""}}]}}
Set 'sideImage' to the exact slug of the most relevant player/manager from the available list, or leave empty.
{images_hint}
The 'results' array must list the actual match results for the team and period described. W=Win, D=Draw, L=Loss."""
    try: return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e: print(f"    [Graphics] LLM hero form failed: {e}"); return None

def _llm_hero_tactical(tag_text: str) -> dict | None:
    """
    Tag format (preferred): "Concept | Team | Formation | Description"
    Legacy format:           "Title, Description"
    """
    # Parse pipe-separated format, fall back to legacy comma format
    if "|" in tag_text:
        parts       = [p.strip() for p in tag_text.split("|")]
        concept     = parts[0]
        team        = parts[1] if len(parts) > 1 else ""
        formation   = parts[2] if len(parts) > 2 else "4-3-3"
        description = parts[3] if len(parts) > 3 else concept
    else:
        parts       = [p.strip() for p in tag_text.split(",", 1)]
        concept     = parts[0]
        team        = ""
        formation   = "4-3-3"
        description = parts[1] if len(parts) > 1 else concept

    team_color = TEAM_CONFIG.get(team, FALLBACK_CONFIG)["color"] if team else "#C8102E"

    print(f"    [Graphics] LLM tactical: concept={concept!r}, team={team!r}, formation={formation!r}")

    prompt = f"""You are a football tactics expert. Generate a TWO-TEAM tactical pitch diagram for an animation showing how a tactical concept plays out against an opposition shape.

TACTIC:       {concept}
TEAM:         {team or "unknown"}
FORMATION:    {formation}
DESCRIPTION:  {description}

=== COORDINATE SYSTEM ===
The pitch is a vertical rectangle viewed from above:
  x: 0 (left touchline) → 100 (right touchline)
  y: 0 (top of screen) → 100 (bottom of screen)
OUR TEAM defends at the BOTTOM (GK at y≈88–92) and attacks UPWARD toward y=0.
OPPOSITION defends at the TOP (their GK at y≈8–12) and attacks DOWNWARD toward y=100.

=== OUR TEAM POSITIONS (`players`) ===
Place ALL 11 of our players for a {formation} formation. y-range guidelines:
  GK:         y ≈ 88–92
  Defenders:  y ≈ 68–78
  Midfielders:y ≈ 44–62
  Forwards:   y ≈ 22–40
Spread x positions horizontally to reflect formation width.
Use short positional labels: GK, RB, CB, LB, DM, CM, CAM, RM, LM, RW, ST, LW.

=== OPPOSITION POSITIONS (`oppositionPlayers`) ===
Place ALL 11 opposition players representing what they're doing in the moment
the tactic fires (typically: holding the ball / building from the back, OR
attacking us / breaking forward). Default to a 4-2-3-1 holding shape unless
the tactic implies otherwise:
  Their GK:         y ≈ 8–12
  Their defenders:  y ≈ 18–28
  Their midfielders:y ≈ 35–48
  Their forwards:   y ≈ 55–72
Use the same short labels (GK, RB, CB, LB, DM, CM, AM, RW, LW, ST).
The opposition is the FOIL — their positions establish what our team is
pressing/marking/breaking against.

=== PRESS POSITIONS (`pressX` / `pressY`) — THE FORMATION SHIFT ===
For each OUR-TEAM player that MOVES when the tactic fires, add OPTIONAL
`pressX`/`pressY` fields = where that player ENDS UP after the shift.
The template animates each dot from (x,y) → (pressX,pressY) over ~38 frames.
Do NOT include pressX/pressY for players who don't move (e.g. the GK,
sometimes the back line in a high-press scenario).

The `pressX/pressY` fields are how every tactical style is choreographed —
this is the most important part of the output. Examples:

GEGENPRESSING / HIGH PRESS:
  - Front three pressX/pressY: squeeze diagonally inward + forward (toward
    opposition back line at y≈22). e.g. RW (x=78,y=36) → (76, 28)
  - Midfield three pressX/pressY: step up 6–10 units to compact the block
  - Back four: hold (no pressX/pressY) OR step up 3–5 units as a high line
  - Opposition: stationary (don't add pressX/pressY to their dots)

LOW BLOCK / DEFENSIVE COMPACTION:
  - Forwards drop DEEPER (pressY higher) and narrower
  - Midfield + defence pull TOGETHER into two compact banks
  - Lateral squeeze toward x=50 for narrowness
  - Opposition: stationary (just builds in front of the block)

POSITIONAL PLAY / TIKI-TAKA (BARCELONA-STYLE BUILD-UP):
  - Subtle pressX/pressY shifts into half-spaces (2–4 unit moves)
  - Fullbacks invert into midfield (e.g. RB x=80 → x=68)
  - Wingers stretch wide and high (e.g. RW x=78 → x=85, y=36 → y=24)
  - Pivot drops between the CBs (DM y=58 → y=66)

COUNTER-ATTACK / TRANSITION:
  - 2–4 forwards/midfielders sprint to attacking positions (large
    pressY shifts, e.g. y=55 → y=20)
  - Rest of team holds shape

MAN-MARKING:
  - Each marker's pressX/pressY = an opposition player's (x,y) coordinates
    (literally tracking the man — combine with `targetIndex` arrows below)

=== ARROWS — TARGETING OPPOSITION NODES ===
Generate 3–6 arrows illustrating the key tactical movement.
Style:
  "solid"  — aggressive pressing/attacking runs (bright, prominent, with a glowing arrowhead)
  "dashed" — cover runs / second-press triggers / passing-lane indicators

EACH ARROW should ideally have a `targetIndex` = the index (0-based) into
`oppositionPlayers` of the player it's targeting. When set, the arrow's tip
auto-resolves to that opposition node AND the target node receives a pulse
highlight when the arrowhead arrives — reads as "pressed at source."

For arrows WITHOUT a specific target (passing lanes, free runs into space),
omit `targetIndex` and use raw `toX`/`toY` coordinates pointing into space.

`fromX`/`fromY` MUST exactly match an entry in `players` for the player-drift
animation to fire (the originating dot tugs toward the arrow direction
during the draw).

=== TACTIC-SPECIFIC ARROW GUIDANCE ===
GEGENPRESSING: 3–4 solid arrows from front three + nearest midfielder, each with a `targetIndex` pointing at the opposition player they're pressing. Optional 1 dashed arrow showing cover shadow.
LOW BLOCK: 1–2 solid arrows on press triggers (e.g. striker → opposition CB), 2–3 dashed arrows showing compactness/cover lines (no targetIndex, raw coords).
POSSESSION/BUILD-UP: 3–5 dashed arrows showing passing lanes (no targetIndex, raw to-coords on the receiver's position). Optional 1 solid arrow for a key forward run.
MAN-MARKING: One solid arrow per marker → his man (every arrow has a `targetIndex`).
COUNTER-ATTACK: 2–3 solid arrows in the direction of attack, mostly raw coords (the runs are into space, not toward opposition).

=== COLORS ===
- `teamColor`: provided as "{team_color}" — use exactly this.
- `oppositionColor`: pick a contrasting hex that doesn't clash. Default options:
  - If our team is red: opposition "#1F4E8C" (deep blue)
  - If our team is blue: opposition "#A1262C" (deep red)
  - If our team is yellow/gold: opposition "#3A1F5C" (deep purple)
  - If our team is green: opposition "#2C2C2C" (charcoal)
  - If our team is black/dark: opposition "#D9A441" (mustard)
  - Otherwise: "#1F4E8C" (default deep blue) is always safe.

=== OUTPUT ===
Return ONLY valid JSON, no explanation:
{{
  "title": "{concept}",
  "description": "{description}",
  "teamColor": "{team_color}",
  "oppositionColor": "#1F4E8C",
  "bgColor": "#141414",
  "players": [
    {{"label": "GK", "x": 50, "y": 90}},
    {{"label": "RW", "x": 78, "y": 36, "pressX": 76, "pressY": 28}},
    ... (all 11 players, with pressX/pressY ONLY on those that move)
  ],
  "oppositionPlayers": [
    {{"label": "GK", "x": 50, "y": 10}},
    {{"label": "RB", "x": 78, "y": 22}},
    ... (all 11 opposition players)
  ],
  "arrows": [
    {{"fromX": 78, "fromY": 36, "toX": 0, "toY": 0, "targetIndex": 1, "style": "solid"}},
    ... (3–6 arrows; use `targetIndex` to point at opposition nodes; if no target, use raw `toX`/`toY` and omit targetIndex)
  ]
}}

CRITICAL:
- Every `players[].x/y` MUST exactly match the `fromX/fromY` of any arrow originating from that player (otherwise drift won't fire).
- Every `targetIndex` MUST be a valid index into `oppositionPlayers` (0–10).
- Do NOT add `dateline` or `source` — those fields are deprecated and not rendered."""

    try:
        return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e:
        print(f"    [Graphics] LLM hero tactical failed: {e}")
        return None

def _llm_hero_bigstat(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this big stat tag and return data as JSON.
Tag: "{tag_text}"
Return JSON: {{"stat":"","unit":"","label":"","stat2":"","unit2":"","label2":"","context":"","badgeSlug":"","source":"","playerImage":"","accentColor":"","darkMode":false,"bgColor":"#f0ece4"}}
- stat/unit/label: the primary stat (e.g. "31", "goals", "in a single Premier League season.")
- stat2/unit2/label2: a second related stat if one can be inferred. Leave empty strings if none.
- context: "Subject · Club · Season" separated by " · " (e.g. "Luis Suárez · Liverpool · 2013/14")
- badgeSlug: club SVG slug (e.g. "liverpool.svg", "barcelona.svg"); empty if unknown
- source: short attribution like "stats · fbref" or "opta"; leave empty if unknown
- accentColor: the club's primary hex colour
Set 'playerImage' to the slug of the subject player from the available list, or leave empty if not available.
{images_hint}"""
    try:
        data = json.loads(ask_llm(prompt, expect_json=True))
        _prefer_player_image(data, "playerImage", data.get("context", ""), _player_from_context(tag_text))
        # Auto-fill badge from club name in context if the LLM omitted it
        if not data.get("badgeSlug"):
            parts = [p.strip() for p in (data.get("context") or "").split("·")]
            club = parts[1] if len(parts) >= 2 else ""
            if club:
                slug = _ensure_badge(club) if "_ensure_badge" in globals() else ""
                if slug:
                    data["badgeSlug"] = slug
        return data
    except Exception as e: print(f"    [Graphics] LLM hero bigstat failed: {e}"); return None

def _llm_hero_graph(tag_text: str) -> dict | None:
    prompt = f"""Parse this league graph tag and return realistic title race data as JSON.
Tag: "{tag_text}"
Return JSON:
{{
  "season": "2013-14",
  "title": "Title Race",
  "competition": "Premier League",
  "source": "stats · fbref",
  "accentColor": "#C8102E",
  "maxPosition": 6,
  "bgColor": "",
  "teamA": {{
    "name": "Liverpool",
    "color": "#C8102E",
    "badgeSlug": "liverpool",
    "data": [{{"matchday": 1, "position": 4}}, {{"matchday": 5, "position": 3}}, {{"matchday": 38, "position": 2}}]
  }},
  "teamB": {{
    "name": "Manchester City",
    "color": "#6CABDD",
    "badgeSlug": "manchester_city",
    "data": [{{"matchday": 1, "position": 2}}, {{"matchday": 5, "position": 1}}, {{"matchday": 38, "position": 1}}]
  }}
}}
Rules:
- teamA is the documentary subject's club; teamB is their title rival
- data must have ~12-15 matchday points spread across the season (not just start/end)
- positions should reflect the actual title race narrative (lead changes, close finishes)
- maxPosition: show top 4-6 positions only (e.g. 6)
- badgeSlug: lowercase club name with underscores (e.g. "manchester_city", "liverpool")
- competition: real competition name for the small-caps dateline ("Premier League", "La Liga", etc.)
- accentColor: hex of teamA's authentic primary colour (the subject team)
- bgColor: leave empty "" — paper background is the editorial default
- source: short attribution like "stats · fbref"; leave empty if unknown"""
    try: return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e: print(f"    [Graphics] LLM hero graph failed: {e}"); return None

def _llm_hero_transfer(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this transfer record tag and return data as JSON.
Tag: "{tag_text}"
Return JSON: {{"title":"","subtitle":"","sideImage":"","accentColor":"","transfers":[{{"year":"","player":"","fromClub":"","toClub":"","fee":"","feeValue":0,"highlight":false}}]}}
Set 'sideImage' to the exact slug of the highlighted player from the available list, or leave empty.
{images_hint}"""
    try:
        data = json.loads(ask_llm(prompt, expect_json=True))
        _prefer_player_image(data, "sideImage", _highlighted_transfer_player(data), _player_from_context(tag_text))
        return data
    except Exception as e: print(f"    [Graphics] LLM hero transfer failed: {e}"); return None

def _llm_hero_intro(tag_text: str, output_dir: str = "") -> dict | None:
    import re as _re
    # Prefer the documentary title from context.md over the tag text (which is often just the entity)
    subtitle = ""
    if output_dir:
        ctx_path = os.path.join(output_dir, "context.md")
        if os.path.exists(ctx_path):
            for line in open(ctx_path).readlines():
                if line.startswith("## Title"):
                    subtitle = line.replace("## Title", "").strip()
                    break
    if not subtitle:
        subtitle = _re.sub(r'^(?:documentary|title|video|doc)\s*:\s*', '', tag_text.strip(), flags=_re.IGNORECASE).strip()
    data = {"subtitle": subtitle, "bgColor": "#f0ece4", "sideImage": ""}
    _prefer_player_image(data, "sideImage", tag_text, subtitle)
    return data

def _llm_hero_outro(tag_text: str) -> dict | None:
    """Parse HERO OUTRO tag content. Format generated by storyboard post-processor:
       "<lead-in> ::: <subscribe ask> ::: <left video title> ::: <right video title>"
    All four fields are optional — defaults supplied for missing slots so the
    template still renders cleanly even if the LLM produced a sparse tag."""
    parts = [p.strip() for p in tag_text.split(":::")]
    while len(parts) < 4:
        parts.append("")
    lead_in, sub_ask, left_title, right_title = parts[:4]
    return {
        "leadIn":          lead_in or "If this story stayed with you, there's more where it came from.",
        "subscribeAsk":    sub_ask or "Subscribe for a new story every week.",
        "videoLeftTitle":  left_title or "Watch next",
        "videoRightTitle": right_title or "Or this one",
        "videoLeftSrc":    "",
        "videoRightSrc":   "",
        "videoLeftImage":  "",
        "videoRightImage": "",
        "bgColor":         "#f0ece4",
        "accentColor":     "#0a0a0a",
    }

def _llm_hero_quote(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this quote tag and return data as JSON.
Tag: "{tag_text}"
Return JSON: {{"quote":"","attribution":"","context":"","playerImage":"","accentColor":"","bgColor":"#f0ece4"}}
Set 'playerImage' to the exact slug of the speaker from the available list, or leave empty.
{images_hint}"""
    try:
        data = json.loads(ask_llm(prompt, expect_json=True))
        _prefer_player_image(data, "playerImage", data.get("attribution", ""), data.get("context", ""), _player_from_context(tag_text))
        return data
    except Exception as e: print(f"    [Graphics] LLM hero quote failed: {e}"); return None

def _llm_hero_chapter(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this chapter word tag and return data as JSON.
Tag: "{tag_text}"
Return JSON: {{"word":"","chapterLabel":"CHAPTER","player1Image":"","player2Image":"","blob1Color":"","blob2Color":"","bgColor":"#f0ece4"}}
Set player images to slugs from the available list. blob colors should match the emotional tone of the word.
chapterLabel: short small-caps marker shown top-left (e.g. "CHAPTER", "ACT III", "INTERLUDE"). Default to "CHAPTER" if unclear.
{images_hint}"""
    try: return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e: print(f"    [Graphics] LLM hero chapter failed: {e}"); return None

def _llm_hero_concept(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this concept comparison tag and return data as JSON.
Tag: "{tag_text}"
Return JSON: {{"wordLeft":"","wordRight":"","centerImage":"","centerCaption":"","leftClipImage":"","rightClipImage":"","bgColor":"#f0ece4"}}
Set centerImage to the relevant player slug from the available list, or leave empty.
{images_hint}"""
    try:
        data = json.loads(ask_llm(prompt, expect_json=True))
        _prefer_player_image(data, "centerImage", _player_from_context(tag_text), data.get("centerCaption", ""))
        return data
    except Exception as e: print(f"    [Graphics] LLM hero concept failed: {e}"); return None

def _llm_hero_scatter(tag_text: str) -> dict | None:
    prompt = f"""Parse this scatter plot tag and return data as JSON.
Tag: "{tag_text}"
Return JSON: {{"axisXLabel":"","axisYLabel":"","q1Label":"","q2Label":"","q3Label":"","q4Label":"","showWizardArrow":true,"bgColor":"#111111","players":[{{"name":"","image":"","ringColor":"","x":0,"y":0}}]}}"""
    try: return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e: print(f"    [Graphics] LLM hero scatter failed: {e}"); return None

def _llm_hero_shot_map(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this shot map tag and return JSON for a football xG shot map.
Tag: "{tag_text}"
Format: "Player Name, Competition Season" (e.g. "Luis Su?rez, Premier League 2013/14")

Return JSON:
{{"playerName":"","competition":"","playerImage":"","accentColor":"#C8102E","bgColor":"#f0ece4","stagger":6,
"shots":[{{"x":48,"y":12,"xg":0.72,"goal":true,"minute":14}},{{"x":30,"y":20,"xg":0.18,"goal":false,"minute":32}},{{"x":55,"y":8,"xg":0.82,"goal":true,"minute":45}},{{"x":70,"y":25,"xg":0.06,"goal":false,"minute":58}},{{"x":42,"y":15,"xg":0.45,"goal":true,"minute":67}},{{"x":50,"y":28,"xg":0.09,"goal":false,"minute":74}},{{"x":38,"y":10,"xg":0.61,"saved":true,"goal":false,"minute":81}},{{"x":52,"y":6,"xg":0.88,"goal":true,"minute":90}}]}}
Set playerImage to the relevant player slug from the available list when possible.
{images_hint}
Generate plausible shot locations for the player/season. x=0-100 (centre=50), y=0-100 (0=goal line). xg=0.05-0.9 weighted by position."""
    try:
        data = json.loads(ask_llm(prompt, expect_json=True))
        _prefer_player_image(data, "playerImage", data.get("playerName", ""), _player_from_context(tag_text))
        return data
    except Exception as e: print(f"    [Graphics] LLM hero shot map failed: {e}"); return None

def _llm_hero_match_timeline(tag_text: str) -> dict | None:
    prompt = f"""Parse this match timeline tag and return JSON for an animated match event timeline.
Tag: "{tag_text}"
Format: "Home Team N-N vs Away Team N-N, DD Mon YYYY" or "Home vs Away Score, Date"

Return JSON:
{{"homeTeam":"","awayTeam":"","homeScore":0,"awayScore":0,"competition":"","date":"","accentColor":"#C8102E","bgColor":"#f0ece4","stagger":16,
"events":[{{"minute":18,"type":"goal","player":"","team":"home"}},{{"minute":56,"type":"goal","player":"","team":"away","detail":"Pen."}}]}}
Event types: goal, assist, yellowCard, redCard, sub, var, penalty, ownGoal. team: "home" or "away".
Generate the key events for the match. If score is unknown, use plausible events."""
    try: return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e: print(f"    [Graphics] LLM hero match timeline failed: {e}"); return None


def _parse_season_timeline_tag(tag_text: str) -> dict | None:
    """
    Parse: Subject Name | img:file.png | 22/23:8th, 23/24:2nd, 24/25:1st(PL,FA) | headline:word?
    Returns HeroSeasonTimeline props dict.
    """
    import re as _re
    parts = [p.strip() for p in tag_text.split("|")]
    subject_name  = parts[0] if len(parts) > 0 else "Subject"
    subject_image = ""
    seasons_raw   = ""
    headline      = None

    for part in parts[1:]:
        pl = part.lower()
        if pl.startswith("img:"):
            subject_image = part[4:].strip()
        elif pl.startswith("headline:"):
            headline = part[9:].strip()
        else:
            # must be the seasons string
            seasons_raw = part

    # Parse seasons: "22/23:8th, 23/24:2nd, 24/25:1st(PL,FA)"
    seasons = []
    POSITION_NUM = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6,
                    "7th": 7, "8th": 8, "9th": 9, "10th": 10, "1st?": 1, "2nd?": 2}
    for entry in _re.split(r",\s*", seasons_raw):
        m = _re.match(r"(\d{2}/\d{2}):([^\(]+)(?:\(([^)]+)\))?", entry.strip())
        if not m:
            continue
        season_str = m.group(1)
        pos_label  = m.group(2).strip()
        trophy_str = m.group(3) or ""
        trophies   = [t.strip() for t in trophy_str.split(",") if t.strip()] if trophy_str else []
        pos_num    = POSITION_NUM.get(pos_label.lower(), 20)
        seasons.append({
            "season": season_str,
            "positionLabel": pos_label,
            "position": pos_num,
            "trophies": trophies,
        })

    if not seasons:
        print(f"    [Graphics] SEASON TIMELINE: no seasons parsed from {tag_text!r}")
        return None

    if not subject_image:
        # Use _prefer_player_image so we get loose-form normalization
        # ("messi.jpg" → "messi") AND _DOC_ENTITY fallback when the
        # subject name doesn't match an available slug directly.
        _stub: dict = {"subjectImage": ""}
        _prefer_player_image(_stub, "subjectImage", subject_name, _player_from_context(tag_text))
        subject_image = _stub.get("subjectImage", "") or _resolve_player_image(subject_name)

    return {
        "subjectName": subject_name,
        "subjectImage": subject_image,
        "seasons": seasons,
        "headline": headline,
        "bgColor": "#5E1212",
        "lineColor": "#C41E3A",
        "accentColor": "#E8E0D0",
    }


def _llm_hero_awards_list(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this awards list tag and return JSON for a year-by-year award history.
Tag: "{tag_text}"
Format: "Award Name, Entity Name" (e.g. "Ballon d'Or, Lionel Messi")

Return JSON:
{{
  "award":"",
  "entityName":"",
  "subjectImage":"",
  "dateline":"",
  "source":"",
  "accentColor":"#C9A84C",
  "clubColor":"",
  "bgColor":"#f0ece4",
  "stagger":18,
  "holdDuration":40,
  "years":[
    {{"year":"2009","winner":"","entity":"","position":1,"detail":""}},
    {{"year":"2010","winner":"","entity":"","position":2,"detail":""}}
  ]
}}
- Include ALL years the award was given where the entity is known (typically last 10-15 years)
- winner: the actual winner that year
- entity: the documented player (same as entityName)
- position: 1=won, 2=runner-up, 3=third (omit if unknown)
- detail: club name or vote count
- accentColor: gold #C9A84C for Ballon d'Or/Golden Boot, or primary club color for club awards
- clubColor: optional — primary club hex when the documentary is club-focused (Liverpool red etc.)
- subjectImage: exact slug of the entity from the available list
- dateline: small-caps line under the folio, e.g. "1956 — present  ·  N wins" — leave empty for engine default
- source: short attribution like "France Football · archive" or "uefa.com"; leave empty if unknown
{images_hint}"""
    try:
        data = json.loads(ask_llm(prompt, expect_json=True))
        _prefer_player_image(data, "subjectImage", data.get("entityName", ""), _player_from_context(tag_text))
        return data
    except Exception as e:
        print(f"    [Graphics] LLM hero awards list failed: {e}")
        return None


def _llm_hero_comparison_radar(tag_text: str) -> dict | None:
    prompt = f"""Parse this comparison radar tag and return JSON for a dual-player radar chart.
Tag: "{tag_text}"
Format: "Player A vs Player B, Competition, Season"

Return JSON:
{{"playerA":"","playerB":"","seasonA":"","seasonB":"","competition":"","accentColorA":"#C8102E","accentColorB":"#003087","bgColor":"#f0ece4","stagger":14,"introFrames":30,
"metrics":[
  {{"label":"Goals\\n(per 90)","percentileA":98,"percentileB":97,"valueA":1.14,"valueB":1.08}},
  {{"label":"xG\\n(per 90)","percentileA":97,"percentileB":94,"valueA":0.96,"valueB":0.88}},
  {{"label":"Key Passes\\n(per 90)","percentileA":96,"percentileB":74,"valueA":3.8,"valueB":2.2}},
  {{"label":"Dribbles\\n(per 90)","percentileA":99,"percentileB":71,"valueA":4.6,"valueB":2.1}},
  {{"label":"Shot\\nAccuracy","percentileA":88,"percentileB":92,"valueA":0.56,"valueB":0.61}},
  {{"label":"Aerial\\nDuels Won","percentileA":28,"percentileB":91,"valueA":0.9,"valueB":4.1}},
  {{"label":"Progressive\\nRuns","percentileA":97,"percentileB":85,"valueA":6.2,"valueB":4.8}},
  {{"label":"Touches in\\nBox","percentileA":95,"percentileB":89,"valueA":8.4,"valueB":6.9}}
]}}
- accentColorA: primary club/country color for Player A
- accentColorB: primary club/country color for Player B
- seasonA/seasonB: the season being compared (usually same season for peak vs peak)
- percentile values 1-99 reflecting how each player ranks vs positional peers
- Use accurate stats for well-known players, plausible estimates for others"""
    try: return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e: print(f"    [Graphics] LLM hero comparison radar failed: {e}"); return None


def _llm_player_trio(tag_text: str) -> dict | None:
    images_hint = f"Available player images (use exact slug, no extension): {_PLAYER_IMAGES}" if _PLAYER_IMAGES else ""
    prompt = f"""Parse this player trio comparison tag and return data as JSON.
Tag: "{tag_text}"
Format is: "Title, Player1 vs Player2 vs Player3" or "Title, Player1 | Player2 | Player3"

Return JSON:
{{"title":"","subtitle":"","bgColor":"#f0ece4","players":[
  {{"name":"","image":"","club":"","clubColor":"#C9A84C","badgeSlug":"","stat":"","statLabel":""}},
  {{"name":"","image":"","club":"","clubColor":"#C9A84C","badgeSlug":"","stat":"","statLabel":""}},
  {{"name":"","image":"","club":"","clubColor":"#C9A84C","badgeSlug":"","stat":"","statLabel":""}}
]}}
- title: short punchy comparison title (e.g. "the debate", "the golden era")
- subtitle: brief context (e.g. "2013/14 season", "peak vs peak")
- For each player: set 'image' to their slug from available list (or empty), 'stat' to their key stat, 'statLabel' to what it measures
- clubColor must be the club's primary color
{images_hint}"""
    try: return json.loads(ask_llm(prompt, expect_json=True))
    except Exception as e: print(f"    [Graphics] LLM player trio failed: {e}"); return None


# ── Sourced clip renderer ──────────────────────────────────────────────────────

def _render_sourced_clips(output_dir: str, results: dict) -> None:
    """
    Check remotiontest/public/clips/ for any footage the user has dropped in.
    For each clip in clips_needed.json whose file is present, render it through
    HeroClipSingle and add the output path to `results`.

    Accepted extensions: .mp4 .mov .webm .jpg .jpeg .png .webp
    Expected filenames:  clip_001.mp4  (single)
                         clip_001_left.mp4 / clip_001_right.mp4  (compare)
    """
    from utils.remotion_renderer import (
        REMOTION_DIR,
        render_hero_clip_single as _render_clip,
        render_hero_concept as _render_compare,
    )

    clips_json = os.path.join(output_dir, "clips_needed.json")
    if not os.path.exists(clips_json):
        return

    with open(clips_json) as f:
        clips_needed = json.load(f)

    public_clips = os.path.join(REMOTION_DIR, "public", "clips")
    os.makedirs(public_clips, exist_ok=True)

    _EXTS = (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp")

    def _find_file(stem: str) -> str | None:
        for ext in _EXTS:
            p = os.path.join(public_clips, stem + ext)
            if os.path.exists(p):
                return p
        return None

    rendered_any = False
    for clip in clips_needed:
        cid   = clip["id"]          # e.g. "clip_001"
        label = clip.get("label", "")
        desc  = clip.get("description", "")

        if clip.get("type") == "compare":
            # CLIP COMPARE → HeroClipCompare (dual-clip side-by-side)
            # Both left and right files must be present to render the combined graphic.
            found_left  = _find_file(f"{cid}_left")
            found_right = _find_file(f"{cid}_right")
            key = f"CLIP COMPARE {cid}"
            if key in results:
                continue
            if not found_left and not found_right:
                continue

            ext_l = os.path.splitext(found_left)[1]  if found_left  else ".mp4"
            ext_r = os.path.splitext(found_right)[1] if found_right else ".mp4"
            src_l = f"clips/{cid}_left{ext_l}"   if found_left  else ""
            src_r = f"clips/{cid}_right{ext_r}"  if found_right else ""

            # Parse label / description from "LEFT: x | RIGHT: y" format
            lbl_l = label.split(" | ")[0].strip()
            lbl_r = label.split(" | ")[-1].strip()
            desc_l = desc.split("| RIGHT:")[0].replace("LEFT:", "").strip()
            parts  = desc.split("| RIGHT:")
            desc_r = parts[1].strip() if len(parts) > 1 else desc

            out = os.path.abspath(os.path.join(output_dir, f"rendered_{cid}.mp4"))
            print(f"    [Clips] Found compare clip: {cid} — rendering HeroClipCompare…")
            ok = _render_compare({
                "clipLeft":   src_l,
                "clipRight":  src_r,
                "labelLeft":  lbl_l,
                "labelRight": lbl_r,
                "title":      desc_l or desc_r,
            }, out)
            results[key] = out if ok else None
            rendered_any = True
        else:
            found = _find_file(cid)
            if not found:
                continue
            ext = os.path.splitext(found)[1]
            src = f"clips/{cid}{ext}"
            out = os.path.abspath(os.path.join(output_dir, f"rendered_{cid}.mp4"))
            key = f"CLIP SINGLE {cid}"
            if key not in results:
                print(f"    [Clips] Found sourced clip: {found} — rendering…")
                ok = _render_clip({"clip": src, "label": label, "title": desc}, out)
                results[key] = out if ok else None
                rendered_any = True

    if rendered_any:
        sourced = sum(1 for k, v in results.items() if k.startswith("CLIP") and v)
        print(f"    [Clips] Rendered {sourced} sourced clip(s) (single→HeroClipSingle, compare→HeroClipCompare).")
    else:
        missing = [c["id"] for c in clips_needed]
        if missing:
            print(f"    [Clips] No sourced clips found in public/clips/ yet — drop files named:")
            for c in clips_needed[:5]:
                print(f"           {c['id']}.mp4  ({c.get('description','')[:60]})")
            if len(missing) > 5:
                print(f"           …and {len(missing)-5} more (see production_sheet.md)")


# ── Hero visual pass ─────────────────────────────────────────────────────────

def _hero_fallback_render(template: str, content: str, renders_dir: str):
    """Render a hero_visual scene using its standard template handler as fallback.

    Returns (path, props) tuple on success, None on failure.
    """
    slug = re.sub(r"\W+", "_", content.lower())[:30]
    tpl  = template.upper()

    if tpl == "HERO BIG STAT":
        data = _llm_hero_bigstat(content)
        out  = os.path.abspath(os.path.join(renders_dir, f"hero_bigstat_{slug}.mp4"))
        return (out, data) if data and render_hero_bigstat(data, out) else None

    if tpl == "CAREER TIMELINE":
        data = _llm_timeline(content)
        out  = os.path.abspath(os.path.join(renders_dir, f"hero_timeline_{slug}.mp4"))
        return (out, data) if data and render_career_timeline(data, out) else None

    if tpl == "HERO STAT BARS":
        data = _llm_hero_bars(content)
        out  = os.path.abspath(os.path.join(renders_dir, f"hero_statbars_{slug}.mp4"))
        return (out, data) if data and render_hero_statbars(data, out) else None

    if tpl == "PLAYER RADAR":
        data = _llm_attacking_radar(content) if callable(globals().get("_llm_attacking_radar")) else None
        out  = os.path.abspath(os.path.join(renders_dir, f"hero_radar_{slug}.mp4"))
        return (out, data) if data and render_attacking_radar(data, out) else None

    return None


def _render_hero_visuals(output_dir: str, renders_dir: str, manifest: list):
    """Proactive motion_agent pass: render bespoke graphics for hero_visual scenes.

    Reads storyboard.json from output_dir, finds scenes with hero_visual:true,
    calls motion_agent.generate_motion_graphic() with force_generate=True.

    Fallback: if motion_agent fails, renders the scene's existing template
    via the standard handler and logs a warning.

    Guarantee: at least 1 motion_agent render per video (ACT 3 hero_visual is mandatory).
    """
    sb_path = os.path.join(output_dir, "storyboard.json")
    if not os.path.exists(sb_path):
        print("    [Hero] No storyboard.json found — skipping hero visual pass.")
        return

    try:
        with open(sb_path) as f:
            scenes = json.load(f)
    except Exception as e:
        print(f"    [Hero] Could not read storyboard.json: {e}")
        return

    hero_scenes = [s for s in scenes if s.get("hero_visual") and s.get("type") == "graphic"]
    if not hero_scenes:
        print("    [Hero] No hero_visual scenes found in storyboard.")
        return

    print(f"\n[*] Hero Visual Pass — {len(hero_scenes)} hero_visual scene(s) to render via motion_agent")

    for scene in hero_scenes:
        content  = scene.get("content", "")
        template = scene.get("template", "")
        act      = scene.get("act", "")
        slug     = re.sub(r"\W+", "_", content.lower())[:40]
        out_path = os.path.abspath(os.path.join(renders_dir, f"hero_{slug}.mp4"))

        print(f"\n    [Hero] Scene: [{template}: {content}] ({act})")

        # Build props hint from scene content
        props_hint = {
            "title":       content,
            "subtitle":    act,
            "bgColor":     "#0a0a0a",
            "accentColor": "#C9A84C",
        }
        description = f"Hero visual for {act}: {template} — {content}"

        result = generate_motion_graphic(
            scene_description=description,
            props_hint=props_hint,
            output_dir=output_dir,
            force_generate=True,
        )

        if result.get("status") in ("rendered", "generated") and result.get("rendered_path"):
            rendered_path = result["rendered_path"]
            print(f"    [Hero] ✓ motion_agent rendered: {os.path.basename(rendered_path)}")
            manifest.append({
                "filename":    os.path.basename(rendered_path),
                "type":        "hero_visual",
                "tag":         f"[{template}: {content}]",
                "tag_text":    content,
                "props":       result.get("props", props_hint),
                "composition": result.get("composition_id"),
                "hero_visual": True,
                "act":         act,
            })
        else:
            # Fallback: render using existing template handler
            print(f"    [Hero] ⚠ motion_agent failed — falling back to template: {template}")
            fallback_result = None
            try:
                fallback_result = _hero_fallback_render(template, content, renders_dir)
            except Exception as e:
                print(f"    [Hero] Fallback render error: {e}")

            if fallback_result:
                fb_path = fallback_result[0] if isinstance(fallback_result, tuple) else fallback_result
                fb_props = fallback_result[1] if isinstance(fallback_result, tuple) else {}
                print(f"    [Hero] ✓ Fallback rendered: {os.path.basename(fb_path)}")
                manifest.append({
                    "filename":    os.path.basename(fb_path),
                    "type":        "hero_visual_fallback",
                    "tag":         f"[{template}: {content}]",
                    "tag_text":    content,
                    "props":       fb_props,
                    "hero_visual": True,
                    "act":         act,
                })
            else:
                print(f"    [Hero] ✗ Hero visual failed entirely for: {content[:60]}")


# ── Visual identity validation ────────────────────────────────────────────────

_CHANNEL_ACCENT = "#C9A84C"   # gold — from shared.tsx COLORS.gold


def _hex_distance(a: str, b: str) -> float:
    """Rough perceptual distance between two hex colour strings (0–441)."""
    def _parse(h):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    try:
        r1, g1, b1 = _parse(a)
        r2, g2, b2 = _parse(b)
        return ((r1-r2)**2 + (g1-g2)**2 + (b1-b2)**2) ** 0.5
    except Exception:
        return 0.0


def _validate_visual_identity(manifest: list, renders_dir: str, output_dir: str):
    """Post-render identity checks — warnings only, never blocks the pipeline.

    Checks:
      1. Accent colour drift — any render whose accentColor prop deviates
         significantly from the channel gold (#C9A84C).
      2. motion_signature compliance — if templates/motion_signature.json exists,
         verify rendered comp IDs are listed; skip gracefully if file absent.
    """
    from pathlib import Path as _Path
    issues = []

    # ── 1. Accent colour drift ────────────────────────────────────────────────
    DRIFT_THRESHOLD = 80   # perceptual distance out of 441 — anything above is a warning
    for entry in manifest:
        props = entry.get("props") or {}
        accent = props.get("accentColor") or props.get("accent_color")
        if not accent:
            continue
        dist = _hex_distance(accent, _CHANNEL_ACCENT)
        if dist > DRIFT_THRESHOLD:
            issues.append(
                f"  ⚠ Accent drift [{entry.get('type','?')}] {entry.get('tag_text','')[:40]}: "
                f"{accent} (distance {dist:.0f} from {_CHANNEL_ACCENT})"
            )

    # ── 2. motion_signature compliance ───────────────────────────────────────
    sig_path = _Path(output_dir).parent.parent / "templates" / "motion_signature.json"
    if sig_path.exists():
        try:
            import json as _j
            sig = _j.loads(sig_path.read_text())
            allowed_comps = set(sig.get("compositions", []))
            if allowed_comps:
                for entry in manifest:
                    comp = entry.get("composition") or entry.get("type", "")
                    if comp and comp not in allowed_comps:
                        issues.append(
                            f"  ⚠ motion_signature: comp '{comp}' not in approved list"
                        )
        except Exception as e:
            print(f"    [Identity] motion_signature check error: {e}")
    # else: motion_signature.json not yet created — skip silently

    # ── Report ────────────────────────────────────────────────────────────────
    if issues:
        print(f"\n[*] Visual Identity Validation — {len(issues)} warning(s):")
        for msg in issues:
            print(msg)
    else:
        print(f"\n[*] Visual Identity Validation — ✓ no drift or compliance issues")


# ── Main agent function ────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Track B — Pure-renderer public API (render + build_payload + provenance)
# ─────────────────────────────────────────────────────────────────────────────

from pathlib import Path as _Path_track_b

# Try-import RenderRequest from Track A (server.py); fall back to local stub so
# this module compiles independently per the parallel-execution contract.
try:
    from server import RenderRequest as _RenderRequest_track_b  # type: ignore
except Exception:
    from dataclasses import dataclass as _dc_track_b

    @_dc_track_b(frozen=True)
    class _RenderRequest_track_b:  # type: ignore
        template_id: str
        payload: dict
        scene_id: str

RenderRequest = _RenderRequest_track_b

# Output directory for renders. Track D's orchestrator should call set_renders_dir().
_RENDERS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "output", "_renders"))


def set_renders_dir(path: str) -> None:
    """Configure the directory render() writes to. Called once by orchestrator."""
    global _RENDERS_DIR
    _RENDERS_DIR = os.path.abspath(path)
    os.makedirs(_RENDERS_DIR, exist_ok=True)


def _scene_text(scene: dict) -> str:
    """Pull the freeform tag text from a scene dict."""
    return (scene.get("tag_text") or scene.get("content") or scene.get("tag") or "").strip()


def _build_hero_bars(s):           d = _llm_hero_bars(_scene_text(s));         return (d, "llm") if d else None
def _build_hero_form(s):           d = _llm_hero_form(_scene_text(s));         return (d, "llm") if d else None
def _build_hero_tactical(s):       d = _llm_hero_tactical(_scene_text(s));     return (d, "llm") if d else None
def _build_hero_bigstat(s):        d = _llm_hero_bigstat(_scene_text(s));      return (d, "llm") if d else None
def _build_hero_graph(s):          d = _llm_hero_graph(_scene_text(s));        return (d, "llm") if d else None
def _build_hero_transfer(s):       d = _llm_hero_transfer(_scene_text(s));     return (d, "llm") if d else None
def _build_hero_quote(s):          d = _llm_hero_quote(_scene_text(s));        return (d, "llm") if d else None
def _build_hero_chapter(s):        d = _llm_hero_chapter(_scene_text(s));      return (d, "llm") if d else None
def _build_hero_concept(s):        d = _llm_hero_concept(_scene_text(s));      return (d, "llm") if d else None
def _build_hero_scatter(s):        d = _llm_hero_scatter(_scene_text(s));      return (d, "llm") if d else None
def _build_hero_shot_map(s):       d = _llm_hero_shot_map(_scene_text(s));     return (d, "llm") if d else None
def _build_hero_match_timeline(s): d = _llm_hero_match_timeline(_scene_text(s));return (d, "llm") if d else None
def _build_hero_awards_list(s):    d = _llm_hero_awards_list(_scene_text(s));  return (d, "llm") if d else None
def _build_hero_comparison_radar(s): d = _llm_hero_comparison_radar(_scene_text(s)); return (d, "llm") if d else None
def _build_player_trio(s):            d = _llm_player_trio(_scene_text(s));          return (d, "llm") if d else None
def _build_match_result(s):           d = _llm_match(_scene_text(s));                return (d, "llm") if d else None
def _build_transfer(s):               d = _llm_transfer(_scene_text(s));             return (d, "llm") if d else None
def _build_trophy(s):                 d = _llm_trophy(_scene_text(s));               return (d, "llm") if d else None
def _build_career_timeline(s):        d = _llm_timeline(_scene_text(s));             return (d, "llm") if d else None
def _build_disciplinary(s):           d = _llm_disciplinary(_scene_text(s));         return (d, "llm") if d else None
def _build_quote_card(s):             d = _llm_quote(_scene_text(s));                return (d, "llm") if d else None


def _build_hero_intro(s):
    rd = _RENDERS_DIR
    d = _llm_hero_intro(_scene_text(s), rd)
    return (d, "llm") if d else None

def _build_hero_outro(s):
    d = _llm_hero_outro(_scene_text(s))
    return (d, "llm") if d else None


def _build_player_stats(s):
    t = _scene_text(s)
    m = SEASON_RE.search(t)
    season = _parse_season(t)[1] if m else "unknown"
    player_name = re.sub(SEASON_RE, "", t).strip()
    data = _llm_player_stats(player_name, season)
    if not data:
        return None
    data.setdefault("season", season)
    data.setdefault("competition", "Premier League")
    _prefer_player_image(data, "playerImageSlug", player_name, _player_from_context(t))
    return data, "llm"


def _build_season_comparison(s):
    t = _scene_text(s)
    vs_match = re.search(r"(.+?)\s+vs\s+(.+?),\s*(.+?)\s+(\d{4}[/\-–]\d{2,4})\s*$", t.strip(), re.IGNORECASE)
    if not vs_match:
        return None
    player_a, player_b, competition = vs_match.group(1).strip(), vs_match.group(2).strip(), vs_match.group(3).strip()
    season_raw = vs_match.group(4)
    m = SEASON_RE.match(season_raw)
    if m:
        sy, ep = m.group(1), m.group(2)
        ey = ep if len(ep) == 4 else sy[:2] + ep
        season = f"{sy}/{ey[2:]}"
    else:
        season = season_raw
    data = _llm_comparison(player_a, player_b, season, competition)
    return (data, "llm") if data else None


def _build_team_lineup(s):
    """Lineup: real API only. NO LLM fallback (Track B kills hallucinated lineups)."""
    t = _scene_text(s)
    data = fetch_lineup_for_tag(t)
    if data:
        return data, data.get("_source") or "footballdata"
    return None  # data_gate will produce a clean miss; orchestrator skips render


def _build_standings(s):
    """Standings: Wikipedia scrape only. NO LLM fallback."""
    t = _scene_text(s)
    try:
        wiki_title, season = _parse_season(t)
    except ValueError:
        return None
    teams = _scrape_wikipedia_standings(wiki_title)
    if not teams:
        return None
    return {"rows": teams, "season": season}, "wikipedia"


def _build_top_scorers(s):
    """Top scorers: Wikipedia scrape only. NO LLM fallback."""
    t = _scene_text(s)
    try:
        wiki_title, season = _parse_season(t)
    except ValueError:
        return None
    competition = re.sub(SEASON_RE, "", t).strip(" -–")
    rows = _scrape_wikipedia_scorers(wiki_title, "goals")
    if not rows:
        return None
    return {"rows": rows, "season": season, "competition": competition, "stat": "goals"}, "wikipedia"


def _build_top_assists(s):
    t = _scene_text(s)
    try:
        wiki_title, season = _parse_season(t)
    except ValueError:
        return None
    competition = re.sub(SEASON_RE, "", t).strip(" -–")
    rows = _scrape_wikipedia_scorers(wiki_title, "assists")
    if not rows:
        return None
    return {"rows": rows, "season": season, "competition": competition, "stat": "assists"}, "wikipedia"


def _build_attacking_radar(s):
    """Real FBref data via radar_agent. Fallback to LLM is stamped 'llm' by radar_agent."""
    from agents.radar_agent import build_radar_props
    t = _scene_text(s)
    parts = [p.strip() for p in t.split(",")]
    if len(parts) < 4:
        return None
    player, club, competition, season = parts[0], parts[1], parts[2], parts[3]
    props = build_radar_props(player, club, competition, season)
    if not props:
        return None
    return props, props.get("_source", "llm")


def _build_season_timeline(s):
    data = _parse_season_timeline_tag(_scene_text(s))
    return (data, "manual_curated") if data else None


def _build_tournament_bracket(s):
    """Bracket data lookup only — never LLM. Banned from autogen unless explicit."""
    t = _scene_text(s)
    m = re.search(r"^(.+?),\s*focus:\s*(.+?)$", t.strip(), re.IGNORECASE)
    if not m:
        return None
    tournament, focus_team = m.group(1).strip(), m.group(2).strip()
    matches = lookup_bracket(tournament)
    if not matches:
        return None
    all_teams = {mt["teamA"] for mt in matches} | {mt["teamB"] for mt in matches}
    if focus_team not in all_teams:
        focus_team_resolved = next((team for team in all_teams if team.lower() == focus_team.lower()), None)
        if not focus_team_resolved:
            return None
        focus_team = focus_team_resolved
    return {"matches": matches, "focusTeam": focus_team, "title": tournament,
            "subtitle": f"{focus_team}'s path"}, "manual_curated"


_PAYLOAD_BUILDERS = {
    "HeroStatBars":         _build_hero_bars,
    "HeroFormRun":          _build_hero_form,
    "HeroTactical":         _build_hero_tactical,
    "HeroBigStat":          _build_hero_bigstat,
    "HeroLeagueGraph":      _build_hero_graph,
    "HeroTransferRecord":   _build_hero_transfer,
    "HeroIntro":            _build_hero_intro,
    "HeroOutro":            _build_hero_outro,
    "HeroQuote":            _build_hero_quote,
    "HeroChapterWord":      _build_hero_chapter,
    "HeroConceptCard":      _build_hero_concept,
    "HeroClipCompare":      _build_hero_concept,
    "HeroScatterPlot":      _build_hero_scatter,
    "HeroShotMap":          _build_hero_shot_map,
    "HeroMatchTimeline":    _build_hero_match_timeline,
    "HeroAwardsList":       _build_hero_awards_list,
    "HeroComparisonRadar":  _build_hero_comparison_radar,
    "HeroSeasonTimeline":   _build_season_timeline,
    "PlayerTrio":              _build_player_trio,
    "PlayerStats":             _build_player_stats,
    "AttackingRadar":          _build_attacking_radar,
    "MatchResult":             _build_match_result,
    "Transfer":                _build_transfer,
    "Trophy":                  _build_trophy,
    "CareerTimeline":          _build_career_timeline,
    "SeasonComparison":        _build_season_comparison,
    "TeamLineup":              _build_team_lineup,
    "DisciplinaryRecord":      _build_disciplinary,
    "QuoteCard":               _build_quote_card,
    "PremierLeagueTable":      _build_standings,
    "StandingsTable":          _build_standings,
    "TopScorersTable":         _build_top_scorers,
    "TopAssistsTable":         _build_top_assists,
    "TournamentBracket":       _build_tournament_bracket,
}


_RENDER_DISPATCH = {
    "HeroStatBars":         render_hero_statbars,
    "HeroFormRun":          render_hero_formrun,
    "HeroTactical":         render_hero_tactical,
    "HeroBigStat":          render_hero_bigstat,
    "HeroLeagueGraph":      render_hero_leaguegraph,
    "HeroTransferRecord":   render_hero_transfer_record,
    "HeroIntro":            render_hero_intro,
    "HeroOutro":            render_hero_outro,
    "HeroQuote":            render_hero_quote,
    "HeroChapterWord":      render_hero_chapter,
    "HeroConceptCard":      render_hero_concept,
    "HeroClipCompare":      render_hero_concept,
    "HeroScatterPlot":      render_hero_scatter,
    "HeroClipSingle":       render_hero_clip_single,
    "HeroShotMap":          render_hero_shot_map,
    "HeroMatchTimeline":    render_hero_match_timeline,
    "HeroAwardsList":       render_hero_awards_list,
    "HeroComparisonRadar":  render_hero_comparison_radar,
    "HeroSeasonTimeline":   render_hero_season_timeline,
    "PlayerTrio":              render_player_trio,
    "PlayerStats":             render_player_stats,
    "AttackingRadar":          render_attacking_radar,
    "MatchResult":             render_match_result,
    "Transfer":                render_transfer,
    "Trophy":                  render_trophy,
    "CareerTimeline":          render_career_timeline,
    "SeasonComparison":        render_season_comparison,
    "TeamLineup":              render_team_lineup,
    "DisciplinaryRecord":      render_disciplinary_record,
    "QuoteCard":               render_quote_card,
    "PremierLeagueTable":      lambda d, out: render_standings(d.get("rows", []), d.get("season", ""), out),
    "StandingsTable":          lambda d, out: render_standings(d.get("rows", []), d.get("season", ""), out),
    "TopScorersTable":         lambda d, out: render_top_scorers(d.get("rows", []), d.get("season", ""), d.get("competition", ""), d.get("stat", "goals"), out),
    "TopAssistsTable":         lambda d, out: render_top_scorers(d.get("rows", []), d.get("season", ""), d.get("competition", ""), d.get("stat", "assists"), out),
    "TournamentBracket":       render_tournament_bracket,
}


def build_payload(template_id: str, scene: dict) -> dict | None:
    """Public payload builder. Stamps `_source` based on origin.
    Runs formation validation for tactical/lineup templates and substitutes
    canonical stock layout (utils.formation_validator.STOCK_FORMATION_PYTHON)
    on validation failure.
    """
    builder = _PAYLOAD_BUILDERS.get(template_id)
    if builder is None:
        print(f"    [Graphics] build_payload: no builder for {template_id!r}")
        return None
    try:
        result = builder(scene)
    except Exception as e:
        print(f"    [Graphics] build_payload {template_id} raised: {e}")
        return None
    if not result:
        return None
    payload, source = result
    payload["_source"] = source

    if template_id in {"HeroTactical", "TeamLineup"}:
        from utils.formation_validator import validate_formation, STOCK_FORMATION_PYTHON
        ok, reason = validate_formation(payload)
        if not ok:
            formation_name = payload.get("formation")
            stock = STOCK_FORMATION_PYTHON.get(formation_name)
            if stock:
                print(f"    [Graphics] formation invalid ({reason}) — substituting stock {formation_name!r}")
                payload["nodes"] = stock
                payload["_source"] = "stock_formation"
            else:
                print(f"    [Graphics] formation invalid ({reason}) — no stock for {formation_name!r}, refusing")
                return None
    return payload


def render(request: RenderRequest) -> "_Path_track_b | None":
    """Single public mp4 render entry point. Pure renderer — no gating, no fallback.
    Caller (Track A's data_gate) must have validated payload before invocation.
    """
    fn = _RENDER_DISPATCH.get(request.template_id)
    if fn is None:
        print(f"    [Graphics] render: no dispatcher for {request.template_id!r}")
        return None
    os.makedirs(_RENDERS_DIR, exist_ok=True)
    safe_id = re.sub(r"\W+", "_", str(request.scene_id)).strip("_") or "scene"
    out = os.path.abspath(os.path.join(_RENDERS_DIR, f"{request.template_id}_{safe_id}.mp4"))
    try:
        ok = fn(request.payload, out)
    except Exception as e:
        print(f"    [Graphics] render {request.template_id} failed: {e}")
        return None
    return _Path_track_b(out) if ok else None


def render_preview(request: RenderRequest) -> "_Path_track_b | None":
    """PNG-preview entry point. Writes a single still frame to renders/previews/.
    Reuses the same dispatch as render() — _render() in remotion_renderer detects
    the .png extension and switches to `npx remotion still`.
    """
    fn = _RENDER_DISPATCH.get(request.template_id)
    if fn is None:
        print(f"    [Graphics] render_preview: no dispatcher for {request.template_id!r}")
        return None
    previews_dir = os.path.join(_RENDERS_DIR, "previews")
    os.makedirs(previews_dir, exist_ok=True)
    safe_id = re.sub(r"\W+", "_", str(request.scene_id)).strip("_") or "scene"
    out = os.path.abspath(os.path.join(previews_dir, f"{request.template_id}_{safe_id}.png"))
    try:
        ok = fn(request.payload, out)
    except Exception as e:
        print(f"    [Graphics] render_preview {request.template_id} failed: {e}")
        return None
    return _Path_track_b(out) if ok else None


# ─────────────────────────────────────────────────────────────────────────────
# End Track B — Pure-renderer public API
# ─────────────────────────────────────────────────────────────────────────────


def generate_graphics(script: str, output_dir: str) -> dict:
    """
    DEPRECATED — Track B has removed the inline tag-dispatch loop.
    Track D's orchestrator should call render(RenderRequest) per scene.
    The post-loop cleanup helpers (`_render_sourced_clips`, `_render_hero_visuals`,
    `_validate_visual_identity`) remain importable at module level.

    Retains image-bank initialisation and bg_map loading so legacy callers do
    not crash, but performs no rendering. Returns an empty results dict.
    """
    global _PLAYER_IMAGES, _DOC_ENTITY
    # Refresh image list so images added since server start are picked up.
    _PLAYER_IMAGES = _get_available_player_images()
    # Extract documentary subject from context.md title for use as fallback in all image lookups.
    _DOC_ENTITY = ""
    _ctx_path = os.path.join(output_dir, "context.md")
    if os.path.exists(_ctx_path):
        for _line in open(_ctx_path):
            if _line.startswith("## Title"):
                continue
            stripped = _line.strip()
            if stripped:
                _DOC_ENTITY = stripped.split(":")[0].strip()
                break
    if _DOC_ENTITY:
        print(f"[*] Graphics Agent — documentary entity: {_DOC_ENTITY!r}, {len(_PLAYER_IMAGES)} player image(s) available")

    print("[*] Graphics Agent scanning script for visual tags...")

    renders_dir = os.path.join(output_dir, "renders")
    os.makedirs(renders_dir, exist_ok=True)

    results  = {}
    seen     = set()
    manifest = []  # one entry per successful render — written to renders/manifest.json

    # Load canonical bgColor map written by server.py at storyboard save time
    _bg_map = {}
    _bg_map_path = os.path.join(output_dir, "bg_map.json")
    if os.path.exists(_bg_map_path):
        try:
            with open(_bg_map_path) as _f:
                _bg_map = json.load(_f)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # Track B: inline tag-dispatch loop REMOVED.
    # Track D's orchestrator now drives rendering by calling
    # graphics_agent.render(RenderRequest) per scene. This function retains
    # only the post-loop cleanup so legacy callers don't crash.
    # ─────────────────────────────────────────────────────────────────────

    # (dispatch loops removed — see Track B note above)

    # ── Sourced clips — render any clips the user has dropped into public/clips/ ──
    _render_sourced_clips(output_dir, results)

    # ── Hero visuals — motion_agent renders for hero_visual:true storyboard scenes ──
    _render_hero_visuals(output_dir, renders_dir, manifest)

    # Save manifest so Studio can read render props (for image-swap, re-render)
    import datetime as _dt
    manifest_path = os.path.join(renders_dir, "manifest.json")
    with open(manifest_path, "w") as _mf:
        json.dump(manifest, _mf, indent=2, default=str)

    # ── Visual identity validation ─────────────────────────────────────────────
    _validate_visual_identity(manifest, renders_dir, output_dir)

    # ── Missing player images report ──────────────────────────────────────────
    _IMAGE_PROPS = {"playerImage", "playerImageSlug", "sideImage", "homeImage", "awayImage"}
    missing_images = []
    for entry in manifest:
        props = entry.get("props", {})
        for prop in _IMAGE_PROPS:
            if prop in props and not props[prop]:
                missing_images.append({
                    "composition": entry.get("filename", ""),
                    "tag":         entry.get("tag", ""),
                    "prop":        prop,
                })
    _missing_path = os.path.join(output_dir, "missing_images.md")
    if missing_images:
        lines = ["# Missing Player Images\n",
                 "These renders were completed WITHOUT a player/side image.\n",
                 "Source the images, place them in `remotiontest/public/players/`,\n",
                 "then re-render from the Studio page before exporting.\n\n",
                 "| File | Tag | Prop |\n",
                 "|------|-----|------|\n"]
        for m in missing_images:
            lines.append(f"| `{m['composition']}` | `{m['tag']}` | `{m['prop']}` |\n")
        with open(_missing_path, "w") as _mf:
            _mf.writelines(lines)
        print(f"\n[!] MISSING IMAGES: {len(missing_images)} render(s) are missing player images.")
        print(f"    → See {_missing_path}")
        for m in missing_images:
            print(f"    • {m['tag']} — needs '{m['prop']}'")
    else:
        if os.path.exists(_missing_path):
            os.remove(_missing_path)

    # Summary
    succeeded = [k for k, v in results.items() if v]
    print(f"\n[*] Graphics Agent complete: {len(succeeded)}/{len(results)} renders successful.")
    for tag, path in results.items():
        print(f"    {tag!r}: {'→ ' + path if path else '✗ failed'}")

    return results
