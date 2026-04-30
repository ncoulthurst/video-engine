"""
Style Agent — downloads transcripts from YouTube channels and extracts writing rules.

Usage:
    python3 style_agent.py
    (prompts for channel URLs interactively)

Or import and call:
    from agents.style_agent import build_style_rules
    build_style_rules(["https://www.youtube.com/@SomeChannel", ...])
"""

import os
import re
import sys
import subprocess
import tempfile
from pathlib import Path

# Allow running standalone
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.llm_utils import ask_gemini

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STYLE_RULES_PATH = TEMPLATES_DIR / "style_rules.md"
TRANSCRIPTS_DIR = Path(__file__).parent.parent / "transcripts"
VIDEOS_PER_CHANNEL = 3


def _download_captions(channel_url: str, out_dir: Path) -> list[Path]:
    """Download auto-captions for the N most recent videos from a channel."""
    print(f"    [*] Fetching captions from: {channel_url}")
    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--sub-lang", "en",
        "--skip-download",
        "--playlist-items", f"1:{VIDEOS_PER_CHANNEL}",
        "--sub-format", "vtt",
        "--no-playlist-reverse",
        "--output", str(out_dir / "%(channel)s - %(title)s.%(ext)s"),
        "--quiet",
        "--no-warnings",
        channel_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    [!] yt-dlp error: {result.stderr[:200]}")

    vtt_files = list(out_dir.glob("*.vtt"))
    print(f"    -> Downloaded {len(vtt_files)} caption file(s)")
    return vtt_files


def _vtt_to_text(vtt_path: Path) -> str:
    """Strip VTT timestamps and formatting tags, return clean transcript text."""
    raw = vtt_path.read_text(encoding="utf-8", errors="ignore")

    # Remove WEBVTT header block
    raw = re.sub(r'^WEBVTT.*?\n\n', '', raw, flags=re.DOTALL)

    # Remove timestamp lines (00:00:00.000 --> 00:00:05.000)
    raw = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}[^\n]*\n', '', raw)

    # Remove VTT tags (<c>, </c>, <00:00:00.000>)
    raw = re.sub(r'<[^>]+>', '', raw)

    # Remove cue identifiers (lines that are just numbers)
    raw = re.sub(r'^\d+\s*$', '', raw, flags=re.MULTILINE)

    # Collapse whitespace
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    text = ' '.join(lines)

    # Deduplicate repeated phrases (auto-captions often duplicate)
    # Split into sentences and deduplicate consecutive identical chunks
    words = text.split()
    deduped = []
    i = 0
    while i < len(words):
        # Look for repeated chunks of 5+ words
        found_repeat = False
        for chunk_size in range(8, 4, -1):
            if i + chunk_size * 2 <= len(words):
                chunk = words[i:i + chunk_size]
                next_chunk = words[i + chunk_size:i + chunk_size * 2]
                if chunk == next_chunk:
                    deduped.extend(chunk)
                    i += chunk_size * 2
                    found_repeat = True
                    break
        if not found_repeat:
            deduped.append(words[i])
            i += 1

    return ' '.join(deduped)


