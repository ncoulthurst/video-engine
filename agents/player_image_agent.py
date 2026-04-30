"""
Player Image Auto-Pipeline
Fetches high-quality transparent-background player PNG renders automatically.

Sources (in priority order):
1. Futwiz — transparent background PNG renders (best quality, football-specific)
2. Wikipedia Commons — fallback thumbnail image

Output: <REMOTION_PROJECT_PATH>/public/players/<slug>.png

Usage:
    from agents.player_image_agent import fetch_player_images
    results = fetch_player_images(["Luis Suárez", "Lionel Messi"], output_dir)
"""

import os
import re
import json
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

import requests

REMOTION_PROJECT = Path(
    os.environ.get(
        "REMOTION_PROJECT_PATH",
        str(Path(__file__).parent.parent.parent / "remotiontest"),
    )
).resolve()
REMOTION_PUBLIC = REMOTION_PROJECT / "public"
PLAYERS_DIR     = REMOTION_PUBLIC / "players"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _slugify(name: str) -> str:
    """Convert 'Luis Suárez' → 'luis_suarez' (ascii, lowercase, underscores)."""
    # Normalise accented characters → ascii equivalents
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = ascii_str.lower().strip()
    slug = re.sub(r"[^a-z0-9\s]", "", slug)
    slug = re.sub(r"\s+", "_", slug)
    return slug.strip("_")


def _image_exists(slug: str) -> Path | None:
    """Return path if any variant of this slug exists in players dir or public root."""
    PLAYERS_DIR.mkdir(parents=True, exist_ok=True)
    for search_dir in (PLAYERS_DIR, REMOTION_PUBLIC):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            p = search_dir / (slug + ext)
            if p.exists():
                return p
    return None


# ── Source 1: Futwiz ─────────────────────────────────────────────────────────

