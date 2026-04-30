from utils.llm_utils import ask_gemini, ask_llm
from utils.file_utils import save_text, load_text
import os, json, re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# ── Track C — Narration Rhythm Layer (NRL) ──────────────────────────────────
# Imports module-local to keep Track C compile-isolated. Both modules ship as
# part of Track C; if missing the post-processor degrades to a no-op.
try:
    from templates.narration_profile import NARRATION_PROFILE
    from templates.pronunciation     import CANONICAL_SPELLINGS
except Exception:
    NARRATION_PROFILE = {
        "sentence_length": {"min_words": 4, "max_words": 28, "target_avg": 14},
        "rhythm": {"max_consecutive_long_sentences": 2, "min_short_sentences_per_act": 3,
                   "comma_breath_max_per_sentence": 3},
        "forbidden_tokens": [],
        "forbidden_phrases": [],
        "broadcast_wpm": 156,
        "length_tolerance": 0.20,
    }
    CANONICAL_SPELLINGS = set()


# Strip [TAG: ...] visual tags so sentence-shape validation runs on prose only.
_TAG_STRIP_RE = re.compile(r"\[[^\[\]]+?\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _strip_tags_for_validation(text: str) -> str:
    return _TAG_STRIP_RE.sub("", text)


def narration_post_processor(text: str, scene_meta: dict) -> tuple[str, list[str]]:
    """Strip forbidden tokens/phrases, validate sentence rhythm + per-scene
    word-count length, return (cleaned_text, violations).

    scene_meta must include at minimum {"duration": int_seconds}. Track C does
    not assume any other field — callers may pass classification/data_kind but
    they are not required.
    """
    violations: list[str] = []

    # Hard strip: timing markers + editing instructions (regex)
    for pattern in NARRATION_PROFILE.get("forbidden_tokens", []):
        if re.search(pattern, text, re.I):
            violations.append(f"forbidden_token:{pattern}")
            text = re.sub(pattern, "", text, flags=re.I)

    # Hard strip: visual-reactive phrases (substring, case-insensitive)
    for phrase in NARRATION_PROFILE.get("forbidden_phrases", []):
        if phrase.lower() in text.lower():
            violations.append(f"forbidden_phrase:{phrase}")
            text = re.sub(re.escape(phrase), "", text, flags=re.I)

    # Sentence-shape validation runs on prose only (tags excluded)
    prose = _strip_tags_for_validation(text).strip()
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(prose) if s.strip()]
    word_counts = [len(s.split()) for s in sentences]

    bounds = NARRATION_PROFILE["sentence_length"]
    rhythm = NARRATION_PROFILE["rhythm"]

    long_run = 0
    fragment_run = 0
    short_count = 0
    max_frag_run = rhythm.get("max_consecutive_fragments", 3)
    for wc in word_counts:
        if wc > bounds["max_words"]:
            violations.append(f"sentence_too_long:{wc}")
        if 0 < wc < bounds["min_words"]:
            violations.append(f"sentence_too_short:{wc}")
        if wc <= 8:
            short_count += 1
        # Fragment run cap — fragments (≤3 words) are intentional but a wall reads as choppy
        fragment_run = fragment_run + 1 if wc <= 3 else 0
        if fragment_run > max_frag_run:
            violations.append(f"rhythm:fragment_run:{fragment_run}")
        long_run = long_run + 1 if wc > 20 else 0
        if long_run > rhythm["max_consecutive_long_sentences"]:
            violations.append("rhythm:too_many_consecutive_longs")

    if word_counts and short_count < rhythm["min_short_sentences_per_act"]:
        violations.append(
            f"rhythm:not_enough_short_beats:{short_count}/{rhythm['min_short_sentences_per_act']}"
        )

    # Comma-breath check: max N commas per sentence
    cb_max = rhythm["comma_breath_max_per_sentence"]
    for s in sentences:
        if s.count(",") > cb_max:
            violations.append(f"rhythm:too_many_commas:{s.count(',')}")

    # Per-scene length targeting: actual ≈ duration_s × wpm / 60, ±tolerance
    duration = scene_meta.get("duration") if isinstance(scene_meta, dict) else None
    if isinstance(duration, (int, float)) and duration > 0:
        wpm = NARRATION_PROFILE.get("broadcast_wpm", 156)
        tol = NARRATION_PROFILE.get("length_tolerance", 0.20)
        expected = duration * (wpm / 60.0)
        actual   = sum(word_counts)
        if actual < expected * (1 - tol):
            violations.append(f"length_underrun:{actual}/{expected:.0f}")
        if actual > expected * (1 + tol):
            violations.append(f"length_overrun:{actual}/{expected:.0f}")

    return text.strip(), violations


def _summarise_act(text: str) -> dict:
    """Inter-act rhythm summary. Returns {avg_sentence_words, ended_on}."""
    prose = _strip_tags_for_validation(text).strip()
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(prose) if s.strip()]
    wcs = [len(s.split()) for s in sentences]
    if not wcs:
        return {"avg_sentence_words": 0.0, "ended_on": "short"}
    return {
        "avg_sentence_words": round(sum(wcs) / len(wcs), 1),
        "ended_on":           "long" if wcs[-1] > 20 else "short",
    }


# Hard violations that justify a regeneration retry. Soft violations
# (forbidden_token, forbidden_phrase) are stripped inline; rhythm/length
# require the LLM to write differently.
_HARD_VIOLATION_PREFIXES = ("sentence_too_long", "sentence_too_short", "rhythm:", "length_")


def _has_hard_violations(violations: list[str]) -> bool:
    return any(v.startswith(_HARD_VIOLATION_PREFIXES) for v in violations)
# ── End Track C NRL ─────────────────────────────────────────────────────────

def _load_template(name):
    path = TEMPLATES_DIR / name
    return path.read_text() if path.exists() else ""


# ── Thematic detection ────────────────────────────────────────────────────────

_THEMATIC_KEYWORDS = [
    "history of", "story of", "decline of", "rise of", "death of", "end of",
    "why did", "how football", "evolution of", "identity", "tactical revolution",
    "playmakers", "pressing", "system", "philosophy", "culture of", "era of",
    "golden generation", "lost generation", "what happened to", "where did",
    "the fall of", "the rise of", "the problem with", "why england", "why brazil",
    "why france", "why spain", "why germany", "why italy", "why argentina",
]

def _is_thematic(entity: str, context: str) -> bool:
    """Return True if this is a thematic/systemic doc, not a player biography."""
    combined = (entity + " " + context[:600]).lower()
    return any(kw in combined for kw in _THEMATIC_KEYWORDS)


