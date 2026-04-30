"""
Remotion renderer utility.
Renders the PremierLeagueTable composition with injected props.
"""

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

REMOTION_DIR = os.path.abspath(
    os.environ.get(
        "REMOTION_PROJECT_PATH",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "remotiontest"),
    )
)

# Team brand colours and badge filenames for known PL clubs.
# Add entries here as new badges are added to remotiontest/public/badges/
TEAM_CONFIG = {
    "Manchester City":             {"color": "#6CABDD", "badgeSlug": "manchester-city.svg"},
    "Liverpool":                   {"color": "#C8102E", "badgeSlug": "liverpool.svg"},
    "Chelsea":                     {"color": "#034694", "badgeSlug": "chelsea.svg"},
    "Arsenal":                     {"color": "#EF0107", "badgeSlug": "arsenal.svg"},
    "Everton":                     {"color": "#003399", "badgeSlug": "everton.svg"},
    "Tottenham Hotspur":           {"color": "#132257", "badgeSlug": "tottenham.png"},
    "Tottenham":                   {"color": "#132257", "badgeSlug": "tottenham.png"},
    "Manchester United":           {"color": "#DA291C", "badgeSlug": "manchester-united.svg"},
    "Leicester City":              {"color": "#0053A0", "badgeSlug": "leicester-city.svg"},
    "Newcastle United":            {"color": "#241F20", "badgeSlug": "newcastle-united.svg"},
    "Aston Villa":                 {"color": "#95BFE5", "badgeSlug": "aston-villa.svg"},
    "West Ham United":             {"color": "#7A263A", "badgeSlug": "west-ham-united.svg"},
    "Wolverhampton Wanderers":     {"color": "#FDB913", "badgeSlug": "wolves.svg"},
    "Leeds United":                {"color": "#FFCD00", "badgeSlug": "leeds-united.svg"},
    "Nottingham Forest":           {"color": "#DD0000", "badgeSlug": "nottingham-forest.svg"},
    "Blackburn Rovers":            {"color": "#009EE0", "badgeSlug": "blackburn-rovers.svg"},
    "Southampton":                 {"color": "#D71920", "badgeSlug": "southampton.svg"},
    "Sunderland":                  {"color": "#EB172B", "badgeSlug": "sunderland.svg"},
    "Bolton Wanderers":            {"color": "#263570", "badgeSlug": "bolton.svg"},
    "Swansea City":                {"color": "#121212", "badgeSlug": "swansea.svg"},
    "Stoke City":                  {"color": "#E03A3E", "badgeSlug": "stoke-city.svg"},
    "Fulham":                      {"color": "#CC0000", "badgeSlug": "fulham.svg"},
    "West Bromwich Albion":        {"color": "#122F67", "badgeSlug": "west-brom.svg"},
    "Crystal Palace":              {"color": "#1B458F", "badgeSlug": "crystal-palace.svg"},
    "Brentford":                   {"color": "#E30613", "badgeSlug": "brentford.svg"},
    "Brighton & Hove Albion":      {"color": "#0057B8", "badgeSlug": "brighton.svg"},
    "Brighton":                    {"color": "#0057B8", "badgeSlug": "brighton.svg"},
    "Burnley":                     {"color": "#6C1D45", "badgeSlug": "burnley.svg"},
    "Ipswich Town":                {"color": "#0044A9", "badgeSlug": "ipswich.svg"},
    "FC Barcelona":                {"color": "#004D98", "badgeSlug": "barcelona.svg"},
    "Barcelona":                   {"color": "#004D98", "badgeSlug": "barcelona.svg"},
    "Real Madrid":                 {"color": "#FEBE10", "badgeSlug": "real-madrid.svg"},
    "Juventus":                    {"color": "#000000", "badgeSlug": "juventus.svg"},
    "Paris Saint-Germain":         {"color": "#004170", "badgeSlug": "psg.svg"},
    "Atletico Madrid":             {"color": "#CB3524", "badgeSlug": "atletico-madrid.svg"},
}

FALLBACK_CONFIG = {"color": "#555555", "badgeSlug": "premier-league.svg"}

BADGES_DIR = os.path.join(REMOTION_DIR, "public", "badges")

_WP_API = "https://en.wikipedia.org/w/api.php"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YTEngine/1.0)"}

