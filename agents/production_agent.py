"""
Production Agent — parses the script and outputs a human-readable production sheet.

Separates:
  - CLIP tags  → clips_needed.json + listed in production_sheet.md (user sources these)
  - GRAPHIC tags → listed in production_sheet.md (auto-rendered by graphics agent)
"""

import re
import json
import urllib.parse
from pathlib import Path
from utils.file_utils import load_text, save_text

# Greedy match on description so commas inside the description text don't break parsing.
# The duration marker (\d+)s is unambiguous enough to anchor the boundary.
_CLIP_SINGLE_RE  = re.compile(r'\[CLIP SINGLE:\s*(.+),\s*(\d+)s,\s*([^\]]+)\]', re.IGNORECASE)
_CLIP_COMPARE_RE = re.compile(r'\[CLIP COMPARE:\s*(.+)\|(.+),\s*(\d+)s,\s*([^|]+)\|([^\]]+)\]', re.IGNORECASE)

_TRANSITION_RE = re.compile(r'\[TRANSITION:\s*([^\]]+)\]', re.IGNORECASE)

# All auto-rendered infographic tags
_GRAPHIC_TAGS_RE = re.compile(
    r'\[(STANDINGS TABLE|TOP SCORERS|TOP ASSISTS|PLAYER STATS|MATCH RESULT|TRANSFER|TROPHY|'
    r'CAREER TIMELINE|SEASON COMPARISON|TEAM LINEUP|DISCIPLINARY RECORD|QUOTE CARD|'
    r'HERO STAT BARS|HERO FORM RUN|HERO TACTICAL|HERO BIG STAT|'
    r'HERO LEAGUE GRAPH|HERO TRANSFER RECORD|HERO INTRO|HERO QUOTE|'
    r'HERO CHAPTER|HERO CONCEPT|HERO SCATTER|ATTACKING RADAR)'
    r':\s*([^\]]+)\]',
    re.IGNORECASE
)

# Act header to know which section each tag is in
_ACT_HEADER_RE = re.compile(r'^###\s+(.+)$', re.MULTILINE)


def _split_by_act(script: str) -> list[tuple[str, str]]:
    """Return list of (act_name, act_text) tuples."""
    headers = list(_ACT_HEADER_RE.finditer(script))
    acts = []
    for i, match in enumerate(headers):
        start = match.end()
        end   = headers[i + 1].start() if i + 1 < len(headers) else len(script)
        acts.append((match.group(1).strip(), script[start:end]))
    return acts