# ── Dynamic word count ────────────────────────────────────────────────────────

_ACT_OUTLINE_KEYS = {
    # act name fragment → outline section header fragment
    "COLD OPEN":    "COLD OPEN SCENE",
    "ORIGINS":      "ACT 1",
    "THE MYTH":     "ACT 1",
    "RISE":         "ACT 2",
    "THE SHIFT":    "ACT 2",
    "PEAK":         "ACT 3",
    "THE BREAK":    "ACT 3",
    "DEFINING":     "ACT 4",
    "CONSEQUENCE":  "ACT 4",
    "REDEMPTION":   "ACT 5",
    "THE QUESTION": "ACT 5",
}

_WORDS_PER_MINUTE = 130  # documentary narrator pace


def _act_depth_guidance(act_name: str, outline: str) -> str:
    """
    Return depth guidance for the LLM based on how many events are in this act.
    Returns a minimum-floor string and per-event guidance — no ceiling.

    The goal is retention, not a word count. Each event should breathe.
    Cold open is one cinematic moment; regular acts cover their assigned events fully.
    """
    import re as _re

    if "COLD OPEN" in act_name.upper():
        return (
            "DEPTH: Cold open = one cinematic moment + thesis hook. "
            "Minimum 60 seconds of narration (~130 words). "
            "Do not pad — write only what serves the hook."
        )

    section_key = next(
        (v for k, v in _ACT_OUTLINE_KEYS.items() if k in act_name.upper()), None
    )
    if not section_key:
        return (
            "DEPTH: Cover each assigned event fully. "
            "Minimum 90 seconds of narration per major event (~200 words each). "
            "Do not pad — stop when the story is done."
        )

    pattern = _re.compile(
        rf'({_re.escape(section_key)}[^\n]*)\n(.*?)(?=\n[A-Z][A-Z ]+[:\-—]|\Z)',
        _re.DOTALL | _re.IGNORECASE,
    )
    m = pattern.search(outline)
    section_text = m.group(2).strip() if m else ""

    content_lines = [
        ln for ln in section_text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    event_count = max(1, len(content_lines))
    floor_words = event_count * 150  # ~70 seconds per event minimum

    return (
        f"DEPTH: This act has {event_count} event(s). "
        f"Give each event at least 60-90 seconds of narration (minimum floor: ~{floor_words} words total). "
        "Cover the story with the depth it deserves — do not pad to hit a number, "
        "but do not rush either. Every event in the outline must be felt, not just named."
    )


ACTS = [
    {
        "name": "COLD OPEN (0:00 - 1:30)",
        "instruction": (
            "STRUCTURE — the cold open MUST be in this exact order:\n"
            "  1. HOOK FIRST: open with the most cinematic moment + a sharp narrated hook (2-4 sentences). "
            "Name the exact date, stadium, opponent, score where applicable. Put the viewer inside the moment. "
            "No 'This is the story of…'. One central thesis line. End the hook with a beat that makes stopping impossible.\n"
            "  2. AFTER the hook lands, drop the branding card. On its own line:\n"
            "     [HERO INTRO: <exact video title from the Director's Brief — copy it verbatim>]\n"
            "  3. IMMEDIATELY AFTER the [HERO INTRO] tag, write ONE narration line (and only one) introducing the channel + thesis:\n"
            "     EXAMPLE pattern (do NOT copy verbatim — write a fresh, topic-specific version each time):\n"
            "     \"This is Frequency. And this is why <topic-specific framing>.\"\n"
            "     The line must mention 'Frequency' once. The second sentence must be a fresh, topic-aware reframing — not a hardcoded template.\n"
            "  4. After the branding line, continue the cold open with one or two more clip beats that deepen the hook before the act break.\n\n"
            "COLD OPEN RULES:\n"
            "- HOOK BEFORE BRANDING — never put 'This is Frequency' as the very first words. The viewer has to be hooked first.\n"
            "- Every clip must be a specific, searchable YouTube moment — no 'iconic goal celebration', no generic descriptions\n"
            "- The HERO INTRO tag must use the EXACT title — do not paraphrase, do not add 'DOCUMENTARY:' prefix\n"
            "- The branding line is ONE sentence with 'Frequency' in it; vary phrasing every video"
        ),
    },
    {
        "name": "ACT 1 — ORIGINS",
        "instruction": (
            "Begin this act with [TRANSITION: letterbox] on a new line to mark the chapter break. "
            "Cover only the events assigned to this act — early life and youth. "
            "Include where they came from, what drove them, and the first warning signs of the flaw that will define them. "
            "End with a tension that pulls the viewer into Act 2."
        ),
    },
    {
        "name": "ACT 2 — RISE",
        "instruction": (
            "Begin this act with [TRANSITION: push] to mark the shift from origins to rise. "
            "Cover only the events assigned to this act — the breakthrough and early professional years. "
            "Include one specific match with date and result, one precise statistic, one human moment from the anecdotes. "
            "The warning sign from Act 1 must resurface here, bigger. "
            "End with a hook signalling the peak is coming — but so is the darkness."
        ),
    },
    {
        "name": "ACT 3 — PEAK",
        "instruction": (
            "Begin this act with [TRANSITION: letterbox] to signal the peak. "
            "MANDATORY REHOOK — the very first narration sentence after the transition must be a standalone rehook: "
            "a bold restatement of the central tension that re-engages anyone who drifted. Max 12 words. "
            "Example: 'This is where it mattered most. And this is where it cracked.' "
            "Cover only the two iconic moments assigned to this act in the outline — no more. "
            "OPTIONAL — PLAYER TRIO: Only include this when peer comparison genuinely advances the narrative "
            "(e.g. the player's GOAT debate IS the act's argument, or a same-era rival shaped their peak). "
            "Skip it for rise-and-fall biographies, single-club epics, thematic docs, or any story where peer comparison would derail the focus. "
            "When you DO include one: Format: [PLAYER TRIO: the debate, [Subject Full Name] vs [Peer1 Full Name] vs [Peer2 Full Name]] "
            "THREE INDIVIDUAL PLAYERS ONLY — do NOT write 'MSN vs other trios' or group names — name each person. "
            "Example for a Liverpool/Barcelona player: [PLAYER TRIO: the debate, Luis Suárez vs Lionel Messi vs Cristiano Ronaldo] "
            "MANDATORY: Include [PLAYER RADAR: Player Name, Club, Competition, Season] for the peak season analysis. "
            "MANDATORY: Include [HERO STAT BARS: goals per 90, Subject vs Rival Year, Club, Rival Club] to compare with a specific rival. "
            "MANDATORY: For each specific named match in this act, include [TEAM LINEUP: Team N-N-N vs Opposition, DD Mon YYYY]. "
            "Structure: Iconic Moment 1 → [TEAM LINEUP] of that match → Iconic Moment 2 → [HERO STAT BARS] → [PLAYER RADAR] → pivot to decline. "
            "Include peak season stats from the data. "
            "The final line must be the PIVOT sentence from the outline. "
            "Leave the viewer dreading Act 4."
        ),
    },
    {
        "name": "ACT 4 — THE DEFINING EVENT",
        "instruction": (
            "Begin this act with [TRANSITION: grain] to signal chaos and darkness. "
            "Cover only the single turning point assigned to this act. "
            "Describe it in real time — viewer inside the moment. "
            "Name the date, opponent, score, stadium. "
            "Only use facts confirmed in the research. Do not invent a single detail."
        ),
    },
    {
        "name": "ACT 5 — REDEMPTION AND LEGACY",
        "instruction": (
            "Begin this act with [TRANSITION: paper] to signal the fading of the era. "
            "Cover only the decline, later career, and legacy assigned to this act. "
            "Be honest about flaws and greatness equally — no sentimentality. "
            "The final paragraph is the Brutal Truth. "
            "The very last sentence must land like a full stop on a career — declarative, specific, unforgettable. "
            "Not: 'He reminded everyone what football could be.' "
            "Instead something like: 'For a few brief years, football belonged to him. The game has been chasing that joy ever since.' "
            "No question mark. No platitude."
        ),
    },
]


THEMATIC_ACTS = [
    {
        "name": "COLD OPEN (0:00 - 1:30)",
        "instruction": (
            "STRUCTURE — the cold open MUST be in this exact order:\n"
            "  1. HOOK FIRST: open with the single moment that encapsulates the thesis — the most cinematic scene that shows "
            "the contrast at its sharpest. This is one specific instant, not a historical summary. State the thesis as one "
            "declarative sentence. End the hook on a beat that makes the question impossible to ignore. (2-4 sentences total.)\n"
            "  2. AFTER the hook lands, drop the branding card. On its own line:\n"
            "     [HERO INTRO: <exact video title from the Director's Brief — copy it verbatim>]\n"
            "  3. IMMEDIATELY AFTER the [HERO INTRO] tag, write ONE narration line introducing the channel + thesis:\n"
            "     EXAMPLE pattern (do NOT copy verbatim — write a fresh, topic-specific version each time):\n"
            "     \"This is Frequency. And this is why <topic-specific framing>.\"\n"
            "     Must mention 'Frequency' once. The second sentence must be a fresh, topic-aware reframing — not a hardcoded template.\n"
            "  4. Optionally one more clip beat after the branding line before the act break.\n\n"
            "RULES:\n"
            "- HOOK BEFORE BRANDING — never put 'This is Frequency' as the very first words. The viewer has to be hooked first."
        ),
    },
    {
        "name": "ACT 1 — THE MYTH",
        "instruction": (
            "Begin with [TRANSITION: letterbox] to establish the era.\n\n"
            "This act shows what was true — the golden reality before the shift. "
            "This is the era people remember, the standard against which everything else is measured. "
            "Show the players, the tactics, the culture. Name real dates, real matches, real moments. "
            "Use CLIP COMPARE to show what this era looked like vs what came after. "
            "Use HERO TACTICAL for positional freedom, movement patterns, heatmaps — show WHERE players "
            "operated, not just statistics. "
            "By the end: the viewer understands what was lost and why it mattered. "
            "Close with the first hint that this world was fragile."
        ),
    },
    {
        "name": "ACT 2 — THE SHIFT",
        "instruction": (
            "Begin with [TRANSITION: push] to signal forward momentum — something is changing.\n\n"
            "This act shows when the world started to change. Not the collapse — the first signs. "
            "Name the exact forces: a tournament loss, a tactical revolution abroad, a policy change, "
            "a generation that didn't come through. Be specific — real years, real events. "
            "Use CLIP COMPARE to contrast old and new approaches in the same moment or role. "
            "Use HERO TACTICAL to show the structural shift — the pressing shape replacing the playmaker role, "
            "the formation that moved the creative player out. "
            "The ANCHOR CHARACTER must appear here — introduce them as the person caught between both worlds. "
            "End with the point of no return: the moment the shift became a break."
        ),
    },
    {
        "name": "ACT 3 — THE BREAK",
        "instruction": (
            "Begin with [TRANSITION: grain] to signal the system breaking down.\n\n"
            "MANDATORY REHOOK — the very first narration sentence after the transition must be a standalone rehook: "
            "a bold restatement of the central tension that re-engages anyone who drifted. Max 12 words. "
            "Example: 'This is the moment it stopped being about talent.' "
            "This is where the old way was abandoned or collapsed. Name the specific failure: "
            "a tournament, a tactical decision, a structural change. This is not slow decline — "
            "this is the moment the gap became visible and undeniable. "
            "Use PLAYER TRIO: compare three players from the same nation, same position, different eras — "
            "show what changed. All three must be from the same nation. "
            "Use HERO TACTICAL for before/after heatmaps — the playmaker's positional freedom vs "
            "the defensive midfielder's box. Show it, don't just state it. "
            "The ANCHOR CHARACTER reaches their peak here — they are the last example of what was. "
            "MANDATORY: Include [PLAYER RADAR: Player, Club, Competition, Season] at the anchor's peak. "
            "End with: the contrast loop sentence — stated clearly by the narrator."
        ),
    },
    {
        "name": "ACT 4 — THE CONSEQUENCE",
        "instruction": (
            "Begin with [TRANSITION: dataLine] to signal clinical, data-driven reality.\n\n"
            "This is the current world. Show what football looks like now in this area — "
            "the numbers, the names, the results. Be specific. Compare era to era using real stats. "
            "Use HERO STAT BARS but only for comparisons that can be understood immediately — "
            "prefer spatial/movement data over counting stats. "
            "The ANCHOR CHARACTER is now in their final chapter — show where they are in this new world. "
            "Echo the contrast loop: state it again, now with evidence behind it. "
            "End with: what has been permanently lost, stated as a fact, not an opinion."
        ),
    },
    {
        "name": "ACT 5 — THE QUESTION",
        "instruction": (
            "Begin with [TRANSITION: paper] to signal reflection and open space.\n\n"
            "This act does NOT conclude. It asks. "
            "Show the current state: are there any signs of return? Who are the outliers? What would it take? "
            "The ANCHOR CHARACTER gets their closing line here — as the last of their kind, what does their "
            "existence mean? "
            "Echo the contrast loop one final time — but now it lands differently because the viewer has seen the evidence. "
            "The final line is the closing provocation — an open question, not an answer. "
            "Not: 'Football has changed forever.' "
            "Instead: 'Neymar is 32. After him, who?' or 'The last playmaker is running out of time. The question isn't whether Brazil lost them — it's whether anyone is coming to bring them back.' "
            "No conclusion. No platitude. The question hangs in the air."
        ),
    },
]


def _generate_outline(entities, research, analysis, anecdotes, user_context, is_comp):
    """Generate a subject-specific event map before writing any acts.
    This prevents overlap by assigning concrete events to each act."""

    prompt = f"""You are a documentary producer creating a strict event map. Every event appears in EXACTLY ONE act — zero overlap.

SUBJECT: {entities}
DIRECTOR'S BRIEF: {user_context}

RESEARCH:
{research[:4000]}

ANECDOTES:
{anecdotes[:2000]}

RULES:
- Each emotional beat (e.g. family tragedy, personal loss) appears in ONE act only. Mark it: [EMOTIONAL BEAT — use once, do not repeat in other acts]
- The PEAK act must contain EXACTLY TWO iconic moments, then immediately pivot to the start of decline.
- Search for and include these types of moments if confirmed in research: stadium standing ovations from rival fans, viral commercials or media moments, iconic World Cup moments, legal troubles or personal crises.
- The COLD OPEN must be the single most cinematic moment in the subject's career — not a summary, not early life.
- ANCHOR CHARACTER: For thematic/national identity docs, the anchor must be the BRIDGE player — active or recently retired, exists in both the golden era and modern football. The anchor creates forward tension ("what comes after them?") not nostalgia. Do NOT choose the player who represents only the peak era.
- SAME-NATIONALITY COMPARISONS: All player-to-player comparisons must be within the same national team. For a Brazil documentary, compare Brazilians to Brazilians (same role, different era). Never compare a Brazilian to an Argentine or European player as the primary contrast.

Return as plain text in this exact format:

COLD OPEN SCENE:
[One specific cinematic moment — the most iconic scene in the career]

CENTRAL THESIS:
[One sentence — the paradox that drives the whole documentary]

ACT 1 — ORIGINS EVENTS:
[Bullet list — early life only. Mark any emotional beats with [ONCE ONLY]]

ACT 2 — RISE EVENTS:
[Bullet list — early professional years and breakthrough. No peak club material.]

ACT 3 — PEAK EVENTS (two moments maximum, then decline begins):
- ICONIC MOMENT 1: [most famous peak moment with date and opponent]
- ICONIC MOMENT 2: [second peak moment]
- PIVOT: [the exact moment the decline begins — one sentence]

ACT 4 — DEFINING EVENT:
[The single turning point — one match, one decision, one incident. Date, opponent, context.]

ACT 5 — LEGACY EVENTS:
[Decline, later clubs, any legal/personal issues, retirement, lasting impact]

Be specific. Real dates, clubs, match names from the research only. Do not invent."""

    return ask_llm(prompt)


def _build_retention_injection(brief: dict, act_name: str) -> str:
    """Build the per-act retention mechanic block to inject into the prompt."""
    cf  = brief.get("contrast_frame", {})
    anc = brief.get("anchor_character", {})
    rfs = brief.get("act_reframes", [])

    loop = cf.get("loop_sentence", "")
    past = cf.get("past_label", "")
    present = cf.get("present_label", "")
    anchor_name = anc.get("name", "")
    anchor_frame = anc.get("framing", "")
    closing_line = anc.get("closing_line", "")
    closing_q = brief.get("closing_question", "")

    # Find the reframe for this act
    act_reframe = next((r for r in rfs if r.get("act", "").lower() in act_name.lower()), None)

    lines = ["RETENTION MECHANICS — mandatory structural constraints:"]
    if loop:
        lines.append(f"\nCORE CONTRAST LOOP: \"{loop}\"")
        if past and present:
            lines.append(f"  Past state: \"{past}\"  →  Present state: \"{present}\"")
        lines.append("  This loop sentence MUST be echoed in different words at the end of this act.")
    if anchor_name:
        lines.append(f"\nANCHOR CHARACTER: {anchor_name}")
        lines.append(f"  Framing: {anchor_frame}")
        if "act 5" in act_name.lower() or "question" in act_name.lower():
            if closing_line:
                lines.append(f"  This act's closing line about them: \"{closing_line}\"")
            if closing_q:
                lines.append(f"  CLOSING PROVOCATION (final line of the video): \"{closing_q}\"")
    if act_reframe:
        lines.append(f"\nTHIS ACT'S PURPOSE: Answer — \"{act_reframe.get('question', '')}\"")
        lines.append(f"  Payoff: {act_reframe.get('payoff', '')}")

    return "\n".join(lines)


def _write_act(act, entities, outline, style_rules, visual_grammar, retention_patterns,
               research, analysis, anecdotes, user_context, is_comp, previous_ending,
               video_title=None, retention_brief=None, is_thematic=False, covered_points=None,
               prev_act_summary: dict | None = None,
               storyboard_context: str = ""):

    word_target = _act_depth_guidance(act['name'], outline)

    instruction = act['instruction']
    if video_title and act['name'].startswith('COLD OPEN'):
        instruction = instruction.replace(
            '<exact video title from the Director\'s Brief — copy it verbatim>',
            video_title
        )
        instruction = instruction.replace(
            '<exact video title for opening line>',
            video_title
        )

    type_note = (
        "This is a COMPARISON documentary. Maintain the head-to-head tension throughout."
        if is_comp else
        "This is a single-subject DOCUMENTARY. Every sentence serves the central thesis."
    )

    continuity = ""
    if previous_ending:
        continuity = f"""
CONTINUITY — this is how the previous act ended (pick up from here, do not repeat it):
...{previous_ending}
"""
    already_covered = ""
    if covered_points:
        already_covered = (
            "\n\nIDEAS ALREADY COVERED IN PREVIOUS ACTS — DO NOT REPEAT ANY OF THESE:\n"
            + "\n".join(covered_points)
            + "\n\nEach idea above must appear ZERO times in this act. If a concept was covered, advance past it.\n"
        )

    prompt = f"""You are a senior scriptwriter for Sky Sports Films writing one act of a twenty-minute football documentary.

SUBJECT: {entities}
{type_note}

DIRECTOR'S BRIEF:
{user_context}

STORY OUTLINE — your strict event map (each event appears in ONE act only):
{outline}

{('STORYBOARD (Track D ground truth — narration must align with these locked scenes):' + chr(10) + storyboard_context) if storyboard_context else ''}

YOUR ACT: {act['name']}
{word_target}
{instruction}

Only write the events assigned to {act['name']} in the outline above. Do not stray into other acts' events.

CHRONOLOGICAL DISCIPLINE:
- All events within this act must be in strict chronological order (earliest → latest)
- International matches (World Cups, tournaments) must be placed in the act covering that calendar year — never in a separate act
- Controversies (biting, racism, handballs, dives) MUST be embedded in the act/club era when they occurred — never in their own separate chapter
- A player's ban at Liverpool must appear in the Liverpool act, not after it

STYLE RULES (absolute):
{style_rules}

VISUAL TAGS — mandatory rules:
{visual_grammar}

USE THESE TAGS EXACTLY AS FOLLOWS (match the moment to the template):

TRANSITIONS — use at every act break (one line, on its own):
- Between acts: [TRANSITION: letterbox]  ← cinematic chapter break (black bar crush)
- Into fast-paced sections: [TRANSITION: push]  ← energetic forward momentum
- Into chaotic/dark moments: [TRANSITION: grain]  ← noise burst, high energy
- Into reflective/legacy sections: [TRANSITION: paper]  ← elegant dissolve
- For stats/data moments: [TRANSITION: dataLine]  ← accent wipe

CONTINUOUS-WORLD CAMERA — DEFAULT TO worldPan WITHIN AN ACT:
The video should feel like ONE camera moving through ONE world for the duration of an act, not a stack of slides cutting between unrelated frames. Within a single act, transitions between scenes should default to worldPan — a horizontal pan that carries the camera from one scene to the next, treating the whole act as one wide canvas.

When to emit [TRANSITION: worldPan] explicitly:
- Between two graphic scenes in the same act when they share a subject or moment but live in different visual styles (e.g. CAREER TIMELINE → TEAM LINEUP, HERO FORM RUN → HERO BIG STAT). The camera pans sideways from one to the other.
- Between a CLIP and a graphic in the same act (CLIP SINGLE → HERO STAT BARS, HERO BIG STAT → CLIP SINGLE). Clips and graphics still belong to the same scene world; the camera just continues its move.
- Between any two same-act scenes that are NOT explicitly cutting to a new world.

worldPan is the DEFAULT same-act transition. Use it as much as possible. Only fall back to push / letterbox / grain / paper / dataLine / flash when the narrative GENUINELY jumps to a different world (new act, dramatic tonal shift, scene-break punctuation). Cuts within an act break the documentary feel.

If you don't emit a TRANSITION tag between two same-act scenes, the engine will assign worldPan automatically — but emitting it explicitly when the spatial relationship is meaningful (e.g. "we now pan to the rival's side") gives the viewer a clearer sense of one continuous space.

SAME-WORLD EVOLUTION — even tighter than worldPan, for true continuations:
When two or more HERO-style graphics appear consecutively within the same act (same background colour, same subject), they must NOT cut between each other like separate slides. Instead they must EVOLVE:
- Place [TRANSITION: evolve] between them (NOT letterbox/push)
- The evolve transition holds the outgoing scene fully visible, then lets it drop as new content emerges from the same space — making it feel like ONE continuous scene, not two clips
- This is the reference standard: the opening timeline does not "cut" to the formation — the formation EMERGES from the same red canvas
- Requirement: consecutive scenes using evolve MUST share the same bgColor in their props
- Examples of correct evolve usage:
  [HERO SEASON TIMELINE: ...]
  [TRANSITION: evolve]
  [HERO BIG STAT: ...]       ← same bgColor, same visual world, emerges in the same space

  [CAREER TIMELINE: Player - Focus: Liverpool]
  [TRANSITION: evolve]
  [PLAYER RADAR: Player, Liverpool, Premier League, 2013/14]   ← same dark bg, evolves

  [HERO FORM RUN: Liverpool, title run-in]
  [TRANSITION: evolve]
  [HERO FORM RUN: Manchester City, title run-in]  ← same bg, side-by-side evolution

Use [TRANSITION: evolve] ANY time two infographic scenes share the same background and are about the same subject/moment within an act. Use cuts (letterbox/push) only when the narrative genuinely jumps to a different topic, time, or visual world.

CLIP TAGS — use these the most. They make the video feel real.
- When referencing ANY specific moment, goal, reaction, celebration, arrival, controversy or emotional beat that a viewer could find footage of: use [CLIP SINGLE: description, Xs, label]
  → description must be specific enough to find on YouTube (who, what, when, where)
  → duration: 6s–10s depending on moment weight
  → label: date or short caption shown on screen
  → USE MINIMUM 2 PER ACT — these are the backbone of the video
- When contrasting two moments or how a player performed in two contexts: use [CLIP COMPARE: left description | right description, Xs, left label | right label]

CAREER TIMELINE — use SPARINGLY (max 2 per full script, not per act):
- Only emit when the entire career arc is relevant — NOT every time a player is mentioned
- [CAREER TIMELINE: Player Name - Focus: Club Name]
  → Use once for the subject's peak club; a second time only if a major career shift is the narrative focus
  → DO NOT emit for supporting players, rivals, or players mentioned in passing
  → For thematic / non-single-subject documentaries: emit ZERO CAREER TIMELINE tags. Never emit "[CAREER TIMELINE: None ...]" or any variant with no real player name.

TOURNAMENT BRACKET — the knockout-run graphic (use max 1 per script):
- When an act centres on a team's run through a knockout tournament (World Cup / Euros), emit a single [TOURNAMENT BRACKET: Tournament Name, Focus: Team Name] tag INSTEAD of a chain of MATCH RESULT cards.
- Supported tournaments (exact names, case insensitive): "FIFA World Cup 2002", "FIFA World Cup 2022", "Euro 2024". If the tournament isn't on this list, fall back to individual MATCH RESULT tags per knockout match.
- Examples (illustrative format — do NOT copy these specific tournaments unless they are genuinely the focus of the act):
  [TOURNAMENT BRACKET: FIFA World Cup 2022, Focus: Argentina]
  [TOURNAMENT BRACKET: Euro 2024, Focus: Spain]

TEAM LINEUP — use for every key match. This is underused and should appear in every act:
- Every iconic match must have a lineup: [TEAM LINEUP: Team N-N-N vs Opposition, DD Mon YYYY]
  → Use for: the peak club's title-deciding matches, debut matches, defining moments, any match you name specifically
  → Example: [TEAM LINEUP: Liverpool 4-3-3 vs Arsenal, 09 Feb 2014]
  → Example: [TEAM LINEUP: Uruguay 4-4-2 vs Ghana, 02 Jul 2010]
  → Every named match in the script = one lineup graphic

PLAYER TRIO — use for peer comparisons:
- When comparing the subject to peers or establishing their place in history: [PLAYER TRIO: comparison title, Player1 vs Player2 vs Player3]
  → Use ONCE in ACT 3 (peak) to show where the subject ranks vs the elite of their era
  → Example: [PLAYER TRIO: the debate, Luis Suárez vs Lionel Messi vs Cristiano Ronaldo]
  → Example: [PLAYER TRIO: Premier League's finest, Suárez vs Rooney vs van Persie]

TAG FORMAT RULES — get these exactly right, wrong format = broken render:
- [TOP SCORERS: Premier League 2013/14]  ← competition + season ONLY. NEVER add "Golden Boot" or award names.
- [TOP ASSISTS: La Liga 2015/16]  ← same format
- [PLAYER STATS: Luis Suárez 2013/14]  ← for season stat cards (goals, assists, apps). NOT for awards/trophies.
- [HERO BIG STAT: 31, goals, in a single Premier League season, Luis Suárez · 2013/14]  ← for records, awards, milestones
- [MATCH RESULT: Liverpool 4-0 Everton, 28 Oct 2012]  ← actual score, actual date
- [HERO STAT BARS: goals per 90, Suárez vs Ronaldo 2013/14, Liverpool, Real Madrid]  ← 4 parts: title, subtitle, team A, team B
- [HERO FORM RUN: Liverpool, the 2013/14 title run-in]  ← team name, descriptive label

INFOGRAPHIC TAGS:
- League table or where a club finished: [STANDINGS TABLE: Competition YYYY/YY - Top 6 Final Standings]
- Player's goal tally for a season: [TOP SCORERS: Competition YYYY/YY]  — competition + season ONLY
- Player's assist tally: [TOP ASSISTS: Competition YYYY/YY]
- Season stats card (goals/assists/apps): [PLAYER STATS: Player Name YYYY/YY]  — max 1 per script; only for the subject's definitive season
- Records, awards, trophy counts: [HERO BIG STAT: Stat, Unit, Label, Context]
- Specific match score: [MATCH RESULT: Home N-N Away, DD Mon YYYY]
- Two players compared in the same season: [SEASON COMPARISON: PlayerA vs PlayerB, Competition YYYY/YY]
  ★ MANDATORY when the Director's Brief asks to compare the subject to peers — emit ONE tag per matchup.
  e.g. for "compare Suárez to Messi then Ronaldo" emit BOTH:
    [SEASON COMPARISON: Luis Suárez vs Lionel Messi, La Liga 2015/16]
    [SEASON COMPARISON: Luis Suárez vs Cristiano Ronaldo, La Liga 2015/16]
  Place these in ACT 3 (peak). If a PLAYER TRIO scene also exists, place SEASON COMPARISON tags immediately after it; otherwise just place them in ACT 3.
- Biting, bans or disciplinary history: [DISCIPLINARY RECORD: Player Name]
- Direct quote: [HERO QUOTE: "Quote text" — Attribution, Context]  ← prefer over QUOTE CARD
- Tactic/pressing system: [HERO TACTICAL: Concept | Team | Formation | Description]  — max 1 per script; only when the tactic itself is the narrative focus, not every time pressing is mentioned
- Head-to-head stat comparison: [HERO STAT BARS: Title, Subtitle, Team A, Team B]
- TITLE RACES — MANDATORY: show both teams' runs:
  [HERO FORM RUN: Team1, label for the run]
  [HERO FORM RUN: Team2, label for the same period]
- League position across a season: [HERO LEAGUE GRAPH: Team Name, Season]
- Record-breaking transfer context: [HERO TRANSFER RECORD: Title, Subtitle]
- Historical transfer fee timeline: [HERO TRANSFER RECORD: Title, Subtitle]
  → Use when narrating a record-breaking transfer

{"CLIP COMPARE is the primary evolution tool for this documentary — use it whenever showing how something changed between eras. CLIP SINGLE is for a single specific moment; CLIP COMPARE is for the thesis." if is_thematic else "CLIP SINGLE is the backbone of this video — it must appear for EVERY specific moment you narrate. Every goal, every save, every reaction, every celebration, every arrival, every controversy, every emotional beat must have a [CLIP SINGLE: ...] tag."} Aim for 3-5 clip tags per act. Across the full script: minimum 20 clip tags.

{"SPATIAL OVER STATISTICAL: For comparisons between eras, prefer HERO TACTICAL (heatmaps, movement paths, positional freedom) over HERO STAT BARS. A heatmap showing where a player roamed tells the story instantly — a stat requires interpretation. Use HERO STAT BARS only when the number itself is the point (e.g. 0 playmakers in the squad)." if is_thematic else ""}

{"SAME-NATIONALITY COMPARISONS: All player comparisons must be within the same national team — same role, different era. Never compare across nationalities for national identity documentaries." if is_thematic else ""}

{_build_retention_injection(retention_brief, act["name"]) if retention_brief else ""}

RETENTION:
{retention_patterns}

RESEARCH (FACT BANK — use ONLY for sourcing names, dates, numbers, and confirmed events):
{research[:3000]}

RESEARCH-USE RULES (mandatory — violation = scene rejected):
- The research is a FACT BANK, NOT narration source material. Never paraphrase, summarise, or recite Wikipedia-style background.
- BANNED CONTENT IN NARRATION: colonial history, founding years (e.g. 1500/1822/1888), independence dates, political regimes, GDP/economy stats, "founding member of UN/G20/BRICS", census data, geographic descriptions, broad cultural overviews. None of this serves a football documentary.
- If a beat needs a fact (a date, a score, a manager name, a transfer fee), pull the SPECIFIC fact from research and weave it into a beat. Do NOT pad acts with research text just because it's there.
- If you cannot find a fact in research that directly serves the act's thesis, write less. Short and on-thesis beats every documentary; padded research dumps make it feel AI-generated.

STATS:
{analysis[:1500]}

ANECDOTES:
{anecdotes[:2000]}
{continuity}{already_covered}
FACT DISCIPLINE: Every statistic, date, quote, and match result must appear in the research above. If uncertain, leave it out. No fabrication.

OUTPUT FORMAT RULES — these are absolute:
- Write narration as plain prose. NO "NARRATION:" prefix on any line — ever. Just the words the narrator speaks.
- Do NOT write headers like "Act 1:" or "COLD OPEN:" — tags and narration only.
- Sentence length: 8–14 words. One idea per sentence.
- No filler phrases: "the beautiful game", "this wasn't just", "the world was shifting" — cut them.

OPENING LINE (cold open and each act's first narration sentence):
- Must be a bold claim or a contradiction. Max 12 words. No soft phrasing.
- BAD: "The elegant dance of Brazilian flair once captivated the world."
- GOOD: "Brazil stopped producing playmakers. The game took them first."
- BAD: "For many years, the No.10 was the most important player on the pitch."
- GOOD: "There used to be a position in football called the playmaker. It's gone."

PUNCHLINES — mandatory rhythm rule:
- Every 120–150 words of narration must contain at least one standalone punchline sentence of ≤10 words.
- These are mini-conclusions that land before the viewer loses attention.
- Examples: "That space no longer exists." / "He never played for Brazil again." / "Street football didn't disappear. It was sold."
- Do not bury punchlines inside longer sentences — they must stand alone on their own line.

VISUAL-FIRST WRITING — prefer visual statements over abstract explanation:
- BAD: "The game became more structured and tactically disciplined."
- GOOD: "The middle of the pitch became crowded. No space for a free eight."
- BAD: "Brazil's development model changed significantly over this period."
- GOOD: "At 16, they were on a plane. The academies were waiting."
- Every abstract idea must have a concrete visual equivalent. Write what the camera sees.

NARRATION RULES (Track C — violation = scene rejected; spelled-out enforcement runs after generation):
1. Sentences are 4–28 words. Target average 14.
2. Maximum 2 consecutive sentences over 20 words.
3. Each act must contain at least 3 sentences ≤8 words (stress beats).
4. Maximum 3 commas per sentence.
5. Present tense. No second-person ("you", "your").
6. FIRST-PERSON PERSONALITY (mandatory rhythm break — 1 to 2 times per act, NOT every paragraph):
   Drop in a first-person beat that sounds like a real person remembered something or has an opinion.
   GOOD: "I remember the first time I saw Ronaldinho's no-look pass — it didn't feel like football, it felt like jazz."
   GOOD: "I'd argue this was the moment Brazil lost it. Not 7-1. This."
   GOOD: "What we saw next was the system absorbing the artist."
   BAD: third-person omniscient throughout (sounds like Wikipedia, kills retention).
   BAD: first-person every sentence (sounds like a vlog, breaks documentary tone).
   Use sparingly to add personality and prevent the AI-essay feel.
7. VOCABULARY REGISTER — write like a smart 20-year-old football fan in a pub, not a Guardian long-read.
   Target voice: someone who knows the game inside out and can explain it without sounding clever for the sake of it.
   BANNED essay/thesaurus words (do NOT use these or anything similar):
     intrinsically, irrevocably, unparalleled, unprecedented, unequivocally, ostensibly,
     intrinsic, paradigm, zenith, nadir, juxtaposition, dichotomy, paradigm shift,
     wellspring, lineage, embodied, encapsulated, exemplified, manifested,
     uninhibited, sublime (used as adjective for skill), ineffable,
     symphony of, tapestry of, the very fabric of, the essence of, the soul of,
     a beacon of hope, a stark reminder, a poignant moment, a profound shift,
     once again, however (use "but"), nevertheless, therefore (use "so"),
     in the realm of, in the world of, the landscape of.
   PREFER plain words: "always" not "in perpetuity", "stopped" not "ceased", "showed" not "exemplified",
     "big" not "monumental", "huge" not "seismic", "mood" not "zeitgeist", "peak" not "apotheosis".
   COUNTRY NAMES — always use the formal name when referring to a national team. Never use casual labels:
     - "Soviet Union" / "USSR" — never "the Soviets"
     - "United States" — never "the Yanks"
     - "West Germany" / "East Germany" pre-1990; "Germany" post-1990. Never "the Germans" / "the Krauts" etc.
     - For anachronisms: name the country as it existed at the time of the event (Brazil vs Soviet Union, 1982 — not Brazil vs Russia).
   PLAYER INTROS — do NOT use tagline-style introductions where the player's nickname is bolted on as a fragment:
     BAD: "Then came Zico. The White Pelé." / "And Sócrates. The Doctor."
     GOOD: "Zico arrived next — a number 10 they were already calling the white Pelé."
     GOOD: "Then Sócrates, the doctor of midfield, taller and stranger than the rest."
     The nickname can land in the same sentence as the introduction, woven in. Don't drop it as a one-word reverent fragment.
   GOOD: "Brazil stopped producing playmakers. Not slowly. All at once."
   BAD: "Brazil's lineage of unparalleled creative geniuses irrevocably ceased to manifest."
8. POETIC FLOURISH CAP — at most ONE poetic/lyrical line per act. Prose-led otherwise.
   A poetic line is a metaphor or rhythmic flourish that breaks the matter-of-fact register.
   GOOD (1 per act): "The pitches got smaller and the kids got smaller with them."
   BAD: poetic line every paragraph — feels like a TED talk, kills documentary credibility.
   Default to declarative beats. Earn the poetic line by surrounding it with plain ones.
9. PUNCTUATION FOR BREATH — this script is VOICED, not read silently. Punctuation literally controls the narrator's breath.
   - Prefer FULL STOPS over commas. Two short sentences read better than one long one.
   - Use EM-DASHES (—) for asides and stress beats. They cue the voice to drop or pause.
   - Sentence FRAGMENTS are allowed and encouraged for rhythm: "Not slowly. All at once."
   - Place a hard period before an emotional turn so the line lands: "Brazil had everything. Then they didn't."
   - Avoid comma-splice run-ons. If a sentence has 3 commas, it almost certainly should be 2 or 3 sentences.
   - Read every line aloud in your head. If you can't say it in one breath without rushing, break it.
   GOOD: "It started in the favelas. Tight pitches. Bare feet. Endless space to invent."
   BAD: "It started in the favelas, where the tight pitches and bare feet and endless space allowed players to invent freely without constraint."
10. Never reference visuals: no "as you can see", "this chart", "on screen", "look at this", "as shown".
11. Never emit timing markers: no "0.4s break", "[BEAT]", "[PAUSE]", "(pause)", "cut to", "smash cut".
12. Use canonical spelling for: {sorted(CANONICAL_SPELLINGS)}. Do not invent phonetic spellings — pronunciation is handled downstream.
13. {('Open with a sentence ≤10 words to reset breath after a long previous-act ending.' if (prev_act_summary or {}).get('ended_on') == 'long' else 'Vary opening sentence length naturally.')}
14. Narration leads the graphic. Write as if the viewer has not yet seen the image.
{('PREVIOUS ACT RHYTHM: avg ' + str((prev_act_summary or {}).get('avg_sentence_words', 0)) + ' words/sentence, ended on a ' + ((prev_act_summary or {}).get('ended_on') or 'short') + ' sentence.') if prev_act_summary else ''}

Write {act['name']} now. Visual tags and narration prose only.
"""

    # Track C: bounded retry-on-violation. Strips forbidden tokens/phrases inline,
    # regenerates up to 2× on hard rhythm/length violations.
    scene_meta = {"duration": act.get("duration", 0)}
    text = ask_gemini(prompt)
    if not text:
        return text
    cleaned, violations = narration_post_processor(text, scene_meta)
    retries = 0
    while violations and _has_hard_violations(violations) and retries < 2:
        retry_prompt = (
            prompt
            + "\n\nPREVIOUS ATTEMPT FAILED narration validation: "
            + ", ".join(v for v in violations if v.startswith(_HARD_VIOLATION_PREFIXES))
            + ". Regenerate observing rules 1–10."
        )
        text = ask_gemini(retry_prompt)
        if not text:
            break
        cleaned, violations = narration_post_processor(text, scene_meta)
        retries += 1
    if violations:
        print(f"    [NRL] {act.get('name','act')}: residual violations after {retries} retries: {violations}")
    return cleaned


def generate_script(entities, output_dir, storyboard=None, storyboard_context: str = ""):
    """Track D: accepts optional `storyboard` (list[dict]) + `storyboard_context`
    (pre-built prompt block from orchestrator._build_storyboard_context). When
    provided, the storyboard is injected as ground truth into each act's prompt
    so narration aligns with the locked scenes that ShouldRenderGate kept."""
    print(f"[*] Script Agent drafting content for: {entities}...")

    is_comp = os.path.exists(os.path.join(output_dir, "comparison_dossier.md"))
    res_file = "comparison_dossier.md" if is_comp else "research.md"

    research     = load_text(f"{output_dir}/{res_file}")
    analysis     = load_text(f"{output_dir}/analysis.md")
    anecdotes    = load_text(f"{output_dir}/anecdotes_dossier.md")
    user_context = load_text(f"{output_dir}/context.md")

    style_rules        = _load_template("style_rules.md")
    visual_grammar     = _load_template("visual_grammar.md")
    retention_patterns = _load_template("retention_patterns.md")

    # Load retention brief if it was generated in the browser flow
    retention_brief = None
    retention_brief_path = Path(output_dir) / "retention_brief.json"
    if retention_brief_path.exists():
        try:
            retention_brief = json.loads(retention_brief_path.read_text())
            print(f"    -> Retention brief loaded (anchor: {retention_brief.get('anchor_character', {}).get('name', 'unknown')})")
        except Exception:
            pass

    # Detect documentary type
    thematic = _is_thematic(entities, user_context)
    act_list = THEMATIC_ACTS if thematic else ACTS
    if thematic:
        print(f"    -> Thematic documentary detected — using Myth/Shift/Break/Consequence/Question structure")

    # Extract exact video title from context.md for use in HERO INTRO tag
    video_title = entities  # fallback
    for line in user_context.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            # First non-header, non-empty line after "## Title" is the title
            pass
    # Parse "## Title\n{title}" pattern
    import re as _re
    title_match = _re.search(r'##\s*Title\s*\n+([^\n#]+)', user_context)
    if title_match:
        video_title = title_match.group(1).strip()

    # Step 0: Generate the event map before writing any acts
    print(f"    -> Generating story outline...")
    outline = _generate_outline(entities, research, analysis, anecdotes, user_context, is_comp)
    save_text(f"{output_dir}/outline.md", outline)

    sections = []
    previous_ending = ""
    prev_act_summary: dict | None = None  # Track C: inter-act rhythm carrier
    covered_points: list[str] = []  # accumulates key ideas already written

    for act in act_list:
        print(f"    -> Writing {act['name']}...")
        text = None
        for attempt in range(3):
            try:
                text = _write_act(
                    act, entities, outline, style_rules, visual_grammar, retention_patterns,
                    research, analysis, anecdotes, user_context, is_comp, previous_ending,
                    video_title=video_title, retention_brief=retention_brief, is_thematic=thematic,
                    covered_points=covered_points,
                    prev_act_summary=prev_act_summary,
                    storyboard_context=storyboard_context,  # Track D ground truth
                )
                if text and len(text.strip()) > 100:
                    break
                print(f"    [!] {act['name']} returned empty/short response (attempt {attempt+1}/3), retrying…")
            except Exception as e:
                print(f"    [!] {act['name']} Gemini error (attempt {attempt+1}/3): {e}")
                if attempt == 2:
                    text = f"[ACT GENERATION FAILED: {e}]"
        if not text:
            text = f"[ACT GENERATION FAILED: no response after 3 attempts]"
        sections.append(text)
        words = text.split()
        previous_ending = " ".join(words[-200:]) if len(words) > 200 else text
        prev_act_summary = _summarise_act(text)  # Track C: feed next act's rhythm gate
        # Summarise the key ideas of this act so later acts can avoid repeating them
        summary_prompt = (
            f"List 4-6 bullet points of the KEY FACTS AND IDEAS covered in this act. "
            f"Be specific (player names, years, concepts). One bullet = one idea.\n\n{text[:3000]}"
        )
        try:
            summary = ask_llm(summary_prompt)
            covered_points.append(f"=== {act['name']} covered ===\n{summary}")
        except Exception:
            covered_points.append(f"=== {act['name']} ===\n(summary unavailable)")

    full_script = "\n\n---\n\n".join(
        f"### {act['name']}\n\n{text}"
        for act, text in zip(act_list, sections)
    )
    save_text(f"{output_dir}/script_draft.md", full_script)
    return full_script