# Brand colours for teams not in TEAM_CONFIG (fetched badge gets paired with these)
_FALLBACK_COLORS = {
    "Norwich City":          "#00A650",
    "Watford":               "#FBEE23",
    "Middlesbrough":         "#E41B17",
    "Cardiff City":          "#0070B5",
    "Hull City":             "#F18A00",
    "Queens Park Rangers":   "#1D5BA4",
    "Reading":               "#004494",
    "Wigan Athletic":        "#1C4E9D",
    "Charlton Athletic":     "#CC0000",
    "Derby County":          "#101010",
    "Sheffield United":      "#EE2737",
    "Coventry City":         "#87CEEB",
}


def _slugify(name: str) -> str:
    """Convert 'Manchester United' → 'manchester-united'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _fetch_wikipedia_image_url(title: str) -> str | None:
    """Query Wikipedia pageimages API and return the original image URL, or None."""
    params = {
        "action": "query",
        "titles": title,
        "prop": "pageimages",
        "piprop": "original",
        "format": "json",
    }
    url = _WP_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            img = page.get("original", {})
            if img.get("source"):
                return img["source"]
    except Exception as e:
        print(f"    [Badges] Wikipedia API error for {title!r}: {e}")
    return None


def _download_badge(team_name: str) -> str | None:
    """
    Try to download a badge for team_name from Wikipedia.
    Saves to BADGES_DIR and returns the filename (e.g. 'norwich-city.svg'),
    or None on failure.
    """
    os.makedirs(BADGES_DIR, exist_ok=True)
    slug = _slugify(team_name)

    # Wikipedia article titles to try (club FC page, then bare name)
    titles_to_try = [f"{team_name} F.C.", team_name]

    img_url = None
    for title in titles_to_try:
        img_url = _fetch_wikipedia_image_url(title)
        if img_url:
            break

    if not img_url:
        print(f"    [Badges] No image found on Wikipedia for {team_name!r}.")
        return None

    # Determine extension from URL
    url_path = urllib.parse.urlparse(img_url).path
    ext = os.path.splitext(url_path)[1].lower()  # e.g. ".svg" or ".png"
    if ext not in (".svg", ".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"

    filename = f"{slug}{ext}"
    dest = os.path.join(BADGES_DIR, filename)

    try:
        req = urllib.request.Request(img_url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            with open(dest, "wb") as f:
                f.write(resp.read())
        print(f"    [Badges] ✓ Downloaded badge for {team_name!r} → {filename}")
        return filename
    except Exception as e:
        print(f"    [Badges] ✗ Download failed for {team_name!r}: {e}")
        return None


_INVALID_TEAM_NAMES = {"not applicable", "none", "n/a", "unknown", "", "tbd"}

def _ensure_badge(team_name: str) -> str:
    """
    Return a badgeSlug for team_name.
    1. Use TEAM_CONFIG if present and file exists.
    2. Check if any slug-named file already exists in BADGES_DIR.
    3. Auto-download from Wikipedia.
    4. Fall back to premier-league.svg placeholder.
    """
    if not team_name or team_name.lower().strip() in _INVALID_TEAM_NAMES:
        return FALLBACK_CONFIG["badgeSlug"]

    # Known config entry?
    cfg = TEAM_CONFIG.get(team_name)
    if cfg:
        badge_path = os.path.join(BADGES_DIR, cfg["badgeSlug"])
        if os.path.exists(badge_path):
            return cfg["badgeSlug"]

    # Already downloaded in a previous run?
    slug = _slugify(team_name)
    for ext in (".svg", ".png", ".jpg", ".jpeg", ".webp"):
        candidate = slug + ext
        if os.path.exists(os.path.join(BADGES_DIR, candidate)):
            return candidate

    # Try to auto-download
    print(f"    [Badges] Badge missing for {team_name!r} — attempting auto-download...")
    downloaded = _download_badge(team_name)
    if downloaded:
        return downloaded

    return FALLBACK_CONFIG["badgeSlug"]


def _enrich_team(raw: dict) -> dict:
    """Add colour and badge to a raw standings row by matching team name."""
    name = raw.get("name", "")
    cfg = TEAM_CONFIG.get(name)
    color = cfg["color"] if cfg else _FALLBACK_COLORS.get(name, FALLBACK_CONFIG["color"])
    badge_slug = _ensure_badge(name)
    return {
        "pos":       raw["pos"],
        "name":      name,
        "color":     color,
        "badgeSlug": badge_slug,
        "p":         raw.get("p", 38),
        "w":         raw.get("w", 0),
        "d":         raw.get("d", 0),
        "l":         raw.get("l", 0),
        "gd":        raw.get("gd", 0),
        "pts":       raw.get("pts", 0),
    }


# WSL: chrome-headless-shell needs libs that may not be installed system-wide.
# We keep local copies extracted from .deb packages (no sudo required).
# /tmp is wiped on reboot — re-extract if missing.
_LOCAL_LIBS = "/tmp/chromedeps/usr/lib/x86_64-linux-gnu"

def _ensure_chrome_deps() -> None:
    """Download and extract libnspr4 + libnss3 into _LOCAL_LIBS if missing."""
    if os.path.exists(os.path.join(_LOCAL_LIBS, "libnspr4.so")):
        return
    import tempfile, shutil, subprocess as _sp
    print("[Remotion] Chrome deps missing — extracting libnspr4/libnss3 (no sudo needed)...")
    tmpdir = tempfile.mkdtemp()
    try:
        pkgs = ["libnspr4", "libnss3"]
        _sp.run(["apt-get", "download"] + pkgs, cwd=tmpdir,
                capture_output=True, check=False)
        for deb in os.listdir(tmpdir):
            if deb.endswith(".deb"):
                _sp.run(["dpkg-deb", "-x", deb, "/tmp/chromedeps"],
                        cwd=tmpdir, capture_output=True, check=False)
        if os.path.exists(os.path.join(_LOCAL_LIBS, "libnspr4.so")):
            print("[Remotion] ✓ Chrome deps extracted.")
        else:
            print("[Remotion] ✗ Could not extract deps — renders may fail.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

_ensure_chrome_deps()


def _normalize_img(slug: str) -> str:
    """Ensure an image slug resolves to a real file under public/.
    If the bare slug (e.g. 'luis') lives in public/players/ rather than
    public/ root, prepend 'players/' so Remotion's staticFile() finds it."""
    if not slug:
        return slug
    # Already has a path separator — trust it as-is
    if "/" in slug:
        return slug
    # Strip any accidental extension so we can probe cleanly
    base, ext = os.path.splitext(slug)
    players_dir = os.path.join(REMOTION_DIR, "public", "players")
    root_dir    = os.path.join(REMOTION_DIR, "public")
    for probe_ext in ((ext,) if ext else ("", ".png", ".jpg", ".jpeg", ".webp")):
        if os.path.exists(os.path.join(root_dir, slug + probe_ext)):
            return slug          # already resolvable from public/ root
        if os.path.exists(os.path.join(players_dir, slug + probe_ext)):
            return f"players/{slug}"  # needs the players/ prefix
    return slug  # not found either way — pass through and let SmartImg try