def _analyse_transcripts(transcripts: dict[str, str]) -> str:
    """Send all transcripts to Gemini for style pattern analysis."""
    combined = ""
    for filename, text in transcripts.items():
        # Limit each transcript to ~3000 words to stay within context
        words = text.split()[:3000]
        combined += f"\n\n--- TRANSCRIPT: {filename} ---\n{' '.join(words)}"

    prompt = f"""You are a writing analyst studying YouTube football documentary scripts.

Below are transcripts from top football documentary creators. Analyse them carefully and extract concrete, actionable writing rules — the patterns that make their scripts feel human, engaging, and non-AI-generated.

TRANSCRIPTS:
{combined}

---

OUTPUT a detailed style guide with these sections:

## SENTENCE STRUCTURE PATTERNS
- What sentence lengths do they use? How do they vary rhythm?
- Do they use fragments? Rhetorical questions? How often?

## VOCABULARY & WORD CHOICE
- What kinds of words appear frequently? (concrete, specific, colloquial?)
- What words or phrases do they NEVER use?
- How do they refer to players, clubs, matches?

## HOW THEY HANDLE STATISTICS
- Do they lead with stats or bury them?
- How do they make numbers feel human rather than robotic?

## OPENINGS & HOOKS
- How do they open videos/sections?
- What techniques create immediate tension or curiosity?

## TRANSITIONS & PACING
- How do they move between topics?
- What phrases signal a shift in the story?

## TONE MARKERS
- What makes their tone feel authentic and not AI?
- How do they express opinion or emotion without being melodramatic?

## BANNED PATTERNS (things that would sound AI or generic in this niche)
- Specific phrases or constructions these creators would never use

## 10 CONCRETE RULES
List 10 specific, actionable writing rules derived from these transcripts.
Each rule should be a direct instruction the script agent can follow.

Be specific. Quote examples from the transcripts where possible. Do not be vague."""

    return ask_gemini(prompt)


def _merge_with_existing_rules(new_analysis: str) -> str:
    """Ask Gemini to merge new analysis with existing style_rules.md."""
    existing = STYLE_RULES_PATH.read_text(encoding="utf-8") if STYLE_RULES_PATH.exists() else ""

    prompt = f"""You are updating a script writing style guide for a sports YouTube documentary channel called "Frequency".

EXISTING STYLE RULES:
{existing}

NEW ANALYSIS from real creator transcripts:
{new_analysis}

---

Produce an UPDATED style_rules.md that:
1. Keeps all existing rules that are still valid
2. Adds new rules and patterns discovered from the transcripts
3. Expands the BANNED PHRASES list with any new AI/generic phrases identified
4. Keeps the same markdown structure as the existing file
5. Makes rules more specific and concrete where the transcript analysis provides evidence

Output ONLY the updated markdown file content. No preamble."""

    return ask_gemini(prompt)


def build_style_rules(channel_urls: list[str]):
    TRANSCRIPTS_DIR.mkdir(exist_ok=True)

    all_transcripts = {}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        for url in channel_urls:
            vtt_files = _download_captions(url, tmp_path)
            for vtt in vtt_files:
                text = _vtt_to_text(vtt)
                if len(text.split()) > 200:  # skip if too short (bad caption)
                    all_transcripts[vtt.stem] = text
                    # Save raw transcript for reference
                    clean_name = re.sub(r'[^\w\s-]', '', vtt.stem)[:80]
                    (TRANSCRIPTS_DIR / f"{clean_name}.txt").write_text(text, encoding="utf-8")

    if not all_transcripts:
        print("[!] No valid transcripts downloaded. Check the channel URLs.")
        return

    print(f"\n[*] Analysing {len(all_transcripts)} transcripts with Gemini...")
    analysis = _analyse_transcripts(all_transcripts)

    print("[*] Merging with existing style rules...")
    updated_rules = _merge_with_existing_rules(analysis)

    STYLE_RULES_PATH.write_text(updated_rules, encoding="utf-8")
    print(f"\n[✓] Style rules updated: {STYLE_RULES_PATH}")
    print(f"[✓] Raw transcripts saved to: {TRANSCRIPTS_DIR}/")


if __name__ == "__main__":
    print("=== Style Agent — YouTube Transcript Analyser ===")
    print(f"Will download {VIDEOS_PER_CHANNEL} videos per channel.\n")

    urls = []
    print("Paste channel URLs one at a time. Press Enter with empty input when done.")
    print("(e.g. https://www.youtube.com/@TheAthleticFC)\n")

    while True:
        url = input("Channel URL: ").strip()
        if not url:
            break
        urls.append(url)
        print(f"  Added. ({len(urls)} channel(s) queued)\n")

    if not urls:
        print("No URLs provided. Exiting.")
        sys.exit(0)

    print(f"\nProcessing {len(urls)} channel(s)...\n")
    build_style_rules(urls)