def _fetch_futwiz(player_name: str, slug: str) -> Path | None:
    """
    Search Futwiz for the player and download their transparent-bg face PNG.
    Futwiz search: https://www.futwiz.com/en/players?search=<name>
    Face images: https://cdn.futwiz.com/assets/img/fc25/faces/<id>.png
    """
    try:
        search_url = f"https://www.futwiz.com/en/players?search={quote_plus(player_name)}"
        resp = requests.get(search_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None

        html = resp.text

        # Extract player ID from the first player card link
        # Pattern: /en/fc25/player/<slug>/<id>  or  /en/player/<name>/<id>
        id_match = re.search(
            r'href="/en/(?:fc\d+|fifa\d*)/player/[^/]+/(\d+)"',
            html
        )
        if not id_match:
            # Try older URL format
            id_match = re.search(r'href="/en/player/[^/]+/(\d+)"', html)
        if not id_match:
            return None

        player_id = id_match.group(1)

        # Try FC25, FC24, FIFA23 face image URLs in order
        for edition in ("fc25", "fc24", "fc23", "fifa23"):
            img_url = f"https://cdn.futwiz.com/assets/img/{edition}/faces/{player_id}.png"
            img_resp = requests.get(img_url, headers=HEADERS, timeout=15)
            if img_resp.status_code == 200 and len(img_resp.content) > 5000:
                out_path = PLAYERS_DIR / f"{slug}.png"
                out_path.write_bytes(img_resp.content)
                print(f"    [Images] Futwiz ✓ {player_name} → players/{slug}.png ({len(img_resp.content)//1024}kb)")
                return out_path

    except Exception as e:
        print(f"    [Images] Futwiz error for {player_name}: {e}")
    return None


def _fetch_futwiz_direct_search(player_name: str, slug: str) -> Path | None:
    """
    Alternative futwiz approach: use their player search API endpoint.
    """
    try:
        # Futwiz has an autocomplete/search endpoint
        api_url = f"https://www.futwiz.com/en/ajaxrequest/suggestions?term={quote_plus(player_name)}&type=player"
        resp = requests.get(api_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None

        # Try to parse JSON response
        try:
            data = resp.json()
        except Exception:
            return None

        # Find first player result
        players = []
        if isinstance(data, list):
            players = data
        elif isinstance(data, dict):
            players = data.get("players", data.get("results", []))

        if not players:
            return None

        first = players[0] if isinstance(players[0], dict) else {}
        player_id = first.get("id") or first.get("baseId") or first.get("player_id")
        if not player_id:
            return None

        for edition in ("fc25", "fc24", "fc23", "fifa23"):
            img_url = f"https://cdn.futwiz.com/assets/img/{edition}/faces/{player_id}.png"
            img_resp = requests.get(img_url, headers=HEADERS, timeout=15)
            if img_resp.status_code == 200 and len(img_resp.content) > 5000:
                out_path = PLAYERS_DIR / f"{slug}.png"
                out_path.write_bytes(img_resp.content)
                print(f"    [Images] Futwiz API ✓ {player_name} → players/{slug}.png")
                return out_path

    except Exception as e:
        print(f"    [Images] Futwiz API error for {player_name}: {e}")
    return None


# ── Source 2: Wikipedia Commons ───────────────────────────────────────────────

def _fetch_wikipedia(player_name: str, slug: str) -> Path | None:
    """
    Fetch player thumbnail from Wikipedia's pageimages API.
    Returns a decent-quality photo (not transparent bg, but better than nothing).
    """
    try:
        api_url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=query&titles={quote_plus(player_name)}"
            "&prop=pageimages&format=json&pithumbsize=600&redirects=1"
        )
        resp = requests.get(api_url, headers=HEADERS, timeout=12)
        if resp.status_code != 200:
            return None

        pages = resp.json().get("query", {}).get("pages", {})
        page  = next(iter(pages.values()), {})
        thumb = page.get("thumbnail", {}).get("source", "")
        if not thumb:
            return None

        img_resp = requests.get(thumb, headers=HEADERS, timeout=15)
        if img_resp.status_code != 200 or len(img_resp.content) < 5000:
            return None

        # Determine extension from URL
        ext = ".jpg"
        if ".png" in thumb.lower():
            ext = ".png"
        elif ".webp" in thumb.lower():
            ext = ".webp"

        out_path = PLAYERS_DIR / f"{slug}{ext}"
        out_path.write_bytes(img_resp.content)
        print(f"    [Images] Wikipedia ✓ {player_name} → players/{slug}{ext} ({len(img_resp.content)//1024}kb)")
        return out_path

    except Exception as e:
        print(f"    [Images] Wikipedia error for {player_name}: {e}")
    return None


# ── Main entry point ──────────────────────────────────────────────────────────

def fetch_player_image(player_name: str, force: bool = False) -> dict:
    """
    Fetch image for a single player.
    Returns { player_name, slug, path, source, status }
    """
    slug = _slugify(player_name)
    PLAYERS_DIR.mkdir(parents=True, exist_ok=True)

    # Skip if already exists (unless force=True)
    if not force:
        existing = _image_exists(slug)
        if existing:
            return {
                "player_name": player_name,
                "slug": slug,
                "path": str(existing),
                "source": "existing",
                "status": "skipped",
            }

    # Try futwiz main search first
    path = _fetch_futwiz(player_name, slug)

    # Futwiz API fallback
    if not path:
        time.sleep(0.5)  # be polite
        path = _fetch_futwiz_direct_search(player_name, slug)

    # Wikipedia fallback
    if not path:
        time.sleep(0.5)
        path = _fetch_wikipedia(player_name, slug)

    if path:
        return {
            "player_name": player_name,
            "slug": slug,
            "path": str(path),
            "source": "futwiz" if "futwiz" in str(path).lower() else "wikipedia",
            "status": "fetched",
        }
    else:
        print(f"    [Images] ✗ Could not fetch image for: {player_name}")
        return {
            "player_name": player_name,
            "slug": slug,
            "path": None,
            "source": None,
            "status": "failed",
        }


def fetch_player_images(player_names: list[str], output_dir: str = None, force: bool = False) -> list[dict]:
    """
    Fetch images for a list of player names.
    Writes an image_manifest.json to output_dir if provided.
    Returns list of result dicts.
    """
    print(f"[*] Player Image Agent — fetching {len(player_names)} player image(s)...")
    results = []
    for name in player_names:
        if not name or not name.strip():
            continue
        result = fetch_player_image(name.strip(), force=force)
        results.append(result)
        time.sleep(0.3)  # rate limiting

    fetched = sum(1 for r in results if r["status"] == "fetched")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed  = sum(1 for r in results if r["status"] == "failed")
    print(f"    -> Images: {fetched} fetched, {skipped} already existed, {failed} failed")

    if output_dir:
        manifest_path = Path(output_dir) / "image_manifest.json"
        manifest_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    return results


def fetch_images_for_project(output_dir: str) -> list[dict]:
    """
    Auto-detect which player images are needed for a project by reading:
    - player_data.json (main subject)
    - script_draft.md (PLAYER TRIO, PLAYER RADAR tags)
    - production_sheet.md (PLAYER IMAGES NEEDED section)

    Then fetch any missing images.
    Skipped unless context.md contains 'AUTO_FETCH_IMAGES: true'.
    """
    out = Path(output_dir)
    ctx = out / "context.md"
    if not (ctx.exists() and "AUTO_FETCH_IMAGES: true" in ctx.read_text()):
        print("    [PlayerImageAgent] Skipped — set AUTO_FETCH_IMAGES: true in context.md to enable auto-download")
        return []
    needed_names = set()

    # 1. Main subject from player_data.json
    player_data_path = out / "player_data.json"
    if player_data_path.exists():
        try:
            pd = json.loads(player_data_path.read_text())
            if "name" in pd:
                needed_names.add(pd["name"])
            # Also clubs' key players sometimes listed
        except Exception:
            pass

    # 2. From script tags — extract PLAYER TRIO and PLAYER RADAR players
    script_path = out / "script_draft.md"
    if script_path.exists():
        script_text = script_path.read_text()
        # PLAYER TRIO: the debate, Player1 vs Player2 vs Player3
        for m in re.finditer(r'\[PLAYER TRIO:[^\]]+\]', script_text, re.IGNORECASE):
            content = m.group(0)
            # Extract after the comma separator (past the title)
            players_part = re.sub(r'^\[PLAYER TRIO:\s*[^,]+,\s*', '', content, flags=re.IGNORECASE).rstrip(']')
            for p in re.split(r'\s+vs\s+', players_part, flags=re.IGNORECASE):
                name = p.strip()
                if name and len(name.split()) >= 2:
                    needed_names.add(name)
        # PLAYER RADAR: Player, Club, Competition, Season
        for m in re.finditer(r'\[PLAYER RADAR:\s*([^,\]]+)', script_text, re.IGNORECASE):
            name = m.group(1).strip()
            if name and len(name.split()) >= 2:
                needed_names.add(name)

    # 3. From production_sheet.md PLAYER IMAGES NEEDED section
    sheet_path = out / "production_sheet.md"
    if sheet_path.exists():
        sheet_text = sheet_path.read_text()
        in_images_section = False
        for line in sheet_text.splitlines():
            if "PLAYER IMAGES" in line.upper():
                in_images_section = True
                continue
            if in_images_section:
                if line.startswith("="):
                    break
                m = re.match(r'\s+([A-Z][a-zA-Z\s]+?)\s{2,}', line)
                if m:
                    needed_names.add(m.group(1).strip())

    if not needed_names:
        print("    [Images] No player names found to fetch images for")
        return []

    print(f"    [Images] Players needed: {', '.join(sorted(needed_names))}")
    return fetch_player_images(list(needed_names), output_dir)


if __name__ == "__main__":
    import sys
    names = sys.argv[1:] if len(sys.argv) > 1 else ["Luis Suárez", "Lionel Messi"]
    results = fetch_player_images(names)
    for r in results:
        print(f"  {r['player_name']}: {r['status']} → {r.get('path', 'N/A')}")