def _normalize_props_images(props: object) -> object:
    """Recursively walk props and normalise any image-path fields."""
    if isinstance(props, dict):
        return {
            k: (_normalize_img(v) if k in ("image", "playerImage", "playerImageSlug",
                                           "sideImage", "player1Image", "player2Image")
                                  and isinstance(v, str)
                else _normalize_props_images(v))
            for k, v in props.items()
        }
    if isinstance(props, list):
        return [_normalize_props_images(item) for item in props]
    return props


# Frame to capture for PNG preview stills. ~30 places us past intro/spring-in
# settle for most compositions while still being well within any 60+ frame clip.
PREVIEW_STILL_FRAME = 30


def _render(composition_id: str, props: dict, output_path: str, label: str) -> bool:
    """Generic Remotion render call.

    If output_path ends in .png we shell `npx remotion still` (single frame, ~5×
    faster than mp4) for the PNG-preview pipeline. Otherwise normal mp4 render.
    """
    props = dict(props)  # don't mutate caller's dict
    props = _normalize_props_images(props)
    is_still = output_path.lower().endswith(".png")
    # durationInFrames is kept in props — calculateMetadata in Root.tsx reads it
    # to set the composition length dynamically for clip compositions.
    if is_still:
        cmd = [
            "npx", "remotion", "still",
            "src/index.ts",
            composition_id,
            output_path,
            "--props", json.dumps(props),
            "--frame", str(PREVIEW_STILL_FRAME),
        ]
    else:
        cmd = [
            "npx", "remotion", "render",
            "src/index.ts",
            composition_id,
            output_path,
            "--props", json.dumps(props),
        ]
    # Extend LD_LIBRARY_PATH so chrome-headless-shell can find libnspr4 etc.
    env = os.environ.copy()
    existing = env.get("LD_LIBRARY_PATH", "")
    extra = f"{_LOCAL_LIBS}:{_LOCAL_LIBS}/gbm"
    env["LD_LIBRARY_PATH"] = f"{extra}:{existing}" if existing else extra

    mode = "still" if is_still else "render"
    print(f"    [Remotion] {mode} {label} → {output_path}")
    result = subprocess.run(cmd, cwd=REMOTION_DIR, capture_output=True, text=True, env=env)
    if result.returncode == 0:
        print(f"    [Remotion] ✓ {mode} OK.")
        return True
    else:
        print(f"    [Remotion] ✗ {mode} failed:\n{result.stderr[-1000:]}")
        return False