def generate_production_sheet(output_dir: str):
    print("[*] Production Agent parsing script...")

    script = load_text(f"{output_dir}/script_draft.md")
    if not script:
        print("    [!] No script_draft.md found.")
        return

    acts         = _split_by_act(script)
    clips_needed = []
    clip_id      = 1

    sheet_lines  = []
    sheet_lines.append("# PRODUCTION SHEET\n")
    sheet_lines.append("=" * 60)
    sheet_lines.append("")
    sheet_lines.append("## CLIPS NEEDED  (source these manually, drop into remotiontest/public/clips/)")
    sheet_lines.append("")

    for act_name, act_text in acts:
        act_clips = []

        # CLIP SINGLE
        for m in _CLIP_SINGLE_RE.finditer(act_text):
            cid  = f"clip_{clip_id:03d}"
            desc = m.group(1).strip()
            dur  = int(m.group(2))
            lbl  = m.group(3).strip()
            act_clips.append({
                "id":       cid,
                "type":     "single",
                "act":      act_name,
                "description": desc,
                "label":    lbl,
                "duration": dur,
                "files":    [f"clips/{cid}.mp4"],
                "youtube_search": "https://www.youtube.com/results?search_query=" + urllib.parse.quote(desc),
            })
            clip_id += 1

        # CLIP COMPARE
        for m in _CLIP_COMPARE_RE.finditer(act_text):
            cid        = f"clip_{clip_id:03d}"
            desc_left  = m.group(1).strip()
            desc_right = m.group(2).strip()
            dur        = int(m.group(3))
            lbl_left   = m.group(4).strip()
            lbl_right  = m.group(5).strip()
            act_clips.append({
                "id":            cid,
                "type":          "compare",
                "act":           act_name,
                "description":   f"LEFT: {desc_left} | RIGHT: {desc_right}",
                "label":         f"{lbl_left} | {lbl_right}",
                "duration":      dur,
                "files":         [f"clips/{cid}_left.mp4", f"clips/{cid}_right.mp4"],
                "youtube_search_left":  "https://www.youtube.com/results?search_query=" + urllib.parse.quote(desc_left),
                "youtube_search_right": "https://www.youtube.com/results?search_query=" + urllib.parse.quote(desc_right),
            })
            clip_id += 1

        if act_clips:
            sheet_lines.append(f"### {act_name}")
            sheet_lines.append("")
            for clip in act_clips:
                if clip["type"] == "single":
                    yt_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(clip['description'])
                    sheet_lines.append(f"  {clip['id']}  [{clip['duration']}s]")
                    sheet_lines.append(f"  Scene:  {clip['description']}")
                    sheet_lines.append(f"  Label:  {clip['label']}")
                    sheet_lines.append(f"  Search: {yt_url}")
                    sheet_lines.append(f"  Drop:   public/clips/{clip['id']}.mp4")
                else:
                    desc_left  = clip['description'].split('| RIGHT:')[0].replace('LEFT: ', '').strip()
                    desc_right = clip['description'].split('| RIGHT:')[1].strip()
                    yt_left  = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(desc_left)
                    yt_right = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(desc_right)
                    sheet_lines.append(f"  {clip['id']}  [{clip['duration']}s]  COMPARE")
                    sheet_lines.append(f"  Left:   {desc_left}")
                    sheet_lines.append(f"  Search: {yt_left}")
                    sheet_lines.append(f"  Right:  {desc_right}")
                    sheet_lines.append(f"  Search: {yt_right}")
                    sheet_lines.append(f"  Labels: {clip['label']}")
                    sheet_lines.append(f"  Drop:   public/clips/{clip['id']}_left.mp4  +  public/clips/{clip['id']}_right.mp4")
                sheet_lines.append("")
            clips_needed.extend(act_clips)

    if not clips_needed:
        sheet_lines.append("  (no clip tags found in script — re-run pipeline)")
        sheet_lines.append("")

    # Auto-rendered graphics section
    sheet_lines.append("=" * 60)
    sheet_lines.append("")
    sheet_lines.append("## AUTO-RENDERED GRAPHICS  (no action needed)")
    sheet_lines.append("")

    for act_name, act_text in acts:
        act_graphics = _GRAPHIC_TAGS_RE.findall(act_text)
        if act_graphics:
            sheet_lines.append(f"### {act_name}")
            for tag_type, tag_content in act_graphics:
                sheet_lines.append(f"  [{tag_type.upper()}: {tag_content.strip()}]")
            sheet_lines.append("")

    # ── Assembly transitions ───────────────────────────────────────────────────
    all_transitions = []
    for act_name, act_text in acts:
        for m in _TRANSITION_RE.finditer(act_text):
            all_transitions.append({"act": act_name, "type": m.group(1).strip()})

    if all_transitions:
        sheet_lines.append("=" * 60)
        sheet_lines.append("")
        sheet_lines.append("## ASSEMBLY TRANSITIONS  (apply these in VideoSequence.tsx)")
        sheet_lines.append("")
        for t in all_transitions:
            sheet_lines.append(f"  [{t['type'].upper()}]  at start of {t['act']}")
        sheet_lines.append("")

    # ── Player images needed ──────────────────────────────────────────────────
    import re as _re
    # Extract player names from PLAYER TRIO and PLAYER RADAR tags in script
    _PLAYER_IMG_RE = _re.compile(
        r'\[(?:PLAYER TRIO|PLAYER RADAR|HERO QUOTE|HERO BIG STAT|SEASON COMPARISON):\s*([^,\]]+)', re.IGNORECASE
    )
    images_needed = set()
    for _, act_text in acts:
        for m in _PLAYER_IMG_RE.finditer(act_text):
            raw = m.group(1).strip()
            if ' vs ' in raw:
                for name in raw.split(' vs '):
                    images_needed.add(name.strip())
            elif '|' in raw:
                for name in raw.split('|'):
                    images_needed.add(name.strip())
            else:
                images_needed.add(raw)

    from pathlib import Path as _Path
    public_dir = _Path(__file__).parent.parent.parent / "remotiontest" / "public"
    available_slugs = set()
    if public_dir.exists():
        for f in public_dir.iterdir():
            if f.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
                available_slugs.add(f.stem.lower())

    if images_needed:
        sheet_lines.append("=" * 60)
        sheet_lines.append("")
        sheet_lines.append("## PLAYER IMAGES  (drop PNG into /remotiontest/public/)")
        sheet_lines.append("  Name the file: firstname_lastname.png  (lowercase, underscore)")
        sheet_lines.append("")
        for name in sorted(images_needed):
            slug = name.lower().replace(" ", "_").replace(".", "").replace("'", "")
            status = "✓ found" if slug in available_slugs else "✗ MISSING — source needed"
            sheet_lines.append(f"  {name:<30}  {slug}.png   {status}")
        sheet_lines.append("")

    # Summary
    sheet_lines.append("=" * 60)
    sheet_lines.append(f"  Total clips to source:  {len(clips_needed)}")
    total_duration = sum(c["duration"] for c in clips_needed)
    sheet_lines.append(f"  Total footage needed:   ~{total_duration}s ({total_duration // 60}m {total_duration % 60}s)")
    sheet_lines.append("")
    sheet_lines.append("Once all clips are in the clips/ folder, run:")
    sheet_lines.append("  python3 assemble.py  (coming soon)")

    production_sheet = "\n".join(sheet_lines)
    save_text(f"{output_dir}/production_sheet.md", production_sheet)
    print(f"    -> Saved: production_sheet.md")

    # Save structured JSON for the assembler
    # Also ensure the public/clips drop folder exists in the Remotion project
    public_clips = Path(__file__).parent.parent.parent.parent / "remotiontest" / "public" / "clips"
    public_clips.mkdir(parents=True, exist_ok=True)

    with open(f"{output_dir}/clips_needed.json", "w") as f:
        json.dump(clips_needed, f, indent=2)
    print(f"    -> Saved: clips_needed.json ({len(clips_needed)} clips)")
    print(f"    -> Drop footage into: {public_clips}")

    # Music plan — select a track per act if music library exists
    try:
        from agents.music_selector import build_music_plan
        build_music_plan(script, output_dir)
    except Exception as e:
        print(f"    [music] Skipped — {e}")

    return clips_needed
