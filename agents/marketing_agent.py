import json
import re
from pathlib import Path
from utils.llm_utils import ask_llm
from utils.file_utils import save_text, load_text


def _slugify(name: str) -> str:
    """'Luis Suárez' → 'luis_suarez'"""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = ascii_str.lower().strip()
    slug = re.sub(r"[^a-z0-9\s]", "", slug)
    slug = re.sub(r"\s+", "_", slug)
    return slug.strip("_")


def _generate_thumbnail_props(entity: str, output_dir: str, accent_color: str = "#C9A84C") -> dict:
    """
    Generate props for the Thumbnail Remotion composition.
    Reads context.md for the video title and key hook, picks the player image slug.
    """
    out = Path(output_dir)

    # Read context for title + brief
    context = ""
    context_path = out / "context.md"
    if context_path.exists():
        context = context_path.read_text(encoding="utf-8")

    # Extract title
    video_title = entity
    title_m = re.search(r'##\s*Title\s*\n+([^\n#]+)', context)
    if title_m:
        video_title = title_m.group(1).strip()

    # Ask LLM to generate a punchy thumbnail hook from the title
    hook_prompt = f"""You are writing a YouTube thumbnail for a football documentary.
VIDEO TITLE: {video_title}
SUBJECT: {entity}

Generate:
1. HOOK: 2-3 word punchy hook line for the thumbnail, ALL CAPS, ideally with line breaks using \\n (e.g. "THE GENIUS\\n& MADNESS" or "31 GOALS\\n1 OBSESSION")
2. SUB: A 40-character max subtitle (player name + club + season)
3. STAT: A 25-character max stat pill (e.g. "31 goals · 1 season")

Return JSON only: {{"hook": "...", "sub": "...", "stat": "..."}}"""

    hook_data = {"hook": video_title.upper()[:20], "sub": entity, "stat": ""}
    try:
        raw = ask_llm(hook_prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        hook_data = json.loads(raw.strip())
    except Exception:
        pass

    # Determine player image slug — check what's available
    slug = _slugify(entity.split(",")[0].strip())  # first entity if multiple
    player_image = slug

    # Check public dir for closest match
    remotion_public = Path(__file__).parent.parent.parent / "remotiontest" / "public"
    players_dir = remotion_public / "players"
    for search_dir in (players_dir, remotion_public):
        if search_dir.exists():
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                if (search_dir / (slug + ext)).exists():
                    player_image = f"players/{slug}" if search_dir == players_dir else slug
                    break

    return {
        "hookLine":    hook_data.get("hook", video_title.upper()[:20]),
        "subLine":     hook_data.get("sub", entity),
        "statPill":    hook_data.get("stat", ""),
        "playerImage": player_image,
        "accentColor": accent_color,
        "channelName": "Frequency",
        "bgColor":     "#0a0a0a",
        "layout":      "player-right",
        "vignette":    True,
    }


def generate_marketing(entity, output_dir):
    print(f"[*] Marketing Agent creating packaging for: {entity}...")
    out = Path(output_dir)

    # 1. Generate text marketing assets
    context_text = ""
    context_path = out / "context.md"
    if context_path.exists():
        context_text = context_path.read_text(encoding="utf-8")[:2000]

    prompt = f"""Generate YouTube marketing assets for a football documentary about {entity}.

CONTEXT:
{context_text}

Produce:
1. TITLES (5 options) — high-CTR YouTube titles, under 70 chars each
2. THUMBNAIL CONCEPTS (3) — visual descriptions (what to show, what text, colour scheme)
3. SHORTS CAPTIONS (3) — punchy 1-2 line captions for vertical Shorts clips, hook in first 3 words
4. TAGS — 15 relevant YouTube search tags (comma-separated)
5. DESCRIPTION — 100-word YouTube description with watch hook, key moments, channel CTA

Format clearly with headers for each section."""

    res = ask_llm(prompt)
    save_text(f"{output_dir}/marketing_assets.md", res)

    # 2. Generate and render thumbnail
    print(f"    -> Generating thumbnail...")
    try:
        # Determine accent colour from player's primary club
        accent = "#C9A84C"  # default gold
        if "liverpool" in context_text.lower():
            accent = "#C8102E"
        elif "barcelona" in context_text.lower() or "barca" in context_text.lower():
            accent = "#004D98"
        elif "manchester city" in context_text.lower():
            accent = "#6CABDD"
        elif "manchester united" in context_text.lower():
            accent = "#DA291C"
        elif "arsenal" in context_text.lower():
            accent = "#EF0107"
        elif "chelsea" in context_text.lower():
            accent = "#034694"
        elif "real madrid" in context_text.lower():
            accent = "#FFD700"
        elif "ajax" in context_text.lower():
            accent = "#D2122E"

        thumb_props = _generate_thumbnail_props(entity, output_dir, accent_color=accent)

        # Save props so user can re-render with tweaks
        thumb_props_path = out / "thumbnail_props.json"
        thumb_props_path.write_text(json.dumps(thumb_props, indent=2, ensure_ascii=False))

        thumb_out = str(out / "thumbnail.png")
        from utils.remotion_renderer import render_thumbnail
        ok = render_thumbnail(thumb_props, thumb_out)
        if ok:
            print(f"    -> Saved: thumbnail.png (1280×720)")
        else:
            print(f"    -> Thumbnail render failed — props saved to thumbnail_props.json for manual render")
    except Exception as e:
        print(f"    -> Thumbnail generation error: {e}")

    return res