def render_standings(teams_raw: list, season: str, output_path: str) -> bool:
    """
    Render the PremierLeagueTable composition with the given standings data.

    Args:
        teams_raw:   List of dicts with pos/name/p/w/d/l/gd/pts
        season:      Display string, e.g. "2013–14"
        output_path: Absolute path for the output MP4

    Returns:
        True on success, False on failure.
    """
    teams = [_enrich_team(t) for t in teams_raw]
    props = {"teams": teams, "season": season}
    return _render("PremierLeagueTable", props, output_path, f"{season} standings")


def render_top_scorers(
    players_raw: list,
    season: str,
    competition: str,
    stat: str,
    output_path: str,
) -> bool:
    players = []
    for p in players_raw:
        club  = p.get("club", "")
        cfg   = TEAM_CONFIG.get(club, FALLBACK_CONFIG)
        badge = _ensure_badge(club) if club else FALLBACK_CONFIG["badgeSlug"]
        players.append({
            "pos":       p.get("pos", 0),
            "name":      p.get("name", ""),
            "club":      club,
            "badgeSlug": badge,
            "clubColor": cfg["color"],
            "goals":     p.get("goals", 0),
            "assists":   p.get("assists", 0),
            "apps":      p.get("apps", 0),
        })
    comp_id = "TopScorersTable" if stat == "goals" else "TopAssistsTable"
    label   = "Goals" if stat == "goals" else "Assists"
    props   = {"players": players, "season": season, "competition": competition,
               "statLabel": label, "statKey": stat}
    return _render(comp_id, props, output_path, f"{label} table {season}")


def render_player_stats(data: dict, output_path: str) -> bool:
    club  = data.get("club", "")
    cfg   = TEAM_CONFIG.get(club, FALLBACK_CONFIG)
    badge = _ensure_badge(club) if club else data.get("badgeSlug", FALLBACK_CONFIG["badgeSlug"])
    props = {
        "playerName":      data.get("playerName", ""),
        "club":            club,
        "season":          data.get("season", ""),
        "competition":     data.get("competition", "Premier League"),
        "badgeSlug":       badge,
        "clubColor":       data.get("clubColor", cfg["color"]),
        "playerImageSlug": data.get("playerImageSlug", ""),
        "stats":           data.get("stats", []),
    }
    return _render("PlayerStats", props, output_path, f"PlayerStats {props['playerName']}")


def render_match_result(data: dict, output_path: str) -> bool:
    for team_key, badge_key, color_key in [
        ("homeTeam", "homeBadgeSlug", "homeColor"),
        ("awayTeam", "awayBadgeSlug", "awayColor"),
    ]:
        club = data.get(team_key, "")
        if club:
            cfg              = TEAM_CONFIG.get(club, FALLBACK_CONFIG)
            data[badge_key]  = _ensure_badge(club)
            data[color_key]  = data.get(color_key) or cfg["color"]
    return _render("MatchResult", data, output_path,
                   f"MatchResult {data.get('homeTeam')} v {data.get('awayTeam')}")


def render_transfer(data: dict, output_path: str) -> bool:
    for club_key, badge_key, color_key in [
        ("fromClub", "fromBadgeSlug", "fromColor"),
        ("toClub",   "toBadgeSlug",   "toColor"),
    ]:
        club = data.get(club_key, "")
        if club:
            cfg             = TEAM_CONFIG.get(club, FALLBACK_CONFIG)
            data[badge_key] = _ensure_badge(club)
            data[color_key] = data.get(color_key) or cfg["color"]
    return _render("TransferAnnouncement", data, output_path, f"Transfer {data.get('playerName')}")


def render_trophy(data: dict, output_path: str) -> bool:
    club = data.get("clubName", "")
    if club:
        cfg               = TEAM_CONFIG.get(club, FALLBACK_CONFIG)
        data["badgeSlug"] = _ensure_badge(club)
        data["clubColor"] = data.get("clubColor") or cfg["color"]
    return _render("TrophyGraphic", data, output_path, f"Trophy {data.get('trophyName')}")


def render_career_timeline(data: dict, output_path: str) -> bool:
    for ev in data.get("events", []):
        club = ev.get("club", "")
        if club:
            ev["badgeSlug"] = _ensure_badge(club)
            if not ev.get("clubColor"):
                ev["clubColor"] = TEAM_CONFIG.get(club, FALLBACK_CONFIG)["color"]
    return _render("CareerTimeline", data, output_path, f"Timeline {data.get('playerName')}")


def render_season_comparison(data: dict, output_path: str) -> bool:
    for slot in ("playerA", "playerB"):
        p = data.get(slot, {})
        club = p.get("club", "")
        if club:
            if not p.get("badgeSlug"):
                p["badgeSlug"] = _ensure_badge(club)
            if not p.get("color"):
                p["color"] = TEAM_CONFIG.get(club, FALLBACK_CONFIG)["color"]
        data[slot] = p
    label = f"{data.get('playerA', {}).get('name', '')} vs {data.get('playerB', {}).get('name', '')}"
    return _render("SeasonComparison", data, output_path, f"Comparison {label}")


def render_team_lineup(data: dict, output_path: str) -> bool:
    team = data.get("teamName", "")
    if team:
        cfg               = TEAM_CONFIG.get(team, FALLBACK_CONFIG)
        data["badgeSlug"] = _ensure_badge(team)
        data["teamColor"] = data.get("teamColor") or cfg["color"]
    return _render("TeamLineup", data, output_path, f"Lineup {team}")


def render_disciplinary_record(data: dict, output_path: str) -> bool:
    # Enrich any incidents missing badge/color enrichment
    for inc in data.get("incidents", []):
        club = inc.get("club", "")
        if club and (not inc.get("badgeSlug") or inc.get("badgeSlug") == "premier-league.svg"):
            inc["badgeSlug"] = _ensure_badge(club)
        if club and not inc.get("clubColor"):
            inc["clubColor"] = TEAM_CONFIG.get(club, FALLBACK_CONFIG)["color"]
    return _render("DisciplinaryRecord", data, output_path,
                   f"DisciplinaryRecord {data.get('playerName')}")


def render_quote_card(data: dict, output_path: str) -> bool:
    return _render("QuoteCard", data, output_path,
                   f"QuoteCard {data.get('attribution', '')}")


# ── hero-style templates ──────────────────────────────────────────────────

def render_hero_statbars(data: dict, output_path: str) -> bool:
    return _render("HeroStatBars", data, output_path, f"HeroStatBars {data.get('title')}")

def render_hero_formrun(data: dict, output_path: str) -> bool:
    return _render("HeroFormRun", data, output_path, f"HeroFormRun {data.get('teamName')}")

def render_hero_tactical(data: dict, output_path: str) -> bool:
    return _render("HeroTactical", data, output_path, f"HeroTactical {data.get('title')}")

def render_hero_bigstat(data: dict, output_path: str) -> bool:
    return _render("HeroBigStat", data, output_path, f"HeroBigStat {data.get('unit')}")

def render_hero_leaguegraph(data: dict, output_path: str) -> bool:
    return _render("HeroLeagueGraph", data, output_path, f"HeroLeagueGraph {data.get('teamName')}")

def render_hero_transfer_record(data: dict, output_path: str) -> bool:
    return _render("HeroTransferRecord", data, output_path, f"HeroTransferRecord {data.get('title')}")

def render_hero_intro(data: dict, output_path: str) -> bool:
    return _render("HeroIntro", data, output_path, f"HeroIntro {data.get('channelName')}")

def render_hero_outro(data: dict, output_path: str) -> bool:
    return _render("HeroOutro", data, output_path, f"HeroOutro {data.get('subscribeAsk', '')[:40]}")

def render_hero_quote(data: dict, output_path: str) -> bool:
    return _render("HeroQuote", data, output_path, f"HeroQuote {data.get('attribution')}")

def render_hero_chapter(data: dict, output_path: str) -> bool:
    return _render("HeroChapterWord", data, output_path, f"HeroChapter {data.get('word')}")

def render_hero_concept(data: dict, output_path: str) -> bool:
    return _render("HeroClipCompare", data, output_path, "HeroClipCompare")

def render_hero_scatter(data: dict, output_path: str) -> bool:
    return _render("HeroScatterPlot", data, output_path, f"HeroScatter {data.get('axisXLabel')}")


def render_attacking_radar(data: dict, output_path: str) -> bool:
    return _render("AttackingRadar", data, output_path,
                   f"AttackingRadar {data.get('entityName', '')}")


def render_hero_clip_single(data: dict, output_path: str) -> bool:
    """Render HeroClipSingle — clip frame with border glow. data must include 'clip' (public-relative path)."""
    return _render("HeroClipSingle", data, output_path,
                   f"HeroClipSingle {data.get('title', data.get('label', ''))}")


def render_player_trio(data: dict, output_path: str) -> bool:
    """Render PlayerTrio — three-player editorial comparison card."""
    for player in data.get("players", []):
        club = player.get("club", "")
        if club and not player.get("badgeSlug"):
            player["badgeSlug"] = _ensure_badge(club)
        if club and not player.get("clubColor"):
            player["clubColor"] = TEAM_CONFIG.get(club, FALLBACK_CONFIG)["color"]
    return _render("PlayerTrio", data, output_path, f"PlayerTrio {data.get('title','')}")


def render_hero_shot_map(data: dict, output_path: str) -> bool:
    return _render("HeroShotMap", data, output_path,
                   f"HeroShotMap {data.get('playerName', '')}")


def render_hero_match_timeline(data: dict, output_path: str) -> bool:
    return _render("HeroMatchTimeline", data, output_path,
                   f"HeroMatchTimeline {data.get('homeTeam', '')} vs {data.get('awayTeam', '')}")


def render_hero_awards_list(data: dict, output_path: str) -> bool:
    return _render("HeroAwardsList", data, output_path,
                   f"HeroAwardsList {data.get('award', '')}")


def render_hero_comparison_radar(data: dict, output_path: str) -> bool:
    return _render("HeroComparisonRadar", data, output_path,
                   f"HeroComparisonRadar {data.get('playerA', '')} vs {data.get('playerB', '')}")


def render_hero_season_timeline(data: dict, output_path: str) -> bool:
    return _render("HeroSeasonTimeline", data, output_path,
                   f"HeroSeasonTimeline {data.get('subjectName', '')}")


def render_tournament_bracket(data: dict, output_path: str) -> bool:
    return _render("TournamentBracket", data, output_path,
                   f"TournamentBracket {data.get('title', '')} / {data.get('focusTeam', '')}")


def render_thumbnail(data: dict, output_path: str) -> bool:
    """
    Render Thumbnail composition as a PNG still image (1280×720).
    Uses Remotion's `still` command to extract a single frame.
    output_path should end in .png
    """
    props_str  = json.dumps(data)
    output_png = output_path if output_path.endswith(".png") else output_path + ".png"

    cmd = [
        "npx", "remotion", "still",
        "Thumbnail",
        output_png,
        f"--props={props_str}",
    ]
    try:
        result = subprocess.run(
            cmd, cwd=REMOTION_DIR, capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print(f"    [Remotion] ✓ Thumbnail → {output_png}")
            return True
        else:
            print(f"    [Remotion] ✗ Thumbnail failed: {(result.stderr or result.stdout)[:300]}")
            return False
    except Exception as e:
        print(f"    [Remotion] ✗ Thumbnail exception: {e}")
        return False
