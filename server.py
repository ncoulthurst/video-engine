"""
Documentary Engine — Context Server
Title → Context + Fact Checklist → Blueprint Preview → Run
Run: python3 server.py  |  Open: http://localhost:5000
"""

from flask import Flask, request, jsonify, render_template_string
from pathlib import Path
import requests as http_requests
import json, time, sys, os, subprocess

sys.path.insert(0, str(Path(__file__).parent))
from utils.llm_utils import ask_llm, ask_gemini, _cached_infer
from utils.file_utils import save_text

app = Flask(__name__)
BASE_OUTPUT = Path(__file__).parent / "output"
ENGINE_DIR  = str(Path(__file__).parent)

# Path to the separate Remotion (React) project. Override via
# REMOTION_PROJECT_PATH env var. Falls back to a sibling layout where the
# Remotion project lives next to the engine root.
REMOTION_DIR = Path(
    os.environ.get(
        "REMOTION_PROJECT_PATH",
        str(Path(__file__).parent.parent / "remotiontest"),
    )
).resolve()

# One pipeline at a time (local dev tool)
_job = {"proc": None, "log_path": None, "safe_name": None}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


# ── Wikipedia ─────────────────────────────────────────────────────────────────

def _wikipedia_full(name):
    """Get full Wikipedia article text (up to 8000 chars)."""
    try:
        url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=query&prop=extracts&explaintext"
            f"&titles={http_requests.utils.quote(name)}&format=json&redirects=1"
        )
        resp  = http_requests.get(url, headers=HEADERS, timeout=12)
        pages = resp.json().get("query", {}).get("pages", {})
        page  = next(iter(pages.values()), {})
        return page.get("extract", "")[:8000]
    except Exception:
        return ""


def _google_news_headlines(query, num=6):
    headlines = []
    try:
        from bs4 import BeautifulSoup
        url  = f"https://news.google.com/rss/search?q={http_requests.utils.quote(query)}&hl=en&gl=GB&ceid=GB:en"
        resp = http_requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "xml")
        for item in soup.select("item")[:num]:
            t = item.find("title")
            s = item.find("source")
            if t:
                headlines.append({"title": t.get_text(strip=True),
                                   "source": s.get_text(strip=True) if s else ""})
    except Exception:
        pass
    return headlines


def _extract_entity(topic):
    return ask_llm(
        f'Extract the primary football player(s) or subject from: "{topic}"\n'
        'Return only the name(s), nothing else. If multiple, comma-separated.'
    ).strip()


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _suggest_context(topic, entity, wiki, headlines):
    headline_lines = "\n".join(f'- "{h["title"]}" ({h["source"]})' for h in headlines) or "None."
    return ask_llm(f"""You are a senior documentary producer. Write a director's brief for this football documentary.

VIDEO TITLE: {topic}
SUBJECT: {entity}

WIKIPEDIA (full article):
{wiki[:3000] if wiki else "Not found."}

RECENT HEADLINES:
{headline_lines}

Write in plain text (no JSON, no markdown headers). Cover:
1. Central thesis — one sentence that is the spine of the documentary
2. Narrative arc — rise, peak, defining event, legacy (name SPECIFIC matches, incidents, seasons)
3. Tone and angle — what makes this story unique
4. What to emphasise — name specific moments, games, relationships
5. What to avoid or treat carefully

Be specific. Reference real matches, dates, and incidents from the career. Write as if briefing a scriptwriter.""")


def _extract_facts(entity, wiki):
    prompt = f"""Extract specific, concrete career facts about {entity} from this Wikipedia article.

WIKIPEDIA:
{wiki[:5000] if wiki else "Not available."}

Return a JSON list of 18–28 facts. Each must be a specific, verifiable event or moment from the career.

Categories:
- "Career Moment": specific matches, transfers, key games, iconic scenes
- "Controversy": bans, incidents, controversial moments, legal issues
- "Achievement": trophies, records, statistical milestones, awards
- "Personal Story": family, background, relationships, emotional moments

ALWAYS look for and extract these types of moments if they exist:
- Title race deciding matches (dropped points, specific results that decided a championship)
- The exact result and scorer for a famous loss or collapse (e.g. Gerrard slip vs Chelsea, Crystal Palace comeback)
- World Cup moments with exact scorers and results
- Biting/disciplinary incidents with exact opponent, date, ban length
- Specific transfer fees and clubs
- Record-setting goal tallies (31 goals in a season, etc.)

Rules:
- label: under 55 chars, punchy and specific (include names/dates/opponents)
- detail: adds key context (score, date, scorer, ban length, stat figure)
- importance: "high" = absolutely pivotal, "medium" = good to include, "low" = optional colour
- checked: true for high and medium, false for low importance
- Do NOT make up facts — only include what is in the Wikipedia text or is widely known

Return ONLY valid JSON:
{{
  "facts": [
    {{
      "id": "f001",
      "category": "Career Moment",
      "label": "Crystal Palace 3-3 — the title that slipped away",
      "detail": "5 May 2014 · Liverpool drew, City won. Two points off champions.",
      "importance": "high",
      "checked": true
    }}
  ]
}}"""
    try:
        raw = ask_gemini(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        return json.loads(raw.strip()).get("facts", [])
    except Exception as e:
        print(f"  [Server] Facts extraction failed: {e}")
        return []


def _extract_context_facts(entity, context):
    """Extract structured facts from the user's Director's Brief (not Wikipedia)."""
    if not context or len(context.strip()) < 30:
        return []
    prompt = f"""Extract specific story moments from this Director's Brief for a football documentary.

SUBJECT: {entity}

DIRECTOR'S BRIEF:
{context[:3000]}

Extract EVERY named event, match result, incident, statistic, or narrative beat the director mentioned.
These are editorial intentions — moments the producer explicitly wants in the documentary.

Rules:
- Only extract moments explicitly named in the brief — do NOT invent or infer
- label: short punchy description (max 55 chars, include the key names/dates)
- detail: the exact context from the brief (score, date, players, stats)
- All items: importance "high", checked true — director explicitly wants them
- id prefix "ctx_" (e.g. "ctx_001")
- category: always "Director's Intent"
- source: "context"

Return ONLY valid JSON:
{{
  "facts": [
    {{
      "id": "ctx_001",
      "category": "Director's Intent",
      "label": "Crystal Palace 3-3 — title slips away",
      "detail": "5 May 2014 · Liverpool drew 3-3, Man City won the title",
      "importance": "high",
      "checked": true,
      "source": "context"
    }}
  ]
}}"""
    try:
        raw = ask_llm(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        return json.loads(raw.strip()).get("facts", [])
    except Exception as e:
        print(f"  [Server] Context facts extraction failed: {e}")
        return []


_THEMATIC_TOPIC_KEYWORDS = [
    "history of", "story of", "decline of", "rise of", "death of", "end of",
    "why did", "how football", "evolution of", "identity", "tactical revolution",
    "playmakers", "pressing", "system", "philosophy", "culture of", "era of",
    "golden generation", "lost generation", "what happened to", "where did",
    "the fall of", "the rise of", "the problem with",
]

def _is_thematic_topic(topic: str, context: str = "") -> bool:
    combined = (topic + " " + context[:400]).lower()
    return any(kw in combined for kw in _THEMATIC_TOPIC_KEYWORDS)


def _generate_blueprint(topic, entity, context, wiki, checked_facts, excluded_facts):
    must_include = "\n".join(f"• {f}" for f in checked_facts) if checked_facts else "None specified."
    must_exclude = "\n".join(f"• {f}" for f in excluded_facts) if excluded_facts else "None."

    thematic = _is_thematic_topic(topic, context)

    if thematic:
        acts_definition = """ACTS — THEMATIC STRUCTURE (Myth → Shift → Break → Consequence → Question):
- COLD OPEN (0:00–1:30, 300–400 words): Single most cinematic moment embodying the thesis. Hook that states the contrast.
- ACT 1 — THE MYTH (1:30–5:00, 1400–1700 words): The golden era. What people believed. Show the peak of the identity with real players, real matches.
- ACT 2 — THE SHIFT (5:00–9:00, 1400–1700 words): When things started changing. The first cracks. The system arriving. Introduce the anchor character — the bridge between eras.
- ACT 3 — THE BREAK (9:00–13:30, 1400–1700 words): The old way collapses. The definitive failure. Anchor character at their peak — the last of their kind.
- ACT 4 — THE CONSEQUENCE (13:30–17:00, 1100–1400 words): Current reality. What the world looks like now. Evidence the shift is permanent.
- ACT 5 — THE QUESTION (17:00–20:00, 1100–1400 words): No conclusion. An open provocation. What could bring it back? After the anchor, what's left?

ANCHOR CHARACTER RULE: Choose the player who BRIDGES old and new — active or recently retired, exists in both eras. Creates forward tension ("what comes after them?"), not nostalgia. NOT the player who represents only the peak.
COMPARISON RULE: All player comparisons must be within the same nation — same role, different era. For a Brazil doc, compare Brazilians to Brazilians. Never compare across nationalities as the primary contrast."""
    else:
        acts_definition = """ACTS — BIOGRAPHY STRUCTURE:
- COLD OPEN (0:00–1:30, 300–400 words): Single most cinematic moment. Central thesis. Hook.
- ACT 1 — ORIGINS (1:30–5:00, 1400–1700 words): Early life, youth, first signs of defining quality/flaw.
- ACT 2 — RISE (5:00–9:00, 1400–1700 words): Breakthrough, early professional years.
- ACT 3 — PEAK (9:00–13:30, 1400–1700 words): Two iconic career moments. PLAYER RADAR mandatory here. Pivot to decline.
- ACT 4 — THE DEFINING EVENT (13:30–17:00, 1100–1400 words): Single turning point. Real-time.
- ACT 5 — REDEMPTION & LEGACY (17:00–20:00, 1100–1400 words): Later career, honest verdict."""

    prompt = f"""You are a documentary producer. Generate a structural blueprint for this football documentary.

VIDEO TITLE: {topic}
SUBJECT: {entity}

DIRECTOR'S BRIEF:
{context[:1200]}

BACKGROUND:
{wiki[:1500] if wiki else ""}

MUST INCLUDE — these specific moments MUST appear in the script:
{must_include}

MUST EXCLUDE — do NOT include these in any act:
{must_exclude}

Generate a 6-act blueprint. For each act:
1. 2–3 specific narrative events (real moments, with names/dates/opponents)
2. Exact visual tags to embed — use real career details, not placeholders

{acts_definition}

AVAILABLE TAGS:
Clip tags: CLIP SINGLE, CLIP COMPARE
Infographic: STANDINGS TABLE, TOP SCORERS, TOP ASSISTS, PLAYER STATS, MATCH RESULT, TRANSFER, CAREER TIMELINE, SEASON COMPARISON, TEAM LINEUP, DISCIPLINARY RECORD, QUOTE CARD, TOURNAMENT BRACKET, PLAYER TRIO
Hero: HERO INTRO, HERO FORM RUN, HERO TACTICAL, HERO BIG STAT, HERO LEAGUE GRAPH, HERO STAT BARS, HERO QUOTE, HERO TRANSFER RECORD, HERO SCATTER, HERO SHOT MAP, HERO MATCH TIMELINE, HERO AWARDS LIST, HERO COMPARISON RADAR, HERO SEASON TIMELINE, PLAYER RADAR
(HERO OUTRO is auto-appended by the engine — DO NOT emit one yourself.)
Transitions (act breaks — NOT chapter title cards): TRANSITION: letterbox | TRANSITION: push | TRANSITION: grain | TRANSITION: paper | TRANSITION: dataLine

MANDATORY RULES — every one must be followed:
- Cold Open: HERO INTRO must be the FIRST tag, before any clip tags
- CAREER TIMELINE — use SPARINGLY: MAX 2 per script, only for the player's two most defining club transitions. Do NOT emit one per club. For a thematic video with no single subject, emit ZERO CAREER TIMELINE tags.
- Knockout tournament run (World Cup, Euros): emit a single TOURNAMENT BRACKET tag — "[TOURNAMENT BRACKET: FIFA World Cup YYYY, Focus: Team]". This replaces a chain of MATCH RESULT cards for that tournament.
- ACT 3: PLAYER TRIO tag for peer comparison vs era rivals — OPTIONAL, only emit for player biographies where a real GOAT/peer debate is central. Skip for thematic, national-team, club, or systems-of-football documentaries.
- ACT 3: HERO STAT BARS comparing subject to their greatest rival
- Any title race: HERO FORM RUN for BOTH teams involved
- Every specific match named in an act: TEAM LINEUP tag
- Act breaks: use TRANSITION tags only (letterbox/push/grain/paper). NEVER use HERO CHAPTER — act names must NOT appear on screen
- ACT 3: you MUST include a PLAYER RADAR tag — {{"category":"hero","type":"PLAYER RADAR","content":"[Player], [Club at peak], [Competition], [Season]"}}
- Every act: minimum 2 CLIP SINGLE tags
- Any title race or run of form: use HERO FORM RUN
- All tag content must use real career details, not placeholder text

Return ONLY valid JSON:
{{
  "acts": [
    {{
      "name": "COLD OPEN",
      "timeRange": "0:00–0:45",
      "wordCount": "200–250",
      "events": ["Event 1", "Event 2"],
      "tags": [
        {{"category": "hero", "type": "HERO INTRO", "content": "{topic}"}},
        {{"category": "clip", "type": "CLIP SINGLE", "content": "specific footage description, 8s, label"}}
      ]
    }}
  ],
  "summary": {{"clips": 0, "infographics": 0, "hero": 0, "total": 0}}
}}"""

    try:
        raw = ask_gemini(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"  [Server] Blueprint failed: {e}")
        return _fallback_blueprint(topic)


def _fallback_blueprint(topic):
    acts = [
        ("COLD OPEN", "0:00–0:45", "200–250"),
        ("ACT 1 — ORIGINS", "0:45–4:30", "500–600"),
        ("ACT 2 — RISE", "4:30–8:30", "500–600"),
        ("ACT 3 — PEAK", "8:30–13:00", "500–600"),
        ("ACT 4 — THE DEFINING EVENT", "13:00–16:30", "400–500"),
        ("ACT 5 — REDEMPTION & LEGACY", "16:30–20:00", "400–500"),
    ]
    return {
        "acts": [{"name": n, "timeRange": t, "wordCount": w,
                  "events": ["Blueprint generation failed — run the pipeline."], "tags": []}
                 for n, t, w in acts],
        "summary": {"clips": 0, "infographics": 0, "hero": 0, "total": 0}
    }


_make_sid_counter = 0
def _make_sid():
    """Module-level scene ID generator — counter-based to guarantee uniqueness."""
    global _make_sid_counter
    import time as _t
    _make_sid_counter += 1
    return f"s_auto_{int(_t.time() * 1000) % 1000000}_{_make_sid_counter}"


def _inject_missing_context_moments(scenes, context, entity):
    """Inject high-importance events mentioned in the Director's Brief that are
    absent from the storyboard. Generalised — works for any topic, not subject-specific.

    Replaces: Crystal Palace / Norwich City hardcoded injection blocks.
    """
    if not context or not entity:
        return

    scene_texts = " ".join(
        s.get("content", "") + " " + s.get("label", "") for s in scenes
    ).lower()

    # Ask the LLM to identify up to 3 key moments from the brief that might be missing
    moments = _cached_infer(
        f"From this Director's Brief for a documentary about {entity}, list up to 3 "
        f"specific historical events or moments that are explicitly described as important "
        f"and have concrete details (scores, dates, opponents, specific incidents). "
        f"Format each as a short searchable description (max 8 words). "
        f"Only include moments with specific verifiable details — no vague themes.\n\n"
        f"Brief excerpt:\n{context[:1500]}",
        expected_type="list",
        fallback=[],
    )

    if not moments:
        return

    # Find a stable insertion point — end of ACT 3 (peak)
    def _last_act3_pos():
        pos = -1
        for i, s in enumerate(scenes):
            if s.get("actIndex", 0) == 3:
                pos = i
        return pos if pos != -1 else len(scenes) - 1

    for moment in moments:
        # Check presence: if ≥2 significant keywords already in storyboard, skip
        keywords = [w.lower() for w in moment.split() if len(w) > 4]
        if not keywords:
            continue
        if sum(1 for kw in keywords if kw in scene_texts) >= 2:
            continue

        pivot = _last_act3_pos() + 1
        clip = {
            "id": _make_sid(),
            "act": "ACT 3 — PEAK",
            "actIndex": 3,
            "type": "clip",
            "template": "CLIP SINGLE",
            "content": f"{entity} — {moment}",
            "label": moment,
            "duration": 8,
        }
        scenes.insert(pivot, clip)
        # Update scene_texts so subsequent moments don't double-inject
        scene_texts += " " + moment.lower()


def _enforce_clip_world_continuity(scenes):
    """Insert a push TRANSITION between consecutive clip scenes from different world_ids.

    Requires world_id to already be set on all scenes (_assign_scene_metadata must run first).
    Prevents clips from different eras/phases being stitched with no visual break.
    """
    import time as _time

    def _sid():
        return f"s_cworld_{int(_time.time() * 1000) % 1000000}"

    inserted = 0
    i = 0
    while i < len(scenes) - 1:
        curr = scenes[i]
        nxt = scenes[i + 1]
        if curr.get("type") == "clip" and nxt.get("type") == "clip":
            if curr.get("world_id") != nxt.get("world_id"):
                trans = {
                    "id": _sid(),
                    "act": nxt.get("act", ""),
                    "actIndex": nxt.get("actIndex", 0),
                    "type": "transition",
                    "template": "TRANSITION",
                    "content": "push",
                    "label": "",
                    "duration": 2,
                }
                scenes.insert(i + 1, trans)
                inserted += 1
                i += 2  # skip past the inserted transition
                continue
        i += 1

    if inserted:
        print(f"  [Continuity] Inserted {inserted} world-break transition(s) between clip pairs")


def _assign_scene_metadata(scenes):
    """Layer 2 post-processing: assign role, clip type, world_id, flow_hint, flow_direction.

    Operates on scenes in-place. Called after structural post-processing.
    Never modifies scenes that already carry these fields (LLM-set values are preserved).
    """
    import re as _re

    ACT_DIRECTIONS = {0: "forward", 1: "right", 2: "right", 3: "forward", 4: "left", 5: "forward"}
    DIRECTION_INVERT = {"right": "left", "left": "right", "forward": "forward"}

    # ── 1. world_id — era detection, not LLM ─────────────────────────────────
    # New world at every act boundary; also when content years jump > 4 years within an act.

    def _detect_era(text):
        years = [int(m) for m in _re.findall(r'\b(19[5-9]\d|20[0-3]\d)\b', text)]
        return min(years) if years else None

    world_counter = 0
    prev_act = -1
    prev_era = None

    for s in scenes:
        act_idx = s.get("actIndex", 0)
        era = _detect_era(s.get("content", "") + " " + s.get("label", ""))

        if act_idx != prev_act:
            world_counter += 1
            prev_act = act_idx
            prev_era = era
        elif era and prev_era and abs(era - prev_era) > 4:
            world_counter += 1
            prev_era = era
        elif era:
            prev_era = era

        s.setdefault("world_id", f"w{world_counter:02d}")

    # ── 1b. canonical bgColor per world_id ────────────────────────────────────
    _DARK_TAG_KEYS = {"HERO TACTICAL", "HERO LEAGUE GRAPH"}
    _world_bg: dict = {}

    for s in scenes:
        wid = s["world_id"]
        if wid not in _world_bg:
            tag = s.get("tag_key", "").upper()
            _world_bg[wid] = "#1a1a1a" if tag in _DARK_TAG_KEYS else "#f0ece4"

    for s in scenes:
        s.setdefault("canonical_bgColor", _world_bg[s["world_id"]])

    # ── 2. Role assignment for clip scenes (fallback only — LLM values kept) ──
    clip_scenes = [s for s in scenes if s.get("type") == "clip"]

    if clip_scenes:
        if not clip_scenes[0].get("role"):
            clip_scenes[0]["role"] = "context"

        if len(clip_scenes) > 1 and not clip_scenes[-1].get("role"):
            clip_scenes[-1]["role"] = "emotional_beat"

        # Strongest clip → anchor: longest content in ACT 3, else overall
        candidates = [s for s in clip_scenes if s.get("actIndex") == 3 and not s.get("role")]
        if not candidates:
            candidates = [s for s in clip_scenes if not s.get("role")]
        if candidates:
            anchor = max(candidates, key=lambda s: len(s.get("content", "")))
            anchor["role"] = "anchor"

        # Remaining: short (≤6s) → transition_support, others → evidence
        for s in clip_scenes:
            if not s.get("role"):
                s["role"] = "transition_support" if s.get("duration", 8) <= 6 else "evidence"

    # ── 3. Clip type resolution based on role ─────────────────────────────────
    CONTRAST_KEYWORDS = {
        " vs ", " versus ", "compared", "before vs", "then vs", " | ",
        "but if we look", "another example", "by contrast", "on the other hand",
        "compare that to", "alongside", "side by side",
    }

    for s in scenes:
        if s.get("type") != "clip":
            continue
        role = s.get("role", "evidence")
        content = s.get("content", "").lower()

        if role in ("anchor", "emotional_beat", "context"):
            s["template"] = "CLIP SINGLE"
        elif role == "transition_support":
            s["template"] = "CLIP SINGLE"
            s["duration"] = min(s.get("duration", 6), 6)
        elif role == "evidence":
            # Upgrade to CLIP COMPARE only for explicit contrast; otherwise lock to CLIP SINGLE
            has_contrast = any(kw in content for kw in CONTRAST_KEYWORDS)
            if not has_contrast:
                s["template"] = "CLIP SINGLE"

    # ── 4. flow_hint — inferred where absent ──────────────────────────────────
    # Continuous-world principle: within the same act, we want adjacent scenes
    # to feel like one camera moving through one space. worldPan is the
    # default; "push" is a hard cut-style change-of-page and is reserved for
    # cases where the world genuinely shifts. Across act boundaries we cut
    # because the act break itself is the punctuation.
    for i, s in enumerate(scenes):
        if s.get("flow_hint"):
            continue
        if i == 0:
            s["flow_hint"] = "cut"
            continue
        prev = scenes[i - 1]
        if s.get("actIndex", 0) != prev.get("actIndex", 0):
            s["flow_hint"] = "cut"
        elif s.get("world_id") == prev.get("world_id"):
            s["flow_hint"] = "evolve"
        else:
            # Same-act adjacency — default to worldPan regardless of whether
            # the pair is graphic→graphic, graphic→clip, or clip→graphic.
            # This is the "one continuous world" goal: the camera should
            # almost never break stride within an act.
            s["flow_hint"] = "worldPan"

    # ── 5. flow_direction — act defaults, inverted for emotional_beat ─────────
    for s in scenes:
        if s.get("flow_direction"):
            continue
        direction = ACT_DIRECTIONS.get(s.get("actIndex", 0), "forward")
        if s.get("role") == "emotional_beat":
            direction = DIRECTION_INVERT.get(direction, direction)
        s["flow_direction"] = direction

    # ── 6. skipIntro — true when scene is a direct continuation ───────────────
    for s in scenes:
        if s.get("skipIntro") is not None:
            continue
        s["skipIntro"] = s.get("flow_hint") in ("evolve", "worldPan")


def _reconcile_format(scenes, profile=None):
    """Trim scenes to stay within format budget. Never adds — only removes.

    Clip trim priority (lowest first):
      1. transition_support
      2. weak evidence  (duration ≤6s AND content <60 chars)
      3. remaining evidence

    Graphic trim priority (lowest first):
      1. stat templates  (HERO STAT BARS, TOP SCORERS, etc.)
      2. any remaining non-protected graphic

    Protected templates are never removed regardless of budget.
    Protected roles (anchor, emotional_beat, context) are never removed.
    """
    if not profile:
        profile = {"clips_per_act": 6, "graphics_per_act": 6}

    max_clips    = profile.get("clips_per_act", 6)
    max_graphics = profile.get("graphics_per_act", 6)

    PROTECTED_TEMPLATES = {
        "HERO INTRO", "PLAYER TRIO", "PLAYER RADAR", "CAREER TIMELINE",
        "TEAM LINEUP", "TRANSITION", "HERO FORM RUN", "HERO BIG STAT",
    }
    PROTECTED_ROLES = {"anchor", "emotional_beat", "context"}
    STAT_TEMPLATES = {
        "HERO STAT BARS", "TOP SCORERS", "TOP ASSISTS", "PLAYER STATS",
        "STANDINGS TABLE", "SEASON COMPARISON",
    }

    def _cuttable(i, s):
        return (
            s.get("template", "").upper() not in PROTECTED_TEMPLATES
            and s.get("role") not in PROTECTED_ROLES
            and not s.get("hero_visual")
        )

    def _trim(ordered_candidates, overflow):
        """Return up to `overflow` scene indices from candidates, in order."""
        removed = []
        seen = set()
        for i, s in ordered_candidates:
            if len(removed) >= overflow:
                break
            if i not in seen and _cuttable(i, s):
                removed.append(i)
                seen.add(i)
        return removed

    to_remove = set()

    for act_idx in sorted(set(s.get("actIndex", 0) for s in scenes)):
        act_scenes = [
            (i, s) for i, s in enumerate(scenes)
            if s.get("actIndex") == act_idx and i not in to_remove
        ]
        clips    = [(i, s) for i, s in act_scenes if s.get("type") == "clip"]
        graphics = [(i, s) for i, s in act_scenes if s.get("type") == "graphic"]

        # ── Clip trim ────────────────────────────────────────────────────────
        if len(clips) > max_clips:
            overflow = len(clips) - max_clips
            p1 = [(i, s) for i, s in clips if s.get("role") == "transition_support"]
            p2 = [(i, s) for i, s in clips
                  if s.get("role") == "evidence"
                  and s.get("duration", 8) <= 6
                  and len(s.get("content", "")) < 60]
            p3 = [(i, s) for i, s in clips if s.get("role") == "evidence"]

            ordered, seen = [], set()
            for i, s in p1 + p2 + p3:
                if i not in seen:
                    seen.add(i)
                    ordered.append((i, s))

            to_remove.update(_trim(ordered, overflow))

        # ── Graphic trim ─────────────────────────────────────────────────────
        if len(graphics) > max_graphics:
            overflow = len(graphics) - max_graphics
            p1 = [(i, s) for i, s in graphics
                  if s.get("template", "").upper() in STAT_TEMPLATES]
            p2 = [(i, s) for i, s in graphics
                  if s.get("template", "").upper() not in PROTECTED_TEMPLATES]

            ordered, seen = [], set()
            for i, s in p1 + p2:
                if i not in seen:
                    seen.add(i)
                    ordered.append((i, s))

            to_remove.update(_trim(ordered, overflow))

    if to_remove:
        for i in sorted(to_remove, reverse=True):
            scenes.pop(i)
        print(f"  [_reconcile_format] Trimmed {len(to_remove)} over-budget scenes.")


_TEMPLATE_CAPS = {
    "HERO INTRO":             1,
    "PLAYER TRIO":               1,
    "CAREER TIMELINE":           2,
    "TOURNAMENT BRACKET":        1,
    "PLAYER RADAR":              2,
    "HERO STAT BARS":         3,
    "HERO BIG STAT":          6,
    "TOP SCORERS":               3,
    "TOP ASSISTS":               2,
    "PLAYER STATS":              3,
    "TEAM LINEUP":               6,
    "HERO FORM RUN":          3,
    "STANDINGS TABLE":           2,
    "MATCH RESULT":              5,
    "SEASON COMPARISON":         3,
    "HERO TACTICAL":          3,
    "HERO LEAGUE GRAPH":      2,
    "HERO TRANSFER RECORD":   2,
    "DISCIPLINARY RECORD":       1,
    "HERO QUOTE":             2,
    "HERO CONCEPT":           2,
    "HERO SCATTER":           2,
    "HERO SHOT MAP":          2,
    "HERO MATCH TIMELINE":    2,
    "HERO AWARDS LIST":       2,
    "HERO COMPARISON RADAR":  2,
    "HERO SEASON TIMELINE":   2,
}

_TEMPLATE_ALTERNATIVES = {
    "HERO STAT BARS":       "HERO BIG STAT",
    "TOP SCORERS":             "STANDINGS TABLE",
    "TOP ASSISTS":             "HERO BIG STAT",
    "PLAYER STATS":            "HERO BIG STAT",
    "STANDINGS TABLE":         "HERO BIG STAT",
    "MATCH RESULT":            "HERO BIG STAT",
    "HERO TACTICAL":        "HERO STAT BARS",
    "HERO LEAGUE GRAPH":    "HERO STAT BARS",
    "SEASON COMPARISON":       "HERO STAT BARS",
    "CAREER TIMELINE":         "HERO BIG STAT",
    "PLAYER RADAR":            "HERO STAT BARS",
}


def _enforce_template_caps(scenes):
    """Prevent template over-use and visual repetition.

    Two passes:
      1. Global cap  — if a template exceeds _TEMPLATE_CAPS, replace excess scenes
                       with alternative template (or HERO BIG STAT as fallback)
      2. Consecutive — no 3+ consecutive identical graphic templates
                       (scanning graphic scenes only, skipping narration/clips)

    Protected: hero_visual scenes, anchor/emotional_beat/context roles,
               HERO INTRO, TRANSITION — these are never replaced.

    Also logs diversity_score = unique_templates / total_graphic_renders.
    """
    _NEVER_REPLACE = {"HERO INTRO", "TRANSITION"}
    _NEVER_REPLACE_ROLES = {"anchor", "emotional_beat", "context"}

    def _is_protected(s):
        return (
            s.get("hero_visual")
            or s.get("template", "").upper() in _NEVER_REPLACE
            or s.get("role") in _NEVER_REPLACE_ROLES
        )

    def _replace_template(s):
        tpl = s.get("template", "").upper()
        alt = _TEMPLATE_ALTERNATIVES.get(tpl, "HERO BIG STAT")
        print(f"  [TemplateCap] Replacing over-used {tpl} → {alt}")
        s["template"] = alt

    # ── Pass 1: Global caps ───────────────────────────────────────────────────
    counts = {}
    for s in scenes:
        if s.get("type") != "graphic" or _is_protected(s):
            continue
        tpl = s.get("template", "").upper()
        cap = _TEMPLATE_CAPS.get(tpl)
        if cap is None:
            continue
        counts[tpl] = counts.get(tpl, 0) + 1
        if counts[tpl] > cap:
            _replace_template(s)

    # ── Pass 2: Consecutive cap (max 2 identical in a row) ────────────────────
    graphic_idxs = [i for i, s in enumerate(scenes) if s.get("type") == "graphic"]
    run_tpl, run_count = None, 0
    for idx in graphic_idxs:
        s = scenes[idx]
        tpl = s.get("template", "").upper()
        if tpl == run_tpl:
            run_count += 1
        else:
            run_tpl, run_count = tpl, 1
        if run_count > 2 and not _is_protected(s):
            _replace_template(s)
            run_tpl = s.get("template", "").upper()  # run resets after replacement
            run_count = 1

    # ── Diversity score ───────────────────────────────────────────────────────
    graphic_tpls = [
        s.get("template", "").upper()
        for s in scenes
        if s.get("type") == "graphic"
        and s.get("template", "").upper() not in ("TRANSITION",)
    ]
    total = len(graphic_tpls)
    unique = len(set(graphic_tpls))
    if total:
        score = round(unique / total, 2)
        flag = "✓" if score >= 0.4 else "⚠ below target"
        print(f"  [TemplateCap] diversity_score={score} ({unique} unique / {total} total) {flag}")
        return score
    return None


def _validate_act_openers(scenes):
    """Enforce: no two consecutive acts open with the same graphic template.

    'Act opener' = first non-transition graphic scene in each act.

    Fix: when a duplicate opener is found, swap it for the next available
    different graphic in that act. If none exists, log a warning and leave it.
    """
    # Build a map: act_index → index of the first non-transition graphic scene
    act_openers = {}  # act_idx → scene list-index
    for i, s in enumerate(scenes):
        act_idx = s.get("actIndex", 0)
        if act_idx in act_openers:
            continue
        if s.get("type") == "graphic" and s.get("template", "").upper() != "TRANSITION":
            act_openers[act_idx] = i

    sorted_acts = sorted(act_openers.keys())

    for idx in range(1, len(sorted_acts)):
        prev_act = sorted_acts[idx - 1]
        curr_act = sorted_acts[idx]
        prev_opener_idx = act_openers[prev_act]
        curr_opener_idx = act_openers[curr_act]

        prev_tpl = scenes[prev_opener_idx].get("template", "").upper()
        curr_tpl = scenes[curr_opener_idx].get("template", "").upper()

        if prev_tpl != curr_tpl:
            continue

        # Duplicate opener — find the next different graphic in curr_act
        replacement_idx = None
        for j, s in enumerate(scenes):
            if s.get("actIndex") != curr_act:
                continue
            if j == curr_opener_idx:
                continue
            tpl = s.get("template", "").upper()
            if s.get("type") == "graphic" and tpl != "TRANSITION" and tpl != curr_tpl:
                replacement_idx = j
                break

        if replacement_idx is not None:
            # Swap the two scenes so the different one opens the act
            scenes[curr_opener_idx], scenes[replacement_idx] = (
                scenes[replacement_idx], scenes[curr_opener_idx]
            )
            new_tpl = scenes[curr_opener_idx].get("template", "").upper()
            print(f"  [Identity] ACT {curr_act} opener swapped: {curr_tpl} → {new_tpl}")
        else:
            print(f"  [Identity] ⚠ ACT {curr_act} opener same as ACT {prev_act} ({curr_tpl}) — no swap candidate")


def _break_data_runs(scenes):
    """Prevent more than 2 consecutive pure-data scenes.

    When a run of 3+ data-only scenes is detected, the function looks ahead
    (up to 4 positions) for the next human-imagery scene in the same act and
    swaps it forward to position [run_start + 2].  Only swaps scenes of type
    'graphic' so narration, clips, and transitions are never disrupted.
    """
    DATA_TEMPLATES = {
        "HERO STAT BARS", "HERO LEAGUE GRAPH", "TOP SCORERS",
        "TOP ASSISTS", "PLAYER STATS", "STANDINGS TABLE", "DISCIPLINARY RECORD",
        "HERO MATCH TIMELINE", "HERO GOAL RUSH", "SEASON COMPARISON",
        "HERO SCATTER", "HERO AWARDS LIST",
    }
    HUMAN_TEMPLATES = {
        "HERO QUOTE", "HERO BIG STAT", "HERO FORM RUN", "PLAYER TRIO",
        "HERO PHOTO REEL", "HERO PLAYER REVEAL", "HERO SEASON TIMELINE",
        "HERO INTRO", "HERO TRANSFER RECORD", "CAREER TIMELINE",
        "PLAYER RADAR", "HERO SHOT MAP", "HERO CONCEPT CARD",
    }

    def _is_data(s):
        return (s.get("type") == "graphic"
                and s.get("template", "").upper() in DATA_TEMPLATES)

    def _is_human(s):
        return (s.get("type") == "graphic"
                and s.get("template", "").upper() in HUMAN_TEMPLATES)

    i = 0
    swaps = 0
    while i < len(scenes):
        if not _is_data(scenes[i]):
            i += 1
            continue
        # Count consecutive data scenes
        run_start = i
        while i < len(scenes) and _is_data(scenes[i]):
            i += 1
        run_len = i - run_start
        if run_len < 3:
            continue
        # Run of 3+ — find the next human-imagery scene within 4 positions after the run
        act = scenes[run_start].get("actIndex", -1)
        target_pos = run_start + 2  # inject after the 2nd data scene
        for j in range(i, min(i + 4, len(scenes))):
            if _is_human(scenes[j]) and scenes[j].get("actIndex", -1) == act:
                # Swap it to target_pos
                victim = scenes.pop(j)
                scenes.insert(target_pos, victim)
                swaps += 1
                print(f"  [DataRun] Broke {run_len}-scene data run at idx {run_start}: "
                      f"moved {victim.get('template','')} to position {target_pos}")
                break

    if swaps:
        print(f"  [DataRun] {swaps} swap(s) applied to prevent pure-data stretches")


def _inject_hero_visuals(scenes, entity, topic):
    """Ensure 1–3 hero_visual scenes are marked in the storyboard.

    Rules (from roadmap Issue B):
      - 1 mandatory in ACT 3 (thesis / break)
      - +1 optional in ACT 1 (before state)
      - +1 optional in ACT 5 (after / legacy)
      - Max 3 total, never same act
      - Promotes existing graphic scenes where possible; injects new one only
        for the mandatory ACT 3 slot if no graphic exists there.

    The hero_visual flag signals motion_agent to render a bespoke graphic
    instead of reusing a standard template.
    """
    # Templates that should never be the hero_visual — they're structural not narrative
    SKIP_TEMPLATES = {
        "HERO INTRO", "TRANSITION", "TEAM LINEUP", "HERO FORM RUN",
        "TOP SCORERS", "TOP ASSISTS", "PLAYER STATS", "STANDINGS TABLE",
    }

    # Validate any hero_visual flags already set by the LLM
    acts_covered = set()
    for s in scenes:
        if s.get("hero_visual"):
            if s.get("template", "").upper() in SKIP_TEMPLATES or s.get("type") != "graphic":
                s.pop("hero_visual", None)  # strip invalid flags
            else:
                acts_covered.add(s.get("actIndex"))

    # Attempt to promote an existing graphic in the target act
    def _promote(act_idx):
        candidates = [
            s for s in scenes
            if s.get("actIndex") == act_idx
            and s.get("type") == "graphic"
            and not s.get("hero_visual")
            and s.get("template", "").upper() not in SKIP_TEMPLATES
        ]
        if not candidates:
            return False
        best = max(candidates, key=lambda s: len(s.get("content", "")))
        best["hero_visual"] = True
        acts_covered.add(act_idx)
        return True

    # ACT 3 — mandatory
    if 3 not in acts_covered:
        if not _promote(3):
            # No suitable graphic — inject a placeholder hero_visual scene
            pivot = next(
                (i for i, s in enumerate(scenes) if s.get("actIndex") == 3),
                len(scenes)
            )
            scenes.insert(pivot, {
                "id": _make_sid(),
                "act": "ACT 3 — PEAK",
                "actIndex": 3,
                "type": "graphic",
                "template": "HERO BIG STAT",
                "content": f"{entity} — peak moment",
                "label": "thesis visual",
                "duration": 10,
                "hero_visual": True,
            })
            acts_covered.add(3)

    # ACT 1 — optional (add if we have room)
    if 1 not in acts_covered and len(acts_covered) < 3:
        _promote(1)

    # ACT 5 — optional (add if we have room)
    if 5 not in acts_covered and len(acts_covered) < 3:
        _promote(5)


def _deduplicate_narration_phrases(scenes):
    """Remove repeated multi-word phrases that appear across multiple narration scenes.

    A phrase of ≥8 consecutive words that appears verbatim in a later narration scene
    is stripped from that later scene. This prevents the LLM 'filler loop' where the
    same sentence recurs across acts (e.g. "modern icon burdened with carrying Brazil's
    creative hopes...").
    """
    import re as _re

    narr_scenes = [s for s in scenes if s.get("type") == "narration"]
    seen_phrases: set = set()

    def _extract_phrases(text, min_words=8):
        words = _re.findall(r'\b\w+\b', text.lower())
        return {" ".join(words[i:i+min_words]) for i in range(len(words) - min_words + 1)}

    for s in narr_scenes:
        content = s.get("content", "")
        phrases = _extract_phrases(content)
        # Find sentences that are entirely composed of already-seen phrase material
        sentences = _re.split(r'(?<=[.!?])\s+', content)
        kept = []
        for sent in sentences:
            sent_phrases = _extract_phrases(sent)
            if sent_phrases and sent_phrases.issubset(seen_phrases):
                print(f"  [NarrDedupe] Removed repeated sentence: {sent[:80]}")
            else:
                kept.append(sent)
                seen_phrases.update(_extract_phrases(sent))
        s["content"] = " ".join(kept).strip()
        if not s["content"]:
            s["content"] = "(removed — duplicate narration)"


def _inject_canonical_break_moment(scenes, entity, topic, context):
    """For thematic/national-team-decline docs, ensure the single most iconic low-point
    is present in THE BREAK act (actIndex=3).

    Uses the LLM to identify the moment if it can't be found in the storyboard.
    Adds a CLIP SINGLE + narration pair rather than a generic placeholder.
    """
    break_scenes = [s for s in scenes if s.get("actIndex") == 3]
    if not break_scenes:
        return

    break_text = " ".join(s.get("content","") + " " + s.get("label","") for s in break_scenes).lower()

    # Ask LLM: what is the single most iconic catastrophic/breaking moment for this subject?
    iconic_moment = _cached_infer(
        f"For a documentary called '{topic}' about '{entity}': "
        f"What is the single most iconic, emotionally powerful 'break' moment — "
        f"the event that most symbolises the collapse or turning point of the story? "
        f"Be specific: include year, opponent (if applicable), and a short description. "
        f"Return it as ONE sentence, max 15 words.",
        expected_type="str",
        fallback=None,
    )
    if not iconic_moment:
        return

    # Check if it's already covered (≥2 keywords from the moment already in break scenes)
    keywords = [w.lower() for w in iconic_moment.split() if len(w) > 3]
    keyword_hits = sum(1 for kw in keywords if kw in break_text)
    if keyword_hits >= 2:
        return  # already present

    print(f"  [BreakMoment] Injecting missing canonical break moment: {iconic_moment}")

    import time as _t
    def _sid():
        return f"s_break_{int(_t.time()*1000) % 1000000}"

    act_name = next((s.get("act","ACT 3") for s in break_scenes), "ACT 3")
    insert_pos = max(i for i, s in enumerate(scenes) if s.get("actIndex") == 3) + 1

    scenes.insert(insert_pos, {
        "id": _sid(), "act": act_name, "actIndex": 3,
        "type": "narration", "template": "NARRATION",
        "content": iconic_moment,
        "label": "", "duration": 6,
    })
    scenes.insert(insert_pos, {
        "id": _sid(), "act": act_name, "actIndex": 3,
        "type": "clip", "template": "CLIP SINGLE",
        "content": iconic_moment,
        "label": iconic_moment[:50], "duration": 10,
    })


def _warn_suspect_facts(scenes: list) -> None:
    """Best-effort fact-trap detector. Flags well-known LLM hallucinations
    (player at wrong club, wrong World Cup totals, impossible matches).
    Does not strip scenes — prints a [FactCheck] warning so the user can
    spot-check before approving the storyboard. Cheap, no LLM calls."""
    import re as _re

    # Hardcoded "player NEVER played for X" combos we've seen the LLM invent.
    # Format: lowercased player name → set of clubs they never played for (lowercased).
    NEVER_PLAYED_FOR = {
        "ronaldinho":  {"chelsea", "manchester united", "real madrid", "liverpool", "arsenal", "bayern munich", "juventus"},
        "pelé":        {"real madrid", "barcelona", "manchester united", "ac milan", "juventus"},
        "pele":        {"real madrid", "barcelona", "manchester united", "ac milan", "juventus"},
        "garrincha":   {"real madrid", "barcelona", "manchester united", "ac milan", "psg"},
        "zico":        {"real madrid", "barcelona", "manchester united", "ac milan", "chelsea"},
        "sócrates":    {"real madrid", "barcelona", "manchester united", "ac milan", "chelsea", "psg"},
        "socrates":    {"real madrid", "barcelona", "manchester united", "ac milan", "chelsea", "psg"},
        "rivaldo":     {"real madrid", "manchester united", "psg", "chelsea", "ac milan", "juventus"},
        "ronaldo":     {"manchester united", "liverpool", "chelsea"},  # the Brazilian R9
        "kaká":        {"barcelona", "manchester united", "chelsea", "psg", "liverpool"},
        "kaka":        {"barcelona", "manchester united", "chelsea", "psg", "liverpool"},
        "neymar":      {"manchester united", "real madrid", "liverpool", "chelsea"},
        "messi":       {"manchester united", "real madrid", "chelsea", "manchester city", "bayern munich"},
        "lionel messi":{"manchester united", "real madrid", "chelsea", "manchester city", "bayern munich"},
    }

    # Country → correct World Cup total (men's). Keep loose-but-firm.
    WC_TOTALS = {
        "brazil":    5,
        "italy":     4,
        "germany":   4,  # incl. West Germany
        "argentina": 3,
        "uruguay":   2,
        "france":    2,
        "england":   1,
        "spain":     1,
    }

    # Famous matches that NEVER happened — direct red flag if the LLM names them.
    IMPOSSIBLE_MATCHES = [
        ("brazil",    "france",        "1982 world cup"),
        ("argentina", "italy",         "1986 world cup final"),
        ("brazil",    "germany",       "2014 world cup final"),  # they met in semi, not final
    ]

    def _scene_text(s):
        return f"{s.get('content','')} {s.get('label','')}".lower()

    flagged = 0
    for s in scenes:
        text  = _scene_text(s)
        tmpl  = s.get("template", "").upper()

        # Trap 1: CAREER TIMELINE with impossible Player→Club combo
        if tmpl == "CAREER TIMELINE":
            for player, banned_clubs in NEVER_PLAYED_FOR.items():
                if player in text:
                    for club in banned_clubs:
                        if club in text:
                            print(f"  [FactCheck] ⚠ HALLUCINATION SUSPECT — CAREER TIMELINE puts {player.title()} at {club.title()} (player never played there). Scene id={s.get('id')!r}, content={s.get('content','')!r}")
                            flagged += 1
                            break

        # Trap 2: BIG STAT with wrong World Cup count for a country
        if tmpl == "HERO BIG STAT" and "world cup" in text:
            m = _re.search(r"\b(\d+)\b", s.get("content",""))
            if m:
                claimed = int(m.group(1))
                for country, real_total in WC_TOTALS.items():
                    if country in text and 0 < claimed < 10 and claimed != real_total:
                        print(f"  [FactCheck] ⚠ WRONG WORLD CUP COUNT — claimed {claimed} for {country.title()}, real total is {real_total}. Scene id={s.get('id')!r}, content={s.get('content','')!r}")
                        flagged += 1

        # Trap 3: impossible historical match
        for team_a, team_b, tag in IMPOSSIBLE_MATCHES:
            if team_a in text and team_b in text and tag in text:
                print(f"  [FactCheck] ⚠ IMPOSSIBLE MATCH — '{team_a.title()} vs {team_b.title()} {tag}' did not happen. Scene id={s.get('id')!r}, content={s.get('content','')!r}")
                flagged += 1

    if flagged:
        print(f"  [FactCheck] {flagged} suspect fact(s) flagged — review before approving storyboard.")


def _validate_stat_content(scenes):
    """Flag or demote HERO BIG STAT scenes where content is malformed.

    Valid format: "stat, unit, label, context" (4 comma-separated parts, leading stat is numeric).
    Rejects:
    - No digits at all
    - Fewer than 2 comma-separated parts (e.g. "Ronaldinho 1998/15" — name+career-span, not a stat)
    """
    import re as _re
    for s in scenes:
        if s.get("template","").upper() != "HERO BIG STAT":
            continue
        content = s.get("content","")
        parts = [p.strip() for p in content.split(",")]
        has_digit = bool(_re.search(r'\d', content))
        # First part should be the numeric stat value (e.g. "31", "0.94")
        first_part_is_numeric = bool(parts and _re.match(r'^[\d\.]+$', parts[0]))
        if not has_digit or len(parts) < 2 or not first_part_is_numeric:
            # HERO CONCEPT is deprecated — convert to NARRATION so the beat survives
            # but doesn't render as a broken graphic.
            s["template"] = ""
            s["type"]     = "narration"
            print(f"  [StatValidate] Invalid BIG STAT format — converted to NARRATION: {content[:60]}")


def _recalculate_durations(scenes):
    """Recalculate scene durations to hit a realistic 10–18 minute runtime.

    Narration: word-count based at 130 words/minute (2.17 words/sec), min 6s.
    Clip: LLM value respected if ≥10s; floor at 12s otherwise.
    Graphic: LLM value respected if ≥10s; floor at 10s otherwise.
    Transition: always 2s.
    """
    import re as _re
    for s in scenes:
        stype = s.get("type", "")
        current = s.get("duration", 0) or 0

        if stype == "transition":
            s["duration"] = 2
        elif stype == "narration":
            words = len(_re.findall(r'\b\w+\b', s.get("content", "")))
            # 130 words/min = 2.17 words/sec; add 1.5s lead/trail silence
            calculated = max(6, round(words / 2.17 + 1.5))
            # Keep LLM value if it's higher (LLM sometimes sets longer for dramatic pauses)
            s["duration"] = max(calculated, current) if current > 6 else calculated
        elif stype == "clip":
            s["duration"] = max(12, current)
        elif stype == "graphic":
            s["duration"] = max(10, current)


def _validate_act_structure(scenes, is_thematic):
    """Remap actIndex from act name keywords and remove duplicate/mislabelled act sections.

    Fixes: 'ACT 3 — THE BREAK' followed by 'ACT 3 — THE PEAK' (LLM emits two conflicting
    act names for the same actIndex). Picks the first canonical name per index and drops
    orphaned scenes that belong to a superseded act name.
    After remapping, clamps any out-of-order actIndex to be monotonically non-decreasing.
    """
    THEMATIC_KEYWORD_MAP = {
        "cold open": 0, "myth": 1, "shift": 2, "break": 3, "consequence": 4, "question": 5,
    }
    STANDARD_KEYWORD_MAP = {
        "cold open": 0, "origins": 1, "rise": 2, "peak": 3, "defining": 4,
        "redemption": 5, "legacy": 5,
    }
    keyword_map = THEMATIC_KEYWORD_MAP if is_thematic else STANDARD_KEYWORD_MAP

    # Pass 1: remap actIndex from act name keyword if a keyword matches
    for s in scenes:
        act_lower = s.get("act", "").lower()
        for kw, idx in keyword_map.items():
            if kw in act_lower:
                s["actIndex"] = idx
                break

    # Pass 2: for each actIndex, record the canonical act name (first seen)
    canonical_name: dict = {}
    for s in scenes:
        ai = s.get("actIndex", 0)
        if ai not in canonical_name:
            canonical_name[ai] = s.get("act", "")

    # Remove scenes whose act name conflicts with the canonical name for that actIndex
    before = len(scenes)
    kept = []
    for s in scenes:
        ai = s.get("actIndex", 0)
        if s.get("act", "") == canonical_name.get(ai, s.get("act", "")):
            kept.append(s)
        else:
            print(f"  [ActValidate] Dropped orphan scene (actIndex={ai}, act='{s.get('act')}' vs canonical='{canonical_name.get(ai)}'): {s.get('content','')[:60]}")
    scenes[:] = kept

    # Pass 3: clamp monotonically non-decreasing
    max_seen = 0
    for s in scenes:
        ai = s.get("actIndex", 0)
        if ai < max_seen:
            s["actIndex"] = max_seen
        else:
            max_seen = ai

    if len(scenes) < before:
        print(f"  [ActValidate] Removed {before - len(scenes)} mislabelled scenes")


def _remove_invalid_scenes(scenes):
    """Drop scenes with None/empty content that would break the renderer."""
    import re as _re
    before = len(scenes)
    kept = []

    # Templates whose tag content MUST start with a real player/entity name.
    # When the LLM lacks a clear subject for these (common in thematic docs)
    # it emits placeholder garbage like "None - Focus: Manchester United".
    _NAME_REQUIRED = {
        "PLAYER STATS", "PLAYER RADAR", "PLAYER TRIO", "CAREER TIMELINE",
        "HERO BIG STAT", "DISCIPLINARY RECORD", "HERO SHOT MAP",
    }

    # Detects the "None" placeholder pattern across formats:
    # "None - Focus: X", "None Player", "none, season 22/23", " None  - ..." etc.
    _NONE_PLACEHOLDER = _re.compile(r'^\s*none\b', _re.IGNORECASE)

    for s in scenes:
        stype = s.get("type", "")
        content = s.get("content", "")
        template = (s.get("template", "") or "").upper()

        if stype == "transition":
            kept.append(s)
            continue
        if content in ("", "None", None):
            print(f"  [Validate] Dropped scene with empty content: {template} id={s.get('id','')}")
            continue
        if stype == "graphic" and not s.get("template", ""):
            print(f"  [Validate] Dropped graphic with no template: id={s.get('id','')}")
            continue
        if stype == "clip" and _re.search(r'\bNone\b', str(content)):
            print(f"  [Validate] Dropped clip with None entity: {content[:60]}")
            continue

        # NEW: drop graphic scenes for name-required templates that start with
        # the "None" placeholder (LLM didn't have a real subject).
        if stype == "graphic" and template in _NAME_REQUIRED and _NONE_PLACEHOLDER.match(str(content)):
            print(f"  [Validate] Dropped {template} with 'None' placeholder: {content[:60]!r}")
            continue

        # NEW: PLAYER STATS expects "Player Name YYYY/YY". Drop entries that
        # are clearly stat descriptions ("Average distance covered per game…")
        # rather than a player season tag.
        if stype == "graphic" and template == "PLAYER STATS":
            # Must contain at least one capitalised first+last name AND a year.
            # Reject if first word is lowercase (likely a stat description).
            txt = str(content).strip()
            first_word = txt.split()[0] if txt.split() else ""
            looks_like_player = bool(first_word) and first_word[0].isupper() and first_word.lower() not in {
                "average", "total", "shots", "goals", "passes", "minutes",
                "distance", "tackles", "key", "expected", "all",
            }
            has_year = bool(_re.search(r"\b(19|20)\d{2}", txt))
            if not (looks_like_player and has_year):
                print(f"  [Validate] Dropped PLAYER STATS with no valid player tag: {txt[:60]!r}")
                continue

        kept.append(s)
    scenes[:] = kept
    if len(scenes) < before:
        print(f"  [Validate] Removed {before - len(scenes)} invalid scenes")


def _downgrade_intra_act_push(scenes):
    """Continuous-world rule: an explicit [TRANSITION: push] sitting inside
    an act (i.e. between two non-transition scenes that share the same
    actIndex) is downgraded to worldPan. Push is reserved for genuine world
    breaks; within an act it shatters the one-camera feel.

    Cross-act pushes are left alone — those are punctuation between acts and
    sometimes the right call. Same for letterbox/grain/paper which carry
    intentional emotional weight."""
    DOWNGRADE_FROM = {"push"}
    DOWNGRADE_TO   = "worldPan"

    for i, s in enumerate(scenes):
        if s.get("type") != "transition":
            continue
        ttype = str(s.get("content", "") or s.get("tag_text", "")).strip().lower()
        if ttype not in DOWNGRADE_FROM:
            continue
        # Find the closest non-transition scenes on each side
        prev = next((scenes[k] for k in range(i - 1, -1, -1) if scenes[k].get("type") != "transition"), None)
        nxt  = next((scenes[k] for k in range(i + 1, len(scenes)) if scenes[k].get("type") != "transition"), None)
        if prev is None or nxt is None:
            continue
        if prev.get("actIndex") == nxt.get("actIndex"):
            s["content"] = DOWNGRADE_TO
            s["tag_text"] = DOWNGRADE_TO
            print(f"  [Continuity] Downgraded intra-act push → worldPan (act {prev.get('actIndex')})")


def _split_consecutive_same_template_graphics(scenes):
    """Inject a brief NARRATION beat between two graphics that share the
    same template and act with no narration between them. Two TEAM LINEUPs
    back to back (e.g. Brazil XI then Germany XI for the same match) is the
    canonical case — without a beat between, it reads as a slide show."""
    inserts = []  # (insert_at_idx, narration_scene)
    for i in range(len(scenes) - 1):
        a = scenes[i]
        if a.get("type") != "graphic":
            continue
        # Find the next non-transition scene after `a` (transitions don't
        # count as separation; narration does and breaks the search).
        j = i + 1
        while j < len(scenes) and scenes[j].get("type") == "transition":
            j += 1
        if j >= len(scenes):
            break
        b = scenes[j]
        if b.get("type") != "graphic":
            continue
        if (a.get("template") or "").upper() != (b.get("template") or "").upper():
            continue
        if a.get("actIndex") != b.get("actIndex"):
            continue
        # Skip pairs already deliberately joined by `evolve` (those are
        # designed to read as one continuous scene)
        if any(s.get("type") == "transition" and str(s.get("content", "")).lower() == "evolve"
               for s in scenes[i + 1:j]):
            continue
        beat = {
            "id": _make_sid(),
            "act": a.get("act", ""),
            "actIndex": a.get("actIndex", 0),
            "type": "narration",
            "template": "NARRATION",
            "content": f"— beat between {a.get('template','')} graphics —",
            "label": "(auto-injected beat)",
            "duration": 4,
            "auto_injected": True,
        }
        inserts.append((j, beat))

    # Insert in reverse so earlier indices stay valid
    for idx, beat in reversed(inserts):
        scenes.insert(idx, beat)
    if inserts:
        print(f"  [Continuity] Auto-injected {len(inserts)} narration beat(s) between same-template graphics")


def _enforce_act_type_templates(scenes, is_thematic):
    """Shape templates to match the emotional register of each act.

    For thematic docs:
      - THE BREAK (actIndex=3): replace data-heavy stat templates with HERO BIG STAT
      - THE QUESTION (actIndex=5): replace stat templates

    CLIP COMPARE is no longer capped — chained comparisons (e.g. "but if we
    look at...") are valid editorial moves and the script should be free to
    use multiple per act when narratively justified.

    Replace before remove — only drop if replacement would duplicate an existing template in that act.
    """
    if not is_thematic:
        return

    BREAK_DISCOURAGED = {"HERO STAT BARS", "TOP SCORERS", "TOP ASSISTS", "STANDINGS TABLE", "PLAYER STATS"}
    QUESTION_DISCOURAGED = {"HERO STAT BARS", "TOP SCORERS", "TOP ASSISTS", "STANDINGS TABLE", "PLAYER STATS"}

    def _templates_in_act(act_idx):
        return {s.get("template", "").upper() for s in scenes if s.get("actIndex") == act_idx and s.get("type") == "graphic"}

    for s in scenes:
        ai = s.get("actIndex", 0)
        if s.get("type") != "graphic":
            continue
        tpl = s.get("template", "").upper()

        if ai == 3 and tpl in BREAK_DISCOURAGED:
            existing = _templates_in_act(3)
            replacement = "HERO BIG STAT"
            if replacement not in existing or s.get("template", "").upper() == replacement:
                s["template"] = replacement
                print(f"  [ActTemplates] BREAK: replaced {tpl} → {replacement}")
            else:
                scenes.remove(s)
                print(f"  [ActTemplates] BREAK: dropped {tpl} (replacement already present)")

        elif ai == 5 and tpl in QUESTION_DISCOURAGED:
            existing = _templates_in_act(5)
            replacement = "HERO BIG STAT"
            if replacement not in existing or tpl == replacement:
                s["template"] = replacement
                print(f"  [ActTemplates] QUESTION: replaced {tpl} → {replacement}")
            else:
                scenes.remove(s)
                print(f"  [ActTemplates] QUESTION: dropped {tpl} (replacement already present)")


def _enforce_anchor_presence(scenes, retention_brief):
    """Ensure the anchor character is present in at least 3 acts (including acts 3 and 5).

    Weaves anchor_framing as a sentence into existing narration rather than appending
    generic boilerplate. Never modifies more than 3 scenes.
    """
    if not retention_brief:
        return
    anchor = retention_brief.get("anchor_character") or {}
    anchor_name = (anchor.get("name") or "").strip()
    anchor_framing = (anchor.get("framing") or "").strip()
    if not anchor_name:
        return

    # Build per-act presence map
    def _acts_containing_anchor():
        result = set()
        for s in scenes:
            if anchor_name.lower() in s.get("content", "").lower():
                result.add(s.get("actIndex", 0))
        return result

    required_acts = {3, 5}
    max_modifications = 3
    modified = 0

    def _make_anchor_sentence(framing, act_idx):
        sentence = framing.split(".")[0].strip()
        if not sentence:
            return None
        if not sentence.endswith("."):
            sentence += "."
        return sentence

    def _weave_into(content, sentence, act_idx):
        sentences = content.rstrip().split(". ")
        if len(sentences) <= 1:
            return content + " " + sentence
        # Vary insertion position by act to avoid identical placement across acts
        if act_idx % 2 == 0:
            insert_at = max(len(sentences) - 1, 1)
        else:
            insert_at = max(len(sentences) - 2, 1)
        sentences.insert(insert_at, sentence.rstrip("."))
        return ". ".join(sentences)

    present_acts = _acts_containing_anchor()
    missing = (required_acts | {1, 2}) - present_acts  # check required + early acts
    missing = sorted(missing)[:3]  # limit to 3 modifications

    for ai in missing:
        if modified >= max_modifications:
            break
        present_acts = _acts_containing_anchor()
        if ai in present_acts:
            continue

        # Find longest narration scene in this act
        candidates = [s for s in scenes if s.get("actIndex") == ai and s.get("type") == "narration"]
        if not candidates:
            # Inject a new minimal narration scene using the framing directly
            anchor_scene_act = next((s.get("act", f"ACT {ai}") for s in scenes if s.get("actIndex") == ai), f"ACT {ai}")
            import time as _t
            scenes.append({
                "id": f"s_anchor_{int(_t.time()*1000) % 1000000}",
                "act": anchor_scene_act, "actIndex": ai,
                "type": "narration", "template": "NARRATION",
                "content": anchor_framing,
                "label": "", "duration": 6,
            })
            modified += 1
            print(f"  [AnchorEnforce] Injected narration for anchor in act {ai}")
            continue

        target = max(candidates, key=lambda s: len(s.get("content", "")))
        sentence = _make_anchor_sentence(anchor_framing, ai)
        if sentence and anchor_name.lower() not in target.get("content", "").lower():
            target["content"] = _weave_into(target.get("content", ""), sentence, ai)
            modified += 1
            print(f"  [AnchorEnforce] Wove anchor framing into act {ai} narration")

    final_present = _acts_containing_anchor()
    if len(final_present) < 3:
        print(f"  [AnchorEnforce] Warning: anchor '{anchor_name}' only in {len(final_present)} acts after enforcement")


def _enforce_closing_rule(scenes, retention_brief):
    """Deduplicate redundant final-act narration; ensure last scene is a narration.

    Also injects closing_question from retention_brief if not already present.
    """
    import re as _re

    STOP_WORDS = {"the","a","an","is","was","are","were","and","or","but","in","on","at",
                  "to","of","for","with","that","this","it","he","she","they","his","her"}

    def _sig_words(text):
        return {w for w in _re.findall(r'\b\w{4,}\b', text.lower()) if w not in STOP_WORDS}

    max_act = max((s.get("actIndex", 0) for s in scenes), default=0)
    final_narrations = [s for s in scenes if s.get("actIndex") == max_act and s.get("type") == "narration"]

    # Remove redundant narrations: if two share ≥4 significant words, drop the earlier
    to_remove = set()
    for i, a in enumerate(final_narrations):
        if a.get("id") in to_remove:
            continue
        words_a = _sig_words(a.get("content", ""))
        for b in final_narrations[i+1:]:
            if len(words_a & _sig_words(b.get("content", ""))) >= 4:
                to_remove.add(a.get("id"))
                print(f"  [ClosingRule] Dropped redundant final-act narration: {a.get('content','')[:60]}")
                break

    if to_remove:
        scenes[:] = [s for s in scenes if s.get("id") not in to_remove]
        final_narrations = [s for s in scenes if s.get("actIndex") == max_act and s.get("type") == "narration"]

    # Ensure last scene overall is a narration
    if scenes and scenes[-1].get("type") != "narration" and final_narrations:
        last_narr = final_narrations[-1]
        scenes.remove(last_narr)
        scenes.append(last_narr)
        print(f"  [ClosingRule] Moved final narration to end of storyboard")

    # Inject closing_question if not already present
    if retention_brief:
        cq = (retention_brief.get("closing_question") or "").strip()
        if cq and final_narrations:
            present = any(cq.lower()[:30] in s.get("content","").lower() for s in final_narrations)
            if not present:
                last = final_narrations[-1]
                last["content"] = last["content"].rstrip() + f" {cq}"
                print(f"  [ClosingRule] Appended closing_question to final narration")


def _enforce_minimum_density(scenes, retention_brief=None, blueprint=None):
    """Ensure each act has at least 1 narration, 1 clip, 1 graphic, and 3 non-transition scenes.

    Runs after all removal passes to prevent hollow acts. Injects placeholders derived
    from act_reframes (retention_brief) or act name where real content is unavailable.
    """
    import time as _t
    import re as _re

    def _sid():
        return f"s_density_{int(_t.time()*1000) % 1000000}"

    act_reframes = {}
    if retention_brief:
        for rf in (retention_brief.get("act_reframes") or []):
            act_lower = rf.get("act","").lower()
            for idx, kws in [(0,["cold"]), (1,["myth","origins"]), (2,["shift","rise"]),
                             (3,["break","peak"]), (4,["consequence","defining"]), (5,["question","legacy","redemption"])]:
                if any(kw in act_lower for kw in kws):
                    act_reframes[idx] = rf
                    break

    # Group scenes by act
    act_indices = sorted({s.get("actIndex", 0) for s in scenes})
    for ai in act_indices:
        act_scenes = [s for s in scenes if s.get("actIndex") == ai]
        act_name = next((s.get("act","") for s in act_scenes), f"ACT {ai}")
        non_trans = [s for s in act_scenes if s.get("type") != "transition"]

        has_narration = any(s.get("type") == "narration" for s in non_trans)
        has_clip      = any(s.get("type") == "clip" for s in non_trans)
        has_graphic   = any(s.get("type") == "graphic" and s.get("template","").upper() not in ("HERO INTRO","TRANSITION") for s in non_trans)
        sufficient    = len(non_trans) >= 3

        rf = act_reframes.get(ai, {})
        rf_question = (rf.get("question") or "").strip()

        # Find an anchor insertion position (after last transition in this act, or end)
        def _insert_pos():
            last_trans = max((i for i, s in enumerate(scenes) if s.get("actIndex") == ai and s.get("type") == "transition"), default=None)
            if last_trans is not None:
                return last_trans + 1
            first_in_act = next((i for i, s in enumerate(scenes) if s.get("actIndex") == ai), len(scenes))
            return first_in_act + 1

        if not has_narration:
            content = rf_question or f"{act_name} — narrative moment"
            scenes.insert(_insert_pos(), {
                "id": _sid(), "act": act_name, "actIndex": ai,
                "type": "narration", "template": "NARRATION",
                "content": content, "label": "", "duration": 6,
            })
            print(f"  [Density] Injected narration for act {ai}")

        if not has_clip:
            narr = next((s for s in scenes if s.get("actIndex") == ai and s.get("type") == "narration"), None)
            clip_content = (narr.get("content","")[:80] if narr else f"{act_name} — footage")
            scenes.insert(_insert_pos(), {
                "id": _sid(), "act": act_name, "actIndex": ai,
                "type": "clip", "template": "CLIP SINGLE",
                "content": clip_content, "label": "", "duration": 8,
            })
            print(f"  [Density] Injected clip for act {ai}")

        if not has_graphic:
            stat_content = rf_question or f"{act_name} — key moment"
            scenes.insert(_insert_pos(), {
                "id": _sid(), "act": act_name, "actIndex": ai,
                "type": "graphic", "template": "HERO BIG STAT",
                "content": stat_content, "label": "", "duration": 10,
            })
            print(f"  [Density] Injected graphic for act {ai}")


def _generate_storyboard(topic, entity, blueprint, checked_facts, wiki="", context="", retention_brief=None, format_override=None, director_override=""):
    from agents.storyboard_agent import generate_scenes as _agent_generate

    scenes = _agent_generate(
        topic, entity, blueprint, checked_facts,
        wiki=wiki, context=context, retention_brief=retention_brief,
        director_override=director_override,
    )
    if not scenes:
        return {"scenes": [], "totalDuration": 0}

    result = {"scenes": scenes, "totalDuration": 0}

    # ── Post-process: enforce required scenes the LLM keeps omitting ──────────
    import time as _time
    import re as _re

    _sid_counter = [0]
    def _sid():
        _sid_counter[0] += 1
        return f"s_auto_{int(_time.time() * 1000) % 1000000}_{_sid_counter[0]}"

    # Normalise actIndex by act name keywords — LLM often sets all to 0
    # Covers both biography (origins/rise/peak) and thematic (myth/shift/break/consequence/question) structures
    ACT_KEYWORDS = [
        (0, ["cold open"]),
        (1, ["origins", "act 1", "myth"]),
        (2, ["rise", "act 2", "shift"]),
        (3, ["peak", "act 3", "break"]),
        (4, ["defining", "act 4", "consequence"]),
        (5, ["redemption", "legacy", "act 5", "question"]),
    ]
    for s in scenes:
        act_lower = s.get("act", "").lower()
        for idx, kws in ACT_KEYWORDS:
            if any(kw in act_lower for kw in kws):
                s["actIndex"] = idx
                break

    # Fallback: if fewer than 3 distinct actIndices found (LLM all-zero bug),
    # derive actIndex from unique act names in order of first appearance.
    distinct_indices = set(s.get("actIndex", 0) for s in scenes)
    if len(distinct_indices) < 3:
        print(f"  [Storyboard] actIndex all-zero bug detected ({len(distinct_indices)} distinct) — sequential fallback")
        act_name_to_idx: dict = {}
        seq_counter = 0
        for s in scenes:
            act_name = s.get("act", "").strip()
            if act_name not in act_name_to_idx:
                # Try keyword match first
                act_lower = act_name.lower()
                matched = None
                for idx, kws in ACT_KEYWORDS:
                    if any(kw in act_lower for kw in kws):
                        matched = idx
                        break
                if matched is not None:
                    act_name_to_idx[act_name] = matched
                    seq_counter = max(seq_counter, matched + 1)
                else:
                    act_name_to_idx[act_name] = seq_counter
                    seq_counter += 1
            s["actIndex"] = act_name_to_idx[act_name]
        new_distinct = set(s.get("actIndex", 0) for s in scenes)
        print(f"  [Storyboard] actIndex fallback: {len(new_distinct)} acts resolved — {sorted(new_distinct)}")

    # Detect thematic mode for all downstream enforcement passes
    from agents.script_agent import _is_thematic as _check_thematic
    _is_thematic_doc = _check_thematic(entity, context or "")

    # Validate act structure: fix duplicate act names for same actIndex, clamp ordering
    _validate_act_structure(scenes, _is_thematic_doc)

    # Remove scenes with None/empty content before any further processing
    _remove_invalid_scenes(scenes)

    def _has_template(tpl):
        return any(s.get("template","").upper() == tpl.upper() for s in scenes)

    def _first_scene_of_act(act_idx):
        return next((i for i, s in enumerate(scenes) if s.get("actIndex", -1) == act_idx), None)

    def _last_scene_of_act(act_idx):
        last = -1
        for i, s in enumerate(scenes):
            if s.get("actIndex", -1) == act_idx:
                last = i
        return last

    # 1. HERO INTRO must be scene 0 with exact title
    if not scenes or scenes[0].get("template","").upper() != "HERO INTRO":
        intro = {"id": _sid(), "act": "COLD OPEN", "actIndex": 0,
                 "type": "graphic", "template": "HERO INTRO", "content": topic, "label": "", "duration": 8}
        scenes.insert(0, intro)
    else:
        # Fix content regardless — strip any "DOCUMENTARY:" prefix
        scenes[0]["content"] = _re.sub(r'^(?:documentary|title|video|doc)\s*:\s*', '', scenes[0]["content"], flags=_re.IGNORECASE).strip() or topic

    # 2. TRANSITION at start of each act (before first scene of that act)
    act_transitions = [(1, "letterbox"), (2, "push"), (3, "letterbox"), (4, "grain"), (5, "paper")]
    for act_idx, ttype in reversed(act_transitions):  # reversed so insertions don't shift positions
        first_pos = _first_scene_of_act(act_idx)
        if first_pos is None:
            continue
        # Skip if the first scene of this act IS already a transition (LLM placed it)
        if scenes[first_pos].get("template","").upper() == "TRANSITION":
            continue
        # Skip if the scene immediately before this act is already a transition
        prev = scenes[first_pos - 1] if first_pos > 0 else None
        if prev and prev.get("template","").upper() == "TRANSITION":
            continue
        act_name = scenes[first_pos]["act"]
        trans = {"id": _sid(), "act": act_name, "actIndex": act_idx,
                 "type": "transition", "template": "TRANSITION", "content": ttype, "label": "", "duration": 2}
        scenes.insert(first_pos, trans)

    # 2-pre. HERO CONCEPT is deprecated — coerce to COMPARISON RADAR if content
    # looks like a player-vs-player stat compare, otherwise convert to NARRATION.
    for scene in scenes:
        if scene.get("template", "").strip().upper() == "HERO CONCEPT":
            content = scene.get("content", "")
            if " vs " in content.lower() or " vs. " in content.lower():
                scene["template"] = "HERO COMPARISON RADAR"
                scene["type"]     = "graphic"
                print(f"  [Storyboard] HERO CONCEPT -> COMPARISON RADAR: {content[:60]}")
            else:
                scene["template"] = ""
                scene["type"]     = "narration"
                print(f"  [Storyboard] HERO CONCEPT -> NARRATION: {content[:60]}")

    # 2a. Normalise transition scenes: LLM sometimes emits {template: "push", content: "push"}
    # instead of {template: "TRANSITION", content: "push"}. Promote so the dedup at 2b catches both.
    _TRANSITION_TYPES = {"push", "letterbox", "grain", "paper", "dataline", "flash", "evolve", "worldpan"}
    for scene in scenes:
        tmpl_lc = scene.get("template", "").strip().lower()
        if tmpl_lc in _TRANSITION_TYPES:
            # Move the transition type into content; mark template TRANSITION
            scene["content"]  = scene.get("content") or scene["template"]
            scene["template"] = "TRANSITION"
            scene["type"]     = "transition"

    # 2b. Remove consecutive TRANSITION scenes — keep only the first of any run
    cleaned = []
    for scene in scenes:
        if scene.get("template", "").upper() == "TRANSITION" and cleaned and cleaned[-1].get("template", "").upper() == "TRANSITION":
            continue  # drop duplicate
        cleaned.append(scene)
    scenes[:] = cleaned

    # 3. PLAYER TRIO — validate and fix if needed (LLM keeps writing "MSN vs BBC" etc.)
    # Valid format: "the debate, Player1 vs Player2 vs Player3" — three INDIVIDUAL full player names
    def _trio_peers_for(ent, brief=None):
        """Return (subject, peer1, peer2) for the PLAYER TRIO graphic.

        For national/thematic docs (entity is a country or concept):
          - Use the anchor character as the trio subject
          - Request same-nationality peers
        For player biographies:
          - Use entity as subject, global era peers
        """
        # Detect if entity is a national/thematic concept, not a player name
        ent_lower = ent.lower()
        _NATIONAL_KEYWORDS = [
            "brazil", "argentina", "france", "spain", "germany", "england", "italy",
            "portugal", "netherlands", "belgium", "croatia", "uruguay", "colombia",
            "chile", "mexico", "senegal", "nigeria", "ghana", "why", "how", "story of",
            "decline", "rise of", "death of", "end of", "stopped", "lost",
        ]
        is_national = (
            any(kw in ent_lower for kw in _NATIONAL_KEYWORDS)
            or len(ent.split()) > 3
        )

        if is_national and brief and isinstance(brief, dict):
            # Read anchor_character (singular) first; fall back to anchor_candidates[0]
            # because the engine writes the candidates list but only sets anchor_character
            # when the user explicitly overrides via the anchor dropdown.
            anchor_name = (brief.get("anchor_character") or {}).get("name", "")
            if not anchor_name:
                cands = brief.get("anchor_candidates") or []
                if cands and isinstance(cands[0], dict):
                    anchor_name = cands[0].get("name", "")
            if anchor_name:
                # Detect nationality from entity string
                nat_hint = ""
                for country in ["Brazil", "Argentina", "France", "Spain", "Germany",
                                 "England", "Italy", "Portugal", "Netherlands", "Belgium"]:
                    if country.lower() in ent_lower:
                        nat_hint = f"Return only {country}n players from the same era."
                        break

                peers = _cached_infer(
                    f"Name two footballers from the same era and same nationality as {anchor_name} "
                    f"who played a similar or comparable role — for a documentary comparison. "
                    f"{nat_hint} Return exactly 2 full player names.",
                    expected_type="list",
                    fallback=None,
                )
                if peers and len(peers) >= 2:
                    return anchor_name, peers[0], peers[1]
                # Fallback: global peers for anchor (never produce placeholder strings)
                global_peers = _cached_infer(
                    f"Name the two footballers most widely compared to {anchor_name}. "
                    f"Return exactly 2 full player names.",
                    expected_type="list",
                    fallback=None,
                )
                if global_peers and len(global_peers) >= 2:
                    return anchor_name, global_peers[0], global_peers[1]

        # Standard player biography: global era comparison
        peers = _cached_infer(
            f"Name the two footballers most widely compared to {ent} — their direct "
            f"rivals or peers in the GOAT debate or era comparison. "
            f"Return exactly 2 full player names.",
            expected_type="list",
            fallback=["Lionel Messi", "Cristiano Ronaldo"],
        )
        if peers and len(peers) >= 2:
            return ent, peers[0], peers[1]
        return ent, "Lionel Messi", "Cristiano Ronaldo"

    def _is_valid_trio(content):
        """Return True only if content has exactly 3 individual player names."""
        # Normalise "vs." → "vs" before splitting
        norm = _re.sub(r'\bvs\.\s*', 'vs ', content, flags=_re.IGNORECASE)
        parts = [p.strip() for p in norm.split(' vs ')]
        if len(parts) != 3:
            return False
        # Each part (strip the title prefix from part 0 if present)
        players = [parts[0].split(',')[-1].strip(), parts[1], parts[2]]
        for p in players:
            clean = _re.sub(r'[^A-Za-z\s]', '', p).strip()
            words = clean.split()
            # Need ≥2 words; reject single-word group abbreviations like MSN, BBC, BBC
            if len(words) < 2:
                return False
            if any(w.isupper() and 2 <= len(w) <= 4 for w in words):
                return False  # group abbreviation
        return True

    # Coerce None/empty entity to topic so we never render "None vs Messi vs Ronaldo"
    _trio_entity = entity if (entity and str(entity).strip().lower() not in ("none", "null")) else (topic or "")
    trio_subject, peer1, peer2 = _trio_peers_for(_trio_entity, retention_brief)
    if not trio_subject or str(trio_subject).strip().lower() in ("none", "null", ""):
        trio_subject = _trio_entity or "the subject"
    correct_trio_content = f"the debate, {trio_subject} vs {peer1} vs {peer2}"
    trio_scene = {"id": _sid(), "act": "ACT 3 — PEAK", "actIndex": 3,
                  "type": "graphic", "template": "PLAYER TRIO",
                  "content": correct_trio_content, "label": "peak vs peak", "duration": 12}
    # For national/thematic docs, trio_subject != entity (we swapped to the anchor).
    # Always override in that case — LLM may have produced "Brazil vs Messi vs Ronaldo".
    _force_trio_override = (trio_subject != entity)

    trio_idx = next((i for i, s in enumerate(scenes) if s.get("template","").upper() == "PLAYER TRIO"), None)
    if trio_idx is not None:
        existing = scenes[trio_idx].get("content", "")
        if _force_trio_override or not _is_valid_trio(existing):
            scenes[trio_idx]["content"] = correct_trio_content
            scenes[trio_idx]["label"] = "peak vs peak"
            if _force_trio_override:
                print(f"  [Storyboard] PLAYER TRIO overridden for national doc: {correct_trio_content}")
    # Note: do NOT auto-inject a PLAYER TRIO when none exists. Many docs work
    # better without a peer-comparison scene; the LLM should decide whether to
    # include one. Previously we inserted "Subject vs Lionel Messi vs Cristiano
    # Ronaldo" by default, which the user kept having to delete manually.

    # 3a. Anchor re-validation — verify anchor from retention_brief exists in storyboard content
    if retention_brief and isinstance(retention_brief, dict):
        anchor_name = retention_brief.get("anchor_character", {}).get("name", "")
        if anchor_name:
            _a_parts = [p for p in _re.split(r"\s+", anchor_name.lower()) if len(p) > 2]
            _all_text = " ".join(
                s.get("content", "") + " " + s.get("label", "") for s in scenes
            ).lower()
            if any(p in _all_text for p in _a_parts):
                print(f"  [Storyboard] anchor '{anchor_name}' present in storyboard ✓")
            else:
                print(f"  [!] Storyboard: anchor '{anchor_name}' not found in any scene — "
                      f"retention brief may have wrong anchor")

    # 3b. PLAYER STATS — normalize content to "Player Name YYYY/YY" (LLM adds club/stats inline)
    # PLAYER STATS without a year = misuse (awards/career totals) → remove the scene entirely
    stats_to_remove = []
    for i, s in enumerate(scenes):
        if s.get("template", "").upper() == "PLAYER STATS":
            raw = s.get("content", "")
            yr = _re.search(r'(\d{4})[/\-](\d{2,4})', raw)
            if not yr:
                stats_to_remove.append(i)
                continue
            start_year = int(yr.group(1))
            end_raw    = yr.group(2)
            # Career span detection: end portion is 4 digits (e.g. 1970/89 → end=89 is 2-digit OK,
            # but 1970/1989 → end is 4 digits) OR the span > 3 years (career total, not a season).
            end_year = int(end_raw) if len(end_raw) == 4 else int(f"19{end_raw}" if start_year < 2000 else f"20{end_raw}")
            if end_year - start_year > 2:
                # Career span, not a single season — discard
                stats_to_remove.append(i)
                continue
            year_str = f"{yr.group(1)}/{end_raw[-2:]}"
            name_part = _re.sub(r'\s*(Premier League|La Liga|Eredivisie|Bundesliga|Serie A|Ligue 1|'
                                 r'MLS|World Cup|Season Stats?|season|stats?|goals?|appearances?)\b.*', '',
                                 raw, flags=_re.IGNORECASE).strip()
            name_part = _re.sub(r'\s*\d{4}[/\-]\d{2,4}.*$', '', name_part).strip().rstrip(',').strip()
            s["content"] = f"{name_part} {year_str}"
    for i in reversed(stats_to_remove):
        scenes.pop(i)

    # 3b2. TOP SCORERS — content must be "Competition YYYY/YY" only (LLM adds player name)
    for s in scenes:
        if s.get("template", "").upper() in ("TOP SCORERS", "TOP ASSISTS"):
            raw = s.get("content", "")
            # Extract competition and season, discard anything after them
            # Normalise dash to slash in years (2013-14 → 2013/14)
            clean = _re.sub(r'(\d{4})-(\d{2})\b', r'\1/\2', raw)
            # Remove player names (anything after a comma or player-specific text)
            comp_match = _re.match(r'([A-Za-z\s]+\d{4}/\d{2})', clean.strip())
            if comp_match:
                s["content"] = comp_match.group(1).strip()

    # 3b3. DISCIPLINARY RECORD — content must be just the player name (LLM adds incident descriptions)
    for s in scenes:
        if s.get("template", "").upper() == "DISCIPLINARY RECORD":
            raw = s.get("content", "")
            # Strip anything after " - " or " (" or ","
            s["content"] = _re.split(r'\s*[-–(,]', raw)[0].strip()

    # 3b4. CAREER TIMELINE — remove scenes with non-club content or missing player name
    non_club_words = {"ban", "suspension", "controversy", "disciplin", "incident", "international", "career"}
    def _career_timeline_valid(s):
        if s.get("template", "").upper() != "CAREER TIMELINE":
            return True
        raw = s.get("content", "")
        # Reject non-club content
        if any(w in raw.lower() for w in non_club_words):
            return False
        # Reject when player name portion is empty, "None", or a bare number
        name_part = _re.split(r'\s*[-–]\s*focus:', raw, flags=_re.IGNORECASE)[0].strip()
        if not name_part or name_part.lower() in ("none", "", "unknown") or name_part.isdigit():
            return False
        return True
    scenes = [s for s in scenes if _career_timeline_valid(s)]

    # 3b5. CAREER TIMELINE dedup — keep only the first occurrence per (entity, focus club)
    seen_timelines = set()
    deduped = []
    for s in scenes:
        if s.get("template", "").upper() == "CAREER TIMELINE":
            # Extract focus club from "Player - Focus: Club" or just use full content
            raw = s.get("content", "")
            focus_m = _re.search(r'focus:\s*(.+)', raw, _re.IGNORECASE)
            key = focus_m.group(1).strip().lower() if focus_m else raw.strip().lower()
            if key in seen_timelines:
                continue
            seen_timelines.add(key)
        deduped.append(s)
    scenes = deduped

    # 3c. TRANSFER tags — convert to CAREER TIMELINE (TRANSFER is secondary; CAREER TIMELINE is primary)
    for s in scenes:
        if s.get("template", "").upper() == "TRANSFER":
            raw = s.get("content", "")
            # Try to extract club from "Player from ClubA to ClubB, YYYY, £Xm"
            club_match = _re.search(r'\bto\s+([A-Z][^,]+)', raw, _re.IGNORECASE)
            if club_match:
                club = club_match.group(1).strip()
                s["template"] = "CAREER TIMELINE"
                s["content"] = f"{entity} - Focus: {club}"

    # 3d. Inject key Director's Brief moments if mentioned in context but absent from storyboard
    _inject_missing_context_moments(scenes, context, entity)

    # 4. Ensure BOTH form runs exist for title races — only auto-inject the second if we can infer the opponent
    # from the existing form run label (generic — not hardcoded to any specific club)
    form_runs = [s for s in scenes if s.get("template","").upper() == "HERO FORM RUN"]
    if len(form_runs) == 1:
        existing_content = form_runs[0].get("content", "")
        existing_label   = form_runs[0].get("label", "")
        combined = (existing_content + " " + existing_label).lower()
        # Extract the season from existing content (e.g. "2013/14")
        season_match = _re.search(r'\d{4}/\d{2}', existing_content)
        season = season_match.group(0) if season_match else ""
        # Infer the title rival from the existing form run content — no hardcoded lookup
        other_team = _cached_infer(
            f"This text describes a football title race: '{existing_content[:150]}'. "
            f"What was the main competing club in this title race? "
            f"Return only the club's full official name. "
            f"If the rival is already mentioned in the text, return NONE.",
            expected_type="str",
            fallback=None,
        )
        # Guard: don't inject if rival is already in the existing form run
        if other_team and other_team.lower() in combined:
            other_team = None
        other_content = (
            f"{other_team}, the {season} title run-in" if other_team and season
            else other_team
        )
        if other_content:
            pos = next((i for i, s in enumerate(scenes) if s.get("template","").upper() == "HERO FORM RUN"), len(scenes))
            act = form_runs[0].get("act", "ACT 3 — PEAK")
            act_idx = form_runs[0].get("actIndex", 3)
            second_run = {"id": _sid(), "act": act, "actIndex": act_idx,
                          "type": "graphic", "template": "HERO FORM RUN",
                          "content": other_content, "label": other_team, "duration": 10}
            scenes.insert(pos + 1, second_run)

    # 5. Enforce evolve transition between consecutive HERO graphic scenes in the same act
    # Rule: within any act, two HERO-family graphics back-to-back (ignoring narration)
    # should use [TRANSITION: evolve] not a cut. Also set skipIntro on the second scene.
    #
    # "Consecutive" here means: scanning the full scene list, if we encounter two graphic
    # scenes where:
    #   - both are HERO-family templates (or data infographic types)
    #   - they share the same actIndex
    #   - there is NO non-narration/non-transition scene between them
    #   - the transition immediately before the second is not already "evolve"
    # → insert an evolve TRANSITION between them (or upgrade an existing cut)

    HERO_FAMILY = {
        "HERO INTRO", "HERO STAT BARS", "HERO FORM RUN", "HERO TACTICAL",
        "HERO BIG STAT", "HERO LEAGUE GRAPH", "HERO TRANSFER RECORD", "HERO QUOTE",
        "HERO CONCEPT", "HERO SCATTER", "HERO SHOT MAP", "HERO MATCH TIMELINE",
        "HERO AWARDS LIST", "HERO COMPARISON RADAR", "HERO SEASON TIMELINE",
        "CAREER TIMELINE", "PLAYER TRIO", "PLAYER RADAR", "PLAYER STATS",
        "HERO TRANSFER PROFIT", "TOP SCORERS", "TOP ASSISTS", "SEASON COMPARISON",
        "STANDINGS TABLE", "MATCH RESULT", "DISCIPLINARY RECORD",
    }

    def _is_hero(s):
        return s.get("type") == "graphic" and s.get("template", "").upper() in HERO_FAMILY

    def _is_narration_or_evolve(s):
        """Scenes that can sit between two HERO scenes without breaking the evolve chain."""
        return s.get("type") == "narration"

    # Walk forward; collect runs of HERO graphics separated only by narration
    evolve_pairs = []   # (idx_of_first_graphic, idx_of_second_graphic)
    i = 0
    while i < len(scenes):
        if _is_hero(scenes[i]):
            j = i + 1
            while j < len(scenes) and _is_narration_or_evolve(scenes[j]):
                j += 1
            if j < len(scenes) and _is_hero(scenes[j]) and scenes[i].get("actIndex") == scenes[j].get("actIndex"):
                # Check: is there already a TRANSITION between i and j?
                trans_between = [s for s in scenes[i+1:j] if s.get("type") == "transition"]
                if not trans_between:
                    evolve_pairs.append((i, j))
                elif all(s.get("content","").lower() not in ("evolve",) for s in trans_between):
                    # There's a non-evolve transition — upgrade it
                    for s in trans_between:
                        if s.get("type") == "transition":
                            s["content"] = "evolve"
                            s["template"] = "TRANSITION"
                i = j
                continue
        i += 1

    # Insert evolve transitions (reverse order to preserve indices)
    for first_idx, second_idx in reversed(evolve_pairs):
        act = scenes[first_idx].get("act", "")
        act_idx = scenes[first_idx].get("actIndex", 0)
        evolve_trans = {
            "id": _sid(), "act": act, "actIndex": act_idx,
            "type": "transition", "template": "TRANSITION",
            "content": "evolve", "label": "", "duration": 2,
        }
        # Insert at second_idx (just before the second HERO scene)
        scenes.insert(second_idx, evolve_trans)
        # Mark second scene as skipIntro so its persistent elements don't re-animate
        scenes[second_idx + 1]["skipIntro"] = True

    # Layer 1e: enforce act-appropriate template discipline (replace before remove)
    _enforce_act_type_templates(scenes, _is_thematic_doc)

    # Layer 1e2: continuous-world fixes
    #   - Downgrade explicit intra-act `push` transitions to `worldPan`
    #   - Inject a narration beat between two consecutive same-template
    #     graphics so they don't read as a slideshow
    _downgrade_intra_act_push(scenes)
    _split_consecutive_same_template_graphics(scenes)

    # Layer 1f: ensure anchor character appears in at least 3 acts
    _enforce_anchor_presence(scenes, retention_brief)

    # Layer 1g: deduplicate final act narration; enforce single closing provocation
    _enforce_closing_rule(scenes, retention_brief)

    # Layer 1h: ensure minimum scene density per act (inject placeholders if needed)
    _enforce_minimum_density(scenes, retention_brief=retention_brief, blueprint=blueprint)

    # Layer 1i: deduplicate repeated phrases across ALL narration scenes
    _deduplicate_narration_phrases(scenes)

    # Layer 1j: for thematic/decline docs, ensure canonical break moment is present
    if _is_thematic_doc:
        _inject_canonical_break_moment(scenes, entity, topic, context or "")

    # Layer 1k: validate stat content has numeric values
    _validate_stat_content(scenes)

    # Layer 1l: best-effort fact-check — warns on common LLM hallucinations
    _warn_suspect_facts(scenes)

    # Layer 2: assign role, clip type, world_id, flow_hint, flow_direction
    _assign_scene_metadata(scenes)

    # Layer 2a: enforce clip-world continuity (needs world_id from Layer 2)
    _enforce_clip_world_continuity(scenes)

    # Layer 2b: prevent template over-use and log diversity score
    diversity_score = _enforce_template_caps(scenes)
    if diversity_score is not None:
        result["diversity_score"] = diversity_score

    # Layer 2c: no two consecutive acts open with the same template
    _validate_act_openers(scenes)

    # Layer 2d-pre: break runs of 3+ pure-data scenes by pulling a human-imagery scene forward
    _break_data_runs(scenes)

    # Layer 2e: mark 1–3 hero_visual scenes (motion_agent renders these)
    _inject_hero_visuals(scenes, entity, topic)

    # Layer 2b: trim to format budget
    from utils.format_utils import compute_format_profile as _compute_profile
    fmt_profile = _compute_profile(context=context, entity=entity, blueprint=blueprint,
                                   format_override=format_override)
    _reconcile_format(scenes, profile=fmt_profile)

    # Layer 3: append the outro scene (channel wordmark + linked-video panels + dynamic copy)
    _append_outro_scene(scenes, topic, entity, retention_brief)

    # Recalculate durations after all scene modifications (word-count-based for narration)
    _recalculate_durations(scenes)

    result["scenes"] = scenes
    result["totalDuration"] = sum(s.get("duration", 0) for s in scenes)
    return result


# ── Outro injection ──────────────────────────────────────────────────────────

# Subscribe-ask phrasings — engine rotates to avoid identical outro per video.
_SUBSCRIBE_ASKS = [
    "Subscribe for a new story every week.",
    "Hit subscribe — there's a new story dropping next week.",
    "If you want more like this, the subscribe button keeps them coming.",
    "Subscribe so the next deep-dive lands in your feed.",
    "New story every Wednesday — subscribe so you don't miss it.",
    "Hit subscribe and the next chapter shows up automatically.",
]

def _generate_outro_lead_in(topic: str, entity: str, retention_brief: dict | None) -> str:
    """LLM-write a single-sentence topic-aware lead-in that bridges from the
    final beat into the subscribe ask. Falls back to a safe default on error."""
    closing_line = ""
    if retention_brief and isinstance(retention_brief, dict):
        cands = retention_brief.get("anchor_candidates") or []
        if cands and isinstance(cands[0], dict):
            closing_line = cands[0].get("closing_line", "")

    prompt = (
        f"Write a single-sentence outro lead-in for a football documentary titled \"{topic}\".\n"
        f"Subject: {entity or 'football'}.\n"
        f"{('Closing line of the doc: ' + closing_line) if closing_line else ''}\n\n"
        "Rules:\n"
        "- 12 to 22 words, ONE sentence.\n"
        "- Voice: smart 20-year-old football fan in a pub, NOT a Guardian long-read.\n"
        "- BANNED words/phrases (do not use any of these or anything similar):\n"
        "    intrinsically, irrevocably, unparalleled, unprecedented, unequivocally, ostensibly,\n"
        "    paradigm, zenith, nadir, juxtaposition, dichotomy, wellspring, lineage, embodied,\n"
        "    exemplified, manifested, uninhibited, sublime, ineffable, audacious (overused),\n"
        "    poetic, mesmerizing, symphony of, tapestry of, fabric of, essence of, soul of,\n"
        "    beacon of hope, stark reminder, profound shift, irrevocably lost, hangs in the balance,\n"
        "    artistry (overwrought), era of (overused), realm of, landscape of, in perpetuity,\n"
        "    a question for the ages, deeper dive.\n"
        "- Prefer plain words: stopped, big, mood, peak, showed.\n"
        "- Bridges the doc's emotional ending into a soft subscribe ask.\n"
        "- No second-person commands. No 'you should subscribe'.\n"
        "- No clichés like 'Thanks for watching' or 'I hope you enjoyed'.\n"
        "- Do NOT end with a question.\n"
        "- Reference the doc's theme implicitly, not by title.\n"
        "Return only the sentence, no quotes, no preamble."
    )
    try:
        text = (ask_llm(prompt) or "").strip().strip('"').strip("'")
        if 6 <= len(text.split()) <= 30:
            return text
    except Exception:
        pass
    return "If this story stayed with you, there's more where it came from."


def _append_outro_scene(scenes: list, topic: str, entity: str, retention_brief: dict | None) -> None:
    """Append a single HERO OUTRO scene at the end of the storyboard.
    Skips if an outro already exists (idempotent — re-runs don't double-append)."""
    import random as _random
    if any(s.get("template", "").upper() == "HERO OUTRO" for s in scenes):
        return  # already present
    if not scenes:
        return
    last_act       = scenes[-1].get("act", "ACT 5 — LEGACY")
    last_act_index = scenes[-1].get("actIndex", 5)

    lead_in = _generate_outro_lead_in(topic, entity, retention_brief)
    sub_ask = _random.choice(_SUBSCRIBE_ASKS)
    # Two related-video titles — engine leaves blank for user to populate in Studio.
    # The renderer falls back to "Watch next" / "Or this one" so the placeholder still looks finished.
    left_title  = ""
    right_title = ""
    content = " ::: ".join([lead_in, sub_ask, left_title, right_title])

    import time as _time
    outro_id = f"s_outro_{int(_time.time() * 1000) % 1000000}"
    outro = {
        "id":         outro_id,
        "act":        last_act,
        "actIndex":   last_act_index,
        "type":       "graphic",
        "template":   "HERO OUTRO",
        "content":    content,
        "label":      "outro",
        "duration":   8,
        "evidence_mode": "NARRATIVE",
        "classification": "MUST_VISUALISE",
    }
    scenes.append(outro)
    print(f"  [Storyboard] Outro scene appended: lead-in='{lead_in[:50]}…' ask='{sub_ask[:40]}…'")


def topic_to_safe_name(topic):
    return (topic.lower()
            .replace(" ", "_").replace(":", "").replace("?", "")
            .replace("—", "").replace("-", "_").replace("&", "and")
            .strip("_"))


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Documentary Engine</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #F0EDE8; color: #1a1a1a;
  font-family: 'Segoe UI', system-ui, sans-serif;
  min-height: 100vh;
  display: flex; flex-direction: column; align-items: center;
  padding: 36px 16px 80px;
  position: relative;
}
body::before {
  content: '';
  position: fixed; inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");
  opacity: 0.045;
  pointer-events: none;
  z-index: 9999;
}
h1 { font-size: 1.25rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: #1a1a1a; margin-bottom: 4px; }
.subtitle { font-size: 0.68rem; color: #aaa; letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 30px; }

/* Progress */
.progress { display: flex; align-items: center; gap: 0; margin-bottom: 28px; width: 100%; max-width: 900px; }
.prog-step { display: flex; align-items: center; gap: 8px; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.07em; color: #bbb; }
.prog-step.active { color: #1660FF; }
.prog-step.done { color: #999; }
.prog-dot { width: 20px; height: 20px; border-radius: 50%; border: 1.5px solid #DDD9D3; display: flex; align-items: center; justify-content: center; font-size: 0.6rem; font-weight: 700; flex-shrink: 0; background: #fff; }
.prog-step.active .prog-dot { border-color: #1660FF; color: #1660FF; background: #f0f4ff; }
.prog-step.done .prog-dot { background: #E8E4DF; border-color: #ccc; color: #999; }
.prog-line { flex: 1; height: 1px; background: #E8E4DF; margin: 0 10px; }

/* Layout */
.main { width: 100%; max-width: 720px; }
.main.wide { max-width: 920px; }

/* Cards */
.card { background: #fff; border: 1.5px solid #E8E4DF; border-radius: 10px; padding: 28px; margin-bottom: 14px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
.card.accent { border-left: 3px solid #1660FF; }

label { display: block; font-size: 0.67rem; text-transform: uppercase; letter-spacing: 0.08em; color: #999; margin-bottom: 8px; }

input[type="text"], textarea {
  width: 100%; background: #fff; border: 1.5px solid #DDD9D3; border-radius: 7px;
  color: #1a1a1a; font-family: inherit; font-size: 0.92rem; padding: 11px 13px;
  outline: none; transition: border-color 0.2s, box-shadow 0.2s; margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
input[type="text"]:focus, textarea:focus { border-color: #1660FF; box-shadow: 0 0 0 3px rgba(22,96,255,0.1); }
textarea { resize: vertical; min-height: 180px; line-height: 1.7; font-size: 0.85rem; }

.btn-row { display: flex; gap: 9px; margin-top: 2px; }
button {
  flex: 1; background: #1660FF; color: #fff; border: 1.5px solid #1660FF; border-radius: 7px;
  font-family: inherit; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; padding: 12px; cursor: pointer;
  transition: background 0.15s, box-shadow 0.15s;
  box-shadow: 0 2px 8px rgba(22,96,255,0.25);
}
button:hover { background: #0a4fe0; border-color: #0a4fe0; box-shadow: 0 3px 12px rgba(22,96,255,0.35); }
button:disabled { background: #E8E4DF; color: #bbb; border-color: #E8E4DF; cursor: not-allowed; box-shadow: none; }
button.secondary { background: #fff; border: 1.5px solid #DDD9D3; color: #555; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
button.secondary:hover { background: #f5f5f5; color: #1a1a1a; border-color: #bbb; }
a.btn-link { display:inline-flex; align-items:center; padding:9px 18px; border-radius:7px; font-size:0.82rem; font-weight:700; cursor:pointer; background:#1660FF; color:#fff; text-decoration:none; letter-spacing:0.04em; box-shadow: 0 2px 8px rgba(22,96,255,0.25); }
a.btn-link:hover { background:#0a4fe0; }

.step { display: none; }
.step.active { display: block; }
.loader { display: none; text-align: center; padding: 40px; }
.loader.active { display: block; }
.spinner { width: 22px; height: 22px; border: 2px solid rgba(22,96,255,0.15); border-top-color: #1660FF; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 14px; }
@keyframes spin { to { transform: rotate(360deg); } }
.loader-text { font-size: 0.75rem; color: #999; }
.loader-sub  { font-size: 0.65rem; color: #bbb; margin-top: 5px; }

.entity-tag { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #999; margin-bottom: 18px; }
.entity-tag span { color: #1660FF; }
.error { font-size: 0.78rem; color: #ef4444; margin-bottom: 12px; }
.divider { border: none; border-top: 1px solid #E8E4DF; margin: 20px 0; }

/* ── Checklist ────────────────────────────────────────────────────────── */
.checklist-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 14px;
}
.checklist-title { font-size: 0.67rem; text-transform: uppercase; letter-spacing: 0.08em; color: #999; }
.checklist-actions { display: flex; gap: 8px; }
.check-action { font-size: 0.62rem; color: #999; cursor: pointer; padding: 3px 8px; border: 1.5px solid #DDD9D3; border-radius: 4px; background: #fff; flex: none; box-shadow: none; text-transform: none; letter-spacing: 0; font-weight: 500; }
.check-action:hover { color: #1a1a1a; border-color: #bbb; background: #f5f5f5; }

.facts-loading { text-align: center; padding: 16px; }
.spinner-sm { width: 14px; height: 14px; border: 1.5px solid rgba(22,96,255,0.15); border-top-color: #1660FF; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 8px; }
.facts-load-text { font-size: 0.68rem; color: #bbb; }

.cat-section { margin-bottom: 4px; border: 1.5px solid #E8E4DF; border-radius: 7px; overflow: hidden; }
.cat-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 9px 14px; cursor: pointer; background: #F5F2EE;
  font-size: 0.7rem; font-weight: 600; color: #777;
  transition: background 0.1s;
}
.cat-header:hover { background: #EDE9E4; }
.cat-header-left { display: flex; align-items: center; gap: 8px; }
.cat-icon { font-size: 0.75rem; }
.cat-toggle { font-size: 0.6rem; color: #bbb; transition: transform 0.15s; }
.cat-toggle.open { transform: rotate(180deg); }
.cat-count { font-size: 0.62rem; color: #bbb; }
.cat-items { padding: 4px 6px; background: #fff; display: none; }
.cat-items.open { display: block; }

.fact-item {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 8px 8px; cursor: pointer; border-radius: 4px;
  transition: background 0.1s; margin-bottom: 1px;
}
.fact-item:hover { background: #F5F2EE; }
.fact-item input[type="checkbox"] { display: none; }
.fact-cb {
  width: 14px; height: 14px; border-radius: 3px; border: 1.5px solid #DDD9D3;
  flex-shrink: 0; margin-top: 3px; transition: all 0.12s; position: relative;
  background: #fff;
}
.fact-item input:checked + .fact-cb { background: #1660FF; border-color: #1660FF; }
.fact-item input:checked + .fact-cb::after {
  content: ''; position: absolute; left: 3px; top: 1px;
  width: 4px; height: 7px; border: 1.5px solid #fff;
  border-top: none; border-left: none; transform: rotate(40deg);
}
.fact-text { flex: 1; min-width: 0; }
.fact-label { font-size: 0.78rem; color: #999; line-height: 1.4; transition: color 0.12s; }
.fact-item input:checked + .fact-cb + .fact-text .fact-label { color: #1a1a1a; }
.fact-detail { font-size: 0.66rem; color: #ccc; margin-top: 2px; }
.fact-item input:checked + .fact-cb + .fact-text .fact-detail { color: #999; }
.fact-imp { width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0; margin-top: 6px; }
.fact-imp.high   { background: #ef4444; }
.fact-imp.medium { background: #f59e0b; }
.fact-imp.low    { background: #DDD9D3; }
/* Director's Intent — amber accent to distinguish from Wikipedia facts */
.fact-item.brief-fact { border-left: 2px solid #d97706; padding-left: 6px; margin-left: -8px; }
.fact-item.brief-fact .fact-label { color: #888; }
.fact-item.brief-fact input:checked + .fact-cb + .fact-text .fact-label { color: #7a5f30; }
.fact-item.brief-fact .fact-detail { color: #ccc; }
.fact-item.brief-fact input:checked + .fact-cb + .fact-text .fact-detail { color: #a07840; }

/* ── Blueprint ────────────────────────────────────────────────────────── */
.bp-hero {
  background: #F5F2EE; border: 1.5px solid #E8E4DF; border-radius: 8px;
  padding: 20px 24px; margin-bottom: 18px;
}
.bp-hero-title { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #bbb; margin-bottom: 12px; }

/* Act timeline bar */
.timeline-bar { display: flex; gap: 3px; height: 8px; border-radius: 4px; overflow: hidden; margin-bottom: 16px; }
.tl-seg { border-radius: 2px; transition: opacity 0.15s; cursor: pointer; }
.tl-seg:hover { opacity: 0.7; }

/* Tag summary */
.bp-summary { display: flex; gap: 8px; flex-wrap: wrap; }
.bp-pill {
  padding: 4px 11px; border-radius: 20px; font-size: 0.67rem;
  font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
}
.bp-pill.clip    { background: #f0fff4; color: #2a8a4a; border: 1.5px solid #c0e8cc; }
.bp-pill.info    { background: #f0f4ff; color: #1660FF; border: 1.5px solid #ccd9ff; }
.bp-pill.hero { background: #f5f0ff; color: #7040c0; border: 1.5px solid #e0d0ff; }
.bp-pill.total   { background: #F5F2EE; color: #999; border: 1.5px solid #E8E4DF; }

/* Issue banner */
.issue-banner {
  background: #fffaf0; border: 1.5px solid #f0dfa0; border-radius: 7px;
  padding: 12px 16px; margin-bottom: 14px; font-size: 0.72rem; color: #9a7010; line-height: 1.7;
}
.issue-banner strong { display: block; color: #7a5500; margin-bottom: 3px; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; }

/* Act cards */
.act-card { border: 1.5px solid #E8E4DF; border-radius: 7px; margin-bottom: 8px; overflow: hidden; }
.act-hdr {
  display: flex; align-items: center; gap: 12px; padding: 12px 16px;
  background: #F5F2EE; cursor: pointer; user-select: none;
}
.act-hdr:hover { background: #EDE9E4; }
.act-bar { width: 3px; border-radius: 2px; flex-shrink: 0; align-self: stretch; }
.act-name-col { flex: 1; }
.act-name { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: #555; }
.act-range { font-size: 0.62rem; color: #bbb; margin-top: 1px; }
.act-meta-col { display: flex; align-items: center; gap: 14px; flex-shrink: 0; }
.act-tagcount { font-size: 0.65rem; color: #bbb; }
.act-caret { font-size: 0.6rem; color: #ccc; transition: transform 0.15s; }
.act-caret.open { transform: rotate(180deg); }

.act-body { padding: 14px 16px 16px 31px; display: none; background: #fff; }
.act-body.open { display: block; }

.act-events { margin-bottom: 12px; }
.act-event { display: flex; gap: 10px; font-size: 0.78rem; color: #777; line-height: 1.5; margin-bottom: 5px; }
.act-event::before { content: '—'; color: #ccc; flex-shrink: 0; }

.act-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.tag-chip {
  display: flex; align-items: baseline; gap: 5px;
  padding: 4px 9px; border-radius: 4px; font-size: 0.65rem; max-width: 380px;
}
.tag-chip .tc-type { font-weight: 800; letter-spacing: 0.03em; white-space: nowrap; flex-shrink: 0; }
.tag-chip .tc-content { opacity: 0.55; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag-chip.clip    { background: #f0fff4; color: #2a8a4a; border: 1.5px solid #c0e8cc; }
.tag-chip.info    { background: #f0f4ff; color: #1660FF; border: 1.5px solid #ccd9ff; }
.tag-chip.hero { background: #f5f0ff; color: #7040c0; border: 1.5px solid #e0d0ff; }

.act-warn { display: flex; align-items: center; gap: 7px; margin-top: 10px; padding: 7px 11px; background: #fffaf0; border: 1.5px solid #f0dfa0; border-radius: 4px; font-size: 0.68rem; color: #9a7010; }

/* Command box */
.command-box {
  background: #F5F2EE; border: 1.5px solid #E8E4DF; border-radius: 7px;
  padding: 14px; font-family: 'Courier New', monospace;
  font-size: 0.78rem; color: #1660FF; word-break: break-all; cursor: pointer; margin-bottom: 5px;
}
.command-box:hover { border-color: #bbb; background: #EDE9E4; }
.copy-hint { font-size: 0.65rem; color: #bbb; text-align: right; margin-bottom: 18px; }

/* Log viewer — kept dark as intentional terminal contrast */
.log-normal  { color: #aaa; }
.log-info    { color: #60a5fa; }
.log-success { color: #4ade80; }
.log-warn    { color: #f59e0b; }
.log-error   { color: #f87171; }
.log-head    { color: #c084fc; font-weight: 700; }

.status-running { font-size:0.72rem; color:#d97706; display:flex; align-items:center; gap:8px; }
.status-done    { font-size:0.72rem; color:#2a8a4a; }
.status-error   { font-size:0.72rem; color:#ef4444; }
.pulse { width:7px; height:7px; border-radius:50%; background:#d97706; animation:pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

/* History */
.history { width: 100%; max-width: 720px; margin-top: 20px; }
.history-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #bbb; margin-bottom: 8px; }
.history-item {
  background: #fff; border: 1.5px solid #E8E4DF; border-radius: 7px;
  padding: 11px 14px; margin-bottom: 5px; display: flex; justify-content: space-between; align-items: center;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.history-title { font-size: 0.8rem; color: #555; }
.history-btn { flex: 0; display:inline-block; background: #fff; border: 1.5px solid #DDD9D3; border-radius: 4px; color: #777; font-size: 0.62rem; padding: 3px 9px; cursor: pointer; text-transform: none; letter-spacing: 0; font-weight: 600; text-decoration:none; box-shadow: none; }
.history-btn:hover { border-color: #bbb; color: #1a1a1a; background: #f5f5f5; }
.toggle-wrap { display:flex; align-items:center; gap:8px; cursor:pointer; }
.toggle-wrap input[type=checkbox] { width:16px; height:16px; accent-color:#1660FF; cursor:pointer; }
.toggle-label { font-size:0.78rem; font-weight:600; color:#1a1a1a; }

/* ── Storyboard ──────────────────────────────────────────────────────── */
.sb-controls {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 16px;
}
.sb-stat { font-size: 0.67rem; color: #bbb; }
.sb-stat span { color: #777; }

.sb-list { display: flex; flex-direction: column; gap: 0; }

.act-divider {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 0 8px; margin-top: 4px;
}
.act-divider-bar { flex: 1; height: 1px; background: #E8E4DF; }
.act-divider-label { font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.08em; color: #bbb; white-space: nowrap; }

.sb-add-row {
  display: flex; justify-content: center; padding: 3px 0;
  opacity: 0; transition: opacity 0.15s;
}
.sb-add-row:hover { opacity: 1; }
.sb-add-btn {
  display: flex; gap: 6px; align-items: center;
  font-size: 0.6rem; color: #bbb; border: 1.5px dashed #DDD9D3;
  border-radius: 4px; padding: 3px 10px; cursor: pointer; background: none;
  transition: all 0.15s; box-shadow: none; text-transform: none; letter-spacing: 0; font-weight: 400;
}
.sb-add-btn:hover { color: #777; border-color: #aaa; background: none; }

.scene-card {
  display: flex; align-items: flex-start; gap: 0;
  border-left: 2px solid #E8E4DF; margin-left: 20px;
  position: relative;
}
.scene-card::before {
  content: ''; position: absolute; left: -5px; top: 18px;
  width: 8px; height: 8px; border-radius: 50%;
  background: #DDD9D3; flex-shrink: 0;
}
.scene-card.narration::before { background: #ccc; }
.scene-card.clip::before       { background: #2a8a4a; }
.scene-card.graphic::before    { background: #7040c0; }

.scene-inner {
  flex: 1; margin-left: 14px; padding: 10px 12px;
  border: 1.5px solid #E8E4DF; border-radius: 7px;
  background: #fff; margin-bottom: 2px; display: flex; gap: 10px; align-items: flex-start;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.scene-card.narration .scene-inner { border-color: #DDD9D3; }
.scene-card.clip .scene-inner      { border-color: #c0e8cc; background: #f0fff4; }
.scene-card.graphic .scene-inner   { border-color: #e0d0ff; background: #f5f0ff; }

.scene-badge {
  font-size: 0.55rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.07em;
  padding: 2px 6px; border-radius: 3px; white-space: nowrap; flex-shrink: 0; margin-top: 2px;
}
.scene-card.narration .scene-badge { background: #F5F2EE; color: #999; }
.scene-card.clip .scene-badge      { background: #f0fff4; color: #2a8a4a; border: 1px solid #c0e8cc; }
.scene-card.graphic .scene-badge   { background: #f5f0ff; color: #7040c0; border: 1px solid #e0d0ff; }

.scene-body { flex: 1; min-width: 0; }
.scene-content {
  font-size: 0.8rem; line-height: 1.5; outline: none;
  border-radius: 3px; padding: 1px 4px; margin: -1px -4px;
  transition: background 0.1s;
}
.scene-card.narration .scene-content { color: #777; }
.scene-card.clip .scene-content      { color: #2a8a4a; }
.scene-card.graphic .scene-content   { color: #7040c0; }
.scene-content:focus { background: #F5F2EE; outline: 1.5px solid #DDD9D3; }

.scene-label {
  font-size: 0.65rem; color: #bbb; margin-top: 3px;
  outline: none; padding: 1px 4px; margin-left: -4px;
}
.scene-label:focus { background: #F5F2EE; outline: 1px solid #DDD9D3; border-radius: 2px; }

.scene-meta { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; flex-shrink: 0; }
.scene-dur { font-size: 0.62rem; color: #bbb; font-variant-numeric: tabular-nums; }
.scene-del {
  font-size: 0.6rem; color: #ccc; cursor: pointer; padding: 2px 5px;
  border: 1.5px solid #E8E4DF; border-radius: 3px; background: #fff; line-height: 1;
  transition: all 0.1s; box-shadow: none; flex: none; text-transform: none; letter-spacing: 0; font-weight: 400;
}
.scene-del:hover { color: #ef4444; border-color: #fca5a5; background: #fff0f0; }

.add-picker {
  position: absolute; background: #fff; border: 1.5px solid #E8E4DF;
  border-radius: 7px; padding: 6px; display: flex; flex-direction: column; gap: 3px;
  z-index: 100; font-size: 0.68rem; box-shadow: 0 4px 16px rgba(0,0,0,0.1);
  max-height: 70vh; overflow-y: auto; min-width: 210px;
}
.add-picker-item {
  padding: 5px 10px; cursor: pointer; border-radius: 4px; color: #777;
  white-space: nowrap;
}
.add-picker-item:hover { background: #F5F2EE; color: #1a1a1a; }

.sb-warn {
  margin: 6px 0 6px 34px; padding: 5px 10px;
  background: #fffaf0; border: 1.5px solid #f0dfa0; border-radius: 4px;
  font-size: 0.65rem; color: #d97706;
}

/* Script reviewer */
#reviewBanner {
  margin: 10px 0 6px; padding: 8px 12px; border-radius: 6px;
  font-size: 0.7rem; display: none; gap: 8px; align-items: center;
}
#reviewBanner.has-errors   { background: #fff0f0; border: 1.5px solid #fca5a5; color: #ef4444; display: flex; }
#reviewBanner.has-warnings { background: #fffaf0; border: 1.5px solid #f0dfa0; color: #d97706; display: flex; }
#reviewBanner.all-clear    { background: #f0fff4; border: 1.5px solid #c0e8cc; color: #2a8a4a; display: flex; }
#reviewBanner .rv-icon { font-size: 0.9rem; flex-shrink: 0; }
#reviewBanner .rv-msg  { flex: 1; }
#reviewBanner .rv-detail { font-size: 0.62rem; opacity: 0.7; margin-top: 2px; }

.scene-issue-badge {
  font-size: 0.55rem; font-weight: 800; padding: 1px 5px; border-radius: 3px;
  white-space: nowrap; flex-shrink: 0; margin-top: 2px; cursor: help;
  flex: none; text-transform: none; letter-spacing: 0; box-shadow: none;
}
.scene-issue-badge.error   { background: #fff0f0; color: #ef4444; border: 1.5px solid #fca5a5; }
.scene-issue-badge.warning { background: #fffaf0; color: #d97706; border: 1.5px solid #f0dfa0; }
.scene-issue-badge.info    { background: #f0f4ff; color: #1660FF; border: 1.5px solid #ccd9ff; }
.scene-issue-tip {
  font-size: 0.62rem; color: #888; margin-top: 3px; line-height: 1.4;
  display: none;
}
.scene-issue-tip.visible { display: block; }

/* ── Hamburger nav ────────────────────────────────────────────────────── */
.hamburger-btn {
  position: fixed; top: 20px; left: 20px; z-index: 200;
  background: #fff; border: 1.5px solid #E8E4DF; border-radius: 8px;
  width: 40px; height: 40px; cursor: pointer; display: flex; align-items: center;
  justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  transition: all 0.15s; padding: 0; font-size: 1.1rem; color: #555;
  flex: none; letter-spacing: 0; text-transform: none; font-weight: 400;
}
.hamburger-btn:hover { background: #F5F2EE; border-color: #bbb; }
.nav-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.12); z-index: 210; display: none;
}
.nav-overlay.open { display: block; }
.nav-sidebar {
  position: fixed; top: 0; left: -280px; bottom: 0; width: 260px;
  background: #fff; border-right: 1.5px solid #E8E4DF; z-index: 220;
  display: flex; flex-direction: column;
  box-shadow: 4px 0 20px rgba(0,0,0,0.08);
  transition: left 0.25s cubic-bezier(0.4,0,0.2,1); padding: 28px 0;
}
.nav-sidebar.open { left: 0; }
.nav-brand { padding: 0 24px 24px; border-bottom: 1px solid #F0EDE8; margin-bottom: 12px; }
.nav-brand-name { font-size: 1.2rem; font-weight: 900; letter-spacing: -0.02em; color: #1a1a1a; line-height: 1; }
.nav-brand-name span { color: #1660FF; }
.nav-brand-sub { font-size: 0.62rem; color: #bbb; letter-spacing: 0.07em; text-transform: uppercase; margin-top: 6px; }
.nav-section-label { font-size: 0.6rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: #ccc; padding: 8px 24px 4px; margin-top: 4px; }
.nav-item {
  display: flex; align-items: center; gap: 12px; padding: 10px 24px;
  cursor: pointer; text-decoration: none; color: #555; font-size: 0.82rem; font-weight: 500;
  transition: background 0.12s, color 0.12s; border-left: 3px solid transparent;
}
.nav-item:hover { background: #F5F2EE; color: #1a1a1a; }
.nav-item.active { background: #f0f4ff; color: #1660FF; border-left-color: #1660FF; font-weight: 700; }
.nav-item-icon { font-size: 1rem; width: 20px; text-align: center; }
.nav-close {
  position: absolute; top: 16px; right: 16px; background: none; border: none;
  cursor: pointer; color: #bbb; font-size: 1.1rem; padding: 5px 7px;
  flex: none; box-shadow: none; font-weight: 400; letter-spacing: 0; text-transform: none;
  width: auto; height: auto; border-radius: 5px;
}
.nav-close:hover { color: #555; background: #F5F2EE; }
</style>
</head>
<body>

<button class="hamburger-btn" onclick="toggleNav()" aria-label="Menu">&#9776;</button>
<div class="nav-overlay" id="navOverlay" onclick="closeNav()"></div>
<nav class="nav-sidebar" id="navSidebar">
  <button class="nav-close" onclick="closeNav()">&#x2715;</button>
  <div class="nav-brand">
    <div class="nav-brand-name">Frequency</div>
    <div class="nav-brand-sub">AI Documentary Engine</div>
  </div>
  <div class="nav-section-label">Tools</div>
  <a class="nav-item active" href="/"><span class="nav-item-icon">&#127916;</span> Documentary Engine</a>
  <a class="nav-item" href="/ideas"><span class="nav-item-icon">&#128161;</span> Idea Generator</a>
</nav>

<h1>Documentary Engine</h1>
<p class="subtitle">Frequency · sport &nbsp;·&nbsp; <a href="/ideas" style="color:#bbb;text-decoration:none;letter-spacing:0.06em" onmouseover="this.style.color='#1660FF'" onmouseout="this.style.color='#bbb'">Idea Generator</a></p>

<div class="progress" id="progressBar">
  <div class="prog-step active" id="prog1"><div class="prog-dot">1</div>Title</div>
  <div class="prog-line"></div>
  <div class="prog-step" id="prog2"><div class="prog-dot">2</div>Context</div>
  <div class="prog-line"></div>
  <div class="prog-step" id="prog3"><div class="prog-dot">3</div>Blueprint</div>
  <div class="prog-line"></div>
  <div class="prog-step" id="prog4"><div class="prog-dot">4</div>Storyboard</div>
  <div class="prog-line"></div>
  <div class="prog-step" id="prog5"><div class="prog-dot">5</div>Run</div>
</div>

<div class="main" id="mainWrap">
<div class="card" id="mainCard">

  <!-- STEP 1 -->
  <div class="step active" id="step1">
    <label>Video Title</label>
    <input type="text" id="titleInput"
      placeholder="e.g. The Genius &amp; Madness of Luis Suárez"
      onkeydown="if(event.key==='Enter') analyse()">
    <p class="error" id="step1Error" style="display:none"></p>
    <div class="btn-row">
      <button onclick="analyse()">Analyse Subject →</button>
    </div>

    <div style="margin-top:28px;border-top:1px solid #1e1e1e;padding-top:18px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div style="font-size:.62rem;color:#666;text-transform:uppercase;letter-spacing:.1em;font-weight:700">
          Resume Project
        </div>
        <button class="check-action" onclick="loadProjectsList()" style="padding:3px 10px;font-size:.7rem">
          ↻ Refresh
        </button>
      </div>
      <p style="font-size:.7rem;color:#666;margin-bottom:10px;line-height:1.5">
        Pick an existing project to jump straight to Step 4 with its saved storyboard loaded.
        Your edits and narration script come back exactly as you left them.
      </p>
      <div id="projectsList" style="max-height:320px;overflow-y:auto;border:1px solid #1e1e1e;border-radius:6px;background:#0a0a0a">
        <div style="padding:18px;text-align:center;font-size:.72rem;color:#444">Loading projects…</div>
      </div>
    </div>
  </div>

  <!-- LOADER -->
  <div class="loader" id="loader">
    <div class="spinner"></div>
    <p class="loader-text" id="loaderText">Researching subject...</p>
    <p class="loader-sub" id="loaderSub">Fetching Wikipedia &amp; news</p>
  </div>

  <!-- STEP 2: Context + Checklist -->
  <div class="step" id="step2">
    <p class="entity-tag">Subject detected: <span id="entityTag"></span></p>

    <label>Director's Brief — edit as needed</label>
    <textarea id="contextArea"></textarea>
    <p class="error" id="step2Error" style="display:none"></p>

    <div class="divider"></div>

    <!-- Checklist -->
    <div class="checklist-header">
      <span class="checklist-title">Key Moments &amp; Events</span>
      <div class="checklist-actions">
        <button class="check-action" onclick="checkAll(true)">Select All</button>
        <button class="check-action" onclick="checkAll(false)">Clear All</button>
      </div>
    </div>
    <p style="font-size:0.68rem;color:#999;margin-bottom:12px;line-height:1.6">
      Ticked items will be included in the script. Untick anything you want to exclude.
      <span style="color:#bbb">● red = pivotal &nbsp; ● yellow = recommended &nbsp; ● grey = optional</span>
    </p>
    <div id="checklistArea">
      <div class="facts-loading">
        <div class="spinner-sm"></div>
        <p class="facts-load-text">Extracting career moments from Wikipedia...</p>
      </div>
    </div>

    <div class="divider"></div>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
      <label class="toggle-wrap">
        <input type="checkbox" id="voiceoverToggle">
        <span class="toggle-label">⚡ Generate ElevenLabs voiceover</span>
      </label>
      <span style="font-size:0.68rem;color:#444">(uncheck for test runs — saves API credits)</span>
    </div>
    <div id="voiceSelectWrap" style="display:flex;align-items:center;gap:12px;margin-bottom:14px;padding:10px 14px;background:#f5f5f5;border-radius:6px">
      <span style="font-size:0.78rem;font-weight:700;color:#333;white-space:nowrap">Voice:</span>
      <select id="voiceSelect" style="flex:1;padding:6px 10px;border:1px solid #ccc;border-radius:4px;font-size:0.82rem;background:#fff">
        <option value="21m00Tcm4TlvDq8ikWAM">Rachel — warm, authoritative (default)</option>
        <option value="AZnzlk1XvdvUeBnXmlld">Domi — intense, dramatic</option>
        <option value="EXAVITQu4vr4xnSDxMaL">Bella — conversational, engaging</option>
        <option value="ErXwobaYiN019PkySvjV">Antoni — deep, commanding</option>
        <option value="VR6AewLTigWG4xSOukaG">Arnold — authoritative, broadcast</option>
        <option value="pNInz6obpgDQGcFmaJgB">Adam — measured, documentary</option>
      </select>
      <button class="secondary" style="padding:6px 12px;font-size:0.75rem;white-space:nowrap" onclick="previewVoice()">▶ Preview</button>
    </div>
    <div class="btn-row">
      <button class="secondary" onclick="goStep(1)">← Back</button>
      <button onclick="getBlueprint()">Preview Script Structure →</button>
    </div>
  </div>

  <!-- STEP 3: Blueprint + Retention Brief -->
  <div class="step" id="step3">
    <div id="blueprintContent"></div>
    <div id="retentionPanel" style="display:none;margin-top:20px"></div>
    <div style="display:flex;align-items:center;gap:12px;margin-top:16px;padding:10px 14px;background:#111;border-radius:6px;border:1px solid #1e1e1e">
      <span style="font-size:0.72rem;text-transform:uppercase;letter-spacing:0.08em;color:#555;white-space:nowrap">Format</span>
      <select id="formatSelect" style="flex:1;padding:6px 10px;border:1px solid #2a2a2a;border-radius:4px;font-size:0.82rem;background:#0d0d0d;color:#ccc">
        <option value="auto">Auto-detect</option>
        <option value="documentary">Documentary</option>
        <option value="explainer">Explainer</option>
        <option value="story">Story</option>
        <option value="breakdown">Breakdown</option>
        <option value="news-style">News-style</option>
      </select>
      <span id="formatHint" style="font-size:0.65rem;color:#333;white-space:nowrap"></span>
    </div>
    <div class="btn-row" style="margin-top:16px">
      <button class="secondary" onclick="goStep(2)">← Edit Context</button>
      <button onclick="getStoryboard()">Build Storyboard →</button>
    </div>
  </div>

  <!-- STEP 4: Storyboard editor -->
  <div class="step" id="step4">

    <!-- Director's re-run panel -->
    <div id="rerunPanel" style="background:#0a0a0a;border:1px solid #1e1e1e;border-radius:8px;padding:14px 16px;margin-bottom:16px">
      <div style="display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap">
        <div style="flex:1;min-width:260px">
          <div style="font-size:.58rem;color:#444;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px">Anchor</div>
          <select id="rerunAnchorSelect" style="width:100%;background:#111;border:1px solid #2a2a2a;color:#ccc;border-radius:4px;padding:6px 8px;font-size:.78rem;font-family:inherit">
            <option value="" style="color:#000;background:#fff">— use engine pick —</option>
          </select>
          <style>#rerunAnchorSelect option { color: #000; background: #fff; }</style>
        </div>
        <div style="flex:2;min-width:300px">
          <div style="font-size:.58rem;color:#444;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px">Director's Notes <span style="color:#333;font-weight:400;text-transform:none">— add anything to include or change</span></div>
          <textarea id="rerunDirectorNotes" placeholder="e.g. Add a final act on Endrick, Vinicius and Savinho — the next generation gap. Remove the Juninho section." style="width:100%;box-sizing:border-box;background:#111;border:1px solid #2a2a2a;border-radius:4px;color:#ccc;font-size:.75rem;padding:7px 10px;resize:vertical;min-height:52px;font-family:inherit;line-height:1.5" rows="2"></textarea>
        </div>
        <div style="display:flex;align-items:flex-end;padding-bottom:1px">
          <button onclick="rerunStoryboard()" style="background:#1a1200;border:1px solid #3a2800;color:#C9A84C;border-radius:5px;padding:8px 16px;font-size:.75rem;font-weight:700;cursor:pointer;white-space:nowrap">↺ Re-run Storyboard</button>
        </div>
      </div>
    </div>

    <div class="sb-controls">
      <div>
        <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:0.08em;color:#3a3a3a;margin-bottom:4px">Scene Sequence</div>
        <div class="sb-stat">
          <span id="sbSceneCount">0</span> scenes ·
          <span id="sbDuration">0:00</span> estimated ·
          <span id="sbClipCount">0</span> clips to source
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="check-action" onclick="sbSelectAll()" style="padding:4px 10px">Reset</button>
        <button class="check-action" onclick="copyStoryboard()" id="copyStoryboardBtn" style="padding:4px 10px">Copy Storyboard</button>
        <button class="check-action" onclick="saveStoryboard()" id="saveStoryboardBtn" style="padding:4px 10px">💾 Save</button>
        <span id="saveStoryboardStatus" style="font-size:.65rem;color:#444"></span>
      </div>
    </div>
    <div id="reviewBanner">
      <span class="rv-icon"></span>
      <div><div class="rv-msg" id="reviewMsg"></div><div class="rv-detail" id="reviewDetail"></div></div>
    </div>
    <div class="sb-list" id="sbList"></div>

    <!-- Storyboard chat: Claude-powered scene editing without full re-run -->
    <div id="sbChatBox" style="margin-top:14px;border-top:1px solid #1a1a1a;padding-top:12px;">
      <div style="font-size:.6rem;color:#3a3a3a;text-transform:uppercase;letter-spacing:.08em;margin-bottom:7px">Edit storyboard with Claude</div>
      <div style="display:flex;gap:8px;align-items:center">
        <input id="sbEditInput" type="text"
          placeholder='e.g. "Remove all stat graphics from Act 3" or "Make the anchor more central in Act 2"'
          style="flex:1;background:#0d0d0d;border:1px solid #2a2a2a;color:#ddd;padding:7px 11px;border-radius:4px;font-size:.78rem;font-family:inherit"
          onkeydown="if(event.key==='Enter')submitStoryboardEdit()" />
        <button onclick="submitStoryboardEdit()" id="sbEditBtn"
          style="background:#111;border:1px solid #2a5cff;color:#6699ff;border-radius:4px;padding:7px 14px;font-size:.78rem;cursor:pointer;white-space:nowrap;font-family:inherit">
          Edit
        </button>
      </div>
      <div id="sbEditStatus" style="font-size:.68rem;color:#444;margin-top:5px;min-height:1em"></div>
    </div>

    <div class="btn-row" style="margin-top:16px">
      <button class="secondary" onclick="goStep(3)">← Blueprint</button>
      <button onclick="runPipeline()" id="runPipelineBtn">Confirm &amp; Run Pipeline →</button>
    </div>
  </div>

  <!-- STEP 5: Live pipeline log -->
  <div class="step" id="step5">
    <div id="pipelineStatus" style="margin-bottom:14px"></div>
    <div id="logBox" style="
      background:#111; border:1.5px solid #DDD9D3; border-radius:7px;
      height:420px; overflow-y:auto; padding:14px 16px;
      font-family:'Courier New',monospace; font-size:0.72rem; line-height:1.7;
    "></div>
    <div class="btn-row" style="margin-top:12px">
      <button class="secondary" id="newVideoBtn" onclick="reset()" style="display:none">New Video</button>
      <button id="openOutputBtn" style="display:none" onclick="openOutput()">📁 Copy Output Path</button>
      <a id="clipsBtn" href="#" target="_blank" style="display:none" class="btn-link">📋 Source Clips ↗</a>
      <a id="playerImagesBtn" href="#" target="_blank" style="display:none" class="btn-link">🖼️ Player Images ↗</a>
    </div>
  </div>

</div>
</div>

<!-- History -->
<div class="history" id="historySection" style="display:none">
  <p class="history-label">Previous Videos</p>
  <div id="historyList"></div>
</div>

<script>
let currentTitle = '', currentEntity = '', currentWiki = '', currentSafeName = '';
let currentFacts = [], currentBlueprint = null;

function _toSafeName(t) {{
  return t.toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'');
}}
let _storyboard = [];

const ACT_COLORS  = ['#C9A84C','#6366f1','#22c55e','#f59e0b','#ef4444','#8b5cf6'];
const ACT_WEIGHTS = [1, 2.5, 2.5, 2.5, 2, 2]; // proportional widths for timeline
const CAT_ICONS   = {'Career Moment':'⚽','Controversy':'⚡','Achievement':'🏆','Personal Story':'❤️',"Director's Intent":'📝'};

loadHistory();
loadProjectsList();

// ── Resume project ───────────────────────────────────────────────────────────
async function loadProjectsList() {
  const container = document.getElementById('projectsList');
  if (!container) return;
  container.innerHTML = '<div style="padding:18px;text-align:center;font-size:.72rem;color:#444">Loading projects…</div>';
  try {
    const res = await fetch('/projects').then(r => r.json());
    const projects = res.projects || [];
    if (!projects.length) {
      container.innerHTML = '<div style="padding:18px;text-align:center;font-size:.72rem;color:#444">No projects yet — start one above.</div>';
      return;
    }
    container.innerHTML = projects.map(p => `
      <div onclick="resumeProject('${p.safe_name}')"
           style="padding:11px 14px;border-bottom:1px solid #161616;cursor:pointer;display:flex;align-items:center;gap:14px;transition:background .12s"
           onmouseover="this.style.background='#121212'" onmouseout="this.style.background='transparent'">
        <div style="flex:1;min-width:0">
          <div style="font-size:.82rem;color:#ddd;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.title)}</div>
          <div style="font-size:.62rem;color:#555;margin-top:3px">
            ${p.last_modified}
            ${p.has_storyboard ? ` · <span style="color:#22c55e">📝 ${p.scene_count} scenes</span>` : ' · <span style="color:#777">no storyboard</span>'}
            ${p.has_retention ? ' · 🎯 retention' : ''}
            ${p.has_narration ? ' · 🎙 narration' : ''}
          </div>
        </div>
        <div style="font-size:.7rem;color:#C9A84C;flex-shrink:0">↻ Resume →</div>
      </div>
    `).join('');
  } catch (e) {
    container.innerHTML = `<div style="padding:18px;text-align:center;font-size:.72rem;color:var(--red)">Error: ${esc(e.message)}</div>`;
  }
}

async function resumeProject(safeName) {
  setLoaderText('Loading project…', 'Restoring storyboard, retention brief, and director context');
  showStep('loader');
  try {
    const data = await fetch('/resume/' + encodeURIComponent(safeName)).then(r => r.json());
    if (data.error) { alert('Resume failed: ' + data.error); showStep(1); return; }

    // Hydrate global state
    currentTitle    = data.title || '';
    currentSafeName = data.safe_name || _toSafeName(currentTitle);
    currentEntity   = data.entity || '';
    currentWiki     = '';
    currentFacts    = [];
    currentBlueprint = null;
    currentRetentionBrief = data.retentionBrief || null;
    if (currentRetentionBrief) {
      currentRetentionBrief._candidates = currentRetentionBrief.anchor_candidates ||
        [currentRetentionBrief.anchor_character].filter(Boolean);
    }
    _storyboard = data.storyboard || [];

    // Reflect into UI fields the user might re-edit
    const ti = document.getElementById('titleInput');      if (ti) ti.value = currentTitle;
    const ca = document.getElementById('contextArea');     if (ca) ca.value = data.context || '';
    const vt = document.getElementById('voiceoverToggle'); if (vt) vt.checked = !data.skipVoiceover;
    const vs = document.getElementById('voiceSelect');     if (vs && data.voiceId) vs.value = data.voiceId;

    if (!_storyboard.length) {
      alert('This project has no saved storyboard. Run the wizard from Step 1.');
      showStep(1);
      return;
    }

    renderStoryboard(_storyboard);
    showStep(4);
    if (typeof reviewStoryboard === 'function') reviewStoryboard();
    if (typeof _populateRerunAnchorDropdown === 'function') _populateRerunAnchorDropdown();
  } catch (e) {
    alert('Resume failed: ' + e.message);
    showStep(1);
  }
}

// ── Navigation ───────────────────────────────────────────────────────────────

function setProgress(n) {
  for (let i = 1; i <= 5; i++) {
    const el = document.getElementById('prog' + i);
    if (!el) continue;
    el.className = 'prog-step' + (i === n ? ' active' : i < n ? ' done' : '');
  }
}

function showStep(n) {
  document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
  document.getElementById('loader').classList.remove('active');
  const wrap = document.getElementById('mainWrap');
  if (n === 'loader') {
    document.getElementById('loader').classList.add('active');
    wrap.className = 'main';
  } else {
    document.getElementById('step' + n).classList.add('active');
    wrap.className = (n === 3 || n === 4) ? 'main wide' : 'main';
    setProgress(n);
    if (n !== 3 && n !== 4) document.getElementById('mainCard').style.maxWidth = '';
  }
}

function goStep(n) {
  clearError('step1Error'); clearError('step2Error');
  showStep(n);
}

function showError(id, msg) { const e = document.getElementById(id); e.textContent = msg; e.style.display = 'block'; }
function clearError(id) { document.getElementById(id).style.display = 'none'; }

// Prefill title from ?prefill= query param (set by Curiosity Ideas "Use" button)
(function() {
  const p = new URLSearchParams(window.location.search).get('prefill');
  if (p) { const el = document.getElementById('titleInput'); if (el) el.value = p; }
})();

// ── Step 1 → 2 ───────────────────────────────────────────────────────────────

async function analyse() {
  const title = document.getElementById('titleInput').value.trim();
  if (!title) { showError('step1Error', 'Please enter a video title.'); return; }
  clearError('step1Error');
  currentTitle    = title;
  currentSafeName = _toSafeName(title);

  setLoaderText('Researching subject...', 'Fetching Wikipedia & news headlines');
  showStep('loader');

  try {
    const res  = await fetch('/suggest', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ title })
    });
    const data = await res.json();
    if (data.error) { showStep(1); showError('step1Error', data.error); return; }

    currentEntity = data.entity;
    currentWiki   = data.wiki || '';
    document.getElementById('entityTag').textContent = data.entity;
    document.getElementById('contextArea').value = data.context;
    showStep(2);

    // Load checklist in background
    loadFacts();
  } catch(e) {
    showStep(1); showError('step1Error', 'Server error: ' + e.message);
  }
}

function setLoaderText(main, sub) {
  document.getElementById('loaderText').textContent = main;
  document.getElementById('loaderSub').textContent  = sub;
}

// ── Checklist ────────────────────────────────────────────────────────────────

async function loadFacts() {
  try {
    const res  = await fetch('/facts', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ entity: currentEntity, wiki: currentWiki })
    });
    const data = await res.json();
    currentFacts = data.facts || [];
    renderChecklist(currentFacts);
  } catch(e) {
    document.getElementById('checklistArea').innerHTML =
      '<p style="font-size:0.68rem;color:#222">Could not load facts checklist.</p>';
  }
}

function renderChecklist(facts) {
  if (!facts.length) {
    document.getElementById('checklistArea').innerHTML =
      '<p style="font-size:0.68rem;color:#222">No facts extracted — run the pipeline directly.</p>';
    return;
  }

  const categories = {};
  facts.forEach(f => {
    if (!categories[f.category]) categories[f.category] = [];
    categories[f.category].push(f);
  });

  const catOrder = ["Director's Intent", 'Career Moment','Achievement','Controversy','Personal Story'];
  let html = '';

  catOrder.forEach(cat => {
    const items = categories[cat];
    if (!items || !items.length) return;
    const checked = items.filter(i => i.checked).length;
    const icon = CAT_ICONS[cat] || '●';
    const safeCat = cat.replace(/[\s']+/g,'_');
    const isBrief = cat === "Director's Intent";

    html += `<div class="cat-section">
      <div class="cat-header" onclick="toggleCat('${safeCat}')">
        <div class="cat-header-left">
          <span class="cat-icon">${icon}</span>
          <span>${cat}</span>
          ${isBrief ? '<span style="font-size:0.6rem;color:#d97706;margin-left:6px;text-transform:uppercase;letter-spacing:0.06em">from your brief</span>' : ''}
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          <span class="cat-count" id="cnt_${safeCat}">${checked}/${items.length} selected</span>
          <span class="cat-toggle open" id="tog_${safeCat}">▲</span>
        </div>
      </div>
      <div class="cat-items open" id="items_${safeCat}">
        ${items.map(f => `
          <label class="fact-item${isBrief ? ' brief-fact' : ''}">
            <input type="checkbox" id="fct_${f.id}" ${f.checked?'checked':''} onchange="updateCatCount('${safeCat}','${cat}')">
            <div class="fact-cb"></div>
            <div class="fact-text">
              <div class="fact-label">${esc(f.label)}</div>
              ${f.detail ? `<div class="fact-detail">${esc(f.detail)}</div>` : ''}
            </div>
            <div class="fact-imp ${f.importance}"></div>
          </label>`).join('')}
      </div>
    </div>`;
  });

  document.getElementById('checklistArea').innerHTML = html;
}

function toggleCat(safeCat) {
  const items = document.getElementById('items_' + safeCat);
  const tog   = document.getElementById('tog_' + safeCat);
  const open  = items.classList.toggle('open');
  tog.classList.toggle('open', open);
}

function updateCatCount(safeCat, cat) {
  const items = currentFacts.filter(f => f.category === cat);
  const checked = items.filter(f => {
    const cb = document.getElementById('fct_' + f.id);
    return cb && cb.checked;
  }).length;
  const key = cat.replace(/[\s']+/g,'_');
  const el = document.getElementById('cnt_' + key);
  if (el) el.textContent = `${checked}/${items.length} selected`;
}

function checkAll(state) {
  document.querySelectorAll('#checklistArea input[type="checkbox"]').forEach(cb => cb.checked = state);
  const catOrder = ["Director's Intent", 'Career Moment','Achievement','Controversy','Personal Story'];
  catOrder.forEach(cat => updateCatCount(cat.replace(/[\s']+/g,'_'), cat));
}

function getCheckedFacts() {
  return currentFacts.filter(f => {
    const cb = document.getElementById('fct_' + f.id);
    return cb && cb.checked;
  }).map(f => f.label + (f.detail ? ' — ' + f.detail : ''));
}

function getExcludedFacts() {
  return currentFacts.filter(f => {
    if (f.importance !== 'high') return false;
    const cb = document.getElementById('fct_' + f.id);
    return cb && !cb.checked;
  }).map(f => f.label);
}

// ── Voice preview ────────────────────────────────────────────────────────────

async function previewVoice() {
  const btn = document.querySelector('#voiceSelectWrap button');
  const voiceId = document.getElementById('voiceSelect').value;
  btn.textContent = '…';
  btn.disabled = true;
  try {
    const res  = await fetch('/voice-preview', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ voiceId })
    });
    const data = await res.json();
    if (data.error) { alert('Preview error: ' + data.error); return; }
    const audio = new Audio('data:audio/mpeg;base64,' + data.audio_base64);
    audio.play();
  } catch (e) {
    alert('Preview failed: ' + e.message);
  } finally {
    btn.textContent = '▶ Preview';
    btn.disabled = false;
  }
}

// ── Step 2 → 3: Blueprint ────────────────────────────────────────────────────

async function getBlueprint() {
  clearError('step2Error');
  const context = document.getElementById('contextArea').value.trim();
  if (!context) { showError('step2Error', 'Context cannot be empty.'); return; }

  setLoaderText('Reading your Director\'s Brief...', 'Extracting key moments you want included');
  showStep('loader');

  // Step 1: extract facts from the Director's Brief and add to checklist
  try {
    const cfRes = await fetch('/context-facts', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ entity: currentEntity, context })
    });
    const cfData = await cfRes.json();
    const ctxFacts = cfData.facts || [];
    if (ctxFacts.length) {
      // Remove stale context facts, merge in fresh ones
      currentFacts = currentFacts.filter(f => f.source !== 'context');
      currentFacts = [...currentFacts, ...ctxFacts];
      renderChecklist(currentFacts);
    }
  } catch(e) {
    console.warn('Context facts extraction failed:', e);
  }

  const checkedFacts  = getCheckedFacts();
  const excludedFacts = getExcludedFacts();

  setLoaderText('Building script blueprint...', `Planning acts with ${checkedFacts.length} confirmed moments`);

  try {
    const res  = await fetch('/blueprint', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ title: currentTitle, entity: currentEntity, context, wiki: currentWiki, checkedFacts, excludedFacts })
    });
    const data = await res.json();
    if (data.error) { showStep(2); showError('step2Error', data.error); return; }

    currentBlueprint = data.blueprint;
    renderBlueprint(data.blueprint);
    showStep(3);
    // Fire retention brief asynchronously — panel populates while user reads blueprint
    fetchRetentionBrief();
  } catch(e) {
    showStep(2); showError('step2Error', 'Blueprint error: ' + e.message);
  }
}

let currentRetentionBrief = null;

async function fetchRetentionBrief() {
  const panel = document.getElementById('retentionPanel');
  panel.style.display = 'block';
  panel.innerHTML = '<div style="font-size:.72rem;color:#555;padding:14px 0">Analysing retention mechanics…</div>';

  try {
    const context = document.getElementById('contextArea').value.trim();
    const res = await fetch('/retention-brief', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        entity: currentEntity, blueprint: currentBlueprint,
        context, wiki: currentWiki, safe_name: currentSafeName
      })
    });
    const data = await res.json();
    if (data.retention_brief) {
      currentRetentionBrief = data.retention_brief;
      renderRetentionPanel(data.retention_brief);
    } else {
      panel.style.display = 'none';
    }
  } catch(e) {
    panel.style.display = 'none';
  }
}

function renderRetentionPanel(rb) {
  const panel = document.getElementById('retentionPanel');
  const cf   = rb.contrast_frame    || {};
  const rfs  = rb.act_reframes      || [];
  const cands = rb.anchor_candidates || [rb.anchor_character].filter(Boolean);
  // Store candidates on the brief for later access
  currentRetentionBrief._candidates = cands;
  // Race fix: storyboard step may have rendered before retention brief landed —
  // populate the rerun-anchor dropdown now in case _populateRerunAnchorDropdown
  // already fired with empty candidates.
  try { _populateRerunAnchorDropdown(); } catch (e) { /* dropdown not yet in DOM */ }

  const rfHtml = rfs.map(r =>
    `<div style="padding:5px 0;border-bottom:1px solid #1a1a1a">
      <span style="font-size:.62rem;color:#555;text-transform:uppercase;letter-spacing:.05em">${esc(r.act||'')}</span>
      <div style="font-size:.75rem;color:#999;margin-top:3px">${esc(r.question||'')} <span style="color:#555">→ ${esc(r.payoff||'')}</span></div>
    </div>`
  ).join('');

  // Anchor candidates — scored list
  const maxScore = Math.max(...cands.map(c => c.score || 0), 1);
  const candidateHtml = cands.map((c, i) => {
    const isSelected = i === 0;
    const score = c.score || 0;
    const barW = Math.max(8, Math.round((score / Math.max(maxScore, 1)) * 100));
    return `
    <label style="display:flex;align-items:flex-start;gap:10px;padding:10px 0;border-bottom:1px solid #191919;cursor:pointer;${isSelected?'background:transparent':''}">
      <input type="radio" name="anchorPick" value="${i}" ${isSelected?'checked':''} style="margin-top:3px;accent-color:#C9A84C">
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px">
          <span style="font-size:.82rem;color:#fff;font-weight:700">${esc(c.name||'')}</span>
          ${isSelected ? '<span style="font-size:.55rem;background:#1a1200;color:#C9A84C;border:1px solid #3a2800;border-radius:3px;padding:1px 6px;letter-spacing:.06em">ENGINE PICK</span>' : ''}
          <span style="font-size:.6rem;color:#444;margin-left:auto">score ${score}</span>
        </div>
        <div style="height:3px;background:#1a1a1a;border-radius:2px;margin-bottom:5px">
          <div style="width:${barW}%;height:3px;background:${isSelected?'#C9A84C':'#444'};border-radius:2px;transition:width .3s"></div>
        </div>
        <div style="font-size:.72rem;color:#777;line-height:1.45;margin-bottom:3px">${esc(c.framing||'')}</div>
        ${c.visual_proof ? `<div style="font-size:.68rem;color:#555;font-style:italic">📹 ${esc(c.visual_proof)}</div>` : ''}
      </div>
    </label>`;
  }).join('');

  panel.innerHTML = `
    <div style="border:1px solid #1e1e1e;border-radius:8px;overflow:hidden">
      <div style="background:#0d0d0d;padding:10px 16px;display:flex;align-items:center;gap:10px;border-bottom:1px solid #1a1a1a">
        <span style="font-size:.6rem;color:#555;text-transform:uppercase;letter-spacing:.1em;font-weight:700">Retention Mechanics</span>
        <span style="font-size:.58rem;color:#333;margin-left:auto">auto-injected into storyboard</span>
      </div>

      <div style="padding:14px 16px;border-bottom:1px solid #1a1a1a">
        <div style="font-size:.58rem;color:#444;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px">Contrast Loop</div>
        <div style="font-size:.82rem;color:#e8c84a;font-style:italic;line-height:1.5;margin-bottom:8px">"${esc(cf.loop_sentence||rb.loop_sentence||'')}"</div>
        <div style="display:flex;gap:6px">
          <span style="font-size:.65rem;background:#1a0a00;color:#C9A84C;border:1px solid #3a2000;border-radius:3px;padding:2px 8px">${esc(cf.past_label||'Before')}</span>
          <span style="font-size:.65rem;color:#333">→</span>
          <span style="font-size:.65rem;background:#0a1400;color:#4a8a2a;border:1px solid #1a2a00;border-radius:3px;padding:2px 8px">${esc(cf.present_label||'After')}</span>
        </div>
      </div>

      <div style="padding:14px 16px;border-bottom:1px solid #1a1a1a">
        <div style="font-size:.58rem;color:#444;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Anchor Character <span style="color:#333;font-weight:400;text-transform:none">— pick who drives the narrative</span></div>
        <div id="anchorCandidateList">${candidateHtml}</div>
      </div>

      ${rb.closing_question ? `<div style="padding:10px 16px;background:#0a0800;border-top:1px solid #1a1a1a">
        <span style="font-size:.58rem;color:#444;text-transform:uppercase;letter-spacing:.08em">Closing Provocation</span>
        <div style="font-size:.85rem;color:#C9A84C;font-style:italic;margin-top:4px">"${esc(rb.closing_question)}"</div>
      </div>` : ''}

      ${rfHtml ? `<div style="padding:10px 16px;border-top:1px solid #1a1a1a">
        <div style="font-size:.58rem;color:#444;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Act Purpose</div>
        ${rfHtml}
      </div>` : ''}

      <div style="padding:14px 16px;border-top:1px solid #1a1a1a">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <span style="font-size:.58rem;color:#444;text-transform:uppercase;letter-spacing:.08em">Director's Override Notes</span>
          <span style="font-size:.58rem;color:#333">— injected as MUST INCLUDE into storyboard</span>
        </div>
        <textarea id="directorOverride" placeholder="e.g. Add a final act showing Endrick, Vinicius and Savinho — the next generation gap. Include a CLIP COMPARE between 1982 Falcão and 2022 Casemiro." style="width:100%;box-sizing:border-box;background:#0a0a0a;border:1px solid #2a2a2a;border-radius:4px;color:#ccc;font-size:.75rem;padding:8px 10px;resize:vertical;min-height:70px;font-family:inherit;line-height:1.5" rows="3"></textarea>
      </div>
    </div>`;
}

function _getSelectedAnchor() {
  const brief = currentRetentionBrief;
  if (!brief) return null;
  const radios = document.querySelectorAll('input[name="anchorPick"]');
  let selectedIdx = 0;
  radios.forEach(r => { if (r.checked) selectedIdx = parseInt(r.value); });
  const candidates = brief._candidates || [brief.anchor_character].filter(Boolean);
  return candidates[selectedIdx] || candidates[0] || null;
}

function _getDirectorOverride() {
  const ta = document.getElementById('directorOverride');
  return ta ? ta.value.trim() : '';
}

// ── Blueprint render ──────────────────────────────────────────────────────────

function renderBlueprint(bp) {
  const s      = bp.summary || {};
  const acts   = bp.acts    || [];
  const issues = detectIssues(acts);
  let html = '';

  // Hero: timeline bar + summary
  const total = ACT_WEIGHTS.reduce((a,b) => a+b, 0);
  const tlSegs = acts.map((act, i) => {
    const w = ((ACT_WEIGHTS[i] || 1) / total * 100).toFixed(1);
    return `<div class="tl-seg" style="flex:${ACT_WEIGHTS[i]};background:${ACT_COLORS[i]}" title="${esc(act.name)}" onclick="scrollAct(${i})"></div>`;
  }).join('');

  html += `<div class="bp-hero">
    <div class="bp-hero-title">Video Structure · 20 min documentary</div>
    <div class="timeline-bar">${tlSegs}</div>
    <div class="bp-summary">
      <span class="bp-pill clip">🎬 ${s.clips||0} Clip Tags</span>
      <span class="bp-pill info">📊 ${s.infographics||0} Infographics</span>
      <span class="bp-pill hero">✦ ${s.hero||0} Hero Cards</span>
      <span class="bp-pill total">${s.total||0} total rendered</span>
    </div>
  </div>`;

  // Issues
  if (issues.length) {
    html += `<div class="issue-banner"><strong>⚠ Issues to fix</strong>${issues.map(i=>'• '+i).join('<br>')}</div>`;
  }

  // Acts
  acts.forEach((act, i) => {
    const color    = ACT_COLORS[i] || '#555';
    const tagCount = (act.tags || []).length;
    const clipN    = (act.tags || []).filter(t => t.category === 'clip').length;
    const warnClip = clipN < 2 && i > 0;

    html += `<div class="act-card" id="actCard${i}">
      <div class="act-hdr" onclick="toggleAct(${i})">
        <div class="act-bar" style="background:${color}; height:36px"></div>
        <div class="act-name-col">
          <div class="act-name">${esc(act.name)}</div>
          <div class="act-range">${esc(act.timeRange||'')} · ${esc(act.wordCount||'')} words</div>
        </div>
        <div class="act-meta-col">
          <span class="act-tagcount">${tagCount} tags</span>
          <span class="act-caret open" id="caret${i}">▲</span>
        </div>
      </div>
      <div class="act-body open" id="abody${i}">
        <div class="act-events">
          ${(act.events||[]).map(e=>`<div class="act-event">${esc(e)}</div>`).join('')}
        </div>
        <div class="act-tags">
          ${(act.tags||[]).map(t => renderChip(t)).join('')}
        </div>
        ${warnClip ? `<div class="act-warn">⚠ Only ${clipN} clip tag${clipN!==1?'s':''} — add more CLIP SINGLE moments</div>` : ''}
      </div>
    </div>`;
  });

  document.getElementById('blueprintContent').innerHTML = html;
}

function renderChip(t) {
  const cat     = t.category || 'info';
  const type    = t.type    || '';
  const content = t.content || '';
  return `<div class="tag-chip ${cat}" title="${esc(type+': '+content)}">
    <span class="tc-type">${esc(type)}</span>
    <span class="tc-content">${esc(content)}</span>
  </div>`;
}

function detectIssues(acts) {
  const issues = [];
  if (!acts.length) return issues;
  const cold   = acts[0];
  if (!(cold.tags||[]).some(t => (t.type||'').includes('HERO INTRO')))
    issues.push('Cold Open is missing the mandatory HERO INTRO card.');
  // Transitions are auto-injected by _generate_storyboard() post-processing.
  // Checking for them here (blueprint stage) always produces false negatives — skip.
  const totalClips = acts.reduce((s,a) => s + (a.tags||[]).filter(t=>t.category==='clip').length, 0);
  if (totalClips < 8) issues.push(`Only ${totalClips} clip tags — aim for at least 8.`);
  if (!acts.some(a => (a.tags||[]).some(t => (t.type||'').includes('PLAYER RADAR'))))
    issues.push('No PLAYER RADAR tag — add one to ACT 3 for player analysis.');
  return issues;
}

function toggleAct(i) {
  const body  = document.getElementById('abody' + i);
  const caret = document.getElementById('caret' + i);
  const open  = body.classList.toggle('open');
  caret.classList.toggle('open', open);
}

function scrollAct(i) {
  const el = document.getElementById('actCard' + i);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Step 3 → 4: Run pipeline ──────────────────────────────────────────────────

let _pollInterval = null;
let _logOffset    = 0;
let _safeName     = '';

async function runPipeline() {
  const context       = document.getElementById('contextArea').value.trim();
  const checkedFacts  = getCheckedFacts();
  const excludedFacts = getExcludedFacts();
  const storyboard    = getStoryboardData();

  // Auto-save storyboard before pipeline so it survives any crash
  if (_storyboard && _storyboard.length && currentTitle) {
    const safeName = currentTitle.toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'');
    await fetch('/save-storyboard', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({safe_name: safeName, storyboard: {scenes: _storyboard}})
    }).catch(()=>{});
  }

  try {
    const res  = await fetch('/run', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ title: currentTitle, context, checkedFacts, excludedFacts, storyboard, skipVoiceover: !document.getElementById('voiceoverToggle').checked, voiceId: document.getElementById('voiceSelect').value })
    });
    const data = await res.json();
    if (data.error) { alert(data.error); return; }

    _safeName   = data.safe_name;
    _logOffset  = 0;
    document.getElementById('logBox').innerHTML = '';
    document.getElementById('newVideoBtn').style.display   = 'none';
    document.getElementById('openOutputBtn').style.display = 'none';
    setStatus('running');
    showStep(5);
    loadHistory();

    _pollInterval = setInterval(pollLogs, 1800);
  } catch(e) { alert('Run error: ' + e.message); }
}

async function pollLogs() {
  try {
    const res  = await fetch(`/poll?safe_name=${_safeName}&offset=${_logOffset}`);
    const data = await res.json();

    (data.lines || []).forEach(appendLog);
    _logOffset = data.offset;

    if (data.done) {
      clearInterval(_pollInterval);
      _pollInterval = null;
      setStatus(data.success ? 'done' : 'error');
      document.getElementById('newVideoBtn').style.display   = 'inline-flex';
      document.getElementById('openOutputBtn').style.display = 'inline-flex';
      const cb = document.getElementById('clipsBtn');
      cb.href = `/clips/${encodeURIComponent(_safeName)}`;
      cb.style.display = 'inline-flex';
      const pb = document.getElementById('playerImagesBtn');
      pb.href = `/player-images/${encodeURIComponent(_safeName)}`;
      pb.style.display = 'inline-flex';
    }
  } catch(e) { /* keep polling */ }
}

function appendLog(line) {
  const box = document.getElementById('logBox');
  const div = document.createElement('div');
  div.className = logClass(line);
  div.textContent = line;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function logClass(line) {
  if (/STARTING PIPELINE|PIPELINE COMPLETE|={10}/.test(line)) return 'log-head';
  if (/✓|complete|success/i.test(line)) return 'log-success';
  if (/✗|error|failed|traceback/i.test(line)) return 'log-error';
  if (/warning|warn|skip/i.test(line)) return 'log-warn';
  if (/\[\*\]|\[Graphics\]|\[Engine\]/.test(line)) return 'log-info';
  return 'log-normal';
}

function setStatus(state) {
  const el = document.getElementById('pipelineStatus');
  if (state === 'running') {
    el.innerHTML = '<div class="status-running"><div class="pulse"></div>Pipeline running — this takes 8–12 minutes</div>';
  } else if (state === 'done') {
    el.innerHTML = `<div class="status-done">✓ Pipeline complete &nbsp;<a href="/edit/${_safeName}" style="display:inline-block;margin-left:12px;padding:6px 16px;background:#1660FF;color:#fff;border-radius:7px;font-size:0.78rem;font-weight:700;text-decoration:none;box-shadow:0 2px 8px rgba(22,96,255,0.3)">Open Timeline Editor →</a><a href="/studio/${_safeName}" style="display:inline-block;margin-left:8px;padding:6px 16px;background:#fff;color:#1660FF;border:1.5px solid #1660FF;border-radius:7px;font-size:0.78rem;font-weight:700;text-decoration:none">Grid View</a></div>`;
  } else {
    el.innerHTML = '<div class="status-error">✗ Pipeline error — check log above</div>';
  }
}

function openOutput() {
  // Show the output path (relative to engine root) in a copyable box
  const path = `output/${_safeName}`;
  navigator.clipboard.writeText(path);
  alert(`Path copied: ${path}`);
}

function reset() {
  if (_pollInterval) { clearInterval(_pollInterval); _pollInterval = null; }
  document.getElementById('titleInput').value = '';
  document.getElementById('contextArea').value = '';
  clearError('step1Error'); clearError('step2Error');
  document.getElementById('checklistArea').innerHTML = '';
  document.getElementById('logBox').innerHTML = '';
  currentTitle=''; currentEntity=''; currentWiki=''; currentSafeName='';
  currentFacts=[]; currentBlueprint=null; currentRetentionBrief=null;
  const rp = document.getElementById('retentionPanel');
  if (rp) {{ rp.style.display='none'; rp.innerHTML=''; }}
  _storyboard = [];
  showStep(1);
}

// ── Step 3 → 4: Storyboard ────────────────────────────────────────────────────

async function getStoryboard() {
  setLoaderText('Building storyboard...', 'Planning every scene, clip and graphic in sequence');
  showStep('loader');

  try {
    const context = document.getElementById('contextArea').value.trim();
    const formatSel = document.getElementById('formatSelect');
    const format = formatSel ? (formatSel.value === 'auto' ? null : formatSel.value) : null;
    const anchorOverride   = _getSelectedAnchor();
    const directorOverride = _getDirectorOverride();
    const res  = await fetch('/storyboard', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        title: currentTitle, entity: currentEntity,
        blueprint: currentBlueprint, checkedFacts: getCheckedFacts(),
        wiki: currentWiki, context,
        retentionBrief: currentRetentionBrief,
        format,
        anchorOverride,
        directorOverride,
      })
    });
    const data = await res.json();
    if (data.error) { showStep(3); return; }
    _storyboard = data.storyboard.scenes || [];
    renderStoryboard(_storyboard);
    showStep(4);
    reviewStoryboard();
    _populateRerunAnchorDropdown();
  } catch(e) {
    showStep(3);
    alert('Storyboard error: ' + e.message);
  }
}

function renderStoryboard(scenes) {
  // Clear existing review state
  const banner = document.getElementById('reviewBanner');
  if (banner) { banner.className = ''; banner.style.display = 'none'; }
  _reviewIssues = [];

  const list = document.getElementById('sbList');
  list.innerHTML = '';

  let lastAct = null;
  let lastType = null;

  scenes.forEach((scene, idx) => {
    // Act divider
    if (scene.act !== lastAct) {
      const colors = ['#C9A84C','#6366f1','#22c55e','#f59e0b','#ef4444','#8b5cf6'];
      const color  = colors[scene.actIndex || 0] || '#555';
      const div = document.createElement('div');
      div.className = 'act-divider';
      div.innerHTML = '<div class="act-divider-bar"></div><div class="act-divider-label" style="color:' + color + '">' + esc(scene.act) + '</div><div class="act-divider-bar"></div>';
      list.appendChild(div);
      lastAct = scene.act;
    }

    // Gap warning: two graphics back-to-back
    if (lastType === 'graphic' && scene.type === 'graphic') {
      const warn = document.createElement('div');
      warn.className = 'sb-warn';
      warn.textContent = '\u26a0 Two graphics in a row \u2014 consider adding narration between them';
      list.appendChild(warn);
    }
    lastType = scene.type;

    // Add-before row
    list.appendChild(makeAddRow(idx));

    // Scene card
    list.appendChild(makeSceneCard(scene, idx));
  });

  // Final add row
  list.appendChild(makeAddRow(scenes.length));

  updateSbStats();
}

function makeSceneCard(scene, idx) {
  const card = document.createElement('div');
  card.className = 'scene-card ' + scene.type;
  card.dataset.id = scene.id;

  const badge = scene.type === 'narration' ? 'NARRATION'
              : scene.type === 'clip'      ? 'CLIP'
              : (scene.template || 'GRAPHIC');

  const durLabel = scene.duration ? (scene.duration + 's') : '';
  const labelHtml = scene.label
    ? '<div class="scene-label" contenteditable="true" data-field="label">' + esc(scene.label) + '</div>' : '';

  card.innerHTML =
    '<div class="scene-inner">' +
      '<div class="scene-badge">' + esc(badge) + '</div>' +
      '<div class="scene-body">' +
        '<div class="scene-content" contenteditable="true" data-field="content">' + esc(scene.content || '') + '</div>' +
        labelHtml +
      '</div>' +
      '<div class="scene-meta">' +
        '<span class="scene-dur">' + durLabel + '</span>' +
        '<button class="scene-del" onclick="deleteScene(\'' + scene.id + '\')">&#x2715;</button>' +
      '</div>' +
    '</div>';

  // Save edits back to _storyboard on input
  card.querySelectorAll('[contenteditable]').forEach(el => {
    el.addEventListener('input', () => {
      const field = el.dataset.field;
      const s = _storyboard.find(s => s.id === scene.id);
      if (s) s[field] = el.textContent;
    });
  });

  return card;
}

function makeAddRow(idx) {
  const row = document.createElement('div');
  row.className = 'sb-add-row';
  row.innerHTML = '<button class="sb-add-btn" onclick="showAddPicker(event, ' + idx + ')">+ add scene</button>';
  return row;
}

let _pickerEl = null;

function showAddPicker(event, insertIdx) {
  if (_pickerEl) { _pickerEl.remove(); _pickerEl = null; }
  const picker = document.createElement('div');
  picker.className = 'add-picker';
  picker.style.top  = (event.target.getBoundingClientRect().bottom + window.scrollY + 4) + 'px';
  picker.style.left = (event.target.getBoundingClientRect().left)  + 'px';
  picker.style.position = 'absolute';
  // Sourced from templates/visual_grammar.md — every template graphics_agent
  // can render is listed here so the user can insert any of them manually.
  const GFX = [
    // Narrative primitives
    { t: 'HERO INTRO',           emoji: '&#x1F3AC;', label: 'Intro'              },
    { t: 'TOURNAMENT BRACKET',      emoji: '&#x1F3C6;', label: 'Tournament Bracket' },
    { t: 'CAREER TIMELINE',         emoji: '&#x23F3;',  label: 'Career Timeline'    },
    { t: 'MATCH RESULT',            emoji: '&#x1F4CA;', label: 'Match Result'       },
    { t: 'TEAM LINEUP',             emoji: '&#x2B21;',  label: 'Team Lineup'        },
    { t: 'PLAYER TRIO',             emoji: '&#x1F465;', label: 'Player Trio'        },
    { t: 'PLAYER RADAR',            emoji: '&#x25CE;',  label: 'Player Radar'       },
    { t: 'PLAYER STATS',            emoji: '&#x1F4C8;', label: 'Player Season Stats'},
    { t: 'SEASON COMPARISON',       emoji: '&#x21C6;',  label: 'Season Comparison'  },
    { t: 'STANDINGS TABLE',         emoji: '&#x1F4CB;', label: 'League Standings'   },
    { t: 'TOP SCORERS',             emoji: '&#x26BD;',  label: 'Top Scorers'        },
    { t: 'TOP ASSISTS',             emoji: '&#x1F3AF;', label: 'Top Assists'        },
    { t: 'TRANSFER',                emoji: '&#x1F504;', label: 'Transfer'           },
    { t: 'DISCIPLINARY RECORD',     emoji: '&#x1F7E5;', label: 'Disciplinary Record'},
    { t: 'QUOTE CARD',              emoji: '&#x1F4AC;', label: 'Quote Card'         },
    // Hero family
    { t: 'HERO BIG STAT',        emoji: '&#x2736;',  label: 'Big Stat'           },
    { t: 'HERO STAT BARS',       emoji: '&#x1F4CA;', label: 'Stat Bars'          },
    { t: 'HERO FORM RUN',        emoji: '&#x1F4C8;', label: 'Form Run'           },
    { t: 'HERO TACTICAL',        emoji: '&#x2B21;',  label: 'Tactical Map'       },
    { t: 'HERO LEAGUE GRAPH',    emoji: '&#x1F4C9;', label: 'League Graph'       },
    { t: 'HERO TRANSFER RECORD', emoji: '&#x1F4B0;', label: 'Transfer Record'    },
    { t: 'HERO QUOTE',           emoji: '&#x1F5E8;', label: 'Hero Quote'      },
    { t: 'HERO CONCEPT',         emoji: '&#x2194;',  label: 'Concept Card'       },
    { t: 'HERO SCATTER',         emoji: '&#x271B;',  label: 'Scatter Plot'       },
    { t: 'HERO SHOT MAP',        emoji: '&#x26BD;',  label: 'Shot Map'           },
    { t: 'HERO MATCH TIMELINE',  emoji: '&#x23F1;',  label: 'Match Timeline'     },
    { t: 'HERO AWARDS LIST',     emoji: '&#x1F3C5;', label: 'Awards List'        },
    { t: 'HERO COMPARISON RADAR',emoji: '&#x25CE;',  label: 'Comparison Radar'   },
    { t: 'HERO SEASON TIMELINE', emoji: '&#x1F5D3;', label: 'Season Timeline'    },
  ];
  const gfxHtml = GFX.map(g =>
    '<div class="add-picker-item" onclick="addScene(' + insertIdx + ',\'graphic\',\'' + g.t + '\')">' + g.emoji + ' ' + g.label + '</div>'
  ).join('');
  picker.innerHTML =
    '<div class="add-picker-item" onclick="addScene(' + insertIdx + ',\'narration\')">&#x1F4DD; Narration</div>' +
    '<div class="add-picker-item" onclick="addScene(' + insertIdx + ',\'transition\',\'TRANSITION\')">&#x25B6; Transition (Act Break)</div>' +
    '<div class="add-picker-item" onclick="addScene(' + insertIdx + ',\'clip\')">&#x1F3AC; Clip</div>' +
    '<div style="height:1px;background:#E8E4DF;margin:5px 2px;"></div>' +
    gfxHtml;
  document.body.appendChild(picker);
  _pickerEl = picker;
  setTimeout(() => document.addEventListener('click', closePicker, {once:true}), 0);
}

function closePicker() {
  if (_pickerEl) { _pickerEl.remove(); _pickerEl = null; }
}

function addScene(insertIdx, type, template) {
  closePicker();
  const id = 's' + Date.now();
  const actIndex = (_storyboard[insertIdx - 1] || {}).actIndex || 0;
  const act      = (_storyboard[insertIdx - 1] || {}).act || 'COLD OPEN';
  const newScene = { id, act, actIndex, type, template: template || '', content: type === 'narration' ? 'Write narration here...' : 'Describe this scene...', label: '', duration: type === 'narration' ? 12 : 8 };
  _storyboard.splice(insertIdx, 0, newScene);
  renderStoryboard(_storyboard);
}

function deleteScene(id) {
  _storyboard = _storyboard.filter(s => s.id !== id);
  renderStoryboard(_storyboard);
}

function updateSbStats() {
  const total    = _storyboard.length;
  const clips    = _storyboard.filter(s => s.type === 'clip').length;
  const dur      = _storyboard.reduce((sum, s) => sum + (s.duration || 0), 0);
  const mins     = Math.floor(dur / 60);
  const secs     = dur % 60;
  document.getElementById('sbSceneCount').textContent = total;
  document.getElementById('sbDuration').textContent   = mins + ':' + String(secs).padStart(2,'0');
  document.getElementById('sbClipCount').textContent  = clips;
}

// ── Script Quality Review ─────────────────────────────────────────────────────

function _populateRerunAnchorDropdown() {
  const sel = document.getElementById('rerunAnchorSelect');
  if (!sel || !currentRetentionBrief) return;
  const candidates = currentRetentionBrief._candidates ||
    [currentRetentionBrief.anchor_character].filter(Boolean);
  sel.innerHTML = '<option value="">— use engine pick —</option>';
  candidates.forEach((c, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = `${c.name || '?'}  (score ${c.score ?? '?'})${i === 0 ? ' ← engine pick' : ''}`;
    sel.appendChild(opt);
  });
  // Also allow free-text entry by selecting "custom"
  const custom = document.createElement('option');
  custom.value = 'custom';
  custom.textContent = '+ enter a name manually…';
  sel.appendChild(custom);
}

async function rerunStoryboard() {
  const sel = document.getElementById('rerunAnchorSelect');
  const notesEl = document.getElementById('rerunDirectorNotes');
  const notes = notesEl ? notesEl.value.trim() : '';

  let anchorOverride = null;
  if (sel && sel.value !== '' && sel.value !== 'custom') {
    const idx = parseInt(sel.value);
    const candidates = currentRetentionBrief?._candidates ||
      [currentRetentionBrief?.anchor_character].filter(Boolean);
    anchorOverride = candidates[idx] || null;
  } else if (sel && sel.value === 'custom') {
    const name = prompt('Enter anchor character name:');
    if (!name || !name.trim()) return;
    anchorOverride = { name: name.trim(), framing: '', first_appears: 'ACT 1', closing_line: '' };
  }

  // Merge director notes: prepend re-run notes to any existing override
  const existingNotes = _getDirectorOverride();
  const combinedNotes = [notes, existingNotes].filter(Boolean).join('\n');

  setLoaderText('Re-running storyboard…', anchorOverride
    ? `Anchor locked: ${anchorOverride.name}${combinedNotes ? ' · Director notes injected' : ''}`
    : 'Applying director\'s notes…');
  showStep('loader');

  try {
    const context = document.getElementById('contextArea')?.value.trim() || '';
    const formatSel = document.getElementById('formatSelect');
    const format = formatSel ? (formatSel.value === 'auto' ? null : formatSel.value) : null;
    const res = await fetch('/storyboard', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        title: currentTitle, entity: currentEntity,
        blueprint: currentBlueprint, checkedFacts: getCheckedFacts(),
        wiki: currentWiki, context,
        retentionBrief: currentRetentionBrief,
        format,
        anchorOverride,
        directorOverride: combinedNotes,
      })
    });
    const data = await res.json();
    if (data.error) { showStep(4); return; }
    _storyboard = data.storyboard.scenes || [];
    renderStoryboard(_storyboard);
    showStep(4);
    reviewStoryboard();
    _populateRerunAnchorDropdown();
    // Clear re-run notes after use
    if (notesEl) notesEl.value = '';
  } catch(e) {
    showStep(4);
    alert('Re-run error: ' + e.message);
  }
}

let _reviewIssues = [];

async function reviewStoryboard() {
  const banner = document.getElementById('reviewBanner');
  const msg    = document.getElementById('reviewMsg');
  const detail = document.getElementById('reviewDetail');
  banner.className = '';
  banner.style.display = 'none';
  _reviewIssues = [];

  // Clear any existing issue badges
  document.querySelectorAll('.scene-issue-badge, .scene-issue-tip').forEach(el => el.remove());

  try {
    const res  = await fetch('/review-storyboard', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ scenes: _storyboard, entity: currentEntity })
    });
    const data = await res.json();
    _reviewIssues = data.issues || [];

    // Annotate individual scene cards
    const issuesByScene = {};
    for (const issue of _reviewIssues) {
      if (!issue.scene_id) continue;
      if (!issuesByScene[issue.scene_id]) issuesByScene[issue.scene_id] = [];
      issuesByScene[issue.scene_id].push(issue);
    }
    for (const [sceneId, issues] of Object.entries(issuesByScene)) {
      const card = document.querySelector('[data-id="' + sceneId + '"]');
      if (!card) continue;
      const inner = card.querySelector('.scene-inner');
      if (!inner) continue;
      const meta  = card.querySelector('.scene-meta');
      // Add issue badges before the delete button
      for (const issue of issues) {
        const badge = document.createElement('span');
        badge.className = 'scene-issue-badge ' + issue.severity;
        badge.textContent = issue.severity === 'error' ? '✗' : issue.severity === 'warning' ? '⚠' : 'i';
        badge.title = issue.problem;
        badge.onclick = (e) => {
          e.stopPropagation();
          const tip = badge.nextElementSibling;
          if (tip && tip.classList.contains('scene-issue-tip')) {
            tip.classList.toggle('visible');
          }
        };
        const tip = document.createElement('div');
        tip.className = 'scene-issue-tip';
        tip.textContent = issue.problem;
        meta.insertBefore(tip, meta.firstChild);
        meta.insertBefore(badge, tip);
      }
    }

    // Global banner
    const errorCount   = data.error_count   || 0;
    const warningCount = data.warning_count || 0;
    const infoCount    = data.info_count    || 0;

    // Global issues (no scene_id)
    const globalIssues = _reviewIssues.filter(i => !i.scene_id);
    if (globalIssues.length) {
      const list = document.getElementById('sbList');
      const globalDiv = document.createElement('div');
      globalDiv.id = 'reviewGlobalIssues';
      globalDiv.style.cssText = 'margin:0 0 10px;display:flex;flex-direction:column;gap:4px;';
      for (const issue of globalIssues) {
        const row = document.createElement('div');
        row.className = 'sb-warn';
        row.style.background = issue.severity === 'error' ? '#1a0505' : issue.severity === 'info' ? '#050a15' : '';
        row.style.borderColor = issue.severity === 'error' ? '#5a1010' : issue.severity === 'info' ? '#1a2a50' : '';
        row.style.color       = issue.severity === 'error' ? '#f87171' : issue.severity === 'info' ? '#60a5fa' : '';
        row.textContent = (issue.severity === 'error' ? '✗ ' : issue.severity === 'warning' ? '⚠ ' : 'ℹ ') + issue.problem;
        globalDiv.appendChild(row);
      }
      list.insertBefore(globalDiv, list.firstChild);
    }

    if (errorCount > 0) {
      banner.className = 'has-errors';
      banner.querySelector('.rv-icon').textContent = '✗';
      msg.textContent  = data.summary + ' — fix errors before running pipeline';
      detail.textContent = 'Errors will cause render failures. Click ✗ badges on scenes for details.';
    } else if (warningCount > 0) {
      banner.className = 'has-warnings';
      banner.querySelector('.rv-icon').textContent = '⚠';
      msg.textContent  = data.summary;
      detail.textContent = 'Warnings won\'t block the pipeline but may affect quality.';
    } else {
      banner.className = 'all-clear';
      banner.querySelector('.rv-icon').textContent = '✓';
      msg.textContent  = 'Storyboard looks good — ' + data.summary;
      detail.textContent = '';
    }
  } catch(e) {
    // Review errors are non-fatal — don't block the UI
    console.warn('Review failed:', e);
  }
}

function sbSelectAll() {
  // Reset to original generated storyboard — not implemented, just re-fetches
}

async function saveStoryboard() {
  if (!_storyboard || !_storyboard.length || !currentTitle) return;
  const btn = document.getElementById('saveStoryboardBtn');
  const status = document.getElementById('saveStoryboardStatus');
  btn.disabled = true;
  status.textContent = 'Saving…';
  const safeName = currentTitle.toLowerCase()
    .replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'');
  try {
    const r = await fetch('/save-storyboard', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({safe_name: safeName, storyboard: {scenes: _storyboard}})
    });
    const d = await r.json();
    if (d.error) { status.textContent = 'Error: ' + d.error; }
    else { status.textContent = `Saved (${d.scenes} scenes)`; setTimeout(()=>status.textContent='', 3000); }
  } catch(e) { status.textContent = 'Failed: ' + e.message; }
  btn.disabled = false;
}

async function submitStoryboardEdit() {
  const instruction = document.getElementById('sbEditInput').value.trim();
  const statusEl = document.getElementById('sbEditStatus');
  const btn = document.getElementById('sbEditBtn');
  if (!instruction) return;
  if (!_storyboard || !_storyboard.length) {
    statusEl.textContent = 'No storyboard loaded.';
    return;
  }
  btn.disabled = true;
  btn.textContent = '…';
  statusEl.textContent = 'Claude is editing storyboard…';
  try {
    const res = await fetch('/storyboard-edit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        instruction,
        storyboard: {scenes: _storyboard},
        entity: currentEntity || '',
        title: currentTitle || '',
        context: document.getElementById('contextArea')?.value.trim() || '',
      })
    });
    const data = await res.json();
    if (data.error) {
      statusEl.textContent = 'Error: ' + data.error;
    } else {
      _storyboard = data.storyboard.scenes || [];
      renderStoryboard(_storyboard);
      document.getElementById('sbEditInput').value = '';
      statusEl.textContent = `Done — ${_storyboard.length} scenes`;
    }
  } catch(e) {
    statusEl.textContent = 'Request failed: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Edit';
  }
}

function copyStoryboard() {
  const data = getStoryboardData();
  const lines = [];
  let currentAct = null;
  for (const s of data) {
    if (s.act !== currentAct) {
      currentAct = s.act;
      lines.push('\\n' + s.act.toUpperCase());
      lines.push('─'.repeat(40));
    }
    const label = s.label ? '  [' + s.label + ']' : '';
    const tpl = s.template || (s.type === 'narration' ? 'NARRATION' : s.type || 'SCENE');
    lines.push(tpl + ': ' + (s.content || '') + label);
  }
  const text = lines.join('\\n').trim();
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('copyStoryboardBtn');
    btn.textContent = 'Copied ✓';
    setTimeout(() => btn.textContent = 'Copy Storyboard', 2000);
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    const btn = document.getElementById('copyStoryboardBtn');
    btn.textContent = 'Copied ✓';
    setTimeout(() => btn.textContent = 'Copy Storyboard', 2000);
  });
}

function getStoryboardData() {
  // Collect current state (after user edits)
  return _storyboard.map(s => ({
    ...s,
    content: (document.querySelector('[data-id="' + s.id + '"] [data-field="content"]') || {}).textContent || s.content,
    label:   (document.querySelector('[data-id="' + s.id + '"] [data-field="label"]')   || {}).textContent || s.label,
  }));
}

// ── History ───────────────────────────────────────────────────────────────────

async function loadHistory() {
  try {
    const res  = await fetch('/history');
    const data = await res.json();
    const sec  = document.getElementById('historySection');
    const list = document.getElementById('historyList');
    if (!data.videos || !data.videos.length) { sec.style.display = 'none'; return; }
    sec.style.display = 'block';
    list.innerHTML = data.videos.map(v => `
      <div class="history-item">
        <span class="history-title">${esc(v.title)}</span>
        <a class="history-btn" href="/edit/${encodeURIComponent(v.safe_name)}" target="_blank" style="border-color:#1660FF;color:#fff;background:#1660FF">⚡ Editor</a>
        <a class="history-btn" href="/studio/${encodeURIComponent(v.safe_name)}" target="_blank" style="border-color:#1660FF;color:#1660FF">Grid</a>
        <a class="history-btn" href="/clips/${encodeURIComponent(v.safe_name)}" target="_blank">📋 Source Clips</a>
        <a class="history-btn" href="/player-images/${encodeURIComponent(v.safe_name)}" target="_blank">🖼️ Player Images</a>
        <button class="history-btn" onclick="copyText('${v.command.replace(/'/g,"\\'")}')">Copy Run Cmd</button>
      </div>`).join('');
  } catch(e) {}
}

function copyText(t) { navigator.clipboard.writeText(t); }
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function toggleNav() {
  const s = document.getElementById('navSidebar');
  const o = document.getElementById('navOverlay');
  const open = s.classList.toggle('open');
  o.classList.toggle('open', open);
}
function closeNav() {
  document.getElementById('navSidebar').classList.remove('open');
  document.getElementById('navOverlay').classList.remove('open');
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeNav(); });
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return HTML


@app.route("/suggest", methods=["POST"])
def suggest():
    data  = request.get_json()
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required."}), 400

    entity    = _extract_entity(title)
    wiki      = _wikipedia_full(entity)
    time.sleep(0.3)
    headlines = _google_news_headlines(f"{entity} football")
    headlines += _google_news_headlines(f"{entity} career documentary", num=3)
    context   = _suggest_context(title, entity, wiki, headlines)

    return jsonify({"entity": entity, "context": context, "wiki": wiki})


@app.route("/facts", methods=["POST"])
def facts():
    data   = request.get_json()
    entity = (data.get("entity") or "").strip()
    wiki   = (data.get("wiki") or "").strip()
    facts_list = _extract_facts(entity, wiki)
    return jsonify({"facts": facts_list})


@app.route("/context-facts", methods=["POST"])
def context_facts():
    data    = request.get_json()
    entity  = (data.get("entity") or "").strip()
    context = (data.get("context") or "").strip()
    facts   = _extract_context_facts(entity, context)
    return jsonify({"facts": facts})


@app.route("/blueprint", methods=["POST"])
def blueprint():
    data           = request.get_json()
    title          = (data.get("title") or "").strip()
    entity         = (data.get("entity") or "").strip()
    context        = (data.get("context") or "").strip()
    wiki           = (data.get("wiki") or "").strip()
    checked_facts  = data.get("checkedFacts") or []
    excluded_facts = data.get("excludedFacts") or []

    if not title or not context:
        return jsonify({"error": "Title and context are required."}), 400

    bp = _generate_blueprint(title, entity, context, wiki, checked_facts, excluded_facts)
    return jsonify({"blueprint": bp})


@app.route("/storyboard", methods=["POST"])
def storyboard():
    data             = request.get_json()
    title            = (data.get("title") or "").strip()
    entity           = (data.get("entity") or "").strip()
    blueprint        = data.get("blueprint") or {}
    checked_facts    = data.get("checkedFacts") or []
    wiki             = (data.get("wiki") or "").strip()
    context          = (data.get("context") or "").strip()
    retention_brief  = data.get("retentionBrief") or None
    format_override  = data.get("format") or None
    anchor_override  = data.get("anchorOverride") or None   # {name, framing, ...} from UI picker
    director_override = (data.get("directorOverride") or "").strip()

    if not title:
        return jsonify({"error": "Title required"}), 400

    # Inject anchor override into retention_brief before storyboard generation
    if anchor_override and isinstance(anchor_override, dict) and anchor_override.get("name"):
        if retention_brief and isinstance(retention_brief, dict):
            retention_brief = {**retention_brief, "anchor_character": anchor_override}
        else:
            retention_brief = {"anchor_character": anchor_override}
        print(f"  [Storyboard] Anchor override applied: {anchor_override.get('name')}")

    sb = _generate_storyboard(title, entity, blueprint, checked_facts, wiki, context,
                               retention_brief, format_override=format_override,
                               director_override=director_override)
    return jsonify({"storyboard": sb})


@app.route("/load-storyboard/<safe_name>", methods=["GET"])
def load_storyboard(safe_name):
    """Return a previously saved storyboard JSON so the browser can restore it."""
    import json as _j
    path = BASE_OUTPUT / safe_name / "storyboard.json"
    if not path.exists():
        return jsonify({"error": "No saved storyboard found"}), 404
    return jsonify(_j.loads(path.read_text()))


@app.route("/save-storyboard", methods=["POST"])
def save_storyboard():
    """Persist the current browser storyboard to disk so the pipeline can use it
    without re-running the LLM."""
    import json as _j
    data      = request.get_json()
    scenes    = (data.get("storyboard") or {}).get("scenes") or []
    safe_name = (data.get("safe_name") or "").strip()
    if not safe_name or not scenes:
        return jsonify({"error": "safe_name and storyboard.scenes required"}), 400
    out_dir = BASE_OUTPUT / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "storyboard.json"
    path.write_text(_j.dumps({"scenes": scenes, "totalDuration": sum(s.get("duration",0) for s in scenes)}, indent=2))
    print(f"  [SaveStoryboard] Saved {len(scenes)} scenes to {path}")

    # Write canonical bgColor map for graphics_agent to consume at render time
    bg_map = {
        f"[{s['tag_key']}: {s['tag_text']}]": s.get("canonical_bgColor", "#f0ece4")
        for s in scenes
        if s.get("tag_key") and s.get("tag_text")
    }
    (out_dir / "bg_map.json").write_text(_j.dumps(bg_map, indent=2))

    return jsonify({"saved": str(path), "scenes": len(scenes)})


@app.route("/storyboard-edit", methods=["POST"])
def storyboard_edit():
    """LLM-powered storyboard patch: apply a free-text instruction to the current storyboard
    without regenerating the full pipeline. Re-runs deterministic guardrail passes after editing."""
    import json as _j
    import re as _re
    from utils.format_utils import compute_format_profile as _compute_profile

    data        = request.get_json()
    instruction = (data.get("instruction") or "").strip()
    storyboard  = data.get("storyboard") or {}
    entity      = (data.get("entity") or "").strip()
    title       = (data.get("title") or "").strip()
    context_txt = (data.get("context") or "").strip()
    format_ovr  = data.get("format") or None

    if not instruction or not storyboard:
        return jsonify({"error": "instruction and storyboard required"}), 400

    scenes = storyboard.get("scenes") or []
    if not scenes:
        return jsonify({"error": "storyboard has no scenes"}), 400

    scenes_json = _j.dumps(scenes, indent=2)
    prompt = (
        f'You are editing a documentary storyboard for "{title}" about {entity}.\n\n'
        f"User instruction: {instruction}\n\n"
        f"Current storyboard scenes (JSON array):\n{scenes_json}\n\n"
        f"Rules:\n"
        f"- Return ONLY a JSON object with key \"scenes\" containing the modified array\n"
        f"- Preserve all existing scene fields (id, act, actIndex, type, template, content, label, duration)\n"
        f"- Only add/remove/modify scenes directly relevant to the instruction\n"
        f"- Never change actIndex values or act names\n"
        f"- Never modify any scene where template == \"HERO INTRO\"\n"
        f"- Preserve existing scene ids; new scenes get id = max(existing_id_numbers) + 1, 2, ...\n"
        f"- Return format: {{\"scenes\": [...]}}\n"
    )

    try:
        text = ask_gemini(prompt).strip()
    except Exception as e:
        return jsonify({"error": f"LLM error: {e}"}), 500

    # Extract JSON from response
    match = _re.search(r'\{[\s\S]*\}', text)
    if not match:
        return jsonify({"error": "Claude returned no valid JSON"}), 500
    try:
        result = _j.loads(match.group())
    except _j.JSONDecodeError as e:
        return jsonify({"error": f"JSON parse error: {e}"}), 500

    new_scenes = result.get("scenes") or []
    if not new_scenes:
        return jsonify({"error": "Claude returned empty scenes list"}), 500

    # Re-run deterministic guardrail passes to preserve structural guarantees
    fmt_profile = _compute_profile(context=context_txt, entity=entity, blueprint={},
                                   format_override=format_ovr)
    _assign_scene_metadata(new_scenes)
    _enforce_clip_world_continuity(new_scenes)
    _enforce_template_caps(new_scenes)
    _reconcile_format(new_scenes, profile=fmt_profile)

    total = sum(s.get("duration", 0) for s in new_scenes)
    return jsonify({"storyboard": {"scenes": new_scenes, "totalDuration": total}})


@app.route("/retention-brief", methods=["POST"])
def retention_brief():
    """Generate retention mechanics from a blueprint — second-pass analysis."""
    data      = request.get_json()
    entity    = (data.get("entity")  or "").strip()
    context   = (data.get("context") or "").strip()
    wiki      = (data.get("wiki")    or "").strip()
    blueprint = data.get("blueprint") or {}
    safe_name = data.get("safe_name") or ""

    from agents.retention_agent import generate_retention_brief
    brief = generate_retention_brief(entity, blueprint, context, wiki)

    # Persist to output dir if safe_name provided
    if safe_name:
        import json as _j
        p = BASE_OUTPUT / safe_name / "retention_brief.json"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_j.dumps(brief, indent=2))
        except Exception:
            pass

    return jsonify({"retention_brief": brief})


@app.route("/voice-preview", methods=["POST"])
def voice_preview():
    """Return a short audio sample for the selected ElevenLabs voice."""
    import base64 as _b64
    data     = request.get_json()
    voice_id = (data.get("voiceId") or "").strip()
    text     = data.get("text") or "From the streets of Montevideo to the summit of world football — this is the story."

    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key or not voice_id:
        return jsonify({"error": "API key or voice ID missing"}), 400

    try:
        import requests as _req
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        payload = {"text": text, "model_id": "eleven_multilingual_v2",
                   "voice_settings": {"stability": 0.4, "similarity_boost": 0.75}}
        headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
        resp = _req.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            return jsonify({"error": f"ElevenLabs {resp.status_code}: {resp.text[:200]}"}), 500
        audio_b64 = _b64.b64encode(resp.content).decode()
        return jsonify({"audio_base64": audio_b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/review-storyboard", methods=["POST"])
def review_storyboard_endpoint():
    from agents.script_reviewer_agent import review_storyboard
    data   = request.get_json()
    scenes = data.get("scenes") or []
    entity = (data.get("entity") or "").strip()
    result = review_storyboard(scenes, entity)
    return jsonify(result)


@app.route("/save", methods=["POST"])
def save():
    data           = request.get_json()
    title          = (data.get("title") or "").strip()
    context        = (data.get("context") or "").strip()
    checked_facts  = data.get("checkedFacts") or []
    excluded_facts = data.get("excludedFacts") or []

    if not title:
        return jsonify({"error": "Title is required."}), 400

    safe_name    = topic_to_safe_name(title)
    out_dir      = BASE_OUTPUT / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build context.md — checked facts become mandatory inclusions for the script agent
    lines = [f"# DIRECTOR'S CONTEXT\n\n## Title\n{title}\n\n## Brief\n{context}\n"]

    if checked_facts:
        lines.append("\n## Key Moments to Include (confirmed by director — these MUST appear in the script)\n")
        for f in checked_facts:
            lines.append(f"- {f}\n")

    if excluded_facts:
        lines.append("\n## Moments to Exclude (director has explicitly removed these)\n")
        for f in excluded_facts:
            lines.append(f"- {f}\n")

    (out_dir / "context.md").write_text("".join(lines))

    command = f"python3 -u orchestrator.py '{title}' > /tmp/pipeline.log 2>&1 & tail -f /tmp/pipeline.log"
    return jsonify({"path": str(out_dir / "context.md"), "command": command})


@app.route("/run", methods=["POST"])
def run():
    """Save context.md and launch orchestrator.py as a background subprocess."""
    data           = request.get_json()
    title          = (data.get("title") or "").strip()
    context        = (data.get("context") or "").strip()
    checked_facts  = data.get("checkedFacts") or []
    excluded_facts = data.get("excludedFacts") or []
    skip_voiceover = data.get("skipVoiceover", True)
    voice_id       = data.get("voiceId", "").strip()

    if not title:
        return jsonify({"error": "Title is required."}), 400

    # Kill any existing job
    if _job["proc"] and _job["proc"].poll() is None:
        _job["proc"].terminate()

    safe_name = topic_to_safe_name(title)
    out_dir   = BASE_OUTPUT / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write context.md
    lines = [f"# DIRECTOR'S CONTEXT\n\n## Title\n{title}\n\n## Brief\n{context}\n"]
    if checked_facts:
        lines.append("\n## Key Moments to Include (confirmed by director — MUST appear in the script)\n")
        for f in checked_facts:
            lines.append(f"- {f}\n")
    if excluded_facts:
        lines.append("\n## Moments to Exclude\n")
        for f in excluded_facts:
            lines.append(f"- {f}\n")

    if skip_voiceover:
        lines.append("\nSKIP_VOICEOVER: true\n")
    if voice_id:
        lines.append(f"\nVOICE_ID: {voice_id}\n")

    storyboard_scenes = data.get("storyboard") or []
    if storyboard_scenes:
        lines.append("\n## Scene Sequence — Director's Shot List\n")
        lines.append("The script MUST follow this scene sequence exactly, in this order.\n\n")
        current_act = None
        for i, scene in enumerate(storyboard_scenes, 1):
            act = scene.get("act", "")
            if act != current_act:
                lines.append(f"\n### {act}\n")
                current_act = act
            stype = scene.get("type", "")
            content = scene.get("content", "")
            label = scene.get("label", "")
            template = scene.get("template", "")
            dur = scene.get("duration", 0)
            if stype == "narration":
                lines.append(f"{i}. NARRATION: {content}\n")
            elif stype == "clip":
                lines.append(f"{i}. [CLIP SINGLE: {content}, {dur}s, {label}]\n")
            elif stype == "graphic":
                lines.append(f"{i}. [{template}: {content}]\n")

    (out_dir / "context.md").write_text("".join(lines))

    # Persist storyboard JSON so graphics_agent can read hero_visual flags
    if storyboard_scenes:
        (out_dir / "storyboard.json").write_text(
            json.dumps(storyboard_scenes, indent=2, default=str)
        )

    log_path = f"/tmp/pipeline_{safe_name}.log"
    with open(log_path, "w") as lf:
        lf.write(f"[Engine] Starting pipeline: {title}\n")

    proc = subprocess.Popen(
        ["python3", "-u", "orchestrator.py", title],
        cwd=ENGINE_DIR,
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
    )
    _job["proc"]      = proc
    _job["log_path"]  = log_path
    _job["safe_name"] = safe_name

    return jsonify({"safe_name": safe_name, "log_path": log_path})


@app.route("/poll")
def poll():
    """Return new log lines since the given byte offset, plus done/success status."""
    safe_name = request.args.get("safe_name", "")
    offset    = int(request.args.get("offset", 0))

    log_path = _job.get("log_path")
    proc     = _job.get("proc")

    if not log_path or not os.path.exists(log_path):
        return jsonify({"lines": [], "offset": 0, "done": True, "success": False})

    with open(log_path, "r", errors="replace") as f:
        f.seek(offset)
        new_text = f.read()
        new_offset = f.tell()

    lines = [l for l in new_text.splitlines() if l.strip()]

    done    = proc is None or proc.poll() is not None
    success = done and proc is not None and proc.returncode == 0

    return jsonify({"lines": lines, "offset": new_offset, "done": done, "success": success})


@app.route("/projects")
def list_projects():
    """List all existing projects on disk for the Step 1 'Resume' picker.
    Returns title, safe_name, last-modified, and which artifacts exist."""
    import datetime as _dt
    projects = []
    if BASE_OUTPUT.exists():
        for d in sorted(BASE_OUTPUT.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            ctx = d / "context.md"
            if not ctx.exists():
                continue
            title = d.name.replace("_", " ").title()
            try:
                for line in ctx.read_text().splitlines():
                    if line.startswith("## Title"):
                        title = line.replace("## Title", "").strip() or title
                        break
            except Exception:
                pass
            sb_path  = d / "storyboard.json"
            rb_path  = d / "retention_brief.json"
            mp3_path = d / "narration.mp3"
            mtime = _dt.datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            sb_count = 0
            if sb_path.exists():
                try:
                    sb = json.loads(sb_path.read_text())
                    sb_count = len(sb) if isinstance(sb, list) else len(sb.get("scenes", []))
                except Exception:
                    pass
            projects.append({
                "title":            title,
                "safe_name":        d.name,
                "last_modified":    mtime,
                "has_storyboard":   sb_path.exists(),
                "scene_count":      sb_count,
                "has_retention":    rb_path.exists(),
                "has_narration":    mp3_path.exists(),
            })
    return jsonify({"projects": projects})


@app.route("/resume/<safe_name>")
def resume_project(safe_name):
    """Load everything needed to resume editing on Step 4:
       title, entity, brief, checked facts, retention_brief, storyboard scenes.
    The wizard skips Steps 1-3 and lands on Step 4 with all state pre-loaded."""
    out_dir = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return jsonify({"error": "Project not found"}), 404

    title       = safe_name.replace("_", " ").title()
    brief_text  = ""
    checked_facts: list = []
    skip_voice  = True
    voice_id    = ""

    ctx_path = out_dir / "context.md"
    if ctx_path.exists():
        ctx = ctx_path.read_text()
        # Parse out title + brief + checked facts from the markdown sections
        cur_section = None
        brief_lines: list = []
        for line in ctx.splitlines():
            if line.startswith("## Title"):
                cur_section = "title"; continue
            if line.startswith("## Brief"):
                cur_section = "brief"; continue
            if line.startswith("## Key Moments"):
                cur_section = "facts"; continue
            if line.startswith("## ") or line.startswith("# "):
                cur_section = None; continue
            if cur_section == "title" and line.strip():
                title = line.strip()
            elif cur_section == "brief":
                brief_lines.append(line)
            elif cur_section == "facts" and line.strip().startswith("- "):
                checked_facts.append(line.strip()[2:])
            if line.startswith("SKIP_VOICEOVER:"):
                skip_voice = "true" in line.lower()
            if line.startswith("VOICE_ID:"):
                voice_id = line.split(":", 1)[1].strip()
        brief_text = "\n".join(brief_lines).strip()

    # Retention brief
    retention_brief = None
    rb_path = out_dir / "retention_brief.json"
    if rb_path.exists():
        try:
            retention_brief = json.loads(rb_path.read_text())
        except Exception:
            retention_brief = None

    # Storyboard — accept either a bare list or {"scenes": [...]} shape
    storyboard_scenes: list = []
    sb_path = out_dir / "storyboard.json"
    if sb_path.exists():
        try:
            sb = json.loads(sb_path.read_text())
            storyboard_scenes = sb if isinstance(sb, list) else sb.get("scenes", [])
        except Exception:
            storyboard_scenes = []

    # Entity guess from retention_brief or first proper-noun in title
    entity = ""
    if retention_brief and isinstance(retention_brief, dict):
        cands = retention_brief.get("anchor_candidates") or []
        if cands and isinstance(cands[0], dict):
            entity = cands[0].get("name", "") or ""

    return jsonify({
        "title":           title,
        "safe_name":       safe_name,
        "entity":          entity,
        "context":         brief_text,
        "checkedFacts":    checked_facts,
        "retentionBrief":  retention_brief,
        "storyboard":      storyboard_scenes,
        "skipVoiceover":   skip_voice,
        "voiceId":         voice_id,
    })


@app.route("/history")
def history():
    videos = []
    if BASE_OUTPUT.exists():
        for d in sorted(BASE_OUTPUT.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if d.is_dir():
                ctx = d / "context.md"
                if ctx.exists():
                    lines      = ctx.read_text().splitlines()
                    title_line = next((l.replace("## Title","").strip() for l in lines if l.startswith("## Title")), d.name.replace("_"," ").title())
                else:
                    title_line = d.name.replace("_"," ").title()
                cmd = f"python3 -u orchestrator.py '{title_line}' > /tmp/pipeline.log 2>&1 & tail -f /tmp/pipeline.log"
                videos.append({"title": title_line, "safe_name": d.name, "command": cmd})
    return jsonify({"videos": videos})


@app.route("/clips/<safe_name>")
def clips_view(safe_name):
    """Human-friendly clip sourcing page with YouTube search links."""
    out_dir = BASE_OUTPUT / safe_name
    clips_file = out_dir / "clips_needed.json"
    if not clips_file.exists():
        return f"No clips_needed.json found for {safe_name}", 404

    clips = json.loads(clips_file.read_text())
    title = safe_name.replace("_", " ").title()
    ctx = out_dir / "context.md"
    if ctx.exists():
        for line in ctx.read_text().splitlines():
            if line.startswith("## Title"):
                title = line.replace("## Title", "").strip()
                break

    rows = ""
    for c in clips:
        if c["type"] == "single":
            yt = c.get("youtube_search", "https://www.youtube.com/results?search_query=" + c["description"].replace(" ", "+"))
            rows += f"""
            <tr>
              <td class="cid">{c['id']}</td>
              <td class="act">{c['act']}</td>
              <td>{c['description']}</td>
              <td class="dur">{c['duration']}s</td>
              <td class="lbl">{c['label']}</td>
              <td class="file">{c['files'][0]}</td>
              <td><a href="{yt}" target="_blank" class="yt-btn">Search YouTube ↗</a></td>
            </tr>"""
        else:
            yt_l = c.get("youtube_search_left", "")
            yt_r = c.get("youtube_search_right", "")
            desc_l = c['description'].split('| RIGHT:')[0].replace('LEFT: ', '').strip()
            desc_r = c['description'].split('| RIGHT:')[1].strip() if '| RIGHT:' in c['description'] else ''
            rows += f"""
            <tr class="compare-row">
              <td class="cid">{c['id']}</td>
              <td class="act">{c['act']}</td>
              <td><b>LEFT:</b> {desc_l}<br><b>RIGHT:</b> {desc_r}</td>
              <td class="dur">{c['duration']}s</td>
              <td class="lbl">{c['label']}</td>
              <td class="file">{c['files'][0]}<br>{c['files'][1]}</td>
              <td>
                <a href="{yt_l}" target="_blank" class="yt-btn">Left ↗</a><br>
                <a href="{yt_r}" target="_blank" class="yt-btn" style="margin-top:4px">Right ↗</a>
              </td>
            </tr>"""

    total_dur = sum(c["duration"] for c in clips)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Clips — {title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0a0a0a; color:#e0e0e0; margin:0; padding:24px; }}
  h1 {{ font-size:1.3rem; font-weight:600; margin-bottom:4px; }}
  .meta {{ color:#666; font-size:0.8rem; margin-bottom:24px; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.82rem; }}
  th {{ text-align:left; padding:8px 12px; background:#111; color:#888; font-weight:500; border-bottom:1px solid #222; }}
  td {{ padding:10px 12px; border-bottom:1px solid #1a1a1a; vertical-align:top; line-height:1.5; }}
  tr:hover td {{ background:#0f0f0f; }}
  .compare-row td {{ background:#0a0a12; }}
  .cid {{ color:#C9A84C; font-family:monospace; white-space:nowrap; }}
  .act {{ color:#666; font-size:0.75rem; white-space:nowrap; }}
  .dur {{ color:#888; white-space:nowrap; }}
  .lbl {{ color:#aaa; font-size:0.78rem; }}
  .file {{ color:#555; font-family:monospace; font-size:0.75rem; }}
  .yt-btn {{ display:inline-block; padding:4px 10px; background:#ff0000; color:#fff; text-decoration:none;
             border-radius:4px; font-size:0.75rem; font-weight:600; white-space:nowrap; }}
  .yt-btn:hover {{ background:#cc0000; }}
  .summary {{ margin-top:24px; padding:16px; background:#111; border-radius:8px; color:#888; font-size:0.82rem; }}
</style>
</head>
<body>
<h1>📋 Clips to Source — {title}</h1>
<p class="meta">{len(clips)} clips · ~{total_dur}s total footage ({total_dur//60}m {total_dur%60}s)</p>
<table>
  <thead><tr>
    <th>ID</th><th>Act</th><th>Description</th><th>Dur</th><th>Label</th><th>Drop to</th><th>Find</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
<div class="summary">
  Drop footage files into <code>{out_dir}/clips/</code> using the filenames in the "Drop to" column.
</div>
</body></html>"""
    return html


@app.route("/fetch-player-image", methods=["POST"])
def fetch_player_image_endpoint():
    """Fetch a single player image on demand."""
    from agents.player_image_agent import fetch_player_image
    data   = request.get_json()
    name   = (data.get("name") or "").strip()
    force  = bool(data.get("force", False))
    if not name:
        return jsonify({"error": "name required"}), 400
    result = fetch_player_image(name, force=force)
    return jsonify(result)


@app.route("/player-images/<safe_name>")
def player_images_view(safe_name):
    """Player image sourcing page — shows what images are needed and which are missing."""
    import re as _re
    out_dir = BASE_OUTPUT / safe_name
    script_file = out_dir / "script_draft.md"
    if not script_file.exists():
        return f"No script_draft.md found for {safe_name}", 404

    script = script_file.read_text()
    title = safe_name.replace("_", " ").title()
    ctx = out_dir / "context.md"
    if ctx.exists():
        for line in ctx.read_text().splitlines():
            if line.startswith("## Title"):
                title = line.replace("## Title", "").strip()
                break

    # Extract player names from image-using tags
    IMG_TAG_RE = _re.compile(
        r'\[(?:PLAYER TRIO|PLAYER RADAR|HERO QUOTE|HERO BIG STAT|HERO CHAPTER|SEASON COMPARISON):\s*([^\]]+)\]',
        _re.IGNORECASE
    )
    names_found = set()
    for m in IMG_TAG_RE.finditer(script):
        content = m.group(1)
        for part in _re.split(r'\s+vs\s+|\s*\|\s*', content):
            name = part.split(',')[0].strip()
            if name and len(name) > 2:
                names_found.add(name)

    REMOTION_PUBLIC = REMOTION_DIR / "public"
    available = {}
    if REMOTION_PUBLIC.exists():
        for f in REMOTION_PUBLIC.iterdir():
            if f.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
                available[f.stem.lower()] = f.name

    rows = ""
    for name in sorted(names_found):
        slug = name.lower().replace(" ", "_").replace(".", "").replace("'", "").replace("-", "_")
        filename = slug + ".png"
        found = slug in available or any(slug in k for k in available)
        actual_file = available.get(slug, "")
        status_html = (
            f'<span style="color:#22c55e">✓ {actual_file}</span>' if found
            else f'<span style="color:#ef4444">✗ missing</span>'
        )
        google_url = f"https://www.google.com/search?q={name.replace(' ', '+')}+football+png+transparent&tbm=isch"
        fetch_btn = (
            f'<button class="fetch-btn" onclick="autoFetch(\'{name.replace(chr(39), "")}\', this)">⬇ Auto-fetch</button> '
            if not found else ''
        )
        rows += f"""
        <tr id="row-{slug}">
          <td class="pname">{name}</td>
          <td class="pslug"><code>{filename}</code></td>
          <td class="status-cell" id="status-{slug}">{status_html}</td>
          <td>{fetch_btn}{'—' if found else f'<a href="{google_url}" target="_blank" class="yt-btn">Search ↗</a>'}</td>
        </tr>"""

    drop_path = str(REMOTION_DIR / "public") + "/"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Player Images — {title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0a0a0a; color:#e0e0e0; margin:0; padding:24px; }}
  h1 {{ font-size:1.3rem; font-weight:600; margin-bottom:4px; }}
  .meta {{ color:#666; font-size:0.8rem; margin-bottom:24px; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.82rem; max-width:860px; }}
  th {{ text-align:left; padding:8px 12px; background:#111; color:#888; font-weight:500; border-bottom:1px solid #222; }}
  td {{ padding:10px 12px; border-bottom:1px solid #1a1a1a; vertical-align:middle; }}
  tr:hover td {{ background:#0f0f0f; }}
  .pname {{ font-weight:600; color:#e0e0e0; }}
  .pslug {{ color:#C9A84C; font-size:0.78rem; }}
  code {{ background:#111; padding:2px 6px; border-radius:3px; font-size:0.78rem; }}
  .yt-btn {{ display:inline-block; padding:4px 10px; background:#4285f4; color:#fff; text-decoration:none;
             border-radius:4px; font-size:0.75rem; font-weight:600; white-space:nowrap; }}
  .yt-btn:hover {{ background:#3367d6; }}
  .fetch-btn {{ padding:4px 10px; background:#C9A84C; color:#000; border:none; cursor:pointer;
               border-radius:4px; font-size:0.75rem; font-weight:700; white-space:nowrap; }}
  .fetch-btn:hover {{ background:#b8933c; }}
  .fetch-btn:disabled {{ background:#333; color:#666; cursor:not-allowed; }}
  .fetch-all-btn {{ padding:7px 16px; background:#C9A84C; color:#000; border:none; cursor:pointer;
                   border-radius:5px; font-size:0.8rem; font-weight:700; margin-bottom:16px; }}
  .fetch-all-btn:hover {{ background:#b8933c; }}
  .instructions {{ margin-top:24px; padding:16px; background:#111; border-radius:8px; color:#888; font-size:0.82rem; line-height:1.8; max-width:860px; }}
  code.path {{ color:#C9A84C; }}
</style>
<script>
async function autoFetch(playerName, btn) {{
  btn.disabled = true;
  btn.textContent = '⏳ Fetching…';
  try {{
    const res  = await fetch('/fetch-player-image', {{
      method: 'POST', headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{ name: playerName }})
    }});
    const data = await res.json();
    if (data.status === 'fetched') {{
      const slug = data.slug;
      const statusCell = document.getElementById('status-' + slug);
      if (statusCell) statusCell.innerHTML = '<span style="color:#22c55e">✓ ' + slug + '.png (auto-fetched)</span>';
      btn.textContent = '✓ Done';
      btn.style.background = '#166534';
      btn.style.color = '#fff';
    }} else {{
      btn.textContent = '✗ Failed — try manually';
      btn.style.background = '#7f1d1d';
      btn.style.color = '#fff';
      btn.disabled = false;
    }}
  }} catch(e) {{
    btn.textContent = '✗ Error';
    btn.disabled = false;
  }}
}}

async function autoFetchAll() {{
  const btns = document.querySelectorAll('.fetch-btn');
  for (const btn of btns) {{
    if (!btn.disabled) {{
      const name = btn.getAttribute('onclick').match(/autoFetch\('([^']+)'/)?.[1];
      if (name) {{ btn.click(); await new Promise(r => setTimeout(r, 1500)); }}
    }}
  }}
}}
</script>
</head>
<body>
<h1>🖼️ Player Images — {title}</h1>
<p class="meta">{len(names_found)} player images referenced in script · {sum(1 for n in names_found if n.lower().replace(' ','_') in available)} already available</p>
<button class="fetch-all-btn" onclick="autoFetchAll()">⬇ Auto-fetch All Missing</button>
<table>
  <thead><tr><th>Player</th><th>Filename</th><th>Status</th><th>Actions</th></tr></thead>
  <tbody>{rows if rows else '<tr><td colspan="4" style="color:#555;padding:20px">No image tags found in script</td></tr>'}</tbody>
</table>
<div class="instructions">
  <strong>Sources tried automatically:</strong> Futwiz (transparent PNG renders) → Wikipedia Commons<br>
  <strong>Manual override:</strong> Save a PNG as the filename above and drop it into: <code class="path">{drop_path}</code><br>
  The engine re-scans on every render — no restart needed.
</div>
</body></html>"""
    return html


# ══════════════════════════════════════════════════════════════════════════════
# RENDER STUDIO
# ══════════════════════════════════════════════════════════════════════════════

REMOTION_PUBLIC = REMOTION_DIR / "public"
STUDIO_STATE_FILE = "studio_state.json"


def _load_studio_state(out_dir: Path) -> dict:
    p = out_dir / STUDIO_STATE_FILE
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_studio_state(out_dir: Path, state: dict):
    (out_dir / STUDIO_STATE_FILE).write_text(json.dumps(state, indent=2))


def _load_manifest(out_dir: Path) -> list:
    p = out_dir / "renders" / "manifest.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return []


def _get_available_player_images() -> list:
    """Return list of {slug, file} for all player images."""
    img_exts = {".jpg", ".jpeg", ".png", ".webp"}
    images = []
    seen = set()
    for scan_dir in [REMOTION_PUBLIC / "players", REMOTION_PUBLIC]:
        if not scan_dir.exists():
            continue
        for f in sorted(scan_dir.iterdir()):
            if f.suffix.lower() in img_exts and not f.name.startswith(".") and not f.name.endswith(":Zone.Identifier"):
                if f.stem not in seen:
                    seen.add(f.stem)
                    rel = str(f.relative_to(REMOTION_PUBLIC))
                    images.append({"slug": f.stem, "file": rel, "name": f.stem.replace("_", " ").title()})
    return images


@app.route("/video/<safe_name>/<filename>")
def serve_render_video(safe_name, filename):
    """Stream an MP4 render file for in-browser preview."""
    from flask import send_from_directory
    renders_dir = BASE_OUTPUT / safe_name / "renders"
    return send_from_directory(str(renders_dir), filename)


@app.route("/preview/<safe_name>/<filename>")
def serve_render_preview(safe_name, filename):
    """Serve a PNG single-frame preview from renders/previews/ (Option A)."""
    from flask import send_from_directory
    previews_dir = BASE_OUTPUT / safe_name / "renders" / "previews"
    return send_from_directory(str(previews_dir), filename)


@app.route("/player-thumb/<path:rel_path>")
def serve_player_thumb(rel_path):
    """Serve a player image from remotiontest/public/ for Studio previews."""
    from flask import send_from_directory
    return send_from_directory(str(REMOTION_PUBLIC), rel_path)


@app.route("/remotion-public/<path:rel_path>")
def serve_remotion_public(rel_path):
    """Serve any file from remotiontest/public/ (clips, images, etc.)."""
    from flask import send_from_directory
    return send_from_directory(str(REMOTION_PUBLIC), rel_path)


@app.route("/narration-audio/<safe_name>")
def narration_audio_file(safe_name):
    """Serve narration.mp3 for in-browser sequence preview."""
    from flask import send_from_directory
    out_dir = BASE_OUTPUT / safe_name
    if not (out_dir / "narration.mp3").exists():
        return "narration.mp3 not found", 404
    return send_from_directory(str(out_dir), "narration.mp3")


@app.route("/studio-data/<safe_name>")
def studio_data(safe_name):
    """Return all Studio data: renders + manifest + clips + studio state + script context."""
    out_dir = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return jsonify({"error": "Project not found"}), 404

    # Title
    title = safe_name.replace("_", " ").title()
    ctx = out_dir / "context.md"
    if ctx.exists():
        for line in ctx.read_text().splitlines():
            if line.startswith("## Title"):
                title = line.replace("## Title", "").strip()
                break

    # Script context — extract narration surrounding each tag
    script_context = _extract_script_contexts(out_dir)

    # Renders — manifest is the source of truth (Option A: PNG previews exist
    # before mp4). Iterate manifest entries first; surface preview_filename so
    # the UI can show the PNG still until the mp4 batch render runs.
    renders_dir  = out_dir / "renders"
    previews_dir = renders_dir / "previews"
    manifest_list = _load_manifest(out_dir)
    manifest      = {e["filename"]: e for e in manifest_list}
    state         = _load_studio_state(out_dir)

    renders = []
    seen = set()
    for entry in manifest_list:
        fn = entry.get("filename")
        if not fn or fn in seen:
            continue
        seen.add(fn)
        gtype    = entry.get("type", _guess_type(fn))
        composition = entry.get("composition") or _TYPE_TO_COMPOSITION.get(gtype, "")
        tag_txt  = entry.get("tag_text", "")
        full_tag = entry.get("tag", "")
        preview_fn = entry.get("preview_filename") or ""
        mp4_path = renders_dir / fn
        png_path = previews_dir / preview_fn if preview_fn else None
        mp4_exists = mp4_path.exists()
        png_exists = bool(png_path and png_path.exists())
        renders.append({
            "filename":         fn,
            "preview_filename": preview_fn,
            "rendered":         mp4_exists,
            "preview_rendered": png_exists,
            "type":             gtype,
            "composition":      composition,
            "tag":              full_tag,
            "tag_text":         tag_txt,
            "props":            entry.get("props", {}),
            "scene_id":         entry.get("scene_id"),
            "scene_index":      entry.get("scene_index"),
            "act":              entry.get("act"),
            "approved":         state.get(fn, {}).get("approved", True),
            "note":             state.get(fn, {}).get("note", ""),
            "size_kb":          round(mp4_path.stat().st_size / 1024) if mp4_exists else 0,
            "context":          script_context.get(full_tag, script_context.get(tag_txt, "")),
        })

    # Legacy fallback: surface any mp4 in renders/ that has no manifest entry
    if renders_dir.exists():
        for f in sorted(renders_dir.iterdir()):
            if f.suffix == ".mp4" and f.name not in seen:
                gtype   = _guess_type(f.name)
                renders.append({
                    "filename":         f.name,
                    "preview_filename": "",
                    "rendered":         True,
                    "preview_rendered": False,
                    "type":             gtype,
                    "composition":      _TYPE_TO_COMPOSITION.get(gtype, ""),
                    "tag":              "",
                    "tag_text":         "",
                    "props":            {},
                    "approved":         state.get(f.name, {}).get("approved", True),
                    "note":             state.get(f.name, {}).get("note", ""),
                    "size_kb":          round(f.stat().st_size / 1024),
                    "context":          "",
                })

    # Missing images warning
    missing_images = []
    _mi_path = out_dir / "missing_images.md"
    if _mi_path.exists():
        import re as _re2
        for _line in _mi_path.read_text().splitlines():
            _m = _re2.match(r'\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|', _line)
            if _m:
                missing_images.append({"file": _m.group(1), "tag": _m.group(2), "prop": _m.group(3)})

    # Clips
    clips = []
    clips_file = out_dir / "clips_needed.json"
    if clips_file.exists():
        try:
            clips = json.loads(clips_file.read_text())
        except Exception:
            pass

    return jsonify({"title": title, "safe_name": safe_name, "renders": renders, "clips": clips, "missing_images": missing_images})


def _guess_type(filename: str) -> str:
    """Infer graphic type from filename for renders without a manifest entry."""
    fn = filename.lower()
    if fn.startswith("player_trio"):    return "player_trio"
    if fn.startswith("radar"):          return "player_radar"
    if fn.startswith("timeline"):       return "career_timeline"
    if fn.startswith("disciplinary"):   return "disciplinary_record"
    if fn.startswith("hero_intro"):  return "hero_intro"
    if fn.startswith("hero_bars"):   return "hero_bars"
    if fn.startswith("hero_bigstat"):return "hero_bigstat"
    if fn.startswith("hero_form"):   return "hero_form"
    if fn.startswith("hero_graph"):  return "hero_graph"
    if fn.startswith("hero_tactical"):return "hero_tactical"
    if fn.startswith("pl_table"):       return "standings_table"
    if fn.startswith("top_scorers"):    return "top_scorers"
    if fn.startswith("lineup"):         return "team_lineup"
    return "graphic"


# ── Composition registry ───────────────────────────────────────────────────────
# Maps internal type key → Remotion composition ID + compatible alternatives + editable field schema

_TYPE_TO_COMPOSITION = {
    "player_trio":        "PlayerTrio",
    "player_radar":       "AttackingRadar",
    "career_timeline":    "CareerTimeline",
    "disciplinary_record":"DisciplinaryRecord",
    "hero_intro":      "HeroIntro",
    "hero_bigstat":    "HeroBigStat",
    "hero_bars":       "HeroStatBars",
    "hero_form":       "HeroFormRun",
    "hero_graph":      "HeroLeagueGraph",
    "hero_tactical":   "HeroTactical",
    "standings_table":    "PremierLeagueTable",
    "top_scorers":        "TopScorersTable",
    "team_lineup":        "TeamLineup",
    "match_result":       "MatchResult",
    "player_stats":       "PlayerStats",
    "season_comparison":  "SeasonComparison",
    "quote_card":         "QuoteCard",
    "trophy":             "TrophyGraphic",
}

# Compatible alternative compositions per type
_COMPATIBLE_COMPOSITIONS = {
    "player_trio":        ["PlayerTrio", "SeasonComparison"],
    "player_radar":       ["AttackingRadar"],
    "hero_bigstat":    ["HeroBigStat", "HeroStatBars"],
    "hero_bars":       ["HeroStatBars", "HeroBigStat"],
    "career_timeline":    ["CareerTimeline"],
    "disciplinary_record":["DisciplinaryRecord"],
    "hero_intro":      ["HeroIntro"],
    "hero_form":       ["HeroFormRun"],
    "hero_graph":      ["HeroLeagueGraph"],
    "hero_tactical":   ["HeroTactical"],
    "team_lineup":        ["TeamLineup"],
    "quote_card":         ["QuoteCard", "HeroQuote"],
    "graphic":            [],
}

# Schema for the props editor — fields shown per composition
_COMPOSITION_SCHEMAS = {
    "PlayerTrio": [
        {"key": "title",    "type": "text",  "label": "Title"},
        {"key": "subtitle", "type": "text",  "label": "Subtitle"},
        {"key": "bgColor",  "type": "color", "label": "Background"},
        {"key": "players",  "type": "player_list", "label": "Players",
         "subfields": [
             {"key": "name",      "type": "text",  "label": "Name"},
             {"key": "image",     "type": "image", "label": "Image"},
             {"key": "club",      "type": "text",  "label": "Club"},
             {"key": "clubColor", "type": "color", "label": "Club colour"},
             {"key": "stat",      "type": "text",  "label": "Stat"},
             {"key": "statLabel", "type": "text",  "label": "Stat label"},
         ]},
    ],
    "HeroBigStat": [
        {"key": "stat",        "type": "text",    "label": "Stat"},
        {"key": "unit",        "type": "text",    "label": "Unit"},
        {"key": "label",       "type": "text",    "label": "Label"},
        {"key": "context",     "type": "text",    "label": "Context (Subject · Club · Season)"},
        {"key": "badgeSlug",   "type": "text",    "label": "Club badge slug (e.g. liverpool.svg)"},
        {"key": "source",      "type": "text",    "label": "Source attribution"},
        {"key": "playerImage", "type": "image",   "label": "Player image"},
        {"key": "accentColor", "type": "color",   "label": "Accent colour"},
        {"key": "darkMode",    "type": "boolean", "label": "Dark mode"},
        {"key": "bgColor",     "type": "color",   "label": "Background"},
    ],
    "AttackingRadar": [
        {"key": "entityName",  "type": "text",  "label": "Player name"},
        {"key": "entityImage", "type": "image", "label": "Player image"},
        {"key": "club",        "type": "text",  "label": "Club"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "CareerTimeline": [
        {"key": "playerName",   "type": "text",    "label": "Player name"},
        {"key": "subjectImage", "type": "image",   "label": "Subject portrait"},
        {"key": "dateline",     "type": "text",    "label": "Folio dateline (e.g. CAREER · 2005–2022)"},
        {"key": "source",       "type": "text",    "label": "Source attribution"},
        {"key": "accentColor",  "type": "color",   "label": "Accent colour"},
        {"key": "bgColor",      "type": "color",   "label": "Background"},
        {"key": "darkMode",     "type": "boolean", "label": "Dark mode"},
    ],
    "HeroIntro": [
        {"key": "subtitle", "type": "text",  "label": "Subtitle"},
        {"key": "bgColor",  "type": "color", "label": "Background"},
    ],
    "HeroOutro": [
        {"key": "leadIn",          "type": "text",  "label": "Lead-in copy (italic serif)"},
        {"key": "subscribeAsk",    "type": "text",  "label": "Subscribe ask (uppercase)"},
        {"key": "videoLeftTitle",  "type": "text",  "label": "Left video title"},
        {"key": "videoRightTitle", "type": "text",  "label": "Right video title"},
        {"key": "videoLeftSrc",    "type": "text",  "label": "Left video src (mp4 path)"},
        {"key": "videoRightSrc",   "type": "text",  "label": "Right video src (mp4 path)"},
        {"key": "videoLeftImage",  "type": "image", "label": "Left poster image"},
        {"key": "videoRightImage", "type": "image", "label": "Right poster image"},
        {"key": "bgColor",         "type": "color", "label": "Background"},
        {"key": "accentColor",     "type": "color", "label": "Accent colour"},
    ],
    "HeroFormRun": [
        {"key": "title",      "type": "text",  "label": "Title"},
        {"key": "subtitle",   "type": "text",  "label": "Subtitle"},
        {"key": "accentColor","type": "color", "label": "Accent colour"},
        {"key": "bgColor",    "type": "color", "label": "Background"},
    ],
    "HeroTactical": [
        {"key": "title",           "type": "text",  "label": "Title"},
        {"key": "description",     "type": "text",  "label": "Description / caption"},
        {"key": "teamColor",       "type": "color", "label": "Our team colour"},
        {"key": "oppositionColor", "type": "color", "label": "Opposition colour"},
        {"key": "accentColor",     "type": "color", "label": "Accent colour"},
        {"key": "bgColor",         "type": "color", "label": "Background"},
    ],
    "HeroStatBars": [
        {"key": "title",      "type": "text",  "label": "Title"},
        {"key": "subtitle",   "type": "text",  "label": "Subtitle"},
        {"key": "accentColor","type": "color", "label": "Accent colour"},
        {"key": "bgColor",    "type": "color", "label": "Background"},
    ],
    "TeamLineup": [
        {"key": "teamName",   "type": "text",  "label": "Team name"},
        {"key": "formation",  "type": "text",  "label": "Formation"},
        {"key": "opposition", "type": "text",  "label": "Opposition"},
        {"key": "date",       "type": "text",  "label": "Date"},
        {"key": "teamColor",  "type": "color", "label": "Team colour"},
        {"key": "bgColor",    "type": "color", "label": "Background"},
    ],
    "MatchResult": [
        {"key": "homeTeam",      "type": "text",  "label": "Home team"},
        {"key": "awayTeam",      "type": "text",  "label": "Away team"},
        {"key": "homeScore",     "type": "text",  "label": "Home score"},
        {"key": "awayScore",     "type": "text",  "label": "Away score"},
        {"key": "competition",   "type": "text",  "label": "Competition"},
        {"key": "date",          "type": "text",  "label": "Date"},
        {"key": "homeColor",     "type": "color", "label": "Home colour"},
        {"key": "awayColor",     "type": "color", "label": "Away colour"},
        {"key": "bgColor",       "type": "color", "label": "Background"},
    ],
    "HeroQuote": [
        {"key": "quote",       "type": "text",  "label": "Quote"},
        {"key": "attribution", "type": "text",  "label": "Attribution"},
        {"key": "playerImage", "type": "image", "label": "Player image"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "ArticleHeadline": [
        {"key": "headline",        "type": "text",  "label": "Headline"},
        {"key": "publication",     "type": "text",  "label": "Publication / masthead"},
        {"key": "category",        "type": "text",  "label": "Category"},
        {"key": "author",          "type": "text",  "label": "Author"},
        {"key": "byline",          "type": "text",  "label": "Byline"},
        {"key": "date",            "type": "text",  "label": "Date"},
        {"key": "edition",         "type": "text",  "label": "Edition / page reference"},
        {"key": "lede",            "type": "text",  "label": "Lede paragraph"},
        {"key": "imageSrc",        "type": "image", "label": "Cropped image (right column)"},
        {"key": "imageCaption",    "type": "text",  "label": "Image caption"},
        {"key": "highlightColor",  "type": "color", "label": "Highlight colour (legacy)"},
        {"key": "accentColor",     "type": "color", "label": "Accent colour"},
        {"key": "bgColor",         "type": "color", "label": "Background"},
    ],
    "HeroTransferRecord": [
        {"key": "title",       "type": "text",  "label": "Title"},
        {"key": "subtitle",    "type": "text",  "label": "Subtitle"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "SeasonComparison": [
        {"key": "season",      "type": "text",  "label": "Season"},
        {"key": "competition", "type": "text",  "label": "Competition"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "PlayerStats": [
        {"key": "playerName",      "type": "text",  "label": "Player name"},
        {"key": "club",            "type": "text",  "label": "Club"},
        {"key": "season",          "type": "text",  "label": "Season"},
        {"key": "competition",     "type": "text",  "label": "Competition"},
        {"key": "clubColor",       "type": "color", "label": "Club colour"},
        {"key": "playerImageSlug", "type": "image", "label": "Player image"},
        {"key": "bgColor",         "type": "color", "label": "Background"},
    ],
    "TopScorersTable": [
        {"key": "season",      "type": "text",  "label": "Season"},
        {"key": "competition", "type": "text",  "label": "Competition"},
        {"key": "statLabel",   "type": "text",  "label": "Stat label"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "PremierLeagueTable": [
        {"key": "season",  "type": "text",  "label": "Season"},
        {"key": "bgColor", "type": "color", "label": "Background"},
    ],
    "HeroLeagueGraph": [
        {"key": "title",       "type": "text",  "label": "Title"},
        {"key": "season",      "type": "text",  "label": "Season"},
        {"key": "competition", "type": "text",  "label": "Competition (folio dateline)"},
        {"key": "source",      "type": "text",  "label": "Source attribution"},
        {"key": "accentColor", "type": "color", "label": "Accent colour (subject team)"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "HeroChapterWord": [
        {"key": "word",         "type": "text",  "label": "Word"},
        {"key": "chapterLabel", "type": "text",  "label": "Chapter label (small caps top-left)"},
        {"key": "player1Image", "type": "image", "label": "Image 1"},
        {"key": "player2Image", "type": "image", "label": "Image 2"},
        {"key": "blob1Color",   "type": "color", "label": "Colour 1"},
        {"key": "blob2Color",   "type": "color", "label": "Colour 2"},
        {"key": "bgColor",      "type": "color", "label": "Background"},
    ],
    "CountdownReveal": [
        {"key": "title",       "type": "text",    "label": "Title"},
        {"key": "subtitle",    "type": "text",    "label": "Subtitle"},
        {"key": "accentColor", "type": "color",   "label": "Accent colour"},
        {"key": "teamColor",   "type": "color",   "label": "Team colour (overrides accent)"},
        {"key": "bgColor",     "type": "color",   "label": "Background"},
        {"key": "darkMode",    "type": "boolean", "label": "Dark mode"},
        {"key": "dwellFrames", "type": "text",    "label": "Dwell frames per item"},
    ],
    "ScoutReport": [
        {"key": "playerName",      "type": "text",  "label": "Player name"},
        {"key": "playerImageSlug", "type": "image", "label": "Player portrait"},
        {"key": "origin",          "type": "text",  "label": "Origin club"},
        {"key": "league",          "type": "text",  "label": "Origin league"},
        {"key": "competition",     "type": "text",  "label": "Competition (alias of league)"},
        {"key": "playerAge",       "type": "text",  "label": "Age"},
        {"key": "signingFee",      "type": "text",  "label": "Signing fee"},
        {"key": "signingYear",     "type": "text",  "label": "Signing year"},
        {"key": "headline",        "type": "text",  "label": "Headline summary (one line under meta row)"},
        {"key": "dateline",        "type": "text",  "label": "Folio dateline override"},
        {"key": "source",          "type": "text",  "label": "Source attribution"},
        {"key": "badgeSlug",       "type": "text",  "label": "Club badge slug"},
        {"key": "clubColor",       "type": "color", "label": "Club / team colour"},
        {"key": "accentColor",     "type": "color", "label": "Accent colour"},
        {"key": "bgColor",         "type": "color", "label": "Background (team colour for dark variant)"},
    ],
    "HeroClipSingle": [
        {"key": "clip",    "type": "clip_file", "label": "Clip"},
        {"key": "label",   "type": "text",    "label": "Label"},
        {"key": "title",   "type": "text",    "label": "Title"},
        {"key": "bgColor", "type": "color",   "label": "Background"},
        {"key": "soundOn", "type": "boolean", "label": "Include audio (default: muted)"},
    ],
    "HeroClipCompare": [
        {"key": "clipLeft",   "type": "clip_file", "label": "Clip Left"},
        {"key": "clipRight",  "type": "clip_file", "label": "Clip Right"},
        {"key": "labelLeft",  "type": "text",  "label": "Left label"},
        {"key": "labelRight", "type": "text",  "label": "Right label"},
        {"key": "title",      "type": "text",  "label": "Title"},
        {"key": "bgColor",    "type": "color", "label": "Background"},
    ],
    "HeroScatterPlot": [
        {"key": "axisXLabel", "type": "text",  "label": "X axis label"},
        {"key": "axisYLabel", "type": "text",  "label": "Y axis label"},
        {"key": "bgColor",    "type": "color", "label": "Background"},
    ],
    "TrioFeature": [
        {"key": "bgColor", "type": "color", "label": "Background"},
    ],
    "HeroAwardsList": [
        {"key": "award",        "type": "text",  "label": "Award (e.g. Ballon d'Or)"},
        {"key": "entityName",   "type": "text",  "label": "Subject name"},
        {"key": "subjectImage", "type": "image", "label": "Subject portrait"},
        {"key": "awardImage",   "type": "image", "label": "Trophy image"},
        {"key": "dateline",     "type": "text",  "label": "Folio dateline (e.g. 1956 — present · 8 wins)"},
        {"key": "source",       "type": "text",  "label": "Source attribution"},
        {"key": "accentColor",  "type": "color", "label": "Accent colour (gold default)"},
        {"key": "clubColor",    "type": "color", "label": "Club tint (overrides accent for club docs)"},
        {"key": "bgColor",      "type": "color", "label": "Background"},
    ],
    "AnnotatedImage": [
        {"key": "imageSrc",    "type": "image", "label": "Image"},
        {"key": "caption",     "type": "text",  "label": "Caption"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "HeroComparisonRadar": [
        {"key": "title",        "type": "text",  "label": "Title"},
        {"key": "subtitle",     "type": "text",  "label": "Subtitle"},
        {"key": "playerAName",  "type": "text",  "label": "Player A name"},
        {"key": "playerAImage", "type": "image", "label": "Player A image"},
        {"key": "playerAColor", "type": "color", "label": "Player A colour"},
        {"key": "playerBName",  "type": "text",  "label": "Player B name"},
        {"key": "playerBImage", "type": "image", "label": "Player B image"},
        {"key": "playerBColor", "type": "color", "label": "Player B colour"},
        {"key": "bgColor",      "type": "color", "label": "Background"},
    ],
    "HeroNewsFeed": [
        {"key": "title",       "type": "text",  "label": "Title"},
        {"key": "subtitle",    "type": "text",  "label": "Subtitle"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "HeroPlayerRevealTrio": [
        {"key": "title",       "type": "text",  "label": "Title"},
        {"key": "subtitle",    "type": "text",  "label": "Subtitle"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "HeroSeasonTimeline": [
        {"key": "subjectName",  "type": "text",  "label": "Subject name"},
        {"key": "subjectImage", "type": "image", "label": "Subject portrait"},
        {"key": "headline",     "type": "text",  "label": "Italic headline"},
        {"key": "accentColor",  "type": "color", "label": "Accent colour"},
        {"key": "bgColor",      "type": "color", "label": "Background"},
    ],
    "HeroShotMap": [
        {"key": "playerName",  "type": "text",  "label": "Player name"},
        {"key": "season",      "type": "text",  "label": "Season"},
        {"key": "competition", "type": "text",  "label": "Competition"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "HeroTransferProfit": [
        {"key": "title",       "type": "text",  "label": "Title"},
        {"key": "subtitle",    "type": "text",  "label": "Subtitle"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "MapCallout": [
        {"key": "title",       "type": "text",  "label": "Title"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "PortraitStatHero": [
        {"key": "playerName",  "type": "text",  "label": "Player name"},
        {"key": "playerImage", "type": "image", "label": "Player image"},
        {"key": "stat",        "type": "text",  "label": "Stat"},
        {"key": "statLabel",   "type": "text",  "label": "Stat label"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "PortraitWithBars": [
        {"key": "playerName",  "type": "text",  "label": "Player name"},
        {"key": "playerImage", "type": "image", "label": "Player image"},
        {"key": "title",       "type": "text",  "label": "Title"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "StatPulse": [
        {"key": "stat",        "type": "text",  "label": "Stat"},
        {"key": "label",       "type": "text",  "label": "Label"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "TimelineScroll": [
        {"key": "title",       "type": "text",  "label": "Title"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "TournamentBracket": [
        {"key": "tournament",  "type": "text",  "label": "Tournament name"},
        {"key": "year",        "type": "text",  "label": "Year"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
    "ValueCurve": [
        {"key": "playerName",  "type": "text",  "label": "Player name"},
        {"key": "accentColor", "type": "color", "label": "Accent colour"},
        {"key": "bgColor",     "type": "color", "label": "Background"},
    ],
}


def _extract_script_contexts(out_dir: Path) -> dict:
    """Return a dict mapping tag text → surrounding narration (150 chars before/after)."""
    script_file = out_dir / "script_draft.md"
    if not script_file.exists():
        return {}
    script = script_file.read_text(errors="replace")
    import re as _re
    TAG_RE = _re.compile(r'\[([A-Z][A-Z _]+):\s*([^\]]+)\]')
    contexts = {}
    for m in TAG_RE.finditer(script):
        full_tag  = m.group(0)
        tag_text  = m.group(2).strip()
        start     = max(0, m.start() - 180)
        end       = min(len(script), m.end() + 180)
        snippet   = script[start:end].strip()
        # Strip other tags from context
        snippet   = TAG_RE.sub('', snippet).strip()
        snippet   = ' '.join(snippet.split())[:300]
        contexts[full_tag]  = snippet
        contexts[tag_text]  = snippet
    return contexts


@app.route("/composition-schemas")
def composition_schemas():
    """Return field schemas + compatible alternatives for every composition."""
    return jsonify({
        "schemas":    _COMPOSITION_SCHEMAS,
        "compatible": _COMPATIBLE_COMPOSITIONS,
        "type_map":   _TYPE_TO_COMPOSITION,
    })


# ── Icons and tag-key hints for each composition ──────────────────────────────
_COMP_META = {
    "HeroIntro":          {"icon": "🎬", "label": "Intro",           "tagKey": "HERO INTRO"},
    "HeroBigStat":        {"icon": "📊", "label": "Big Stat",        "tagKey": "HERO BIG STAT"},
    "HeroStatBars":       {"icon": "📈", "label": "Stat Bars",       "tagKey": "HERO STAT BARS"},
    "HeroFormRun":        {"icon": "📋", "label": "Form Run",        "tagKey": "HERO FORM RUN"},
    "HeroTactical":       {"icon": "🔷", "label": "Tactical",        "tagKey": "HERO TACTICAL"},
    "HeroLeagueGraph":    {"icon": "📉", "label": "League Graph",    "tagKey": "HERO LEAGUE GRAPH"},
    "HeroTransferRecord": {"icon": "💰", "label": "Transfer Record", "tagKey": "HERO TRANSFER RECORD"},
    "HeroQuote":          {"icon": "🗣", "label": "Quote",           "tagKey": "HERO QUOTE"},
    "HeroChapterWord":    {"icon": "🔤", "label": "Chapter Word",    "tagKey": "HERO CHAPTER WORD"},
    "HeroClipCompare":    {"icon": "🎞", "label": "Clip Compare",    "tagKey": "CLIP COMPARE"},
    "HeroClipSingle":     {"icon": "🎞", "label": "Clip Single",     "tagKey": "HERO CLIP SINGLE"},
    "HeroScatterPlot":    {"icon": "🔵", "label": "Scatter Plot",    "tagKey": "HERO SCATTER PLOT"},
    "AttackingRadar":        {"icon": "🎯", "label": "Radar",           "tagKey": "PLAYER RADAR"},
    "PlayerTrio":            {"icon": "👥", "label": "Player Trio",     "tagKey": "PLAYER TRIO"},
    "TrioFeature":           {"icon": "👤", "label": "Trio Feature",    "tagKey": "TRIO FEATURE"},
    "CareerTimeline":        {"icon": "📅", "label": "Career Timeline", "tagKey": "CAREER TIMELINE"},
    "TeamLineup":            {"icon": "⬜", "label": "Team Lineup",     "tagKey": "TEAM LINEUP"},
    "MatchResult":           {"icon": "⚽", "label": "Match Result",    "tagKey": "MATCH RESULT"},
    "PlayerStats":           {"icon": "📌", "label": "Player Stats",    "tagKey": "PLAYER STATS"},
    "SeasonComparison":      {"icon": "⚖️", "label": "Season Compare",  "tagKey": "SEASON COMPARISON"},
    "TopScorersTable":       {"icon": "🏆", "label": "Top Scorers",     "tagKey": "TOP SCORERS"},
    "PremierLeagueTable":    {"icon": "📋", "label": "League Table",    "tagKey": "LEAGUE TABLE"},
    "TransferAnnouncement":  {"icon": "🔄", "label": "Transfer",        "tagKey": "TRANSFER ANNOUNCEMENT"},
    "TrophyGraphic":         {"icon": "🏅", "label": "Trophy",          "tagKey": "TROPHY GRAPHIC"},
    "DisciplinaryRecord":    {"icon": "🟥", "label": "Disciplinary",    "tagKey": "DISCIPLINARY RECORD"},
    "QuoteCard":             {"icon": "💬", "label": "Quote Card",      "tagKey": "QUOTE CARD"},
    "ArticleHeadline":       {"icon": "📰", "label": "Headline",        "tagKey": "ARTICLE HEADLINE"},
}
_SKIP_COMPOSITIONS = {"VideoSequence"}

def _parse_root_tsx() -> list:
    """Parse Root.tsx and return all registered composition IDs with metadata.
    Automatically picks up new compositions as they're added to Root.tsx."""
    import re as _re
    root_tsx = REMOTION_DIR / "src" / "Root.tsx"
    if not root_tsx.exists():
        return []
    text = root_tsx.read_text(errors="replace")
    # Extract all <Composition id="XYZ" ...> entries
    ids = _re.findall(r'<Composition\b[^>]*\bid=["\']([^"\']+)["\']', text)
    result = []
    for cid in ids:
        if cid in _SKIP_COMPOSITIONS:
            continue
        meta = _COMP_META.get(cid, {})
        # Auto-generate label for any unknown composition: CamelCase → words
        if not meta.get("label"):
            label = _re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', cid)
            label = _re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', ' ', label)
        else:
            label = meta["label"]
        result.append({
            "id":     cid,
            "label":  label,
            "icon":   meta.get("icon", "▪"),
            "tagKey": meta.get("tagKey", ""),
        })
    return result


@app.route("/compositions")
def get_compositions():
    """Return all Remotion compositions parsed live from Root.tsx."""
    return jsonify({"compositions": _parse_root_tsx()})


@app.route("/upload-image", methods=["POST"])
def upload_image():
    """Upload a player image directly to remotiontest/public/players/."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f    = request.files["file"]
    name = request.form.get("name", "").strip()
    if not name:
        name = Path(f.filename).stem
    # Sanitise slug
    import re as _re
    slug = _re.sub(r"[^\w]", "_", name.lower()).strip("_")
    ext  = Path(f.filename).suffix.lower() or ".png"
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        return jsonify({"error": "Only jpg/png/webp allowed"}), 400

    players_dir = REMOTION_PUBLIC / "players"
    players_dir.mkdir(exist_ok=True)
    dest = players_dir / (slug + ext)
    f.save(str(dest))
    return jsonify({"ok": True, "slug": slug, "file": f"players/{slug}{ext}"})


@app.route("/upload-clip", methods=["POST"])
def upload_clip():
    """Upload a footage clip to remotiontest/public/clips/."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f    = request.files["file"]
    name = request.form.get("name", "").strip()
    if not name:
        name = Path(f.filename).stem
    import re as _re
    slug = _re.sub(r"[^\w]", "_", name.lower()).strip("_")
    ext  = Path(f.filename).suffix.lower() or ".mp4"
    allowed = {".mp4", ".webm", ".mov", ".jpg", ".jpeg", ".png", ".webp"}
    if ext not in allowed:
        return jsonify({"error": "Allowed types: mp4/webm/mov/jpg/png/webp"}), 400

    clips_dir = REMOTION_PUBLIC / "clips"
    clips_dir.mkdir(exist_ok=True)
    dest = clips_dir / (slug + ext)
    f.save(str(dest))

    # Duration is probed client-side and sent as form field
    duration_frames = None
    try:
        df = request.form.get("duration_frames", "")
        if df:
            duration_frames = max(1, int(float(df)))
    except Exception:
        pass

    return jsonify({"ok": True, "slug": slug, "file": f"clips/{slug}{ext}",
                    "duration_frames": duration_frames})


@app.route("/studio-note", methods=["POST"])
def studio_note():
    """Save a note against a render."""
    data      = request.get_json()
    safe_name = data.get("safe_name", "")
    filename  = data.get("filename", "")
    note      = data.get("note", "")
    out_dir   = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return jsonify({"error": "Project not found"}), 404
    state = _load_studio_state(out_dir)
    state.setdefault(filename, {})["note"] = note
    _save_studio_state(out_dir, state)
    return jsonify({"ok": True})


@app.route("/studio-approve", methods=["POST"])
def studio_approve():
    """Toggle approved/rejected for a render."""
    data      = request.get_json()
    safe_name = data.get("safe_name", "")
    filename  = data.get("filename", "")
    approved  = data.get("approved", True)

    out_dir = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return jsonify({"error": "Project not found"}), 404

    state = _load_studio_state(out_dir)
    state.setdefault(filename, {})["approved"] = approved
    _save_studio_state(out_dir, state)
    return jsonify({"ok": True})


@app.route("/available-images")
def available_images():
    """Return all player images available in remotiontest/public/."""
    return jsonify({"images": _get_available_player_images()})


@app.route("/resolve-tag-props", methods=["POST"])
def resolve_tag_props():
    """Resolve a tag's freeform text into real props payload for compositions
    whose data lives in a server-side lookup table (e.g. TournamentBracket).
    Body: {composition, tag_text}
    Returns: {ok, props} on success, {ok:false, error} on miss.
    """
    data = request.get_json(silent=True) or {}
    composition = data.get("composition", "")
    tag_text    = data.get("tag_text", "")
    if not composition or not tag_text:
        return jsonify({"ok": False, "error": "composition and tag_text required"}), 400

    sys.path.insert(0, ENGINE_DIR)
    try:
        from agents.graphics_agent import _PAYLOAD_BUILDERS
    except Exception as e:
        return jsonify({"ok": False, "error": f"import failed: {e}"}), 500

    builder = _PAYLOAD_BUILDERS.get(composition)
    if not builder:
        return jsonify({"ok": False, "error": f"no payload builder for {composition}"}), 404

    try:
        result = builder({"tag_text": tag_text})
    except Exception as e:
        return jsonify({"ok": False, "error": f"builder raised: {e}"}), 500

    if not result:
        return jsonify({"ok": False, "error": "builder returned no data (lookup miss?)"}), 404

    payload, _source = result
    return jsonify({"ok": True, "props": payload})


@app.route("/re-render", methods=["POST"])
def re_render():
    """Re-render a single graphic. Supports composition switching via 'composition' field."""
    import threading
    data        = request.get_json()
    safe_name   = data.get("safe_name", "")
    filename    = data.get("filename", "")
    gtype       = data.get("type", "")
    composition = data.get("composition", "")  # explicit Remotion composition ID
    props       = data.get("props", {})

    out_dir = BASE_OUTPUT / safe_name
    renders_dir = out_dir / "renders"
    if not renders_dir.exists():
        return jsonify({"error": "Renders dir not found"}), 404

    output_path = str(renders_dir / filename)

    sys.path.insert(0, ENGINE_DIR)
    from utils.remotion_renderer import (
        render_player_trio, render_attacking_radar, render_career_timeline,
        render_hero_intro, render_hero_bigstat, render_hero_formrun,
        render_hero_statbars, render_hero_tactical, render_team_lineup,
        render_disciplinary_record, _render as _remotion_render,
    )

    # Sanitize props: detect the old flat-player-fields bug where name/club/stat
    # ended up at the root of props instead of inside each players[i] entry.
    _PLAYER_FIELDS = ("name", "club", "clubColor", "stat", "statLabel", "badgeSlug")
    if composition in ("PlayerTrio", "TrioFeature", "SeasonComparison") and "players" in props:
        root_has_player_fields = any(k in props for k in _PLAYER_FIELDS)
        if root_has_player_fields:
            # Move root-level player fields into the last player slot that's missing them
            for player in props["players"]:
                for k in _PLAYER_FIELDS:
                    if k not in player and k in props:
                        player[k] = props[k]
            # Strip them from root
            for k in _PLAYER_FIELDS:
                props.pop(k, None)

    # If an explicit composition is provided, use it directly
    if composition:
        def do_render_direct():
            job_key = f"{safe_name}/{filename}"
            try:
                ok = _remotion_render(composition, props, output_path, composition)
                _rerender_jobs[job_key]["status"] = "done" if ok else "failed"
                if ok:
                    _update_manifest(out_dir, filename, gtype, props, composition)
            except Exception as e:
                _rerender_jobs[job_key] = {"status": "failed", "error": str(e)}

        job_key = f"{safe_name}/{filename}"
        _rerender_jobs[job_key] = {"status": "running", "error": None}
        threading.Thread(target=do_render_direct, daemon=True).start()
        return jsonify({"ok": True, "job_key": job_key})

    RENDER_FN = {
        "player_trio":         render_player_trio,
        "player_radar":        render_attacking_radar,
        "career_timeline":     render_career_timeline,
        "hero_intro":       render_hero_intro,
        "hero_bigstat":     render_hero_bigstat,
        "hero_form":        render_hero_formrun,
        "hero_bars":        render_hero_statbars,
        "hero_tactical":    render_hero_tactical,
        "team_lineup":         render_team_lineup,
        "disciplinary_record": render_disciplinary_record,
    }

    fn = RENDER_FN.get(gtype)
    if not fn:
        return jsonify({"error": f"No renderer for type: {gtype}"}), 400

    job_key = f"{safe_name}/{filename}"
    _rerender_jobs[job_key] = {"status": "running", "error": None}

    def do_render():
        try:
            ok = fn(props, output_path)
            _rerender_jobs[job_key]["status"] = "done" if ok else "failed"
            if ok:
                _update_manifest(out_dir, filename, gtype, props, composition)
        except Exception as e:
            _rerender_jobs[job_key] = {"status": "failed", "error": str(e)}

    threading.Thread(target=do_render, daemon=True).start()
    return jsonify({"ok": True, "job_key": job_key})


def _update_manifest(out_dir: Path, filename: str, gtype: str, props: dict, composition: str):
    """Update or insert a manifest entry after a re-render."""
    manifest = _load_manifest(out_dir)
    for entry in manifest:
        if entry["filename"] == filename:
            entry["props"] = props
            if composition:
                entry["composition"] = composition
            break
    else:
        manifest.append({"filename": filename, "type": gtype,
                          "composition": composition, "props": props,
                          "tag": "", "tag_text": ""})
    manifest_path = out_dir / "renders" / "manifest.json"
    manifest_path.parent.mkdir(exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))


@app.route("/re-render-preview", methods=["POST"])
def re_render_preview():
    """Re-render a single PNG still preview after the user edits props.
    Same payload shape as /re-render, but writes to renders/previews/{stem}.png
    via npx remotion still. Updates manifest props + preview_filename only."""
    import threading
    data        = request.get_json()
    safe_name   = data.get("safe_name", "")
    filename    = data.get("filename", "")  # mp4 filename — used as the stem
    composition = data.get("composition", "")
    props       = data.get("props", {})

    out_dir      = BASE_OUTPUT / safe_name
    renders_dir  = out_dir / "renders"
    previews_dir = renders_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)

    if not composition:
        return jsonify({"error": "composition required"}), 400

    stem        = filename.rsplit(".", 1)[0] if "." in filename else filename
    preview_fn  = f"{stem}.png"
    output_path = str(previews_dir / preview_fn)

    sys.path.insert(0, ENGINE_DIR)
    from utils.remotion_renderer import _render as _remotion_render

    job_key = f"{safe_name}/preview/{filename}"
    _rerender_jobs[job_key] = {"status": "running", "error": None}

    def do_preview():
        try:
            ok = _remotion_render(composition, props, output_path, f"preview {composition}")
            _rerender_jobs[job_key]["status"] = "done" if ok else "failed"
            if ok:
                _update_manifest_preview(out_dir, filename, preview_fn, props, composition)
        except Exception as e:
            _rerender_jobs[job_key] = {"status": "failed", "error": str(e)}

    threading.Thread(target=do_preview, daemon=True).start()
    return jsonify({"ok": True, "job_key": job_key, "preview_filename": preview_fn})


def _update_manifest_preview(out_dir: Path, filename: str, preview_fn: str, props: dict, composition: str):
    """Update manifest props + preview_filename without touching mp4 state."""
    manifest = _load_manifest(out_dir)
    for entry in manifest:
        if entry.get("filename") == filename:
            entry["props"] = props
            entry["preview_filename"] = preview_fn
            entry["preview_rendered"] = True
            if composition:
                entry["composition"] = composition
            break
    else:
        manifest.append({
            "filename":         filename,
            "preview_filename": preview_fn,
            "preview_rendered": True,
            "rendered":         False,
            "composition":      composition,
            "props":            props,
            "tag":              "",
            "tag_text":         "",
        })
    manifest_path = out_dir / "renders" / "manifest.json"
    manifest_path.parent.mkdir(exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))


_rerender_jobs: dict = {}


@app.route("/re-render-status/<safe_name>/<filename>")
def re_render_status(safe_name, filename):
    job_key = f"{safe_name}/{filename}"
    result  = _rerender_jobs.get(job_key, {"status": "unknown"})
    return jsonify(result)


@app.route("/job-status/<path:job_key>")
def job_status(job_key):
    """Generic poller for any _rerender_jobs entry — supports keys with slashes
    (e.g. safe_name/preview/filename for PNG preview jobs)."""
    result = _rerender_jobs.get(job_key, {"status": "unknown"})
    return jsonify(result)


# ── Option A: batch mp4 render after PNG-preview review ─────────────────────
_render_batch_jobs: dict = {}


@app.route("/render-batch", methods=["POST"])
def render_batch():
    """Render mp4 for every approved manifest entry that doesn't yet have an mp4.
    Honours studio_state approve/reject. Runs in a background thread.
    Body: {safe_name, force?: bool}  (force=true re-renders even if mp4 exists)
    """
    import threading
    data       = request.get_json() or {}
    safe_name  = data.get("safe_name", "")
    force      = bool(data.get("force", False))

    out_dir     = BASE_OUTPUT / safe_name
    renders_dir = out_dir / "renders"
    if not renders_dir.exists():
        return jsonify({"error": "Renders dir not found"}), 404

    manifest = _load_manifest(out_dir)
    state    = _load_studio_state(out_dir)

    queue = []
    for entry in manifest:
        fn = entry.get("filename")
        if not fn:
            continue
        if state.get(fn, {}).get("approved", True) is False:
            continue
        if not force and (renders_dir / fn).exists():
            continue
        if not entry.get("composition"):
            continue
        queue.append(entry)

    if not queue:
        return jsonify({"ok": True, "queued": 0, "message": "Nothing to render."})

    job_key = f"{safe_name}/render-batch"
    _render_batch_jobs[job_key] = {
        "status":  "running",
        "total":   len(queue),
        "done":    0,
        "failed":  0,
        "current": None,
        "errors":  [],
    }

    def _do_batch():
        sys.path.insert(0, ENGINE_DIR)
        from utils.remotion_renderer import _render as _remotion_render
        job = _render_batch_jobs[job_key]
        for entry in queue:
            fn          = entry["filename"]
            composition = entry["composition"]
            props       = entry.get("props", {})
            output_path = str(renders_dir / fn)
            job["current"] = fn
            try:
                ok = _remotion_render(composition, props, output_path, composition)
            except Exception as e:
                ok = False
                job["errors"].append({"filename": fn, "error": str(e)})
            if ok:
                job["done"] += 1
                # Mark mp4 rendered in manifest
                for m in manifest:
                    if m.get("filename") == fn:
                        m["rendered"] = True
                        break
            else:
                job["failed"] += 1
        (renders_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
        job["current"] = None
        job["status"]  = "done" if job["failed"] == 0 else "done_with_errors"

    threading.Thread(target=_do_batch, daemon=True).start()
    return jsonify({"ok": True, "queued": len(queue), "job_key": job_key})


@app.route("/render-batch-status/<safe_name>")
def render_batch_status(safe_name):
    job_key = f"{safe_name}/render-batch"
    return jsonify(_render_batch_jobs.get(job_key, {"status": "idle"}))


@app.route("/rerun-failed", methods=["POST"])
def rerun_failed():
    """Re-render all graphics in manifest.json that have status 'failed' or are missing the output file."""
    import threading
    data      = request.get_json()
    safe_name = data.get("safe_name", "")
    out_dir   = BASE_OUTPUT / safe_name
    renders_dir = out_dir / "renders"
    manifest_path = renders_dir / "manifest.json"

    if not manifest_path.exists():
        return jsonify({"error": "No manifest found"}), 404

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except Exception as e:
        return jsonify({"error": f"Manifest read error: {e}"}), 500

    failed = []
    for entry in manifest:
        output_path = entry.get("output_path") or entry.get("rendered_path") or ""
        status      = entry.get("status", "")
        if status == "failed" or (output_path and not os.path.exists(output_path)):
            failed.append(entry)

    if not failed:
        return jsonify({"message": "No failed renders found.", "count": 0})

    def _rerender_batch():
        from utils.remotion_renderer import _render
        for entry in failed:
            comp   = entry.get("composition") or entry.get("composition_id") or ""
            props  = entry.get("props") or {}
            output = entry.get("output_path") or entry.get("rendered_path") or ""
            label  = entry.get("label") or comp
            if comp and output:
                print(f"    [Re-run] ↻ {label}")
                ok = _render(comp, props, output, label)
                entry["status"] = "rendered" if ok else "failed"
        # Update manifest
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"    [Re-run] Done — re-rendered {len(failed)} items")

    t = threading.Thread(target=_rerender_batch, daemon=True)
    t.start()

    return jsonify({"message": f"Re-rendering {len(failed)} failed graphic(s) in background.", "count": len(failed)})


@app.route("/studio-export", methods=["POST"])
def studio_export():
    """Copy all approved renders to output/<safe_name>/export/."""
    import shutil
    data      = request.get_json()
    safe_name = data.get("safe_name", "")
    out_dir   = BASE_OUTPUT / safe_name
    renders_dir = out_dir / "renders"
    export_dir  = out_dir / "export"

    if not renders_dir.exists():
        return jsonify({"error": "No renders dir"}), 404

    state = _load_studio_state(out_dir)
    export_dir.mkdir(exist_ok=True)

    copied = []
    for f in sorted(renders_dir.iterdir()):
        if f.suffix != ".mp4":
            continue
        approved = state.get(f.name, {}).get("approved", True)
        if approved:
            dest = export_dir / f.name
            shutil.copy2(str(f), str(dest))
            copied.append(f.name)

    return jsonify({"ok": True, "exported": copied, "count": len(copied),
                    "path": str(export_dir)})


# ── Curiosity Ideas ───────────────────────────────────────────────────────────

@app.route("/ideas")
def ideas_page():
    """Curiosity-driven video idea generator — finds viral hooks for any football topic."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Idea Generator \u2014 Frequency</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: #F0EDE8;
  color: #1a1a1a;
  font-family: 'Segoe UI', system-ui, sans-serif;
  min-height: 100vh;
  display: flex; flex-direction: column; align-items: center;
  padding: 48px 16px 100px;
  position: relative;
}

/* grain overlay */
body::before {
  content: '';
  position: fixed; inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");
  opacity: 0.045;
  pointer-events: none;
  z-index: 9999;
}

.page-header { text-align: center; margin-bottom: 40px; }
h1 {
  font-size: 0.7rem; font-weight: 800; letter-spacing: 0.18em;
  text-transform: uppercase; color: #999; margin-bottom: 8px;
}
.wordmark {
  font-size: 2rem; font-weight: 900; letter-spacing: -0.02em; color: #1a1a1a;
  line-height: 1;
}
.wordmark span { color: #1660FF; }
.sub { font-size: 0.68rem; color: #aaa; letter-spacing: 0.06em; text-transform: uppercase; margin-top: 8px; }

.main { width: 100%; max-width: 720px; }

/* mode tabs */
.mode-tabs {
  display: grid; grid-template-columns: 1fr 1fr;
  margin-bottom: 28px;
  background: #E8E4DF; border-radius: 8px; padding: 3px; gap: 3px;
}
.mode-tab {
  padding: 10px 16px; border: none; border-radius: 6px;
  background: transparent; color: #999;
  font-size: 0.75rem; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase;
  cursor: pointer; text-align: center;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
}
.mode-tab:hover { color: #555; background: rgba(255,255,255,0.5); }
.mode-tab.active {
  background: #fff; color: #1660FF;
  box-shadow: 0 1px 4px rgba(0,0,0,0.1);
}

/* inputs */
input[type=text], textarea {
  background: #fff; border: 1.5px solid #DDD9D3; color: #1a1a1a;
  padding: 11px 14px; border-radius: 7px; font-size: 0.9rem;
  font-family: inherit; outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
input[type=text]:focus, textarea:focus {
  border-color: #1660FF; box-shadow: 0 0 0 3px rgba(22,96,255,0.1);
}
input[type=text] { flex: 1; }
textarea { width: 100%; resize: vertical; min-height: 68px; }

.form-row { display: flex; gap: 10px; margin-bottom: 12px; }
.context-label {
  font-size: 0.65rem; color: #aaa; letter-spacing: 0.07em;
  text-transform: uppercase; margin-bottom: 6px; margin-top: 14px;
}

/* buttons */
button {
  background: #fff; border: 1.5px solid #DDD9D3; color: #555;
  padding: 11px 22px; border-radius: 7px; font-size: 0.8rem; font-weight: 700;
  cursor: pointer; letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap;
  transition: all 0.15s; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
button:hover { background: #f5f5f5; color: #1a1a1a; border-color: #ccc; }
button:disabled { opacity: 0.4; cursor: not-allowed; }
button.primary {
  background: #1660FF; border-color: #1660FF; color: #fff;
  box-shadow: 0 2px 8px rgba(22,96,255,0.3);
}
button.primary:hover { background: #0a4fe0; border-color: #0a4fe0; box-shadow: 0 3px 12px rgba(22,96,255,0.4); }

.discover-hint {
  font-size: 0.8rem; color: #777; line-height: 1.65;
  background: rgba(22,96,255,0.04); border: 1.5px solid rgba(22,96,255,0.12);
  border-radius: 7px; padding: 13px 16px; margin-bottom: 14px;
}

#status {
  font-size: 0.75rem; color: #999; margin: 16px 0 4px;
  min-height: 20px; letter-spacing: 0.03em;
}

/* loading spinner */
.spinner {
  display: inline-block; width: 12px; height: 12px; margin-right: 7px;
  border: 2px solid rgba(22,96,255,0.2); border-top-color: #1660FF;
  border-radius: 50%; animation: spin 0.7s linear infinite; vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* cards */
#results { margin-top: 4px; }
.idea-card {
  background: #fff; border: 1.5px solid #E8E4DF;
  border-radius: 10px; padding: 22px 24px; margin-bottom: 14px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
  transition: box-shadow 0.2s, border-color 0.2s;
}
.idea-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.09); border-color: #D5D0CA; }

.idea-header { display: flex; align-items: flex-start; gap: 14px; margin-bottom: 14px; }
.rank-badge {
  font-size: 0.68rem; font-weight: 800; color: #bbb; letter-spacing: 0.04em;
  background: #F5F2EE; border: 1.5px solid #E8E4DF; border-radius: 5px;
  padding: 3px 8px; flex-shrink: 0; margin-top: 3px;
}
.rank-badge.top { color: #1660FF; border-color: #ccd9ff; background: #f0f4ff; }

.idea-title { font-size: 1.08rem; font-weight: 800; color: #0f0f0f; line-height: 1.3; }
.thumb-hook {
  display: inline-block; font-size: 0.7rem; font-weight: 900; letter-spacing: 0.1em;
  text-transform: uppercase; color: #1660FF;
  background: #f0f4ff; border: 1.5px solid #ccd9ff; border-radius: 4px;
  padding: 3px 9px; margin-top: 8px;
}
.thumb-label { font-size: 0.58rem; color: #ccc; letter-spacing: 0.06em; text-transform: uppercase; }

.meta-row { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0; }
.tag {
  font-size: 0.62rem; letter-spacing: 0.05em; text-transform: uppercase; font-weight: 700;
  padding: 3px 9px; border-radius: 4px;
}
.tag.formula { background: #F5F2EE; color: #888; border: 1.5px solid #E8E4DF; }
.tag.shape   { background: #f5f0ff; color: #7040c0; border: 1.5px solid #e0d0ff; }
.tag.score   { background: #f0fff4; color: #2a8a4a; border: 1.5px solid #c0e8cc; }
.tag.clarity { background: #f0fafa; color: #2a7a7a; border: 1.5px solid #b8e4e4; }
.tag.time    { background: #fffaf0; color: #9a7010; border: 1.5px solid #f0dfa0; }
.tag.combined { background: #f0f4ff; color: #1660FF; border: 1.5px solid #ccd9ff; font-weight: 900; font-size: 0.7rem; }

.divider { height: 1px; background: #F0EDE8; margin: 12px 0; }

.field { margin-top: 11px; }
.field-label {
  font-size: 0.6rem; color: #bbb; letter-spacing: 0.09em;
  text-transform: uppercase; font-weight: 700; margin-bottom: 4px;
}
.field-value { font-size: 0.85rem; color: #555; line-height: 1.55; }
.field-value.question { color: #1a1a1a; font-style: italic; font-weight: 500; }
.field-value.tension  { color: #c06020; }
.field-value.opening  { color: #333; font-style: italic; }
.field-value.whynow   { color: #2a7a40; }

/* anchor pills */
.anchor-pill {
  display: inline-flex; align-items: center; gap: 5px;
  background: #F5F2EE; border: 1.5px solid #E8E4DF; border-radius: 4px;
  padding: 3px 9px; font-size: 0.63rem; color: #888; letter-spacing: 0.03em;
}
.anchor-pill .tmpl { color: #1660FF; font-weight: 700; }

/* discovery badges */
.subject-badge {
  font-size: 0.62rem; background: #f0f4ff; color: #1660FF;
  border: 1.5px solid #ccd9ff; border-radius: 4px;
  padding: 2px 8px; letter-spacing: 0.05em; text-transform: uppercase; font-weight: 700;
}
.category-badge {
  font-size: 0.62rem; background: #f0fff4; color: #2a7a40;
  border: 1.5px solid #b0e4c0; border-radius: 4px;
  padding: 2px 8px; letter-spacing: 0.05em; text-transform: uppercase; font-weight: 700;
}

.use-btn {
  margin-top: 16px; background: #1660FF; border-color: #1660FF; color: #fff;
  padding: 9px 18px; font-size: 0.78rem;
  box-shadow: 0 2px 6px rgba(22,96,255,0.25);
}
.use-btn:hover { background: #0a4fe0; border-color: #0a4fe0; }

.empty {
  color: #ccc; font-size: 0.82rem; text-align: center; padding: 60px 0;
  letter-spacing: 0.03em;
}
.nav-link {
  font-size: 0.64rem; color: #bbb; text-decoration: none;
  letter-spacing: 0.07em; text-transform: uppercase;
  margin-bottom: 32px; display: inline-block;
}
.nav-link:hover { color: #666; }

/* ── Hamburger nav ────────────────────────────────────────────────────── */
.hamburger-btn {
  position: fixed; top: 20px; left: 20px; z-index: 200;
  background: #fff; border: 1.5px solid #E8E4DF; border-radius: 8px;
  width: 40px; height: 40px; cursor: pointer; display: flex; align-items: center;
  justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  transition: all 0.15s; padding: 0; font-size: 1.1rem; color: #555;
}
.hamburger-btn:hover { background: #F5F2EE; border-color: #bbb; }
.nav-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.12); z-index: 210; display: none; }
.nav-overlay.open { display: block; }
.nav-sidebar {
  position: fixed; top: 0; left: -280px; bottom: 0; width: 260px;
  background: #fff; border-right: 1.5px solid #E8E4DF; z-index: 220;
  display: flex; flex-direction: column;
  box-shadow: 4px 0 20px rgba(0,0,0,0.08);
  transition: left 0.25s cubic-bezier(0.4,0,0.2,1); padding: 28px 0;
}
.nav-sidebar.open { left: 0; }
.nav-brand { padding: 0 24px 24px; border-bottom: 1px solid #F0EDE8; margin-bottom: 12px; }
.nav-brand-name { font-size: 1.2rem; font-weight: 900; letter-spacing: -0.02em; color: #1a1a1a; line-height: 1; }
.nav-brand-name span { color: #1660FF; }
.nav-brand-sub { font-size: 0.62rem; color: #bbb; letter-spacing: 0.07em; text-transform: uppercase; margin-top: 6px; }
.nav-section-label { font-size: 0.6rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: #ccc; padding: 8px 24px 4px; margin-top: 4px; }
.nav-item {
  display: flex; align-items: center; gap: 12px; padding: 10px 24px;
  cursor: pointer; text-decoration: none; color: #555; font-size: 0.82rem; font-weight: 500;
  transition: background 0.12s, color 0.12s; border-left: 3px solid transparent;
}
.nav-item:hover { background: #F5F2EE; color: #1a1a1a; }
.nav-item.active { background: #f0f4ff; color: #1660FF; border-left-color: #1660FF; font-weight: 700; }
.nav-item-icon { font-size: 1rem; width: 20px; text-align: center; }
.nav-close {
  position: absolute; top: 16px; right: 16px; background: none; border: none;
  cursor: pointer; color: #bbb; font-size: 1.1rem; padding: 5px 7px; border-radius: 5px;
}
.nav-close:hover { color: #555; background: #F5F2EE; }
</style>
</head>
<body>

<button class="hamburger-btn" onclick="toggleNav()" aria-label="Menu">&#9776;</button>
<div class="nav-overlay" id="navOverlay" onclick="closeNav()"></div>
<nav class="nav-sidebar" id="navSidebar">
  <button class="nav-close" onclick="closeNav()">&#x2715;</button>
  <div class="nav-brand">
    <div class="nav-brand-name">Frequency</div>
    <div class="nav-brand-sub">AI Documentary Engine</div>
  </div>
  <div class="nav-section-label">Tools</div>
  <a class="nav-item" href="/">&#127916; Documentary Engine</a>
  <a class="nav-item active" href="/ideas">&#128161; Idea Generator</a>
</nav>

<div class="page-header">
  <div class="wordmark">idea<span>.</span></div>
  <p class="sub">Viral hook generator \u00b7 7 formulas \u00b7 ranked by curiosity \u00d7 timeliness</p>
</div>

<div class="main">
  <div class="mode-tabs">
    <button class="mode-tab active" id="tabTopic" onclick="setMode('topic')">I have a topic</button>
    <button class="mode-tab" id="tabDiscover" onclick="setMode('discover')">Suggest ideas to me</button>
  </div>

  <div id="panelTopic">
    <div class="form-row">
      <input type="text" id="topic" placeholder="Topic \u2014 e.g. Liverpool, Transfer Market, Erling Haaland" />
      <button class="primary" id="genBtn" onclick="generate()">Generate</button>
    </div>
    <div class="context-label">Context <span style="color:#ccc">(optional \u2014 boosts timeliness scoring)</span></div>
    <textarea id="context" placeholder="e.g. Man City just won the title, Transfer window opens Monday\u2026"></textarea>
  </div>

  <div id="panelDiscover" style="display:none">
    <div class="discover-hint">The engine scans football\u2019s story calendar \u2014 anniversaries, breakout players, structural patterns, forgotten giants \u2014 and surfaces what\u2019s ripe right now.</div>
    <div class="context-label">Context <span style="color:#ccc">(optional \u2014 recent news or timing notes)</span></div>
    <textarea id="contextDiscover" placeholder="e.g. Champions League semis just finished, end of season run-in\u2026"></textarea>
    <div style="margin-top:12px">
      <button class="primary" id="discoverBtn" onclick="discover()">Suggest Ideas</button>
    </div>
  </div>

  <div id="status"></div>
  <div id="results"><div class="empty">Generate ideas above.</div></div>
</div>

<script>
let _mode = 'topic';

function setMode(m) {
  _mode = m;
  document.getElementById('panelTopic').style.display    = m === 'topic'    ? '' : 'none';
  document.getElementById('panelDiscover').style.display = m === 'discover' ? '' : 'none';
  document.getElementById('tabTopic').classList.toggle('active',    m === 'topic');
  document.getElementById('tabDiscover').classList.toggle('active', m === 'discover');
  document.getElementById('results').innerHTML = '<div class="empty">Generate ideas above.</div>';
  document.getElementById('status').textContent = '';
}

function setLoading(btnId, loading, text) {
  const btn = document.getElementById(btnId);
  btn.disabled = loading;
  btn.textContent = loading ? 'Working\u2026' : text;
  document.getElementById('status').innerHTML = loading
    ? '<span class="spinner"></span>Thinking\u2026'
    : '';
}

async function generate() {
  const topic = document.getElementById('topic').value.trim();
  if (!topic) { document.getElementById('topic').focus(); return; }
  const ctx = document.getElementById('context').value.trim();
  setLoading('genBtn', true, 'Generate');
  document.getElementById('results').innerHTML = '';

  try {
    const res = await fetch('/ideas/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic, context: ctx }),
    }).then(r => r.json());

    if (res.error) { document.getElementById('status').textContent = 'Error: ' + res.error; return; }
    const ideas = res.ideas || [];
    document.getElementById('status').textContent = ideas.length + ' ideas for "' + escHtml(res.topic) + '" \u2014 ranked by curiosity \u00d7 timeliness';
    document.getElementById('results').innerHTML = ideas.map(idea => renderCard(idea)).join('');
  } catch (e) {
    document.getElementById('status').textContent = 'Request failed: ' + e.message;
  } finally {
    setLoading('genBtn', false, 'Generate');
  }
}

async function discover() {
  const ctx = document.getElementById('contextDiscover').value.trim();
  setLoading('discoverBtn', true, 'Suggest Ideas');
  document.getElementById('results').innerHTML = '';

  try {
    const res = await fetch('/ideas/discover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context: ctx }),
    }).then(r => r.json());

    if (res.error) { document.getElementById('status').textContent = 'Error: ' + res.error; return; }
    const ideas = res.ideas || [];
    document.getElementById('status').textContent = ideas.length + ' ideas discovered \u2014 ranked by curiosity \u00d7 timeliness';
    document.getElementById('results').innerHTML = ideas.map(idea => renderCard(idea, true)).join('');
  } catch (e) {
    document.getElementById('status').textContent = 'Request failed: ' + e.message;
  } finally {
    setLoading('discoverBtn', false, 'Suggest Ideas');
  }
}

function renderCard(idea, discovery) {
  const isTop = idea.rank <= 3;

  const anchors = (idea.visual_anchors || []).map(function(a) {
    const desc = Array.isArray(a) ? a[0] : a;
    const tmpl = Array.isArray(a) ? a[1] : '';
    return '<span class="anchor-pill">' + escHtml(desc) + (tmpl ? ' <span class="tmpl">' + escHtml(tmpl) + '</span>' : '') + '</span>';
  }).join('');

  const discoveryMeta = discovery ? (
    (idea.suggested_subject ? '<span class="subject-badge">' + escHtml(idea.suggested_subject) + '</span>' : '') +
    (idea.category ? ' <span class="category-badge">' + escHtml(idea.category) + '</span>' : '')
  ) : '';

  return '<div class="idea-card">' +
    '<div class="idea-header">' +
      '<div class="rank-badge' + (isTop ? ' top' : '') + '">#' + String(idea.rank).padStart(2,'0') + '</div>' +
      '<div style="flex:1">' +
        (discoveryMeta ? '<div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">' + discoveryMeta + '</div>' : '') +
        '<div class="idea-title">' + escHtml(idea.title) + '</div>' +
        '<div style="display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap">' +
          '<div class="thumb-hook">' + escHtml(idea.thumbnail_hook) + '</div>' +
          '<div class="thumb-label">thumbnail</div>' +
        '</div>' +
      '</div>' +
    '</div>' +

    '<div class="meta-row">' +
      '<span class="tag formula">' + escHtml(idea.hook_formula || '') + '</span>' +
      '<span class="tag shape">' + escHtml(idea.narrative_shape || '') + '</span>' +
      '<span class="tag score">C\u00a0' + (idea.curiosity_score||'') + '</span>' +
      '<span class="tag clarity">Cl\u00a0' + (idea.clarity_score||'') + '</span>' +
      '<span class="tag time">T\u00a0' + (idea.timeliness||'') + '</span>' +
      '<span class="tag combined">' + (idea.combined_score||'') + '</span>' +
    '</div>' +

    '<div class="divider"></div>' +

    '<div class="field"><div class="field-label">Core Question</div>' +
      '<div class="field-value question">' + escHtml(idea.core_question||'') + '</div></div>' +

    (idea.tension ? '<div class="field"><div class="field-label">Tension</div>' +
      '<div class="field-value tension">' + escHtml(idea.tension) + '</div></div>' : '') +

    '<div class="field"><div class="field-label">Hook</div>' +
      '<div class="field-value">' + escHtml(idea.counterintuitive_element||'') + '</div></div>' +

    '<div class="field"><div class="field-label">Opening line</div>' +
      '<div class="field-value opening">\u201c' + escHtml(idea.opening_line||'') + '\u201d</div></div>' +

    (anchors ? '<div class="field"><div class="field-label">Visual Anchors</div>' +
      '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:5px">' + anchors + '</div></div>' : '') +

    (idea.why_now ? '<div class="field"><div class="field-label">Why now</div>' +
      '<div class="field-value whynow">' + escHtml(idea.why_now) + '</div></div>' : '') +

    '<div class="field"><div class="field-label">Why it works</div>' +
      '<div class="field-value">' + escHtml(idea.why_it_works||'') + '</div></div>' +

    '<button class="use-btn" onclick="useIdea(' + escHtml(JSON.stringify(idea.title)) + ')">Use as documentary title \u2192</button>' +
    '</div>';
}

function useIdea(title) {
  window.location.href = '/?prefill=' + encodeURIComponent(title);
}

function escHtml(s) {
  if (typeof s !== 'string') return String(s || '');
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('topic').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') generate();
  });
});
function toggleNav() {
  const s = document.getElementById('navSidebar');
  const o = document.getElementById('navOverlay');
  const open = s.classList.toggle('open');
  o.classList.toggle('open', open);
}
function closeNav() {
  document.getElementById('navSidebar').classList.remove('open');
  document.getElementById('navOverlay').classList.remove('open');
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeNav(); });
</script>
</body>
</html>"""
    return html


@app.route("/ideas/generate", methods=["POST"])
def ideas_generate():
    """Generate curiosity-driven video ideas for a topic."""
    data    = request.get_json()
    topic   = (data.get("topic") or "").strip()
    context = (data.get("context") or "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    from agents.curiosity_agent import generate_curiosity_ideas
    result = generate_curiosity_ideas(topic, current_context=context)
    return jsonify(result)


@app.route("/ideas/discover", methods=["POST"])
def ideas_discover():
    """Proactively suggest video ideas — no topic required."""
    import datetime
    data    = request.get_json() or {}
    context = (data.get("context") or "").strip()
    current_date = datetime.date.today().strftime("%d %B %Y")
    from agents.curiosity_agent import generate_proactive_ideas
    result = generate_proactive_ideas(current_date=current_date, context=context)
    return jsonify(result)


# ── Full video export (VideoSequence render + narration audio mix) ─────────────
_export_jobs: dict = {}   # safe_name → {status, progress, error, output}

@app.route("/sync-map/<safe_name>")
def get_sync_map(safe_name):
    """Return sync_map.json for a project, or trigger estimation if missing."""
    out_dir  = BASE_OUTPUT / safe_name
    sync_path = out_dir / "sync_map.json"
    if sync_path.exists():
        return jsonify(json.loads(sync_path.read_text()))
    # Build estimated map on-the-fly if script exists
    if (out_dir / "script_draft.md").exists():
        from agents.sync_agent import build_sync_map
        result = build_sync_map(str(out_dir))
        return jsonify(result)
    return jsonify({"error": "No script found"}), 404


@app.route("/export-video", methods=["POST"])
def export_video():
    """Assemble approved graphics+clips into a final MP4 via VideoSequence, then mix narration audio."""
    import threading, subprocess as _sp, json as _json, shutil as _sh
    from pathlib import Path as _Path

    data      = request.get_json()
    safe_name = data.get("safe_name", "")
    out_dir   = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return jsonify({"error": "Project not found"}), 404

    tl_state = out_dir / "timeline_state.json"
    if not tl_state.exists():
        return jsonify({"error": "No timeline saved — save the timeline first"}), 400

    items = _json.loads(tl_state.read_text())

    # ── Load sync_map for narration-paced scene durations ────────────────────
    # sync_map.json maps each graphic tag to its narration_start_seconds.
    # When available, each graphic's duration = gap to next graphic's narration
    # cue instead of the fixed stored duration. Falls back gracefully when absent.
    _sync_scenes   = []
    _sync_total_dur = 0.0
    _sm_path = out_dir / "sync_map.json"
    if _sm_path.exists():
        try:
            _sm = _json.loads(_sm_path.read_text())
            _sync_scenes   = _sm.get("scenes", [])
            _sync_total_dur = float(_sm.get("total_narration_duration_seconds") or 0.0)
            print(f"    [sync] Loaded sync_map: {len(_sync_scenes)} entries, "
                  f"{round(_sync_total_dur)}s total narration")
        except Exception as _e:
            print(f"    [sync] sync_map.json load error: {_e} — using stored durations")

    def _match_sync_time(tag_key: str, tag_text: str, start_idx: int) -> tuple[float | None, int]:
        """Scan _sync_scenes from start_idx forward; return (narr_sec, next_ptr)."""
        tk = tag_key.upper()
        tt = tag_text or ""
        for j in range(start_idx, len(_sync_scenes)):
            sc = _sync_scenes[j]
            if (sc.get("tag") or "").upper() != tk:
                continue
            sc_content = sc.get("content") or ""
            # Match if first 40 chars of content align (handles minor whitespace diffs)
            if (tt[:40] == sc_content[:40]
                    or (len(sc_content) >= 8 and sc_content[:20] in tt)
                    or (len(tt) >= 8 and tt[:20] in sc_content)):
                return sc.get("narration_start_seconds"), j + 1
        return None, start_idx  # no match — leave pointer where it is

    # ── First pass: collect approved graphic/clip candidates ─────────────────
    # Compositions that exist for production reference but must NOT appear in
    # the final video — they break the seamless feel.
    _CHAPTER_COMPS = {"HeroChapterWord", "TitleCard"}

    _candidates = []
    for item in items:
        if item.get("approved") is False:
            continue
        if item.get("track", "") not in ("graphics", "clips"):
            continue
        comp = item.get("composition") or ""
        if not comp:
            continue
        if comp in _CHAPTER_COMPS:
            continue   # chapter markers are production-only — strip from final video
        _candidates.append(item)

    # ── Second pass: match each candidate to its sync timestamp ─────────────
    _FPS        = 30
    _MIN_FRAMES = 90   # 3-second floor — nothing shorter than this
    _sync_timings = []
    _ptr = 0
    for _item in _candidates:
        _narr_sec, _ptr = _match_sync_time(
            _item.get("tag_key") or "",
            _item.get("tag_text") or "",
            _ptr,
        )
        _sync_timings.append(_narr_sec)

    _synced_count = sum(1 for t in _sync_timings if t is not None)
    if _sync_scenes:
        print(f"    [sync] Matched {_synced_count}/{len(_candidates)} graphic scenes to narration cues")

    # ── Pre-pass: backfill flow_hint for items that predate Phase 2 ──────────
    # Uses act string + bgColor from props as proxies for actIndex + canonical_bgColor.
    for _pi, _pitem in enumerate(_candidates):
        if _pitem.get("flow_hint"):
            continue
        if _pi == 0:
            _pitem["_fh"] = "cut"
            continue
        _prev = _candidates[_pi - 1]
        _same_act = _pitem.get("act", "") == _prev.get("act", "")
        _bg  = (_pitem.get("props") or {}).get("bgColor", "")
        _pbg = (_prev.get("props") or {}).get("bgColor", "")
        if not _same_act:
            _pitem["_fh"] = "cut"
        elif _bg and _pbg and _bg == _pbg:
            _pitem["_fh"] = "evolve"
        elif _bg and _pbg:
            _pitem["_fh"] = "worldPan"
        else:
            _pitem["_fh"] = "evolve"

    # ── Third pass: build VideoSequence scenes with sync-paced durations ─────
    scenes = []
    prev_transition = "none"
    prev_itype = ""
    _world_state = {"cameraX": 0.0, "cameraY": 0.0, "zoom": 1.0}
    # Each mutating composition nudges the WorldStateRoot offset by 60px — a
    # subtle parallax shift applied once (in VideoSequence.WorldStateRoot).
    # Templates must NOT apply cameraX internally; the VSQ wrapper is the
    # single authority so that Gap 3 templates only need a schema field.
    _WORLD_MUTATIONS = {
        "HeroBigStat":        {"cameraX": 1920.0},
        "CareerTimeline":        {"cameraX": 1920.0},
        "HeroStatBars":       {"cameraX": 1920.0},
        "AttackingRadar":        {"cameraX": 1920.0},
        "PlayerTrio":            {"cameraX": 1920.0},
        "HeroFormRun":        {"cameraX": 1920.0},
        "HeroQuote":          {"cameraX": 1920.0},
        "HeroSeasonTimeline": {"cameraX": 1920.0},
        "HeroShotMap":        {"cameraX": 1920.0},
        "HeroTactical":       {"cameraX": 1920.0},
        "HeroLeagueGraph":    {"cameraX": 1920.0},
        "DisciplinaryRecord":    {"cameraX": 1920.0},
        "HeroMatchTimeline":  {"cameraX": 1920.0},
        "TeamLineup":            {"cameraX": 1920.0},
        "TopScorersTable":       {"cameraX": 1920.0},
        "PlayerStats":           {"cameraX": 1920.0},
    }
    for _idx, _item in enumerate(_candidates):
        _comp       = _item.get("composition", "") or ""
        _itype      = _item.get("type", "")
        _transition = _item.get("transition") or ""
        _flow_hint  = _item.get("flow_hint") or _item.get("_fh") or "cut"
        _props      = dict(_item.get("props") or {})

        # Clip trim offsets
        if _itype == "clip":
            if _item.get("trimIn") is not None:
                _props["trimIn"] = _item["trimIn"]
            if _item.get("trimOut") is not None:
                _props["trimOut"] = _item["trimOut"]

        # Derive transition from flow_hint when not manually set by the user.
        # Priority: explicit user transition > flow_hint mapping > "none"
        if not _transition or _transition == "none":
            _hint_map = {
                "push":     "push",
                "grain":    "grain",
                "paper":    "paper",
                "dataLine": "dataLine",
                "flash":    "flash",
                "letterbox": "push",   # letterbox is a storyboard marker — use push in final video
                "cut":      "none",
                "worldPan": "worldPan",
            }
            if _flow_hint == "evolve":
                # evolve only works when both scenes share identical backgrounds.
                # Between two raw footage clips it has no effect — use worldPan instead
                # to keep the "continuous canvas" feel across clip boundaries.
                if _itype == "clip" and prev_itype == "clip":
                    _transition = "worldPan"
                else:
                    _transition = "evolve"
            else:
                _transition = _hint_map.get(_flow_hint, "none")

        # Letterbox carried over from a manual setting — replace with push in final video
        if _transition == "letterbox":
            _transition = "push"

        # skipIntro: set when THIS scene's incoming transition is a continuation
        if _itype == "graphic" and (
            _transition in ("evolve", "worldPan") or _item.get("skipIntro")
        ):
            _props["skipIntro"] = True

        # Phase 5: inject world state into props as the root spatial context.
        # Reset on hard cuts (act breaks); preserve/mutate otherwise.
        if _transition == "none" and _flow_hint == "cut":
            _world_state = {"cameraX": 0.0, "cameraY": 0.0, "zoom": 1.0}
        _props["worldState"] = dict(_world_state)

        # Duration: narration-paced if sync data available, else use stored value
        _narr_sec = _sync_timings[_idx]
        if _narr_sec is not None:
            # Find next synced scene's start time
            _next_sec = _sync_total_dur if _sync_total_dur > 0 else None
            for _j in range(_idx + 1, len(_candidates)):
                if _sync_timings[_j] is not None:
                    _next_sec = _sync_timings[_j]
                    break
            if _next_sec is not None and _next_sec > _narr_sec:
                _duration_frames = max(_MIN_FRAMES, int((_next_sec - _narr_sec) * _FPS))
            else:
                _duration_frames = _item.get("duration_frames") or int((_item.get("duration") or 8) * _FPS)
        else:
            _duration_frames = _item.get("duration_frames") or int((_item.get("duration") or 8) * _FPS)

        _scene_def = {
            "compositionId":    _comp,
            "props":            _props,
            "durationInFrames": _duration_frames,
            "transition":       _transition,
        }
        # Wire flow_direction → transitionDirection for push/worldPan transitions
        _flow_dir = _item.get("flow_direction")
        if _flow_dir and _transition in ("push", "worldPan"):
            _scene_def["transitionDirection"] = _flow_dir
        scenes.append(_scene_def)
        prev_transition = _transition
        prev_itype = _itype

        # Apply world-state mutation for next scene.
        # Negate for left-direction acts so camera retreats rather than advances.
        _mutation = _WORLD_MUTATIONS.get(_comp)
        if _mutation:
            _dir_sign = -1 if _flow_dir == "left" else 1
            for _k, _v in _mutation.items():
                _world_state[_k] = _world_state.get(_k, 0.0) + _v * _dir_sign
        print(f"  [WorldState] scene {_idx} {_comp}: cameraX={_world_state['cameraX']:.0f} zoom={_world_state['zoom']}", flush=True)

    if not scenes:
        return jsonify({"error": "No approved, rendered items with compositions to export"}), 400

    export_dir = out_dir / "export"
    export_dir.mkdir(exist_ok=True)
    video_only = export_dir / "video_only.mp4"
    final_out  = export_dir / "final.mp4"
    narr_audio = out_dir / "narration.mp3"

    _export_jobs[safe_name] = {"status": "queued", "progress": "Starting…", "error": None, "output": str(final_out)}

    def _run():
        try:
            _export_jobs[safe_name]["status"] = "running"
            _export_jobs[safe_name]["progress"] = "Rendering video sequence…"

            props_json = _json.dumps({"scenes": scenes})
            remotion_dir = str(REMOTION_DIR)

            # Remotion render: VideoSequence composition
            cmd = [
                "npx", "remotion", "render",
                "VideoSequence",
                str(video_only),
                f"--props={props_json}",
                "--log=verbose",
            ]
            result = _sp.run(cmd, cwd=remotion_dir, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                _export_jobs[safe_name]["status"] = "failed"
                _export_jobs[safe_name]["error"]  = result.stderr[-1000:] or result.stdout[-500:]
                return

            # Mix audio: narration + optional background music
            if narr_audio.exists():
                _export_jobs[safe_name]["progress"] = "Mixing audio…"

                # Check for music plan — pick first act's track as the bg score
                # (single track looped/trimmed over full video for now)
                music_track = None
                music_plan_path = out_dir / "music_plan.json"
                if music_plan_path.exists():
                    try:
                        with open(music_plan_path) as _mf:
                            _mplan = _json.load(_mf)
                        if _mplan:
                            # Use the ACT 2 track (most representative energy) if available,
                            # else fall back to the first track in the plan
                            _track = next(
                                (p for p in _mplan if "ACT 2" in p.get("act","").upper()),
                                _mplan[0]
                            )
                            _tp = _track.get("path","")
                            _vol = _track.get("volume", 0.18)
                            if _tp and _Path(_tp).exists():
                                music_track = (_tp, _vol)
                    except Exception:
                        pass

                if music_track:
                    music_path, music_vol = music_track
                    mix_cmd = [
                        "ffmpeg", "-y",
                        "-i", str(video_only),
                        "-i", str(narr_audio),
                        "-stream_loop", "-1", "-i", music_path,
                        "-filter_complex",
                        f"[2:a]volume={music_vol}[bg];[1:a][bg]amix=inputs=2:duration=first:dropout_transition=3[mixed]",
                        "-map", "0:v",
                        "-map", "[mixed]",
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-shortest",
                        str(final_out),
                    ]
                else:
                    mix_cmd = [
                        "ffmpeg", "-y",
                        "-i", str(video_only),
                        "-i", str(narr_audio),
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-shortest",
                        str(final_out),
                    ]

                mix = _sp.run(mix_cmd, capture_output=True, text=True, timeout=600)
                if mix.returncode != 0:
                    _sh.copy2(str(video_only), str(final_out))
                    _export_jobs[safe_name]["progress"] = "⚠ Audio mix failed — video-only exported"
                else:
                    if music_track:
                        _export_jobs[safe_name]["progress"] = f"Done — narration + music mixed"
            else:
                _sh.copy2(str(video_only), str(final_out))
                _export_jobs[safe_name]["progress"] = "Done (no narration.mp3 found — video only)"

            _export_jobs[safe_name]["status"] = "done"

        except Exception as exc:
            _export_jobs[safe_name]["status"] = "failed"
            _export_jobs[safe_name]["error"]  = str(exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "safe_name": safe_name, "scene_count": len(scenes)})


@app.route("/export-video-status/<safe_name>")
def export_video_status(safe_name):
    job = _export_jobs.get(safe_name)
    if not job:
        return jsonify({"status": "not_started"})
    return jsonify(job)


@app.route("/studio/<safe_name>")
def studio(safe_name):
    """Render Studio — review, approve, edit props, swap images, change template, export."""
    out_dir = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return f"Project '{safe_name}' not found", 404

    title = safe_name.replace("_", " ").title()
    ctx = out_dir / "context.md"
    if ctx.exists():
        for line in ctx.read_text().splitlines():
            if line.startswith("## Title"):
                title = line.replace("## Title", "").strip()
                break

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Studio — {title}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',system-ui,sans-serif;background:#080808;color:#e0e0e0;min-height:100vh}}
:root{{--gold:#C9A84C;--red:#ef4444;--green:#22c55e;--surface:#0f0f0f;--border:#1e1e1e;--border2:#252525}}

/* ── Header ── */
.header{{display:flex;align-items:center;justify-content:space-between;padding:12px 20px;border-bottom:1px solid var(--border);background:#0a0a0a;position:sticky;top:0;z-index:200}}
.header-left{{display:flex;align-items:center;gap:12px}}
.logo{{font-size:0.7rem;font-weight:700;letter-spacing:.14em;color:var(--gold);text-transform:uppercase}}
.project-title{{font-size:0.95rem;font-weight:600;color:#e0e0e0;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.header-right{{display:flex;align-items:center;gap:8px}}
.approved-count{{font-size:0.72rem;color:#555;padding:5px 10px;background:#0d0d0d;border-radius:4px;border:1px solid var(--border)}}
.btn{{padding:6px 14px;border-radius:5px;font-size:0.75rem;font-weight:600;cursor:pointer;border:none;transition:all .15s;letter-spacing:.02em;text-decoration:none;display:inline-flex;align-items:center;gap:5px}}
.btn-gold{{background:var(--gold);color:#000}}
.btn-gold:hover{{background:#d4b060}}
.btn-ghost{{background:none;border:1px solid #242424;color:#666}}
.btn-ghost:hover{{border-color:#3a3a3a;color:#aaa}}

/* ── Filter / toolbar ── */
.toolbar{{display:flex;align-items:center;gap:8px;padding:10px 20px;border-bottom:1px solid var(--border);background:#0a0a0a;overflow-x:auto;flex-wrap:wrap}}
.filter-pill{{padding:4px 11px;border-radius:20px;font-size:0.68rem;font-weight:600;cursor:pointer;border:1px solid #222;color:#555;background:none;transition:all .15s;white-space:nowrap;letter-spacing:.04em}}
.filter-pill:hover{{border-color:#3a3a3a;color:#aaa}}
.filter-pill.active{{background:var(--gold);color:#000;border-color:var(--gold)}}
.toolbar-sep{{width:1px;height:18px;background:#222;flex-shrink:0}}
.bulk-btn{{padding:4px 10px;border-radius:4px;font-size:0.68rem;cursor:pointer;background:none;border:1px solid #1e1e1e;color:#444;transition:all .15s}}
.bulk-btn:hover{{border-color:#333;color:#888}}
.sort-select{{background:#0a0a0a;border:1px solid #222;color:#666;font-size:0.68rem;padding:4px 8px;border-radius:4px;cursor:pointer}}

/* ── Tabs ── */
.tabs{{display:flex;gap:0;border-bottom:1px solid var(--border);padding:0 20px;background:#090909}}
.tab{{padding:10px 16px;font-size:0.78rem;font-weight:500;color:#444;cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;user-select:none}}
.tab:hover{{color:#999}}
.tab.active{{color:var(--gold);border-bottom-color:var(--gold)}}

/* ── Main ── */
.content{{padding:20px}}

/* ── Render grid ── */
.render-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}}
.render-card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;transition:border-color .15s;display:flex;flex-direction:column}}
.render-card.rejected{{opacity:.4;border-color:#1a0a0a}}
.render-card.approved{{border-color:#182018}}

/* Card video */
.card-video{{position:relative;background:#000;aspect-ratio:16/9;cursor:pointer}}
.card-video video{{width:100%;height:100%;display:block;object-fit:contain}}
.play-overlay{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.45);opacity:0;transition:opacity .15s}}
.card-video:hover .play-overlay{{opacity:1}}
.play-icon{{width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,.15);border:2px solid rgba(255,255,255,.3);display:flex;align-items:center;justify-content:center;font-size:1.2rem}}

/* Card header */
.card-head{{display:flex;align-items:center;gap:8px;padding:10px 12px 0}}
.card-type{{font-size:0.58rem;text-transform:uppercase;letter-spacing:.1em;color:var(--gold);background:#1a1500;padding:2px 7px;border-radius:3px;flex-shrink:0;border:1px solid #2a2000}}
.card-comp-select{{font-size:0.62rem;background:#0d0d0d;border:1px solid #222;color:#666;padding:2px 6px;border-radius:3px;cursor:pointer;max-width:160px}}
.card-comp-select:focus{{outline:none;border-color:var(--gold)}}
.card-size{{font-size:0.62rem;color:#2a2a2a;margin-left:auto}}

/* Card tag */
.card-tag-row{{padding:4px 12px 0}}
.card-tag{{font-size:0.68rem;color:#3a3a3a;line-height:1.4;font-style:italic}}

/* Script context */
.card-context{{margin:6px 12px 0;padding:7px 10px;background:#0a0a0a;border-left:2px solid #1e1e1e;border-radius:0 4px 4px 0;font-size:0.68rem;color:#3a3a3a;line-height:1.5;max-height:54px;overflow:hidden;cursor:pointer;transition:max-height .2s}}
.card-context.expanded{{max-height:200px}}
.card-context:hover{{border-left-color:#333;color:#555}}

/* Card actions */
.card-actions{{display:flex;align-items:center;gap:6px;padding:10px 12px 0}}
.approve-btn{{flex:1;padding:6px;border-radius:4px;font-size:0.72rem;font-weight:600;cursor:pointer;border:none;transition:all .15s}}
.approve-btn.approved{{background:#0f2010;color:#4ade80;border:1px solid #14532d}}
.approve-btn.approved:hover{{background:#14532d}}
.approve-btn.rejected{{background:#1f0808;color:#f87171;border:1px solid #7f1d1d}}
.approve-btn.rejected:hover{{background:#7f1d1d}}
.icon-btn{{width:32px;height:32px;border-radius:4px;font-size:0.85rem;cursor:pointer;background:none;border:1px solid #1e1e1e;color:#3a3a3a;display:flex;align-items:center;justify-content:center;transition:all .15s;flex-shrink:0}}
.icon-btn:hover{{border-color:#333;color:#888;background:#111}}
.icon-btn.active{{border-color:var(--gold);color:var(--gold);background:#1a1500}}

/* Note field */
.card-note-row{{padding:6px 12px 0;display:none}}
.card-note-row.open{{display:block}}
.card-note{{width:100%;background:#0a0a0a;border:1px solid #1e1e1e;border-radius:4px;color:#888;font-size:0.72rem;padding:5px 8px;resize:none;font-family:inherit;outline:none}}
.card-note:focus{{border-color:#333}}

/* ── Edit panel ── */
.edit-panel{{margin:10px 12px 12px;border:1px solid var(--border2);border-radius:6px;overflow:hidden;display:none}}
.edit-panel.open{{display:block}}
.edit-tabs{{display:flex;border-bottom:1px solid var(--border2);background:#0a0a0a}}
.edit-tab{{padding:7px 12px;font-size:0.68rem;font-weight:600;color:#3a3a3a;cursor:pointer;border-bottom:2px solid transparent;transition:all .12s;letter-spacing:.04em;text-transform:uppercase}}
.edit-tab:hover{{color:#888}}
.edit-tab.active{{color:var(--gold);border-bottom-color:var(--gold)}}
.edit-body{{padding:12px;background:#090909}}

/* Props form */
.prop-field{{margin-bottom:10px}}
.prop-label{{font-size:0.65rem;color:#555;text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px}}
.prop-input{{width:100%;background:#0d0d0d;border:1px solid #222;border-radius:4px;color:#ccc;font-size:0.78rem;padding:6px 8px;outline:none;font-family:inherit}}
.prop-input:focus{{border-color:var(--gold)}}
.prop-color-row{{display:flex;align-items:center;gap:8px}}
.prop-color-preview{{width:28px;height:28px;border-radius:4px;border:1px solid #333;cursor:pointer;flex-shrink:0}}
input[type=color]{{width:28px;height:28px;border:none;background:none;cursor:pointer;padding:0}}
.prop-boolean{{display:flex;align-items:center;gap:8px}}
.prop-boolean input{{width:14px;height:14px;accent-color:var(--gold)}}
.player-slot{{background:#0d0d0d;border:1px solid #1e1e1e;border-radius:5px;padding:10px;margin-bottom:8px}}
.player-slot-head{{display:flex;align-items:center;gap:8px;margin-bottom:8px}}
.player-slot-num{{font-size:0.6rem;color:var(--gold);font-weight:700;text-transform:uppercase;letter-spacing:.08em}}
.player-slot-name{{font-size:0.78rem;font-weight:600;color:#ccc;flex:1}}
.slot-img-row{{display:flex;align-items:center;gap:8px;margin-bottom:8px}}
.slot-thumb{{width:36px;height:46px;object-fit:cover;border-radius:3px;border:1px solid #2a2a2a;background:#1a1a1a;flex-shrink:0}}
.slot-thumb-empty{{width:36px;height:46px;border-radius:3px;border:1px dashed #2a2a2a;background:#111;display:flex;align-items:center;justify-content:center;color:#2a2a2a;font-size:1rem;flex-shrink:0}}
.slot-img-info{{flex:1;min-width:0}}
.slot-img-slug{{font-size:0.68rem;color:#555;font-family:monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.swap-btn{{padding:4px 9px;border-radius:3px;font-size:0.68rem;cursor:pointer;background:none;border:1px solid #333;color:#888;transition:all .12s;flex-shrink:0}}
.swap-btn:hover{{border-color:var(--gold);color:var(--gold)}}
.json-editor{{width:100%;min-height:160px;background:#060606;border:1px solid #1e1e1e;border-radius:4px;color:#a8c0a0;font-size:0.72rem;padding:10px;font-family:monospace;resize:vertical;outline:none;line-height:1.5}}
.json-editor:focus{{border-color:#333}}
.json-error{{font-size:0.68rem;color:var(--red);margin-top:4px;min-height:16px}}
.rerender-row{{display:flex;align-items:center;gap:8px;margin-top:12px}}
.rerender-btn{{flex:1;padding:7px;border-radius:4px;font-size:0.75rem;font-weight:600;cursor:pointer;background:#1a1500;border:1px solid var(--gold);color:var(--gold);transition:all .15s}}
.rerender-btn:hover{{background:#2a2000}}
.rerender-btn:disabled{{opacity:.4;cursor:default}}
.rerender-status{{font-size:0.68rem;color:#555;margin-top:5px;min-height:14px;text-align:center}}

/* ── Image picker modal ── */
.modal-overlay{{position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:500;display:none;align-items:center;justify-content:center}}
.modal-overlay.open{{display:flex}}
.modal{{background:#0f0f0f;border:1px solid #2a2a2a;border-radius:10px;width:min(820px,96vw);max-height:88vh;display:flex;flex-direction:column;overflow:hidden}}
.modal-header{{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border)}}
.modal-title{{font-size:0.9rem;font-weight:600}}
.modal-close{{width:28px;height:28px;border-radius:50%;background:none;border:1px solid #333;color:#777;cursor:pointer;font-size:0.9rem;display:flex;align-items:center;justify-content:center}}
.modal-close:hover{{border-color:#555;color:#ccc}}
.modal-toolbar{{display:flex;align-items:center;gap:10px;padding:10px 18px;border-bottom:1px solid var(--border)}}
.modal-search{{flex:1;padding:7px 12px;background:#060606;border:1px solid #222;border-radius:4px;color:#e0e0e0;font-size:0.82rem;outline:none}}
.modal-search:focus{{border-color:var(--gold)}}
.upload-zone{{display:flex;align-items:center;gap:8px;padding:8px 12px;border:1px dashed #2a2a2a;border-radius:5px;cursor:pointer;font-size:0.72rem;color:#444;transition:all .15s}}
.upload-zone:hover{{border-color:var(--gold);color:var(--gold)}}
.modal-body{{overflow-y:auto;padding:14px 18px;display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:10px}}
.img-option{{cursor:pointer;border:2px solid transparent;border-radius:5px;overflow:hidden;transition:all .15s;text-align:center;background:#0a0a0a}}
.img-option:hover{{border-color:#3a3a3a}}
.img-option.selected{{border-color:var(--gold);background:#1a1500}}
.img-option img{{width:100%;aspect-ratio:2/3;object-fit:cover;display:block;background:#111}}
.img-option .img-lbl{{font-size:0.58rem;color:#666;padding:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.modal-footer{{padding:12px 18px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:8px}}
.selected-label{{font-size:0.72rem;color:#555}}

/* ── Clips panel ── */
.clips-list{{display:flex;flex-direction:column;gap:8px}}
.clip-card{{background:var(--surface);border:1px solid var(--border);border-radius:7px;padding:12px 14px;display:flex;align-items:flex-start;gap:12px}}
.clip-id{{font-family:monospace;font-size:0.7rem;color:var(--gold);background:#1a1500;padding:3px 7px;border-radius:3px;white-space:nowrap;flex-shrink:0;border:1px solid #2a1800}}
.clip-info{{flex:1;min-width:0}}
.clip-desc{{font-size:0.8rem;color:#bbb;margin-bottom:3px;line-height:1.4}}
.clip-meta{{font-size:0.67rem;color:#3a3a3a}}
.yt-link{{display:inline-block;margin-top:7px;padding:4px 10px;background:#8b0000;color:#fca5a5;text-decoration:none;border-radius:4px;font-size:0.7rem;font-weight:600;border:1px solid #991b1b}}
.yt-link:hover{{background:#991b1b}}
.status-needed{{display:inline-block;padding:3px 7px;border-radius:3px;font-size:0.65rem;font-weight:600;background:#1a0a00;color:#fb923c;border:1px solid #431407}}

/* ── Toast ── */
.toast{{position:fixed;bottom:22px;right:22px;background:#141414;border:1px solid #2a2a2a;border-radius:7px;padding:10px 16px;font-size:0.8rem;color:#e0e0e0;z-index:999;opacity:0;transform:translateY(8px);transition:all .22s;pointer-events:none;max-width:320px}}
.toast.show{{opacity:1;transform:translateY(0)}}
.toast.success{{border-color:#22c55e;color:#86efac}}
.toast.error{{border-color:#ef4444;color:#fca5a5}}

/* ── Empty ── */
.empty{{text-align:center;padding:56px 20px;color:#2a2a2a}}
.empty-icon{{font-size:2.2rem;margin-bottom:10px}}
.empty-msg{{font-size:0.88rem}}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <span class="logo">Frequency · Studio</span>
    <span style="color:#1e1e1e">|</span>
    <span class="project-title" id="projectTitle">Loading…</span>
  </div>
  <div class="header-right">
    <span class="approved-count" id="approvedCount">— / — approved</span>
    <a class="btn btn-ghost" href="/">← Engine</a>
    <button class="btn btn-ghost" id="studioRerunBtn" onclick="rerunFailedRenders()" title="Re-render all failed or missing graphics">&#8635; Re-run Failed</button>
    <button class="btn btn-gold" onclick="doExport()">Export Approved ↓</button>
  </div>
</div>

<div class="toolbar">
  <button class="filter-pill active" id="pill-all" onclick="setFilter('all')">All</button>
  <div class="toolbar-sep"></div>
  <button class="bulk-btn" onclick="bulkApprove(true)">Approve all</button>
  <button class="bulk-btn" onclick="bulkApprove(false)">Reject all</button>
  <div class="toolbar-sep"></div>
  <select class="sort-select" id="sortSelect" onchange="applySort()">
    <option value="filename">Sort: filename</option>
    <option value="type">Sort: type</option>
    <option value="approved">Sort: approved first</option>
  </select>
</div>

<div class="tabs">
  <div class="tab active" id="tab-renders" onclick="switchTab('renders')">Graphics</div>
  <div class="tab" id="tab-clips" onclick="switchTab('clips')">Clips to Source</div>
</div>

<div class="content">
  <div id="panel-renders">
    <div class="render-grid" id="renderGrid">
      <div class="empty"><div class="empty-icon">⏳</div><div class="empty-msg">Loading…</div></div>
    </div>
  </div>
  <div id="panel-clips" style="display:none">
    <div class="clips-list" id="clipsList">
      <div class="empty"><div class="empty-icon">⏳</div><div class="empty-msg">Loading…</div></div>
    </div>
  </div>
</div>

<!-- Image picker modal -->
<div class="modal-overlay" id="imgPickerModal" onclick="if(event.target===this)closeImgPicker()">
  <div class="modal">
    <div class="modal-header">
      <span class="modal-title" id="pickerTitle">Choose Player Image</span>
      <button class="modal-close" onclick="closeImgPicker()">✕</button>
    </div>
    <div class="modal-toolbar">
      <input class="modal-search" id="imgSearch" type="text" placeholder="Search player name…" oninput="filterImages(this.value)">
      <label class="upload-zone" title="Upload a new image to the library">
        <input type="file" id="uploadInput" accept="image/*" style="display:none" onchange="handleUpload(this)">
        ↑ Upload
      </label>
    </div>
    <div class="modal-body" id="imgGrid"></div>
    <div class="modal-footer">
      <span class="selected-label" id="selectedLabel">None selected</span>
      <div style="display:flex;gap:8px">
        <button class="btn btn-ghost" onclick="closeImgPicker()">Cancel</button>
        <button class="btn btn-gold" onclick="confirmImagePick()">Use Image</button>
      </div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const SAFE_NAME = {json.dumps(safe_name)};
let studioData   = null;
let allImages    = [];
let compSchemas  = {{}};
let compatMap    = {{}};
let typeMap      = {{}};
let activeFilter = 'all';
let pickerCtx    = null; // {{renderIdx, field, playerIdx}}
let _selectedSlug = null; // display label only
let _selectedImagePath = null; // full path passed to Remotion (e.g. "players/luis")

// ── Init ─────────────────────────────────────────────────────────────────────
async function init() {{
  const [dataRes, imgRes, schemaRes] = await Promise.all([
    fetch(`/studio-data/${{SAFE_NAME}}`).then(r=>r.json()),
    fetch('/available-images').then(r=>r.json()),
    fetch('/composition-schemas').then(r=>r.json()),
  ]);
  studioData  = dataRes;
  allImages   = imgRes.images || [];
  compSchemas = schemaRes.schemas    || {{}};
  compatMap   = schemaRes.compatible || {{}};
  typeMap     = schemaRes.type_map   || {{}};

  document.getElementById('projectTitle').textContent = studioData.title || SAFE_NAME;
  buildFilterPills();
  renderGrid();
  renderClips();
  updateApprovedCount();
  renderMissingImagesBanner();
}}

function renderMissingImagesBanner() {{
  const missing = studioData && studioData.missing_images || [];
  const existing = document.getElementById('missing-images-banner');
  if (existing) existing.remove();
  if (!missing.length) return;
  const banner = document.createElement('div');
  banner.id = 'missing-images-banner';
  banner.style.cssText = 'background:#fff3cd;border:1.5px solid #f0ad4e;border-radius:8px;padding:14px 20px;margin:0 0 18px 0;font-size:0.85rem;line-height:1.6;';
  const rows = missing.map(m => `<li style="margin:2px 0;font-family:monospace;font-size:0.8rem">${{m.tag}} → <b>${{m.prop}}</b></li>`).join('');
  banner.innerHTML = `<b style="color:#856404">⚠ ${{missing.length}} render${{missing.length>1?'s':''}} missing player images</b> — these compositions rendered without a photo.
    Place images in <code>remotiontest/public/players/</code>, then re-render from this page before exporting.
    <ul style="margin:8px 0 0 16px;padding:0">${{rows}}</ul>`;
  const grid = document.getElementById('renderGrid');
  grid.parentNode.insertBefore(banner, grid);
}}

// ── Tabs ──────────────────────────────────────────────────────────────────────
function switchTab(name) {{
  ['renders','clips'].forEach(t => {{
    document.getElementById('tab-'+t).classList.toggle('active', t===name);
    document.getElementById('panel-'+t).style.display = t===name ? '' : 'none';
  }});
}}

// ── Filter pills ──────────────────────────────────────────────────────────────
function buildFilterPills() {{
  const types = [...new Set((studioData.renders||[]).map(r=>r.type))].filter(Boolean);
  const bar = document.querySelector('.toolbar');
  // Insert pills after the first separator
  const sep = bar.querySelector('.toolbar-sep');
  types.forEach(t => {{
    const btn = document.createElement('button');
    btn.className = 'filter-pill';
    btn.id = 'pill-'+t;
    btn.textContent = t.replace(/_/g,' ');
    btn.onclick = () => setFilter(t);
    bar.insertBefore(btn, sep);
  }});
}}

function setFilter(type) {{
  activeFilter = type;
  document.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
  const target = document.getElementById('pill-'+type);
  if (target) target.classList.add('active');
  renderGrid();
}}

// ── Sort ──────────────────────────────────────────────────────────────────────
function applySort() {{ renderGrid(); }}
function getSorted(renders) {{
  const mode = document.getElementById('sortSelect').value;
  const r = [...renders];
  if (mode === 'type')     r.sort((a,b) => a.type.localeCompare(b.type));
  if (mode === 'approved') r.sort((a,b) => (b.approved===false?0:1) - (a.approved===false?0:1));
  return r;
}}

// ── Bulk ops ──────────────────────────────────────────────────────────────────
async function bulkApprove(val) {{
  const targets = (studioData.renders||[]).filter(r =>
    activeFilter === 'all' || r.type === activeFilter);
  for (const r of targets) {{
    r.approved = val;
    await fetch('/studio-approve', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{safe_name:SAFE_NAME, filename:r.filename, approved:val}})
    }});
  }}
  renderGrid();
  updateApprovedCount();
  showToast(`${{val ? 'Approved' : 'Rejected'}} ${{targets.length}} renders`, val?'success':'');
}}

// ── Render grid ───────────────────────────────────────────────────────────────
function renderGrid() {{
  const grid = document.getElementById('renderGrid');
  let renders = studioData && studioData.renders || [];
  if (activeFilter !== 'all') renders = renders.filter(r => r.type === activeFilter);
  renders = getSorted(renders);

  if (!renders.length) {{
    grid.innerHTML = '<div class="empty"><div class="empty-icon">🎬</div><div class="empty-msg">No renders yet. Run the pipeline first.</div></div>';
    return;
  }}
  // Preserve open edit panels across re-render
  const openPanels = new Set();
  document.querySelectorAll('.edit-panel.open').forEach(el => openPanels.add(el.id));
  const openNotes = new Set();
  document.querySelectorAll('.card-note-row.open').forEach(el => openNotes.add(el.id));

  // Map filename → original index for stable IDs
  grid.innerHTML = renders.map((r, visIdx) => {{
    const origIdx = studioData.renders.indexOf(r);
    return buildCard(r, origIdx, openPanels, openNotes);
  }}).join('');
}}

function buildCard(r, i, openPanels, openNotes) {{
  const approved  = r.approved !== false;
  const typeLabel = r.type.replace(/_/g,' ').toUpperCase();
  const comp      = r.composition || typeMap[r.type] || '';
  const alts      = (compatMap[r.type] || []).filter(c => c !== comp);
  const tag       = r.tag_text || r.tag || r.filename;
  const ctx       = r.context || '';
  const panelId   = `epanel-${{i}}`;
  const noteRowId = `noterow-${{i}}`;
  const isPanelOpen = openPanels && openPanels.has(panelId);
  const isNoteOpen  = openNotes  && openNotes.has(noteRowId);

  // Composition options
  const allComps = [comp, ...alts].filter(Boolean);
  const compOpts = allComps.map(c =>
    `<option value="${{c}}" ${{c===comp?'selected':''}}>${{c}}</option>`).join('');
  const compSel  = comp
    ? `<select class="card-comp-select" id="compsel-${{i}}" onchange="onCompChange(${{i}},this.value)" title="Change Remotion composition">${{compOpts}}</select>`
    : '';

  return `
  <div class="render-card ${{approved?'approved':'rejected'}}" id="card-${{i}}">
    <div class="card-video" onclick="togglePlay(${{i}})">
      <video id="vid-${{i}}" src="/video/${{SAFE_NAME}}/${{r.filename}}" preload="none"
             playsinline style="width:100%;height:100%"></video>
      <div class="play-overlay"><div class="play-icon">▶</div></div>
    </div>
    <div class="card-head">
      <span class="card-type">${{typeLabel}}</span>
      ${{compSel}}
      <span class="card-size">${{r.size_kb}}kb</span>
    </div>
    ${{tag ? `<div class="card-tag-row"><div class="card-tag">${{esc(tag)}}</div></div>` : ''}}
    ${{ctx ? `<div class="card-context" id="ctx-${{i}}" onclick="this.classList.toggle('expanded')"
               title="Click to expand script context">${{esc(ctx)}}</div>` : ''}}
    <div class="card-actions">
      <button class="approve-btn ${{approved?'approved':'rejected'}}"
              id="appbtn-${{i}}" onclick="toggleApprove(${{i}})">
        ${{approved ? '✓ Approved' : '✗ Rejected'}}
      </button>
      <button class="icon-btn ${{isPanelOpen?'active':''}}" id="editbtn-${{i}}"
              onclick="toggleEdit(${{i}})" title="Edit props">✏</button>
      <button class="icon-btn ${{isNoteOpen?'active':''}}" id="notebtn-${{i}}"
              onclick="toggleNote(${{i}})" title="Add note">📝</button>
    </div>
    <div class="card-note-row ${{isNoteOpen?'open':''}}" id="${{noteRowId}}">
      <textarea class="card-note" rows="2" placeholder="Note (why rejected, what to change…)"
        onblur="saveNote(${{i}},this.value)">${{esc(r.note||'')}}</textarea>
    </div>
    <div class="edit-panel ${{isPanelOpen?'open':''}}" id="${{panelId}}">
      ${{buildEditPanel(r, i)}}
    </div>
  </div>`;
}}

// ── Video playback ────────────────────────────────────────────────────────────
function togglePlay(i) {{
  const v = document.getElementById('vid-'+i);
  if (v.paused) {{ v.load(); v.play(); }} else {{ v.pause(); }}
}}

// ── Edit panel ────────────────────────────────────────────────────────────────
function buildEditPanel(r, i) {{
  const comp   = r.composition || typeMap[r.type] || '';
  const schema = compSchemas[comp] || [];
  const hasSchema = schema.length > 0;

  const formHtml = hasSchema ? buildPropsForm(r, i, schema, comp) : '';
  const jsonHtml = buildJsonEditor(r, i);

  if (!hasSchema) {{
    return `<div class="edit-tabs">
      <div class="edit-tab active">JSON</div>
    </div><div class="edit-body">${{jsonHtml}}</div>`;
  }}

  return `<div class="edit-tabs">
    <div class="edit-tab active" id="etab-form-${{i}}" onclick="switchEditTab(${{i}},'form')">Fields</div>
    <div class="edit-tab" id="etab-json-${{i}}" onclick="switchEditTab(${{i}},'json')">JSON</div>
  </div>
  <div class="edit-body" id="ebody-form-${{i}}">${{formHtml}}</div>
  <div class="edit-body" id="ebody-json-${{i}}" style="display:none">${{jsonHtml}}</div>`;
}}

function switchEditTab(i, tab) {{
  ['form','json'].forEach(t => {{
    const tabEl  = document.getElementById(`etab-${{t}}-${{i}}`);
    const bodyEl = document.getElementById(`ebody-${{t}}-${{i}}`);
    if (!tabEl || !bodyEl) return;
    tabEl.classList.toggle('active', t===tab);
    bodyEl.style.display = t===tab ? '' : 'none';
  }});
}}

function buildPropsForm(r, i, schema, comp) {{
  let html = '';
  for (const field of schema) {{
    if (field.type === 'player_list') {{
      html += buildPlayerList(r, i, field);
    }} else {{
      html += buildField(r.props, i, field, '');
    }}
  }}
  html += `<div class="rerender-row">
    <button class="rerender-btn" id="rerbtn-${{i}}" onclick="doRerender(${{i}})">↻ Re-render</button>
  </div>
  <div class="rerender-status" id="rerstatus-${{i}}"></div>`;
  return html;
}}

function buildField(props, i, field, prefix) {{
  const val    = props ? (props[field.key] ?? '') : '';
  const inputId = `prop-${{i}}-${{prefix}}${{field.key}}`;

  if (field.type === 'text') {{
    return `<div class="prop-field">
      <div class="prop-label">${{field.label}}</div>
      <input class="prop-input" id="${{inputId}}" type="text" value="${{esc(String(val))}}"
             oninput="setProp(${{i}},'${{field.key}}',this.value,'${{prefix}}')">
    </div>`;
  }}
  if (field.type === 'color') {{
    return `<div class="prop-field">
      <div class="prop-label">${{field.label}}</div>
      <div class="prop-color-row">
        <input type="color" id="${{inputId}}" value="${{val||'#C9A84C'}}"
               oninput="setProp(${{i}},'${{field.key}}',this.value,'${{prefix}}');this.nextElementSibling.style.background=this.value">
        <div class="prop-color-preview" style="background:${{val||'#C9A84C'}}"
             onclick="document.getElementById('${{inputId}}').click()"></div>
        <input class="prop-input" style="width:90px" type="text" value="${{val||''}}"
               oninput="setProp(${{i}},'${{field.key}}',this.value,'${{prefix}}')">
      </div>
    </div>`;
  }}
  if (field.type === 'boolean') {{
    return `<div class="prop-field">
      <div class="prop-boolean">
        <input type="checkbox" id="${{inputId}}" ${{val?'checked':''}}
               onchange="setProp(${{i}},'${{field.key}}',this.checked,'${{prefix}}')">
        <label for="${{inputId}}" class="prop-label" style="margin:0">${{field.label}}</label>
      </div>
    </div>`;
  }}
  if (field.type === 'image') {{
    return `<div class="prop-field">
      <div class="prop-label">${{field.label}}</div>
      <div class="slot-img-row">
        ${{val
          ? `<img class="slot-thumb" id="thumb-${{i}}-${{field.key}}"
               src="/player-thumb/${{encodeURIComponent(val+(val.includes('.')?'':'.png'))}}"
               onerror="this.style.display='none'">`
          : `<div class="slot-thumb-empty" id="thumb-${{i}}-${{field.key}}">?</div>`
        }}
        <div class="slot-img-info">
          <div class="slot-img-slug" id="imgslug-${{i}}-${{field.key}}">${{val||'(none)'}}</div>
        </div>
        <button class="swap-btn" onclick="openImgPicker(${{i}},'${{field.key}}',null)">Swap</button>
      </div>
    </div>`;
  }}
  return '';
}}

function buildPlayerList(r, i, field) {{
  const players = (r.props && r.props.players) || [
    {{name:'Player 1',image:'',club:'',stat:'',statLabel:''}},
    {{name:'Player 2',image:'',club:'',stat:'',statLabel:''}},
    {{name:'Player 3',image:'',club:'',stat:'',statLabel:''}},
  ];
  let html = `<div class="prop-label" style="margin-bottom:8px">${{field.label}}</div>`;
  players.forEach((p, pi) => {{
    const slug = p.image || '';
    html += `<div class="player-slot" id="pslot-${{i}}-${{pi}}">
      <div class="player-slot-head">
        <span class="player-slot-num">Player ${{pi+1}}</span>
        <span class="player-slot-name" id="pname-${{i}}-${{pi}}">${{esc(p.name||'')}}</span>
      </div>
      <div class="slot-img-row">
        ${{slug
          ? `<img class="slot-thumb" id="pthumb-${{i}}-${{pi}}"
               src="/player-thumb/${{encodeURIComponent(slug+(slug.includes('.')?'':'.png'))}}"
               onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
          : ''
        }}
        <div class="slot-thumb-empty" id="pthumb-empty-${{i}}-${{pi}}"
             style="display:${{slug?'none':'flex'}}">?</div>
        <div class="slot-img-info">
          <div class="slot-img-slug" id="pslug-${{i}}-${{pi}}">${{slug||'(none)'}}</div>
        </div>
        <button class="swap-btn" onclick="openImgPicker(${{i}},null,${{pi}})">Swap</button>
      </div>`;
    // Sub-fields: name, club, stat, statLabel, clubColor
    (field.subfields||[]).filter(sf => sf.key !== 'image').forEach(sf => {{
      html += buildField(p, i, sf, `players.${{pi}}.`);
    }});
    html += `</div>`;
  }});
  return html;
}}

function buildJsonEditor(r, i) {{
  const json = JSON.stringify(r.props||{{}}, null, 2);
  return `<div class="prop-field">
    <div class="prop-label">Props JSON</div>
    <textarea class="json-editor" id="jsoneditor-${{i}}" oninput="onJsonEdit(${{i}})">${{esc(json)}}</textarea>
    <div class="json-error" id="jsonerr-${{i}}"></div>
  </div>
  <div class="rerender-row">
    <button class="rerender-btn" id="rerbtn-json-${{i}}" onclick="doRerenderJson(${{i}})">↻ Re-render with JSON</button>
  </div>
  <div class="rerender-status" id="rerstatus-json-${{i}}"></div>`;
}}

// ── Prop mutation helpers ─────────────────────────────────────────────────────
function setProp(i, key, value, prefix) {{
  const r = studioData.renders[i];
  if (!r.props) r.props = {{}};
  if (!prefix) {{
    r.props[key] = value;
  }} else {{
    // prefix like "players.0."
    const parts = prefix.split('.').filter(Boolean);
    let obj = r.props;
    for (let j = 0; j < parts.length - 1; j++) {{
      const k = isNaN(parts[j]) ? parts[j] : parseInt(parts[j]);
      if (obj[k] === undefined) obj[k] = {{}};
      obj = obj[k];
    }}
    const last = parts[parts.length-1];
    const idx  = isNaN(last) ? last : parseInt(last);
    if (!obj[idx]) obj[idx] = {{}};
    obj[idx][key] = value;
  }}
  // Sync JSON editor if visible
  const je = document.getElementById(`jsoneditor-${{i}}`);
  if (je) je.value = JSON.stringify(r.props, null, 2);
}}

function onJsonEdit(i) {{
  const ta  = document.getElementById(`jsoneditor-${{i}}`);
  const err = document.getElementById(`jsonerr-${{i}}`);
  try {{
    JSON.parse(ta.value);
    err.textContent = '';
  }} catch(e) {{
    err.textContent = e.message;
  }}
}}

// ── Toggle helpers ────────────────────────────────────────────────────────────
function toggleEdit(i) {{
  const panel  = document.getElementById(`epanel-${{i}}`);
  const btn    = document.getElementById(`editbtn-${{i}}`);
  const isOpen = panel.classList.toggle('open');
  btn.classList.toggle('active', isOpen);
}}

function toggleNote(i) {{
  const row = document.getElementById(`noterow-${{i}}`);
  const btn = document.getElementById(`notebtn-${{i}}`);
  const isOpen = row.classList.toggle('open');
  btn.classList.toggle('active', isOpen);
  if (isOpen) row.querySelector('textarea').focus();
}}

async function saveNote(i, text) {{
  const r = studioData.renders[i];
  r.note = text;
  await fetch('/studio-note', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{safe_name:SAFE_NAME, filename:r.filename, note:text}})
  }});
}}

// ── Approve/reject ────────────────────────────────────────────────────────────
async function toggleApprove(i) {{
  const r = studioData.renders[i];
  r.approved = !r.approved;
  const card = document.getElementById(`card-${{i}}`);
  const btn  = document.getElementById(`appbtn-${{i}}`);
  card.className = 'render-card ' + (r.approved?'approved':'rejected');
  btn.className  = 'approve-btn '  + (r.approved?'approved':'rejected');
  btn.textContent = r.approved ? '✓ Approved' : '✗ Rejected';
  updateApprovedCount();
  await fetch('/studio-approve', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{safe_name:SAFE_NAME, filename:r.filename, approved:r.approved}})
  }});
}}

function updateApprovedCount() {{
  const renders  = studioData && studioData.renders || [];
  const approved = renders.filter(r => r.approved !== false).length;
  document.getElementById('approvedCount').textContent = `${{approved}} / ${{renders.length}} approved`;
}}

// ── Composition change ────────────────────────────────────────────────────────
function onCompChange(i, newComp) {{
  const r = studioData.renders[i];
  r.composition = newComp;
  // Rebuild edit panel
  const panel = document.getElementById(`epanel-${{i}}`);
  if (panel.classList.contains('open')) {{
    panel.innerHTML = buildEditPanel(r, i);
  }}
  showToast(`Template → ${{newComp}}. Edit props then Re-render.`, 'success');
}}

// ── Re-render ─────────────────────────────────────────────────────────────────
async function doRerender(i, jsonMode) {{
  const r    = studioData.renders[i];
  const btnId = jsonMode ? `rerbtn-json-${{i}}` : `rerbtn-${{i}}`;
  const statId = jsonMode ? `rerstatus-json-${{i}}` : `rerstatus-${{i}}`;
  const btn    = document.getElementById(btnId);
  const status = document.getElementById(statId);
  if (!btn) return;

  let props = r.props || {{}};
  if (jsonMode) {{
    const ta = document.getElementById(`jsoneditor-${{i}}`);
    try {{ props = JSON.parse(ta.value); }}
    catch(e) {{ status.textContent='Invalid JSON: '+e.message; status.style.color='var(--red)'; return; }}
  }}

  btn.disabled = true;
  btn.textContent = '↻ Rendering…';
  status.textContent = 'Queued…';
  status.style.color = 'var(--gold)';

  const comp = r.composition || typeMap[r.type] || '';
  const res = await fetch('/re-render', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      safe_name: SAFE_NAME, filename: r.filename,
      type: r.type, composition: comp, props
    }})
  }}).then(r=>r.json());

  if (res.error) {{
    status.textContent = res.error;
    status.style.color = 'var(--red)';
    btn.disabled = false; btn.textContent = '↻ Re-render';
    return;
  }}

  const poll = setInterval(async () => {{
    const s = await fetch(`/re-render-status/${{SAFE_NAME}}/${{r.filename}}`).then(r=>r.json());
    if (s.status === 'done') {{
      clearInterval(poll);
      status.textContent = '✓ Done';
      status.style.color = 'var(--green)';
      btn.disabled = false; btn.textContent = '↻ Re-render';
      const vid = document.getElementById(`vid-${{i}}`);
      if (vid) {{ vid.src = `/video/${{SAFE_NAME}}/${{r.filename}}?t=${{Date.now()}}`; }}
      showToast('Re-render complete', 'success');
    }} else if (s.status === 'failed') {{
      clearInterval(poll);
      status.textContent = 'Failed' + (s.error ? ': '+s.error : '');
      status.style.color = 'var(--red)';
      btn.disabled = false; btn.textContent = '↻ Re-render';
    }}
  }}, 2000);
}}

function doRerenderJson(i) {{ doRerender(i, true); }}

// ── Image picker ──────────────────────────────────────────────────────────────
function openImgPicker(renderIdx, fieldKey, playerIdx) {{
  pickerCtx    = {{renderIdx, fieldKey, playerIdx}};
  _selectedSlug = _selectedImagePath = null;
  document.getElementById('imgSearch').value = '';
  document.getElementById('selectedLabel').textContent = 'None selected';
  populateImgGrid(allImages);
  document.getElementById('imgPickerModal').classList.add('open');
}}

function closeImgPicker() {{
  document.getElementById('imgPickerModal').classList.remove('open');
  pickerCtx = _selectedSlug = _selectedImagePath = null;
}}

function filterImages(q) {{
  q = q.toLowerCase();
  populateImgGrid(q ? allImages.filter(img =>
    img.slug.includes(q) || img.name.toLowerCase().includes(q)) : allImages);
}}

function populateImgGrid(images) {{
  const grid = document.getElementById('imgGrid');
  if (!images.length) {{
    grid.innerHTML = '<div style="grid-column:1/-1;color:#333;text-align:center;padding:30px">No images found</div>';
    return;
  }}
  grid.innerHTML = images.map(img => `
    <div class="img-option" onclick="selectImg(this,'${{img.slug}}','${{img.file}}')" data-slug="${{img.slug}}">
      <img src="/player-thumb/${{encodeURIComponent(img.file)}}"
           onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22100%22 height=%22130%22><rect fill=%22%230f0f0f%22 width=%22100%22 height=%22130%22/><text fill=%22%23333%22 x=%2250%22 y=%2275%22 text-anchor=%22middle%22 font-size=%2228%22>?</text></svg>'"
           loading="lazy">
      <div class="img-lbl">${{img.name}}</div>
    </div>`).join('');
}}

function selectImg(el, slug, file) {{
  document.querySelectorAll('.img-option').forEach(e => e.classList.remove('selected'));
  el.classList.add('selected');
  _selectedSlug = slug;
  // Strip extension — SmartImg tries all extensions automatically
  _selectedImagePath = file ? file.replace(/[.][^./]+$/, '') : slug;
  document.getElementById('selectedLabel').textContent = _selectedImagePath;
}}

function confirmImagePick() {{
  if (!_selectedImagePath || !pickerCtx) {{ closeImgPicker(); return; }}
  const {{renderIdx, fieldKey, playerIdx}} = pickerCtx;
  const r = studioData.renders[renderIdx];
  if (!r.props) r.props = {{}};

  // Thumb preview uses the original file path (with extension) from allImages
  const imgData = allImages.find(i => i.slug === _selectedSlug);
  const thumbSrc = imgData ? `/player-thumb/${{encodeURIComponent(imgData.file)}}` : '';

  if (playerIdx !== null && playerIdx !== undefined) {{
    // Player list slot
    if (!r.props.players) r.props.players = [{{name:'Player 1'}},{{name:'Player 2'}},{{name:'Player 3'}}];
    if (!r.props.players[playerIdx]) r.props.players[playerIdx] = {{}};
    r.props.players[playerIdx].image = _selectedImagePath;
    const slugEl  = document.getElementById(`pslug-${{renderIdx}}-${{playerIdx}}`);
    const thumbEl = document.getElementById(`pthumb-${{renderIdx}}-${{playerIdx}}`);
    const emptyEl = document.getElementById(`pthumb-empty-${{renderIdx}}-${{playerIdx}}`);
    if (slugEl) slugEl.textContent = _selectedImagePath;
    if (thumbEl && thumbSrc) {{ thumbEl.src = thumbSrc; thumbEl.style.display=''; }}
    if (emptyEl) emptyEl.style.display = 'none';
  }} else if (fieldKey) {{
    // Single image field
    r.props[fieldKey] = _selectedImagePath;
    const slugEl  = document.getElementById(`imgslug-${{renderIdx}}-${{fieldKey}}`);
    const thumbEl = document.getElementById(`thumb-${{renderIdx}}-${{fieldKey}}`);
    if (slugEl) slugEl.textContent = _selectedImagePath;
    if (thumbEl && thumbSrc) {{ thumbEl.src = thumbSrc; thumbEl.style.display=''; }}
  }}

  // Sync JSON editor
  const je = document.getElementById(`jsoneditor-${{renderIdx}}`);
  if (je) je.value = JSON.stringify(r.props, null, 2);

  closeImgPicker();
  showToast('Image selected — click ↻ Re-render to apply', 'success');
}}

// ── Image upload ──────────────────────────────────────────────────────────────
async function handleUpload(input) {{
  const file = input.files[0];
  if (!file) return;
  const name = prompt('Slug name for this image (e.g. luis_suarez):', file.name.replace(/[.][^.]+$/, '').replace(/[ ]+/g, '_').toLowerCase());
  if (!name) return;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('name', name);
  const res = await fetch('/upload-image', {{method:'POST', body:fd}}).then(r=>r.json());
  if (res.ok) {{
    allImages.push({{slug:res.slug, file:res.file, name:res.slug.replace(/_/g,' ').replace(/\b./g,c=>c.toUpperCase())}});
    filterImages(document.getElementById('imgSearch').value);
    showToast(`Image '${{res.slug}}' added to library`, 'success');
  }} else {{
    showToast('Upload failed: '+(res.error||'unknown'), 'error');
  }}
  input.value = '';
}}

// ── Export ────────────────────────────────────────────────────────────────────
async function doExport() {{
  const btn = event.target;
  btn.textContent = 'Exporting…'; btn.disabled = true;
  const res = await fetch('/studio-export', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{safe_name:SAFE_NAME}})
  }}).then(r=>r.json());
  btn.textContent = 'Export Approved ↓'; btn.disabled = false;
  if (res.ok) showToast(`Exported ${{res.count}} render${{res.count!==1?'s':''}} → ${{res.path}}`, 'success');
  else showToast('Export failed: '+(res.error||'?'), 'error');
}}

async function rerunFailedRenders() {{
  const btn = document.getElementById('studioRerunBtn') || document.getElementById('rerunFailedBtn');
  if (btn) {{ btn.textContent = '⏳ Running…'; btn.disabled = true; }}
  try {{
    const res = await fetch('/rerun-failed', {{
      method: 'POST', headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{safe_name: SAFE_NAME}})
    }}).then(r=>r.json());
    if (res.error) showToast('Error: ' + res.error, 'error');
    else if (res.count === 0) showToast('No failed renders found — everything looks good.', 'info');
    else showToast(`Re-rendering ${{res.count}} failed graphic${{res.count!==1?'s':''}} in background…`, 'success');
  }} catch(e) {{
    showToast('Re-run failed: ' + e.message, 'error');
  }} finally {{
    if (btn) {{ btn.textContent = '↻ Re-run Failed'; btn.disabled = false; }}
  }}
}}

// ── Clips panel ───────────────────────────────────────────────────────────────
function renderClips() {{
  const clips = studioData && studioData.clips || [];
  const list  = document.getElementById('clipsList');
  if (!clips.length) {{
    list.innerHTML = '<div class="empty"><div class="empty-icon">🎬</div><div class="empty-msg">No clips required (run pipeline first).</div></div>';
    return;
  }}
  list.innerHTML = clips.map(c => {{
    const desc = c.description || '';
    const yt   = c.youtube_search || '';
    return `<div class="clip-card">
      <span class="clip-id">${{c.id}}</span>
      <div class="clip-info">
        <div class="clip-desc">${{esc(desc)}}</div>
        <div class="clip-meta">${{c.act||''}} · ${{c.duration||0}}s · ${{c.label||''}}</div>
        ${{yt ? `<a href="${{yt}}" target="_blank" class="yt-link">Search YouTube ↗</a>` : ''}}
      </div>
      <div><span class="status-needed">⏳ Needed</span></div>
    </div>`;
  }}).join('');
}}

// ── Utilities ─────────────────────────────────────────────────────────────────
function esc(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

let _toastTimer;
function showToast(msg, type) {{
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show ' + (type||'');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.className='toast', 3500);
}}

init();
</script>
</body>
</html>"""
    return html


# ── NLE Timeline ─────────────────────────────────────────────────────────────


def _tag_to_composition(tag_key: str) -> str:
    TK = tag_key.upper().strip()
    if "CAREER TIMELINE" in TK or TK == "TRANSFER": return "CareerTimeline"
    if "PLAYER TRIO"     in TK: return "PlayerTrio"
    if "PLAYER STATS"    in TK: return "PlayerStats"
    if "PLAYER RADAR"    in TK: return "AttackingRadar"
    if "DISCIPLINARY"    in TK: return "DisciplinaryRecord"
    if "HERO INTRO"   in TK: return "HeroIntro"
    if "HERO BIG STAT"in TK or "HERO BIGSTAT" in TK: return "HeroBigStat"
    if "HERO STAT BARS" in TK or "HERO BARS" in TK: return "HeroStatBars"
    if "HERO FORM"    in TK: return "HeroFormRun"
    if "HERO LEAGUE"  in TK or "HERO GRAPH" in TK: return "HeroLeagueGraph"
    if "HERO TACTICAL"in TK: return "HeroTactical"
    if "HERO TRANSFER"in TK: return "HeroTransferRecord"
    if "HERO QUOTE"   in TK: return "HeroQuote"
    if "HERO CHAPTER" in TK: return "HeroChapterWord"
    if "HERO CLIP"    in TK or "CLIP SINGLE" in TK: return "HeroClipSingle"
    if "CLIP COMPARE"    in TK or "HERO CONCEPT" in TK: return "HeroClipCompare"
    if "HERO SCATTER" in TK: return "HeroScatterPlot"
    if "TEAM LINEUP"     in TK: return "TeamLineup"
    if "STANDINGS"       in TK: return "PremierLeagueTable"
    if "TOP SCORERS"     in TK: return "TopScorersTable"
    if "TOP ASSISTS"     in TK: return "TopScorersTable"
    if "MATCH RESULT"    in TK: return "MatchResult"
    if "QUOTE CARD"      in TK: return "QuoteCard"
    if "TROPHY"          in TK: return "TrophyGraphic"
    if "SEASON COMPARISON" in TK: return "SeasonComparison"
    return ""


def _find_render_for_tag(full_tag: str, tag_text: str, manifest_by_tag: dict, renders_dir: Path):
    """Match a script tag to its rendered MP4 filename."""
    # 1. Exact match via manifest
    if full_tag in manifest_by_tag:
        return manifest_by_tag[full_tag]["filename"]
    if tag_text in manifest_by_tag:
        return manifest_by_tag[tag_text]["filename"]
    # 2. Fuzzy match against renders dir
    if not renders_dir.exists():
        return None
    import re as _re
    slug = _re.sub(r"[^\w]", "_", tag_text.lower())[:35].strip("_")
    # derive prefix from tag type
    full_upper = full_tag.upper()
    prefix_map = [
        ("HERO INTRO",    "hero_intro"),
        ("HERO BIG STAT", "hero_bigstat"),
        ("HERO STAT BARS","hero_bars"),
        ("HERO FORM RUN", "hero_form"),
        ("HERO FORM",     "hero_form"),
        ("HERO LEAGUE",   "hero_graph"),
        ("HERO TACTICAL", "hero_tactical"),
        ("HERO TRANSFER", "hero_transfer"),
        ("HERO QUOTE",    "hero_quote"),
        ("HERO CHAPTER",  "hero_chapter"),
        ("HERO CONCEPT",  "hero_concept"),
        ("HERO SCATTER",  "hero_scatter"),
        ("PLAYER TRIO",      "player_trio"),
        ("PLAYER RADAR",     "radar"),
        ("CAREER TIMELINE",  "timeline"),
        ("DISCIPLINARY",     "disciplinary"),
        ("TEAM LINEUP",      "lineup"),
        ("PLAYER STATS",     "player_stats"),
        ("TOP SCORERS",      "top_scorers"),
        ("TOP ASSISTS",      "top_assists"),
        ("MATCH RESULT",     "match"),
        ("STANDINGS",        "pl_table"),
    ]
    prefix = ""
    for key, val in prefix_map:
        if key in full_upper:
            prefix = val
            break
    if not prefix:
        return None
    # Find best matching file
    candidates = [f for f in renders_dir.iterdir()
                  if f.suffix == ".mp4" and f.name.startswith(prefix)]
    if not candidates:
        return None
    # Score by how much of the slug appears in the filename
    def score(f):
        fn = f.name.lower()
        return sum(1 for ch in slug[:20] if ch in fn)
    return max(candidates, key=score).name


def _parse_script_timeline(out_dir: Path) -> list:
    """Parse script_draft.md into ordered timeline items for the editor."""
    import re as _re
    script_file = out_dir / "script_draft.md"
    if not script_file.exists():
        return []

    script    = script_file.read_text(errors="replace")
    manifest  = _load_manifest(out_dir)
    state     = _load_studio_state(out_dir)
    renders_dir = out_dir / "renders"

    # Build manifest lookup: tag -> entry, tag_text -> entry
    manifest_by_tag: dict = {}
    for entry in manifest:
        if entry.get("tag"):
            manifest_by_tag[entry["tag"]]      = entry
        if entry.get("tag_text"):
            manifest_by_tag[entry["tag_text"]] = entry

    TAG_RE  = _re.compile(r'^\[([A-Z][A-Z _]+):\s*([^\]]+)\]', _re.IGNORECASE)
    ACT_RE  = _re.compile(r'^###\s+(.+)$')
    CLIP_DUR_RE = _re.compile(r',\s*(\d+)s', _re.IGNORECASE)

    items       = []
    position    = 0
    current_act = "COLD OPEN"
    narr_buf    = []
    narr_start  = position
    item_id     = 0

    def flush_narration():
        nonlocal position, item_id, narr_start, narr_buf
        text = " ".join(narr_buf).strip()
        if not text:
            narr_buf = []
            return
        words = len(text.split())
        items.append({
            "id":          f"n{item_id}",
            "type":        "narration",
            "track":       "narration",
            "act":         current_act,
            "tag":         "",
            "tag_key":     "",
            "tag_text":    "",
            "composition": "",
            "filename":    None,
            "rendered":    False,
            "approved":    True,
            "note":        "",
            "content":     text[:200],
            "full_text":   text,
            "words":       words,
            "duration":    max(4, words // 3),
            "props":       {},
            "position":    position,
        })
        item_id  += 1
        position += 1
        narr_buf  = []

    for line in script.splitlines():
        stripped = line.strip()

        # Act header
        act_m = ACT_RE.match(stripped)
        if act_m:
            flush_narration()
            current_act = act_m.group(1).strip()
            continue

        # Horizontal rule - act separator, ignore
        if stripped.startswith("---"):
            continue

        # Tag line
        tag_m = TAG_RE.match(stripped)
        if tag_m:
            flush_narration()
            tag_key  = tag_m.group(1).strip().upper()
            tag_text = tag_m.group(2).strip()
            full_tag = f"[{tag_key}: {tag_text}]"

            # Track assignment
            if tag_key == "TRANSITION":
                track = "transitions"
            elif "CLIP SINGLE" in tag_key or "CLIP COMPARE" in tag_key:
                track = "clips"
            else:
                track = "graphics"

            # Duration from clip tag
            dur = 8
            if track == "clips":
                dur_m = CLIP_DUR_RE.search(tag_text)
                if dur_m:
                    dur = int(dur_m.group(1))

            filename = None
            rendered = False
            preview_filename = ""
            preview_rendered = False
            props    = {}
            comp     = _tag_to_composition(tag_key)
            entry    = None

            if track != "transitions":
                filename = _find_render_for_tag(full_tag, tag_text, manifest_by_tag, renders_dir)
                rendered = filename is not None
                if filename:
                    entry = manifest_by_tag.get(full_tag) or manifest_by_tag.get(tag_text) or {}
                    props = entry.get("props", {})
                else:
                    # Option A: no mp4 yet — try matching manifest by tag for PNG preview
                    entry = manifest_by_tag.get(full_tag) or manifest_by_tag.get(tag_text) or {}
                    if entry:
                        props    = entry.get("props", {})
                        filename = entry.get("filename")
                if entry:
                    preview_filename = entry.get("preview_filename", "") or ""
                    preview_rendered = bool(entry.get("preview_rendered"))
                    if preview_filename and not (renders_dir / "previews" / preview_filename).exists():
                        preview_rendered = False

            approved = state.get(filename or "", {}).get("approved", True)
            note     = state.get(filename or "", {}).get("note", "")

            items.append({
                "id":          f"i{item_id}",
                "type":        "transition" if track == "transitions" else ("clip" if track == "clips" else "graphic"),
                "track":       track,
                "act":         current_act,
                "tag":         full_tag,
                "tag_key":     tag_key,
                "tag_text":    tag_text,
                "composition": comp,
                "filename":    filename,
                "rendered":    rendered,
                "preview_filename": preview_filename,
                "preview_rendered": preview_rendered,
                "approved":    approved,
                "note":        note,
                "content":     tag_text[:80],
                "full_text":   tag_text,
                "words":       0,
                "duration":    dur,
                "props":       props,
                "position":    position,
            })
            item_id  += 1
            position += 1
            continue

        # Narration text
        if stripped and not stripped.startswith("#"):
            narr_buf.append(stripped)

    flush_narration()
    return items


@app.route("/timeline-data/<safe_name>")
def timeline_data(safe_name):
    out_dir = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return jsonify({"error": "Project not found"}), 404

    # Check for saved custom timeline order
    tl_state = out_dir / "timeline_state.json"
    if tl_state.exists():
        try:
            items = json.loads(tl_state.read_text())
            renders_dir = out_dir / "renders"
            state = _load_studio_state(out_dir)

            # Build manifest lookup by filename so we can backfill empty props
            manifest_by_fn = {}
            manifest_file = renders_dir / "manifest.json"
            if manifest_file.exists():
                try:
                    for entry in json.loads(manifest_file.read_text()):
                        fn_key = entry.get("filename", "")
                        if fn_key:
                            manifest_by_fn[fn_key] = entry
                except Exception:
                    pass

            previews_dir = renders_dir / "previews"
            for item in items:
                fn = item.get("filename")
                if fn and renders_dir.exists():
                    item["rendered"] = (renders_dir / fn).exists()
                item["approved"] = state.get(fn or "", {}).get("approved", item.get("approved", True))
                item["note"]     = state.get(fn or "", {}).get("note", item.get("note", ""))
                # Backfill props + preview state from manifest when item missing them
                entry = manifest_by_fn.get(fn or "")
                if entry:
                    if not item.get("props"):
                        item["props"] = entry.get("props", {})
                    if not item.get("preview_filename"):
                        item["preview_filename"] = entry.get("preview_filename", "") or ""
                pf = item.get("preview_filename") or ""
                item["preview_rendered"] = bool(pf) and (previews_dir / pf).exists()
            return jsonify({"items": items})
        except Exception:
            pass

    items = _parse_script_timeline(out_dir)
    return jsonify({"items": items})


@app.route("/music-plan/<safe_name>")
def music_plan(safe_name):
    """Return music_plan.json for the timeline editor music track."""
    plan_path = BASE_OUTPUT / safe_name / "music_plan.json"
    if not plan_path.exists():
        return jsonify([])
    with open(plan_path) as f:
        return jsonify(json.load(f))


@app.route("/timeline-save/<safe_name>", methods=["POST"])
def timeline_save(safe_name):
    out_dir = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return jsonify({"error": "Project not found"}), 404
    data  = request.get_json()
    items = data.get("items", [])
    (out_dir / "timeline_state.json").write_text(json.dumps(items, indent=2, default=str))
    return jsonify({"ok": True})


@app.route("/timeline-item-update/<safe_name>", methods=["POST"])
def timeline_item_update(safe_name):
    out_dir = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return jsonify({"error": "Project not found"}), 404
    data     = request.get_json()
    item_id  = data.get("id")
    patch    = data.get("patch", {})   # fields to update on the item

    tl_state = out_dir / "timeline_state.json"
    if tl_state.exists():
        try:
            items = json.loads(tl_state.read_text())
            for item in items:
                if item.get("id") == item_id:
                    item.update(patch)
                    break
            tl_state.write_text(json.dumps(items, indent=2, default=str))
        except Exception:
            pass

    # Also update studio state for approve/note
    if "approved" in patch or "note" in patch:
        filename = data.get("filename", "")
        if filename:
            state = _load_studio_state(out_dir)
            state.setdefault(filename, {})
            if "approved" in patch: state[filename]["approved"] = patch["approved"]
            if "note"     in patch: state[filename]["note"]     = patch["note"]
            _save_studio_state(out_dir, state)

    return jsonify({"ok": True})


@app.route("/edit/<safe_name>")
def editor(safe_name):
    out_dir = BASE_OUTPUT / safe_name
    if not out_dir.exists():
        return f"Project '{safe_name}' not found", 404

    title = safe_name.replace("_", " ").title()
    ctx = out_dir / "context.md"
    if ctx.exists():
        for line in ctx.read_text().splitlines():
            if line.startswith("## Title"):
                title = line.replace("## Title", "").strip()
                break

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Editor — {title}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;overflow:hidden;font-family:'Inter',system-ui,sans-serif;background:#080808;color:#e0e0e0}}
:root{{
  --gold:#C9A84C;--green:#22c55e;--red:#ef4444;--blue:#60a5fa;--purple:#c084fc;
  --bg:#080808;--surface:#0f0f0f;--surface2:#111;--border:#1e1e1e;--border2:#252525;
  --track-h:60px;--label-w:72px;
}}

/* ── App shell ── */
.app{{display:flex;flex-direction:column;height:100vh}}

/* ── Header ── */
.header{{
  display:flex;align-items:center;gap:12px;padding:0 16px;
  height:42px;background:#0a0a0a;border-bottom:1px solid var(--border);
  flex-shrink:0;z-index:100
}}
.logo{{font-size:0.65rem;font-weight:700;letter-spacing:.15em;color:var(--gold);text-transform:uppercase;flex-shrink:0}}
.project-name{{font-size:0.85rem;font-weight:600;color:#ccc;flex-shrink:0;max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.header-sep{{width:1px;height:18px;background:#222;flex-shrink:0}}
.timecode{{font-family:monospace;font-size:0.8rem;color:#555;flex-shrink:0;letter-spacing:.04em}}
.header-right{{margin-left:auto;display:flex;align-items:center;gap:8px}}
.hbtn{{padding:5px 12px;border-radius:4px;font-size:0.72rem;font-weight:600;cursor:pointer;border:none;transition:all .15s}}
.hbtn-gold{{background:var(--gold);color:#000}}
.hbtn-gold:hover{{background:#d4b060}}
.hbtn-ghost{{background:none;border:1px solid #242424;color:#666;font-size:0.7rem}}
.hbtn-ghost:hover{{border-color:#3a3a3a;color:#aaa}}

/* ── Workspace ── */
.workspace{{display:flex;flex:1;overflow:hidden}}

/* ── Left panel ── */
.left-panel{{
  width:200px;flex-shrink:0;background:#090909;border-right:1px solid var(--border);
  display:flex;flex-direction:column;overflow:hidden
}}
.panel-title{{
  font-size:0.6rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:#333;padding:10px 12px 6px
}}
.panel-scroll{{flex:1;overflow-y:auto}}
.preset-section{{padding:0 8px 12px}}
.preset-section-head{{
  font-size:0.6rem;color:#2a2a2a;text-transform:uppercase;letter-spacing:.1em;
  padding:6px 4px 4px;display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none
}}
.preset-section-head:hover{{color:#555}}
.preset-grid{{display:grid;grid-template-columns:1fr 1fr;gap:4px}}
.preset-card{{
  background:#111;border:1px solid #1a1a1a;border-radius:5px;padding:8px 6px;
  cursor:grab;text-align:center;transition:all .15s;user-select:none
}}
.preset-card:hover{{border-color:#333;background:#161616}}
.preset-card:active{{cursor:grabbing}}
.preset-icon{{font-size:1.1rem;margin-bottom:3px}}
.preset-label{{font-size:0.58rem;color:#555;line-height:1.3}}
.preset-card.transition-card .preset-label{{color:#888}}
.preset-card[data-track="graphics"]{{border-left:2px solid #4a2080}}
.preset-card[data-track="clips"]{{border-left:2px solid #1a4a20}}
.preset-card[data-track="transitions"]{{border-left:2px solid #303030}}

/* ── Centre ── */
.centre{{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}}

/* Preview */
.preview-area{{
  height:38%;flex-shrink:0;display:flex;background:#050505;
  border-bottom:1px solid var(--border)
}}
.preview-player{{
  flex:1;display:flex;align-items:center;justify-content:center;
  background:#000;position:relative;overflow:hidden
}}
.preview-video{{width:100%;height:100%;object-fit:contain;display:block}}
.preview-placeholder{{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  color:#1a1a1a;gap:8px;pointer-events:none
}}
.preview-placeholder-icon{{font-size:2.5rem}}
.preview-placeholder-label{{font-size:0.8rem;letter-spacing:.08em;text-transform:uppercase}}
.preview-info{{
  width:220px;flex-shrink:0;padding:12px;border-left:1px solid var(--border);
  display:flex;flex-direction:column;gap:6px;overflow:hidden
}}
.preview-info-title{{font-size:0.72rem;font-weight:600;color:#ccc;line-height:1.4;margin-bottom:2px}}
.preview-info-type{{font-size:0.6rem;text-transform:uppercase;letter-spacing:.1em;color:var(--gold);margin-bottom:4px}}
.preview-info-act{{font-size:0.65rem;color:#3a3a3a}}
.preview-info-meta{{font-size:0.65rem;color:#333;margin-top:4px;line-height:1.6}}
.preview-narration{{
  font-size:0.68rem;color:#444;line-height:1.6;overflow-y:auto;flex:1;
  border-top:1px solid var(--border);padding-top:8px;margin-top:4px
}}

/* Timeline area */
.timeline-area{{flex:1;display:flex;overflow:hidden}}
.track-labels{{
  width:var(--label-w);flex-shrink:0;background:#090909;border-right:1px solid var(--border);
}}
.track-label-cell{{
  height:var(--track-h);display:flex;align-items:center;justify-content:flex-end;
  padding-right:10px;font-size:0.58rem;color:#2a2a2a;text-transform:uppercase;
  letter-spacing:.08em;border-bottom:1px solid var(--border);font-weight:600
}}

/* Scrollable timeline */
.timeline-scroll{{flex:1;overflow-x:auto;overflow-y:hidden;position:relative}}
.timeline-inner{{position:relative;display:flex;flex-direction:column;min-height:100%;padding-bottom:8px;width:max-content;min-width:100%}}

/* Time ruler */
.tl-ruler{{
  height:28px;position:sticky;top:0;z-index:10;background:#090909;
  border-bottom:1px solid var(--border2);cursor:pointer;flex-shrink:0;
  overflow:hidden;user-select:none;min-width:100%
}}
.tl-tick{{position:absolute;bottom:0;width:1px;background:#1e1e1e;pointer-events:none}}
.tl-tick.major{{height:10px;background:#2a2a2a}}
.tl-tick.minor{{height:5px}}
.tl-tick-lbl{{
  position:absolute;bottom:10px;font-size:.42rem;color:#2e2e2e;letter-spacing:.04em;
  transform:translateX(-50%);pointer-events:none;font-family:monospace;white-space:nowrap
}}
/* Playhead (child of timeline-inner) */
.tl-playhead{{
  position:absolute;top:0;bottom:0;width:2px;background:#ef4444;
  z-index:30;pointer-events:none;margin-left:-1px
}}
.tl-playhead-handle{{
  position:absolute;top:-1px;left:50%;transform:translateX(-50%);
  width:0;height:0;
  border-left:5px solid transparent;border-right:5px solid transparent;
  border-top:8px solid #ef4444;
  cursor:grab;pointer-events:all;margin-left:-1px
}}
.tl-playhead-handle:active{{cursor:grabbing}}

/* Tracks */
.track-row{{
  height:var(--track-h);position:relative;
  border-bottom:1px solid var(--border);min-width:100%;
}}
.track-row.drag-over{{background:rgba(201,168,76,.05) !important}}

/* Timeline items */
.tl-item{{
  height:52px;border-radius:4px;display:flex;align-items:center;
  padding:0 6px;cursor:pointer;
  position:absolute;top:4px;
  border:1px solid transparent;transition:border-color .12s,opacity .12s;
  overflow:hidden;white-space:nowrap;user-select:none;min-width:40px;
}}
.tl-item:hover{{filter:brightness(1.15)}}
.tl-item.selected{{border-color:var(--gold) !important}}
.tl-item.rejected{{opacity:.35}}

/* Item type colours */
.tl-item.type-graphic{{background:#1a0f2e;border-color:#2a1a40}}
.tl-item.type-clip{{background:#122812;border-color:#1a3a1a}}
.tl-item.type-narration{{background:#060d1a;border-color:#0f1830;height:42px;top:4px}}
.tl-item.type-transition{{
  background:#141414;border-color:#222;
  justify-content:center;font-size:0.7rem;color:#333;height:20px
}}
.tl-item.unrendered{{background:repeating-linear-gradient(45deg,#111,#111 4px,#141414 4px,#141414 8px)}}
.tl-item.unrendered.type-graphic{{background:repeating-linear-gradient(45deg,#1a0f2e,#1a0f2e 4px,#150a25 4px,#150a25 8px)}}

.tl-label{{font-size:0.62rem;font-weight:600;overflow:hidden;text-overflow:ellipsis}}
.tl-label.graphic{{color:#c084fc}}
.tl-label.clip{{color:#86efac}}
.tl-label.narration{{color:#60a5fa;font-size:0.58rem;font-weight:400}}
.tl-item-dot{{
  width:6px;height:6px;border-radius:50%;flex-shrink:0;margin-right:5px
}}
.dot-rendered{{background:var(--green)}}
.dot-unrendered{{background:#333}}
.tl-approved-bar{{
  position:absolute;bottom:0;left:0;right:0;height:2px
}}
.bar-approved{{background:var(--green)}}
.bar-rejected{{background:var(--red)}}

/* Drop target indicator */
.drop-indicator{{
  width:3px;height:44px;background:var(--gold);border-radius:2px;flex-shrink:0;
  display:none
}}
.drop-indicator.visible{{display:block}}

/* ── Right panel — Inspector ── */
.right-panel{{
  width:280px;flex-shrink:0;background:#090909;border-left:1px solid var(--border);
  display:flex;flex-direction:column;overflow:hidden
}}
.inspector-header{{
  padding:10px 12px;border-bottom:1px solid var(--border);flex-shrink:0
}}
.inspector-type{{font-size:0.58rem;text-transform:uppercase;letter-spacing:.1em;color:var(--gold);margin-bottom:4px}}
.inspector-name{{font-size:0.82rem;font-weight:600;color:#ccc;line-height:1.3}}
.inspector-body{{flex:1;overflow-y:auto;padding:12px}}
.inspector-empty{{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  height:100%;color:#1e1e1e;gap:8px;text-align:center;padding:20px
}}
.inspector-empty-icon{{font-size:2rem}}
.inspector-empty-msg{{font-size:0.75rem}}

/* Inspector sections */
.insp-section{{margin-bottom:14px}}
.insp-section-head{{
  font-size:0.58rem;text-transform:uppercase;letter-spacing:.1em;color:#2a2a2a;
  margin-bottom:8px;font-weight:700
}}
.insp-row{{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;gap:8px}}
.insp-label{{font-size:0.65rem;color:#444;flex-shrink:0}}
.approve-toggle{{
  display:flex;border-radius:4px;overflow:hidden;border:1px solid #222
}}
.approve-toggle button{{
  padding:4px 12px;font-size:0.68rem;font-weight:600;cursor:pointer;background:none;
  border:none;color:#444;transition:all .12s
}}
.approve-toggle button.active.approve{{background:#0f2010;color:var(--green)}}
.approve-toggle button.active.reject{{background:#200f0f;color:var(--red)}}

/* Prop fields (reused from Studio) */
.prop-field{{margin-bottom:9px}}
.prop-label{{font-size:0.62rem;color:#444;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px}}
.prop-input{{
  width:100%;background:#0d0d0d;border:1px solid #1e1e1e;border-radius:4px;
  color:#ccc;font-size:0.75rem;padding:5px 8px;outline:none;font-family:inherit
}}
.prop-input:focus{{border-color:var(--gold)}}
.prop-color-row{{display:flex;align-items:center;gap:6px}}
.color-swatch{{width:24px;height:24px;border-radius:3px;border:1px solid #2a2a2a;cursor:pointer;flex-shrink:0}}
input[type=color]{{width:24px;height:24px;border:none;background:none;cursor:pointer;padding:0;flex-shrink:0}}
.player-slot{{background:#0d0d0d;border:1px solid #1a1a1a;border-radius:4px;padding:8px;margin-bottom:6px}}
.slot-num{{font-size:0.58rem;color:var(--gold);font-weight:700;text-transform:uppercase;margin-bottom:5px}}
.slot-img-row{{display:flex;align-items:center;gap:6px;margin-bottom:5px}}
.slot-thumb{{width:28px;height:36px;object-fit:cover;border-radius:2px;border:1px solid #222;background:#111;flex-shrink:0}}
.slot-thumb-empty{{width:28px;height:36px;border-radius:2px;border:1px dashed #222;background:#111;display:flex;align-items:center;justify-content:center;color:#222;flex-shrink:0;font-size:.8rem}}
.clip-file-row{{display:flex;align-items:flex-start;gap:8px;margin-bottom:5px}}
.clip-thumb{{width:80px;height:45px;object-fit:cover;border-radius:3px;border:1px solid #222;background:#111;flex-shrink:0}}
.clip-thumb-empty{{width:80px;height:45px;border-radius:3px;border:1px dashed #222;background:#111;display:flex;align-items:center;justify-content:center;color:#2a2a2a;flex-shrink:0;font-size:.6rem}}
.clip-file-info{{display:flex;flex-direction:column;flex:1;min-width:0}}
.upload-zone{{display:flex;align-items:center;gap:5px;padding:4px 8px;border:1px dashed #2a2a2a;border-radius:4px;cursor:pointer;font-size:0.65rem;color:#3a3a3a;transition:all .15s}}
.upload-zone:hover{{border-color:var(--gold);color:var(--gold)}}
.slot-img-slug{{font-size:0.62rem;color:#3a3a3a;font-family:monospace;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.swap-btn{{padding:3px 7px;border-radius:3px;font-size:0.62rem;cursor:pointer;background:none;border:1px solid #2a2a2a;color:#555;transition:all .12s;flex-shrink:0}}
.swap-btn:hover{{border-color:var(--gold);color:var(--gold)}}
.note-ta{{width:100%;background:#0d0d0d;border:1px solid #1e1e1e;border-radius:4px;color:#666;font-size:0.7rem;padding:5px 8px;resize:none;font-family:inherit;outline:none}}
.note-ta:focus{{border-color:#333}}
.render-btn{{
  width:100%;padding:7px;border-radius:4px;font-size:0.75rem;font-weight:600;
  cursor:pointer;background:#1a1500;border:1px solid var(--gold);color:var(--gold);
  transition:all .15s;margin-top:10px
}}
.render-btn:hover{{background:#2a2000}}
.render-btn:disabled{{opacity:.4;cursor:default}}
.render-status{{font-size:0.65rem;color:#444;text-align:center;margin-top:4px;min-height:14px}}
.yt-link{{
  display:inline-block;padding:4px 10px;background:#7f0000;color:#fca5a5;
  text-decoration:none;border-radius:4px;font-size:0.68rem;font-weight:600;
  border:1px solid #991b1b;margin-top:6px
}}
.yt-link:hover{{background:#991b1b}}
.comp-select{{
  width:100%;background:#0d0d0d;border:1px solid #1e1e1e;border-radius:4px;
  color:#888;font-size:0.72rem;padding:5px 8px;outline:none;cursor:pointer
}}
.comp-select:focus{{border-color:var(--gold)}}

/* Image picker modal */
.modal-overlay{{position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:500;display:none;align-items:center;justify-content:center}}
.modal-overlay.open{{display:flex}}
.modal{{background:#0f0f0f;border:1px solid #2a2a2a;border-radius:8px;width:min(700px,95vw);max-height:85vh;display:flex;flex-direction:column}}
.modal-hd{{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid #1e1e1e}}
.modal-title{{font-size:0.85rem;font-weight:600}}
.modal-x{{width:26px;height:26px;border-radius:50%;background:none;border:1px solid #2a2a2a;color:#666;cursor:pointer;font-size:.85rem;display:flex;align-items:center;justify-content:center}}
.modal-x:hover{{border-color:#555;color:#ccc}}
.modal-toolbar{{display:flex;align-items:center;gap:8px;padding:8px 16px;border-bottom:1px solid #1a1a1a}}
.modal-search{{flex:1;padding:6px 10px;background:#060606;border:1px solid #1e1e1e;border-radius:4px;color:#e0e0e0;font-size:0.78rem;outline:none}}
.modal-search:focus{{border-color:var(--gold)}}
.upload-lbl{{display:flex;align-items:center;gap:5px;padding:5px 10px;border:1px dashed #222;border-radius:4px;cursor:pointer;font-size:0.68rem;color:#3a3a3a;transition:all .12px}}
.upload-lbl:hover{{border-color:var(--gold);color:var(--gold)}}
.modal-body{{overflow-y:auto;padding:12px 16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:8px}}
.img-opt{{cursor:pointer;border:2px solid transparent;border-radius:4px;overflow:hidden;background:#0a0a0a;text-align:center;transition:all .12s}}
.img-opt:hover{{border-color:#2a2a2a}}
.img-opt.selected{{border-color:var(--gold);background:#1a1500}}
.img-opt img{{width:100%;aspect-ratio:2/3;object-fit:cover;display:block;background:#111}}
.img-opt-lbl{{font-size:0.55rem;color:#555;padding:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.modal-ft{{padding:10px 16px;border-top:1px solid #1a1a1a;display:flex;justify-content:space-between;align-items:center}}
.modal-sel-lbl{{font-size:0.68rem;color:#444}}
.modal-btns{{display:flex;gap:8px}}
.mbtn{{padding:5px 12px;border-radius:4px;font-size:0.72rem;font-weight:600;cursor:pointer;border:none}}
.mbtn-gold{{background:var(--gold);color:#000}}
.mbtn-ghost{{background:none;border:1px solid #2a2a2a;color:#666}}
.mbtn-ghost:hover{{border-color:#444;color:#aaa}}

/* ── Inspector tabs ── */
.insp-tabs{{display:flex;border-bottom:1px solid var(--border);flex-shrink:0}}
.insp-tab{{
  padding:6px 14px;font-size:0.6rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  cursor:pointer;color:#2a2a2a;border-bottom:2px solid transparent;transition:color .12s;user-select:none
}}
.insp-tab:hover{{color:#555}}
.insp-tab.active{{color:var(--gold);border-bottom-color:var(--gold)}}
.json-ta{{
  width:100%;min-height:300px;resize:vertical;
  background:#070707;border:1px solid #1a1a1a;border-radius:4px;
  color:#7aa070;font-size:0.68rem;font-family:monospace;
  padding:8px;outline:none;line-height:1.55
}}
.json-ta:focus{{border-color:#2a2a2a}}
.json-apply-btn{{
  width:100%;margin-top:6px;padding:6px;border-radius:4px;font-size:0.72rem;font-weight:600;
  cursor:pointer;background:#0a120a;border:1px solid #2a4a2a;color:#86efac;transition:all .15s
}}
.json-apply-btn:hover{{background:#0f1f0f;border-color:#3a6a3a}}
.json-err{{font-size:.62rem;color:var(--red);margin-top:4px;min-height:14px}}
.autosave-badge{{
  font-size:.55rem;color:#1e1e1e;margin-left:auto;font-weight:400;letter-spacing:.04em;
  transition:color .3s
}}
.autosave-badge.saved{{color:var(--green)}}

/* Toast */
.toast{{position:fixed;bottom:18px;right:18px;background:#141414;border:1px solid #2a2a2a;border-radius:6px;padding:9px 14px;font-size:0.78rem;z-index:999;opacity:0;transform:translateY(6px);transition:all .2s;pointer-events:none;max-width:280px}}
.toast.show{{opacity:1;transform:translateY(0)}}
.toast.ok{{border-color:var(--green);color:#86efac}}
.toast.err{{border-color:var(--red);color:#fca5a5}}

/* ── Drag & drop improvements ── */
.tl-item.dragging{{opacity:0.2!important;pointer-events:none}}
.tl-item.drop-before{{box-shadow:-4px 0 0 0 var(--gold)!important;border-left-color:var(--gold)!important}}
.tl-item.drop-after{{box-shadow:4px 0 0 0 var(--gold)!important;border-right-color:var(--gold)!important}}
.track-row.drag-over{{background:rgba(201,168,76,.04)!important;outline:1px dashed rgba(201,168,76,.25)!important;outline-offset:-1px}}
.track-row{{transition:background .1s}}

/* Transitions (FX) track — thin strip */
.track-row.trans-row{{height:28px!important;background:#060606}}
.tl-item.type-transition{{height:20px!important;border-radius:3px;font-size:.58rem;padding:0 5px;min-width:32px}}

/* Position time badge (bottom-right, read-only timecode) */
.tl-pos{{font-size:.46rem;color:#222;position:absolute;bottom:2px;right:3px;font-family:monospace;pointer-events:none;letter-spacing:-.02em}}

/* Track-position number badge (top-left, editable) */
.tl-num{{
  position:absolute;top:2px;left:3px;
  font-size:.5rem;font-weight:700;font-family:monospace;
  color:#252525;background:none;
  cursor:pointer;line-height:1;padding:1px 2px;border-radius:2px;
  transition:color .1s,background .1s;
  z-index:5
}}
.tl-num:hover{{color:var(--gold);background:rgba(201,168,76,.1)}}
.tl-num-input{{
  position:absolute;top:1px;left:2px;
  width:26px;height:16px;font-size:.56rem;font-weight:700;font-family:monospace;
  background:#1a1500;border:1px solid var(--gold);border-radius:2px;
  color:var(--gold);text-align:center;outline:none;padding:0;z-index:10
}}

/* Timecode — active when playing */
.timecode.playing{{color:var(--red)!important}}

/* ── Currently-playing item on timeline ── */
@keyframes seqPulse{{
  0%,100%{{box-shadow:0 0 0 1px rgba(201,168,76,.5),inset 0 0 0 1px rgba(201,168,76,.15)}}
  50%{{box-shadow:0 0 0 2px rgba(201,168,76,.8),inset 0 0 0 1px rgba(201,168,76,.3)}}
}}
.tl-item.seq-active{{
  animation:seqPulse 1.6s ease-in-out infinite;
  border-color:var(--gold)!important;
  z-index:4;
}}
.tl-item.seq-active .tl-label{{color:var(--gold)!important}}

/* ── Inline sequence controls (inside preview-info) ── */
#inlineSeqCtrl{{
  position:absolute;inset:0;background:#080808;
  display:none;flex-direction:column;gap:8px;padding:10px 12px;
  border-left:1px solid var(--border);
}}
#inlineSeqCtrl.open{{display:flex}}
.iseq-row{{display:flex;align-items:center;gap:6px}}
.iseq-btn{{
  background:none;border:1px solid #222;border-radius:3px;color:#888;
  font-size:.8rem;width:28px;height:26px;cursor:pointer;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;transition:all .12s;
}}
.iseq-btn:hover{{border-color:#444;color:#ddd}}
.iseq-tc{{font-family:monospace;font-size:.68rem;color:#444;flex:1;letter-spacing:.04em}}
.iseq-scrubber{{
  width:100%;height:3px;-webkit-appearance:none;appearance:none;
  background:linear-gradient(to right,var(--gold) 0%,var(--gold) var(--pct,0%),#1e1e1e var(--pct,0%),#1e1e1e 100%);
  border-radius:2px;cursor:pointer;outline:none;margin:2px 0;
}}
.iseq-scrubber::-webkit-slider-thumb{{
  -webkit-appearance:none;width:10px;height:10px;border-radius:50%;background:var(--gold);cursor:pointer;
}}
.iseq-scrubber::-moz-range-thumb{{width:10px;height:10px;border-radius:50%;background:var(--gold);cursor:pointer;border:none}}
.iseq-item-label{{font-size:.6rem;color:#444;line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}}
.iseq-stop{{
  margin-top:auto;padding:4px;border-radius:3px;font-size:.62rem;
  background:none;border:1px solid #1a1a1a;color:#333;cursor:pointer;transition:all .12s;text-align:center;
}}
.iseq-stop:hover{{border-color:#333;color:#888}}

/* ── Sequence preview overlay ── */
.seq-overlay{{
  position:fixed;inset:0;z-index:600;
  background:#000;display:none;flex-direction:column;
}}
.seq-overlay.open{{display:flex}}
.seq-header{{
  display:flex;align-items:center;gap:12px;
  padding:8px 16px;background:#0a0a0a;border-bottom:1px solid #1a1a1a;flex-shrink:0
}}
.seq-header-title{{font-size:.7rem;font-weight:700;letter-spacing:.1em;color:var(--gold);text-transform:uppercase}}
.seq-item-label{{font-size:.7rem;color:#555;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.seq-close{{background:none;border:1px solid #222;border-radius:3px;color:#555;
  font-size:.7rem;padding:4px 10px;cursor:pointer;}}
.seq-close:hover{{border-color:#444;color:#ccc}}
.seq-body{{flex:1;position:relative;background:#000;overflow:hidden}}
.seq-video{{width:100%;height:100%;object-fit:contain;display:block}}
.seq-placeholder{{
  position:absolute;inset:0;display:none;flex-direction:column;
  align-items:center;justify-content:center;gap:10px;
  background:#070707;
}}
.seq-ph-icon{{font-size:3rem;color:#1a1a1a}}
.seq-ph-label{{font-size:.75rem;letter-spacing:.12em;text-transform:uppercase;color:#1e1e1e}}
.seq-controls{{
  display:flex;align-items:center;gap:10px;
  padding:8px 14px;background:#080808;border-top:1px solid #141414;flex-shrink:0
}}
.seq-btn{{
  background:none;border:1px solid #222;border-radius:3px;color:#888;
  font-size:.8rem;width:30px;height:28px;cursor:pointer;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;transition:all .12s
}}
.seq-btn:hover{{border-color:#444;color:#ddd}}
.seq-btn.active{{color:var(--gold);border-color:rgba(201,168,76,.4)}}
.seq-timecode{{
  font-family:monospace;font-size:.72rem;color:#444;flex-shrink:0;min-width:96px;letter-spacing:.04em
}}
.seq-scrubber{{
  flex:1;height:4px;-webkit-appearance:none;appearance:none;
  background:linear-gradient(to right,var(--gold) 0%,var(--gold) var(--pct,0%),#1e1e1e var(--pct,0%),#1e1e1e 100%);
  border-radius:2px;cursor:pointer;outline:none;
}}
.seq-scrubber::-webkit-slider-thumb{{
  -webkit-appearance:none;width:12px;height:12px;border-radius:50%;
  background:var(--gold);cursor:pointer;
}}
.seq-scrubber::-moz-range-thumb{{
  width:12px;height:12px;border-radius:50%;background:var(--gold);cursor:pointer;border:none
}}
.seq-audio-btn{{font-size:.9rem}}

/* ── Delete button on timeline items ── */
.tl-del{{
  position:absolute;top:2px;right:3px;
  width:14px;height:14px;
  font-size:.6rem;line-height:14px;text-align:center;
  color:#2a1a1a;background:none;border:none;
  border-radius:2px;cursor:pointer;
  opacity:0;transition:opacity .12s,color .12s,background .12s;
  z-index:6;padding:0;
}}
.tl-item:hover .tl-del{{opacity:1}}
.tl-del:hover{{color:var(--red);background:rgba(239,68,68,.1)}}

/* ── Transition pill between clip/graphic items ── */
.tl-trans-gap{{
  display:inline-flex;align-items:center;justify-content:center;
  width:26px;flex-shrink:0;cursor:pointer;
  font-size:.45rem;color:#2a2a2a;letter-spacing:.02em;
  border-left:1px solid #141414;border-right:1px solid #141414;
  transition:color .12s,background .12s;
  height:var(--track-h);
}}
.tl-trans-gap:hover{{color:var(--gold);background:rgba(201,168,76,.06)}}
.tl-trans-gap.has-trans{{color:#4a6a4a}}

/* ── Transition picker dropdown ── */
.trans-pick{{
  display:flex;flex-wrap:wrap;gap:4px;padding:2px 0;
}}
.trans-opt{{
  padding:3px 8px;border-radius:3px;font-size:.65rem;font-weight:600;
  cursor:pointer;border:1px solid #222;color:#555;background:none;
  transition:all .12s;letter-spacing:.04em;
}}
.trans-opt:hover{{border-color:#444;color:#ccc}}
.trans-opt.active{{background:rgba(74,106,74,.15);border-color:#4a6a4a;color:#4ade80}}

/* ── Splice section ── */
.splice-row{{display:flex;align-items:center;gap:6px;margin-top:4px}}
.splice-input{{
  flex:1;background:#0d0d0d;border:1px solid #222;border-radius:3px;
  color:#ccc;font-size:.72rem;padding:4px 7px;outline:none;
}}
.splice-input:focus{{border-color:var(--gold)}}
.splice-btn{{
  padding:4px 10px;border-radius:3px;font-size:.68rem;font-weight:600;
  background:rgba(201,168,76,.1);border:1px solid rgba(201,168,76,.3);
  color:var(--gold);cursor:pointer;white-space:nowrap;
}}
.splice-btn:hover{{background:rgba(201,168,76,.2)}}

/* ── Export video modal ── */
.export-modal{{max-width:440px}}
.export-progress{{
  font-size:.75rem;color:#888;padding:10px 0 4px;line-height:1.6;
}}
.export-bar-wrap{{height:4px;background:#111;border-radius:2px;margin:8px 0}}
.export-bar{{height:4px;border-radius:2px;background:var(--gold);width:0%;transition:width .4s}}
.export-result{{
  font-size:.7rem;padding:8px;background:#0a0f0a;border:1px solid #152815;
  border-radius:4px;color:#4ade80;font-family:monospace;word-break:break-all;
  margin-top:8px;display:none;
}}
</style>
</head>
<body>

<div class="app">

<!-- Header -->
<div class="header">
  <span class="logo">Frequency</span>
  <div class="header-sep"></div>
  <span class="project-name" id="projName">Loading&#8230;</span>
  <div class="header-sep"></div>
  <span class="timecode" id="timecode">00:00:00</span>
  <div class="header-right">
    <a class="hbtn hbtn-ghost" href="/studio/{safe_name}" target="_blank">Grid View</a>
    <button class="hbtn hbtn-ghost" onclick="saveTimeline()">Save Order</button>
    <button class="hbtn hbtn-ghost" onclick="openSeqPreview(true)">&#9654; Play in Editor</button>
    <button class="hbtn hbtn-ghost" onclick="openSeqPreview(false)">&#9654; Preview Fullscreen</button>
    <button class="hbtn hbtn-ghost" onclick="doExport()">Export Renders &#8595;</button>
    <button class="hbtn hbtn-ghost" id="syncBtn" onclick="applySyncMap()" title="Auto-position graphics to match spoken narration timestamps">&#9201; Sync to Narration</button>
    <button class="hbtn hbtn-ghost" id="rerunFailedBtn" onclick="rerunFailedRenders()" title="Re-render all graphics that failed or are missing">&#8635; Re-run Failed</button>
    <button class="hbtn hbtn-gold" id="renderApprovedBtn" onclick="renderApproved()" title="Render mp4 for every approved scene that doesn't yet have one">▶ Render Approved</button>
    <span id="renderApprovedStatus" style="font-size:.75rem;color:#888;margin-left:4px"></span>
    <button class="hbtn hbtn-gold" onclick="openExportModal()">&#9654; Export Video</button>
    <div style="display:flex;align-items:center;gap:2px;margin-left:4px">
      <button class="hbtn hbtn-ghost" style="padding:0 8px;font-size:.8rem" title="Zoom in (or Ctrl+scroll)" onclick="_pxPerSec=Math.min(240,_pxPerSec*1.5);renderTimeline()">+</button>
      <button class="hbtn hbtn-ghost" style="padding:0 8px;font-size:.8rem" title="Zoom out (or Ctrl+scroll)" onclick="_pxPerSec=Math.max(8,_pxPerSec/1.5);renderTimeline()">&#8722;</button>
    </div>
  </div>
</div>

<!-- Workspace -->
<div class="workspace">

  <!-- LEFT: Presets panel -->
  <div class="left-panel">
    <div class="panel-title">Presets</div>
    <div class="panel-scroll">

      <!-- Transitions -->
      <div class="preset-section">
        <div class="preset-section-head" onclick="toggleSection('trans')">&#9662; Transitions</div>
        <div id="sec-trans" class="preset-grid">
          {' '.join(f'<div class="preset-card transition-card" draggable="true" data-track="transitions" data-type="transition" data-tag-key="TRANSITION" data-tag-text="{t}" data-composition="" data-label="{t}" ondragstart="presetDragStart(event)"><div class="preset-icon">&#8644;</div><div class="preset-label">{t}</div></div>' for t in ['letterbox','push','grain','paper','dataLine','flash'])}
        </div>
      </div>

      <!-- Graphic templates — populated dynamically from /compositions -->
      <div class="preset-section">
        <div class="preset-section-head" onclick="toggleSection('gfx')">&#9662; Graphics</div>
        <div id="sec-gfx" class="preset-grid">
          <div style="font-size:0.6rem;color:#333;padding:4px 2px;grid-column:1/-1">Loading&#8230;</div>
        </div>
      </div>

      <!-- Clip template -->
      <div class="preset-section">
        <div class="preset-section-head" onclick="toggleSection('clips')">&#9662; Clips</div>
        <div id="sec-clips" class="preset-grid">
          <div class="preset-card" draggable="true"
               data-track="clips" data-type="clip"
               data-tag-key="CLIP SINGLE" data-tag-text=""
               data-composition="HeroClipSingle" data-label="Clip Single"
               ondragstart="presetDragStart(event)">
            <div class="preset-icon">&#127902;</div>
            <div class="preset-label">Clip Single</div>
          </div>
        </div>
      </div>

    </div><!-- panel-scroll -->
  </div><!-- left-panel -->

  <!-- CENTRE -->
  <div class="centre">

    <!-- Preview -->
    <div class="preview-area">
      <div class="preview-player" id="previewPlayer">
        <div class="preview-placeholder" id="previewPlaceholder">
          <div class="preview-placeholder-icon">&#9654;</div>
          <div class="preview-placeholder-label">Select an item</div>
        </div>
        <video class="preview-video" id="previewVideo" controls style="display:none"
               preload="none" playsinline></video>
        <img class="preview-video" id="previewImage" style="display:none;width:100%;height:100%;object-fit:contain;background:#000" />
      </div>
      <div class="preview-info" style="position:relative">
        <div class="preview-info-type" id="piType">&#8212;</div>
        <div class="preview-info-title" id="piTitle">Nothing selected</div>
        <div class="preview-info-act" id="piAct"></div>
        <div class="preview-info-meta" id="piMeta"></div>
        <div class="preview-narration" id="piNarr" style="display:none"></div>
        <!-- Inline sequence controls — absolute overlay, shown when "Play in Editor" is active -->
        <div id="inlineSeqCtrl">
          <div style="font-size:.55rem;font-weight:700;letter-spacing:.1em;color:var(--gold);text-transform:uppercase;margin-bottom:4px">&#9654; Sequence</div>
          <div class="iseq-row">
            <button class="iseq-btn" id="inlinePlayBtn" onclick="toggleSeqPlay()" title="Play / Pause">&#9654;</button>
            <span class="iseq-tc" id="inlineTimecode">00:00 / 00:00</span>
            <span class="iseq-btn" title="Narration audio on" style="color:#333;cursor:default">&#128266;</span>
          </div>
          <input class="iseq-scrubber" id="inlineScrubber" type="range"
                 min="0" max="100" step="0.25" value="0"
                 oninput="onSeqScrub(this.value)"
                 onmousedown="_seqScrubbing=true" onmouseup="_seqScrubbing=false;onSeqScrub(this.value)">
          <div class="iseq-item-label" id="inlineItemLabel"></div>
          <button class="iseq-stop" onclick="stopInlineSeq()">&#10005; Stop</button>
        </div>
      </div>
    </div>

    <!-- Timeline -->
    <div class="timeline-area">
      <div class="track-labels">
        <div class="track-label-cell" style="height:28px;font-size:.44rem;color:#1a1a1a">&#9201;</div>
        <div class="track-label-cell" style="height:28px;font-size:.47rem;color:#1e1e1e">FX</div>
        <div class="track-label-cell">VIDEO</div>
        <div class="track-label-cell">NARR</div>
        <div class="track-label-cell" style="color:#2a5a3a;font-size:.47rem">MUSIC</div>
      </div>
      <div class="timeline-scroll" id="timelineScroll">
        <div class="timeline-inner" id="timelineInner">
          <!-- Time ruler — click/drag to seek -->
          <div class="tl-ruler" id="tlRuler"></div>
          <div class="track-row trans-row" id="track-transitions"
               ondragover="trackDragOver(event,'transitions')"
               ondragleave="trackDragLeave(event,'transitions')"
               ondrop="trackDrop(event,'transitions')"></div>
          <!-- Graphics + clips share one VIDEO row -->
          <div class="track-row" id="track-video"
               ondragover="trackDragOver(event,'graphics')"
               ondragleave="trackDragLeave(event,'graphics')"
               ondrop="trackDrop(event,'graphics')"></div>
          <div class="track-row" id="track-narration"
               ondragover="trackDragOver(event,'narration')"
               ondragleave="trackDragLeave(event,'narration')"
               ondrop="trackDrop(event,'narration')"></div>
          <div class="track-row" id="track-music" style="pointer-events:none"></div>
          <!-- Playhead — spans all tracks, positioned by JS -->
          <div class="tl-playhead" id="tlPlayhead">
            <div class="tl-playhead-handle" id="tlPlayheadHandle"></div>
          </div>
        </div>
      </div>
    </div>

  </div><!-- centre -->

  <!-- RIGHT: Inspector -->
  <div class="right-panel">
    <div class="inspector-header" id="inspHeader">
      <div style="display:flex;align-items:center;gap:0">
        <div class="inspector-type" id="inspType">Inspector</div>
        <span class="autosave-badge" id="autosaveBadge">&#8226; unsaved</span>
      </div>
      <div class="inspector-name" id="inspName">Select an item on the timeline</div>
    </div>
    <div class="insp-tabs" id="inspTabs" style="display:none">
      <div class="insp-tab active" id="tab-props" onclick="switchTab('props')">Props</div>
      <div class="insp-tab" id="tab-json" onclick="switchTab('json')">JSON</div>
    </div>
    <div class="inspector-body" id="inspBody">
      <div class="inspector-empty">
        <div class="inspector-empty-icon">&#9998;</div>
        <div class="inspector-empty-msg">Click any item on the timeline to edit it here</div>
      </div>
    </div>
  </div>

</div><!-- workspace -->
</div><!-- app -->

<!-- Image picker modal -->
<div class="modal-overlay" id="imgModal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="modal-hd">
      <span class="modal-title">Choose Image</span>
      <button class="modal-x" onclick="closeModal()">&#10005;</button>
    </div>
    <div class="modal-toolbar">
      <input class="modal-search" id="modalSearch" type="text" placeholder="Search&#8230;" oninput="filterModalImages(this.value)">
      <label class="upload-lbl" title="Upload new image">
        <input type="file" id="uploadInput" accept="image/*" style="display:none" onchange="handleUpload(this)">
        &#8593; Upload
      </label>
    </div>
    <div class="modal-body" id="modalGrid"></div>
    <div class="modal-ft">
      <span class="modal-sel-lbl" id="modalSelLbl">None selected</span>
      <div class="modal-btns">
        <button class="mbtn mbtn-ghost" onclick="closeModal()">Cancel</button>
        <button class="mbtn mbtn-gold" onclick="confirmModal()">Use Image</button>
      </div>
    </div>
  </div>
</div>

<!-- Sequence preview overlay (fullscreen) -->
<div class="seq-overlay" id="seqOverlay">
  <div class="seq-header">
    <span class="seq-header-title">&#9654; Preview</span>
    <span class="seq-item-label" id="seqLabel"></span>
    <button class="seq-close" onclick="closeSeqPreview()">&#10005; Close</button>
  </div>
  <div class="seq-body">
    <video class="seq-video" id="seqVideo" playsinline preload="auto"></video>
    <div class="seq-placeholder" id="seqPlaceholder">
      <div class="seq-ph-icon">&#8421;</div>
      <div class="seq-ph-label" id="seqPhLabel">Not rendered</div>
    </div>
  </div>
  <audio id="seqAudio" preload="auto"></audio>
  <div class="seq-controls">
    <button class="seq-btn" id="seqPlayBtn" onclick="toggleSeqPlay()" title="Play / Pause">&#9654;</button>
    <span class="seq-timecode" id="seqTimecode">00:00 / 00:00</span>
    <input class="seq-scrubber" id="seqScrubber" type="range"
           min="0" max="100" step="0.25" value="0"
           oninput="onSeqScrub(this.value)"
           onmousedown="_seqScrubbing=true" onmouseup="_seqScrubbing=false;onSeqScrub(this.value)">
    <button class="seq-btn seq-audio-btn" id="seqAudioBtn" onclick="toggleSeqAudio()" title="Toggle narration audio">&#128266;</button>
  </div>
</div>

<!-- Export Video modal -->
<div class="modal-overlay" id="exportModal" onclick="if(event.target===this)closeExportModal()">
  <div class="modal export-modal">
    <div class="modal-hd">
      <span class="modal-title">&#9654; Export Video</span>
      <button class="modal-x" onclick="closeExportModal()">&#10005;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      <div style="font-size:.72rem;color:#555;line-height:1.6;margin-bottom:12px">
        Renders approved graphics &amp; clips into a single MP4 via VideoSequence, then mixes in
        <code style="color:#888">narration.mp3</code> if it exists.<br>
        Only <b style="color:#aaa">approved</b> items with a composition are included.
      </div>
      <div style="font-size:.65rem;color:#333;margin-bottom:10px" id="exportSceneCount"></div>
      <div class="export-progress" id="exportProgress">Ready to export.</div>
      <div class="export-bar-wrap"><div class="export-bar" id="exportBar"></div></div>
      <div class="export-result" id="exportResult"></div>
    </div>
    <div class="modal-ft">
      <button class="mbtn mbtn-ghost" onclick="closeExportModal()">Close</button>
      <button class="mbtn mbtn-gold" id="exportStartBtn" onclick="startExportVideo()">&#9654; Start Export</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const SAFE_NAME = {json.dumps(safe_name)};

// ── State ─────────────────────────────────────────────────────────────────────
let timelineItems  = []; // flat array, ordered
let selectedId     = null;
let allImages      = [];
let compSchemas    = {{}};
let compatMap      = {{}};
let typeMap        = {{}};
let modalCtx       = null; // {{itemId, field, playerIdx}}
let _selSlug       = null;
let _selPath       = null;
let _dragSrc       = null; // {{type:'preset'|'item', data:...}}
let _dragOverTrack = null;
let _dropTargetId  = null; // item id we're hovering over while dragging
let _dropSide      = null; // 'before' | 'after'
let _inspTab       = 'props'; // 'props' | 'json'
let _autosaveTimer = null;
let _unsaved       = false;
let _deletedItems  = [];   // {{item, index}}[] for undo
let _exportPollT   = null;

// ── Premiere-style timeline state ─────────────────────────────────────────────
let _pxPerSec     = 40;    // pixels per second (zoom level)
let _currentTime  = 0;     // playhead position in seconds
let _tlTotalDur   = 0;     // computed total duration
let _tlDragging   = false; // is playhead being dragged?
let _tlRafId      = null;  // RAF id for playhead tracking

// ── Music track ──────────────────────────────────────────────────────────────
function _renderMusicTrack(plan) {{
  const row = document.getElementById('track-music');
  if (!row || !plan || !plan.length) return;
  // Lay out one pill per act, proportional width isn't feasible without timing,
  // so render as a flowing strip of labelled segments
  const colours = ['#1a3a22','#1a2a3a','#2a1a3a','#3a2a1a','#1a3a3a'];
  row.innerHTML = plan.map((p, i) => {{
    const name = (p.track || '').replace(/\.mp3$/,'').replace(/_/g,' ');
    const act  = (p.act  || '').replace(/ACT \d+ — /,'').replace(/COLD OPEN/,'OPEN');
    const bg   = colours[i % colours.length];
    return `<div style="display:inline-flex;align-items:center;gap:5px;background:${{bg}};
      border:1px solid rgba(255,255,255,0.07);border-radius:3px;padding:2px 8px;
      margin:2px 3px;font-size:.5rem;color:rgba(255,255,255,0.45);white-space:nowrap;
      letter-spacing:.04em;text-transform:uppercase;max-width:180px;overflow:hidden">
      <span style="color:rgba(255,255,255,0.25);flex-shrink:0">${{act}}</span>
      <span style="overflow:hidden;text-overflow:ellipsis">${{name}}</span>
    </div>`;
  }}).join('');
}}

// ── Init ─────────────────────────────────────────────────────────────────────
async function init() {{
  const [tlRes, imgRes, schRes, compRes, musicPlan] = await Promise.all([
    fetch(`/timeline-data/${{SAFE_NAME}}`).then(r=>r.json()),
    fetch('/available-images').then(r=>r.json()),
    fetch('/composition-schemas').then(r=>r.json()),
    fetch('/compositions').then(r=>r.json()),
    fetch(`/music-plan/${{SAFE_NAME}}`).then(r=>r.json()).catch(()=>[]),
  ]);
  _renderMusicTrack(musicPlan);
  timelineItems = tlRes.items || [];
  allImages     = imgRes.images || [];
  compSchemas   = schRes.schemas    || {{}};
  compatMap     = schRes.compatible || {{}};
  typeMap       = schRes.type_map   || {{}};

  // Build graphics preset panel from live Root.tsx data
  const comps = compRes.compositions || [];
  const gfxGrid = document.getElementById('sec-gfx');
  if (gfxGrid) {{
    gfxGrid.innerHTML = comps.map(c => {{
      const tagKey = c.tagKey || '';
      const label  = c.label  || c.id;
      const icon   = c.icon   || '▪';
      return `<div class="preset-card" draggable="true"
        data-track="graphics" data-type="graphic"
        data-tag-key="${{tagKey}}" data-tag-text=""
        data-composition="${{c.id}}" data-label="${{label}}"
        ondragstart="presetDragStart(event)"
        title="${{c.id}}">
        <div class="preset-icon">${{icon}}</div>
        <div class="preset-label">${{label}}</div>
      </div>`;
    }}).join('');
  }}

  // Get project name
  const hist = await fetch('/history').then(r=>r.json());
  const proj = (hist.videos||[]).find(v=>v.safe_name===SAFE_NAME);
  document.getElementById('projName').textContent = proj ? proj.title : SAFE_NAME.replace(/_/g,' ');

  renderTimeline();
  updateTimecode();
  _initRulerInteraction();

  // Spacebar play/pause
  document.addEventListener('keydown', e => {{
    if (e.code === 'Space' && !['INPUT','TEXTAREA'].includes(e.target.tagName)) {{
      e.preventDefault();
      if (_seqPlaylist.length > 0) toggleSeqPlay();
    }}
  }});
}}

// ── Timeline render ───────────────────────────────────────────────────────────
function renderTimeline() {{
  _buildSeqTimeline();

  const tracks = {{'graphics':[], 'clips':[], 'narration':[], 'transitions':[]}};
  timelineItems.forEach(item => {{
    const t = item.track || 'graphics';
    if (tracks[t]) tracks[t].push(item);
  }});

  // Resize timeline-inner to fit all content
  const inner = document.getElementById('timelineInner');
  const totalPx = Math.max((_tlTotalDur + 20) * _pxPerSec, 800);
  if (inner) inner.style.minWidth = totalPx + 'px';

  renderRuler();
  renderTrack('transitions', tracks.transitions);
  // Graphics and clips share the video row — render both into track-video
  renderVideoTrack([...tracks.graphics, ...tracks.clips]);
  renderTrack('narration',   tracks.narration);
  updatePlayheadPos(_currentTime);
  updateTimecode();
}}

function renderVideoTrack(items) {{
  const row = document.getElementById('track-video');
  if (!row) return;
  row.innerHTML = '';
  row.setAttribute('ondragover', `trackDragOver(event,'graphics')`);
  row.setAttribute('ondragleave', `trackDragLeave(event,'graphics')`);
  row.setAttribute('ondrop', `trackDrop(event,'graphics')`);
  // Sort by start time so items layer correctly
  items.sort((a, b) => (a._startTime ?? 0) - (b._startTime ?? 0));
  items.forEach(item => row.appendChild(buildItemEl(item)));
}}

function _buildSeqTimeline() {{
  // Assign _startTime to every item based on merged graphics+clips sequence
  let t = 0;
  const seqItems = timelineItems.filter(i => i.track === 'graphics' || i.track === 'clips');
  seqItems.forEach(item => {{
    item._startTime = t;
    t += item.duration || 8;
  }});
  _tlTotalDur = t;

  // Transitions: position at the start of the next seq item after them
  timelineItems.forEach((item, idx) => {{
    if (item.track !== 'transitions') return;
    const nextSeq = timelineItems.slice(idx + 1).find(i => i.track === 'graphics' || i.track === 'clips');
    item._startTime = nextSeq ? (nextSeq._startTime ?? _tlTotalDur) : _tlTotalDur;
  }});

  // Narration: align with graphics items by index
  const gfxItems = seqItems.filter(i => i.track === 'graphics');
  let narIdx = 0;
  timelineItems.filter(i => i.track === 'narration').forEach(item => {{
    const gfx = gfxItems[narIdx] ?? gfxItems[gfxItems.length - 1];
    item._startTime = gfx ? gfx._startTime : narIdx * 8;
    narIdx++;
  }});
}}

function renderTrack(trackName, items) {{
  const row = document.getElementById('track-'+trackName);
  if (!row) return;
  row.innerHTML = '';
  row.setAttribute('ondragover', `trackDragOver(event,'${{trackName}}')`);
  row.setAttribute('ondragleave', `trackDragLeave(event,'${{trackName}}')`);
  row.setAttribute('ondrop', `trackDrop(event,'${{trackName}}')`);
  items.forEach(item => row.appendChild(buildItemEl(item)));
}}

function buildItemEl(item) {{
  const startTime  = item._startTime ?? 0;
  const dur        = item.duration || 8;
  const isSelected = item.id === selectedId;
  const isRejected = item.approved === false;
  const isRendered = item.rendered;

  // Time-based position and width
  let leftPx, widthPx;
  if (item.type === 'transition') {{
    leftPx  = startTime * _pxPerSec;
    widthPx = Math.max(32, _pxPerSec * 1);
  }} else if (item.type === 'narration') {{
    leftPx  = startTime * _pxPerSec;
    widthPx = Math.max(80, (item.words || 50) * (_pxPerSec / 60));
  }} else {{
    leftPx  = startTime * _pxPerSec;
    widthPx = Math.max(48, dur * _pxPerSec);
  }}

  const typeClass  = 'type-' + item.type;
  const unrClass   = (!isRendered && item.type !== 'transition') ? ' unrendered' : '';
  const selClass   = isSelected ? ' selected' : '';
  const rejClass   = isRejected ? ' rejected' : '';
  const dotClass   = isRendered ? 'dot-rendered' : 'dot-unrendered';
  const barClass   = isRejected ? 'bar-rejected' : 'bar-approved';
  const label      = item.content || item.tag_text || item.composition || '&#8230;';
  const labelClass = item.type === 'narration' ? 'narration' : (item.type === 'clip' ? 'clip' : 'graphic');

  const div = document.createElement('div');
  div.className  = `tl-item ${{typeClass}}${{unrClass}}${{selClass}}${{rejClass}}`;
  div.style.left  = leftPx + 'px';
  div.style.width = widthPx + 'px';
  div.dataset.id  = item.id;
  div.draggable   = true;

  // Track-relative position number
  const trackItems = timelineItems.filter(i => i.track === (item.track||'graphics'));
  const posNum = trackItems.findIndex(i => i.id === item.id) + 1;

  if (item.type === 'transition') {{
    div.innerHTML = `
      <span style="font-size:.55rem;color:#3a3a3a;overflow:hidden;text-overflow:ellipsis">&#8644;&thinsp;${{esc(item.tag_text||'')}}</span>
      <button class="tl-del" title="Delete" onclick="event.stopPropagation();deleteItem('${{item.id}}')">&#10005;</button>`;
  }} else {{
    const ts = _fmtSec(startTime);
    div.innerHTML = `
      <span class="tl-num" title="Position — click to move">${{posNum}}</span>
      <div class="tl-item-dot ${{dotClass}}"></div>
      <span class="tl-label ${{labelClass}}">${{esc(label.slice(0,30))}}</span>
      <div class="tl-approved-bar ${{barClass}}"></div>
      <span class="tl-pos">${{ts}}</span>
      <button class="tl-del" title="Delete" onclick="event.stopPropagation();deleteItem('${{item.id}}')">&#10005;</button>`;
  }}

  div.querySelector('.tl-num')?.addEventListener('click', e => {{
    e.stopPropagation();
    _startPosEdit(div, item.id, item.track||'graphics', posNum);
  }});

  div.addEventListener('click', () => selectItem(item.id));
  div.addEventListener('dragstart', e => {{
    itemDragStart(e, item.id);
    requestAnimationFrame(() => div.classList.add('dragging'));
  }});
  div.addEventListener('dragend', () => {{
    div.classList.remove('dragging');
    _dragSrc = null;
    clearDropIndicators();
  }});
  return div;
}}

// ── Time ruler ────────────────────────────────────────────────────────────────
function renderRuler() {{
  const ruler = document.getElementById('tlRuler');
  if (!ruler) return;
  ruler.querySelectorAll('.tl-tick,.tl-tick-lbl').forEach(el => el.remove());

  const totalSec = _tlTotalDur + 20;

  // Adaptive intervals based on zoom
  let majorInt = 10;
  if (_pxPerSec >= 80)  majorInt = 5;
  if (_pxPerSec >= 160) majorInt = 2;
  if (_pxPerSec >= 400) majorInt = 1;
  if (_pxPerSec <= 20)  majorInt = 30;
  if (_pxPerSec <= 10)  majorInt = 60;
  const minorInt = majorInt / 5;

  for (let t = 0; t <= totalSec; t += minorInt) {{
    const rounded  = Math.round(t * 1000) / 1000;
    const isMajor  = rounded % majorInt < 0.001 || majorInt - (rounded % majorInt) < 0.001;
    const x = rounded * _pxPerSec;

    const tick = document.createElement('div');
    tick.className = 'tl-tick ' + (isMajor ? 'major' : 'minor');
    tick.style.left = x + 'px';
    ruler.appendChild(tick);

    if (isMajor) {{
      const lbl = document.createElement('div');
      lbl.className = 'tl-tick-lbl';
      lbl.style.left = x + 'px';
      lbl.textContent = _fmtSec(rounded);
      ruler.appendChild(lbl);
    }}
  }}
}}

// ── Playhead ──────────────────────────────────────────────────────────────────
function updatePlayheadPos(sec) {{
  _currentTime = Math.max(0, sec);
  const ph = document.getElementById('tlPlayhead');
  if (ph) ph.style.left = (_currentTime * _pxPerSec) + 'px';
}}

function seekTo(sec) {{
  updatePlayheadPos(sec);
  // Scroll playhead into view
  const scroll = document.getElementById('timelineScroll');
  const x = sec * _pxPerSec;
  if (scroll && (x < scroll.scrollLeft || x > scroll.scrollLeft + scroll.clientWidth - 40)) {{
    scroll.scrollLeft = Math.max(0, x - scroll.clientWidth / 3);
  }}
  // Sync sequence preview if active
  if (_seqPlaylist.length > 0) {{
    onSeqScrub(sec);
  }}
}}

function _startPlayheadRaf() {{
  if (_tlRafId) return;
  const tick = () => {{
    if (_seqPlaying) {{
      const v = _svEl();
      // Smooth 60fps playhead update from video.currentTime (more precise than ontimeupdate)
      if (v && !v.paused && v.src) {{
        const item    = _seqPlaylist[_seqIdx];
        const tlItem  = timelineItems.find(i => i.id === item?.tlId);
        const base    = tlItem?._startTime ?? _seqGlobalSecBeforeIdx(_seqIdx);
        const local   = Math.max(0, v.currentTime - (item?.trimInFrames ?? 0) / 30);
        updatePlayheadPos(base + local);
      }}
    }}
    _tlRafId = requestAnimationFrame(tick);
  }};
  _tlRafId = requestAnimationFrame(tick);
}}

function _stopPlayheadRaf() {{
  if (_tlRafId) {{ cancelAnimationFrame(_tlRafId); _tlRafId = null; }}
}}

// ── Ruler mouse interaction ───────────────────────────────────────────────────
function _initRulerInteraction() {{
  const ruler  = document.getElementById('tlRuler');
  const scroll = document.getElementById('timelineScroll');
  const handle = document.getElementById('tlPlayheadHandle');
  if (!ruler || !handle) return;

  function _xToSec(clientX) {{
    const rect = ruler.getBoundingClientRect();
    return Math.max(0, (clientX - rect.left + scroll.scrollLeft) / _pxPerSec);
  }}

  // Click on ruler to seek
  ruler.addEventListener('mousedown', e => {{
    if (handle.contains(e.target)) return;
    _tlDragging = true;
    seekTo(_xToSec(e.clientX));
    e.preventDefault();
  }});

  // Drag handle
  handle.addEventListener('mousedown', e => {{
    _tlDragging = true;
    e.stopPropagation();
    e.preventDefault();
  }});

  document.addEventListener('mousemove', e => {{
    if (!_tlDragging) return;
    updatePlayheadPos(_xToSec(e.clientX));
  }});

  document.addEventListener('mouseup', e => {{
    if (!_tlDragging) return;
    _tlDragging = false;
    seekTo(_xToSec(e.clientX));
  }});

  // Ctrl+wheel to zoom
  const inner = document.getElementById('timelineInner');
  inner?.addEventListener('wheel', e => {{
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.25 : 1 / 1.25;
    const anchorSec = _xToSec(e.clientX);
    const anchorPx  = anchorSec * _pxPerSec;
    _pxPerSec = Math.max(8, Math.min(240, _pxPerSec * factor));
    renderTimeline();
    // Keep anchor point stable during zoom
    if (scroll) scroll.scrollLeft += anchorSec * _pxPerSec - anchorPx;
  }}, {{passive: false}});
}}

// ── Select & inspect ──────────────────────────────────────────────────────────
function selectItem(id) {{
  selectedId = id;
  const item = timelineItems.find(i => i.id === id);
  if (!item) return;

  // Highlight on timeline
  document.querySelectorAll('.tl-item').forEach(el => {{
    el.classList.toggle('selected', el.dataset.id === id);
  }});

  // Preview
  updatePreview(item);

  // Inspector
  renderInspector(item);
}}

function updatePreview(item) {{
  // Don't hijack the video element while sequence is playing
  if (_seqPlaying && _seqInline) return;

  const video    = document.getElementById('previewVideo');
  const image    = document.getElementById('previewImage');
  const placeholder = document.getElementById('previewPlaceholder');
  const piType   = document.getElementById('piType');
  const piTitle  = document.getElementById('piTitle');
  const piAct    = document.getElementById('piAct');
  const piMeta   = document.getElementById('piMeta');
  const piNarr   = document.getElementById('piNarr');

  piType.textContent  = (item.type || '').toUpperCase();
  piTitle.textContent = item.content || item.tag_text || item.composition || item.id;
  piAct.textContent   = item.act || '';

  const _hideAllMedia = () => {{
    video.style.display = 'none';
    image.style.display = 'none';
    placeholder.style.display = 'none';
  }};

  if (item.type === 'narration') {{
    _hideAllMedia();
    placeholder.style.display = 'flex';
    placeholder.querySelector('.preview-placeholder-label').textContent = 'Narration';
    piNarr.style.display = '';
    piNarr.textContent = item.full_text || item.content || '';
    piMeta.textContent = `~${{item.words||0}} words · ~${{item.duration||0}}s`;
  }} else if (item.rendered && item.filename) {{
    _hideAllMedia();
    video.style.display = 'block';
    const _bust = item._cacheBust ? `?t=${{item._cacheBust}}` : '';
    video.src = `/video/${{SAFE_NAME}}/${{encodeURIComponent(item.filename)}}${{_bust}}`;
    video.load();
    piNarr.style.display = 'none';
    piMeta.textContent = item.filename;
    const tc = document.getElementById('timecode');
    video.ontimeupdate = () => {{
      const t = video.currentTime;
      const m = Math.floor(t/60), s = Math.floor(t%60),
            cs = String(Math.floor((t%1)*100)).padStart(2,'0');
      tc.textContent = `${{String(m).padStart(2,'0')}}:${{String(s).padStart(2,'0')}}.${{cs}}`;
      tc.classList.add('playing');
    }};
    video.onpause = video.onended = () => {{ tc.classList.remove('playing'); updateTimecode(); }};
  }} else if (item.preview_rendered && item.preview_filename) {{
    // Option A: PNG still preview (mp4 not yet rendered)
    _hideAllMedia();
    image.style.display = 'block';
    const _bust = item._cacheBust ? `?t=${{item._cacheBust}}` : '';
    image.src = `/preview/${{SAFE_NAME}}/${{encodeURIComponent(item.preview_filename)}}${{_bust}}`;
    piNarr.style.display = 'none';
    piMeta.textContent = '◉ Preview · ' + item.preview_filename + ' · mp4 pending';
  }} else {{
    _hideAllMedia();
    placeholder.style.display = 'flex';
    placeholder.querySelector('.preview-placeholder-label').textContent =
      item.composition || item.type || 'Not rendered';
    piNarr.style.display = 'none';
    piMeta.textContent = '⧗ Not yet rendered';
  }}
}}

// ── Inspector tabs ─────────────────────────────────────────────────────────────
function switchTab(tab) {{
  _inspTab = tab;
  document.getElementById('tab-props').classList.toggle('active', tab==='props');
  document.getElementById('tab-json').classList.toggle('active', tab==='json');
  const item = getSelectedItem();
  if (item) renderInspector(item);
}}

// ── Inspector ─────────────────────────────────────────────────────────────────
function renderInspector(item) {{
  document.getElementById('inspType').textContent = (item.type||'').toUpperCase() + (item.composition ? ' · '+item.composition : '');
  document.getElementById('inspName').textContent = item.content || item.tag_text || item.composition || item.id;

  // Show/hide tabs
  const hasTabs = item.type !== 'narration' && item.type !== 'transition';
  document.getElementById('inspTabs').style.display = hasTabs ? '' : 'none';

  const body = document.getElementById('inspBody');
  body.innerHTML = '';

  if (item.type === 'narration') {{
    body.innerHTML = `<div style="font-size:.72rem;color:#444;line-height:1.7;padding:4px 0">${{esc(item.full_text||item.content||'')}}</div>`;
    return;
  }}

  if (item.type === 'transition') {{
    body.innerHTML = `<div style="font-size:.72rem;color:#444;padding:4px 0">Transition: <b style="color:#888">${{esc(item.tag_text||'')}}</b></div>`;
    return;
  }}

  // JSON tab — full editable props
  if (_inspTab === 'json') {{
    renderInspectorJSON(item, body);
    return;
  }}

  // Approve row
  const appDiv = document.createElement('div');
  appDiv.className = 'insp-section';
  const approved = item.approved !== false;
  appDiv.innerHTML = `
    <div class="insp-section-head">Status</div>
    <div class="insp-row">
      <span class="insp-label">Approval</span>
      <div class="approve-toggle">
        <button id="app-yes" class="approve ${{approved?'active':''}}"
                onclick="setApproval(true)">&#10003; Approve</button>
        <button id="app-no"  class="reject ${{!approved?'active':''}}"
                onclick="setApproval(false)">&#10007; Reject</button>
      </div>
    </div>
    <div class="prop-field" style="margin-top:6px">
      <div class="prop-label">Note</div>
      <textarea class="note-ta" rows="2" id="inspNote"
        onblur="saveNote(this.value)">${{esc(item.note||'')}}</textarea>
    </div>`;
  body.appendChild(appDiv);

  // Composition selector (graphic and clip items)
  if (item.type === 'graphic' || item.type === 'clip') {{
    const comp  = item.composition || '';
    const alts  = (compatMap[item.track==='graphics'?'':item.type]||[]).filter(c=>c!==comp);
    const allC  = [comp,...alts].filter(Boolean);
    if (allC.length) {{
      const sec = document.createElement('div');
      sec.className = 'insp-section';
      sec.innerHTML = `<div class="insp-section-head">Template</div>
        <select class="comp-select" onchange="changeComp(this.value)">
          ${{allC.map(c=>`<option value="${{c}}" ${{c===comp?'selected':''}}>${{c}}</option>`).join('')}}
        </select>`;
      body.appendChild(sec);
    }}
  }}

  // Props fields
  // Default clip items to HeroClipSingle if no composition saved
  if (item.type === 'clip' && !item.composition) item.composition = 'HeroClipSingle';
  const comp    = item.composition || '';
  const schema  = compSchemas[comp] || [];

  if (schema.length || Object.keys(item.props||{{}}).length || item.type === 'clip') {{
    const sec = document.createElement('div');
    sec.className = 'insp-section';
    sec.innerHTML = `<div class="insp-section-head">Properties</div>`;
    if (schema.length) {{
      schema.forEach(field => {{
        if (field.type === 'player_list') {{
          sec.appendChild(buildPlayerListField(item, field));
        }} else {{
          sec.appendChild(buildPropField(item.props||{{}}, field, item.id));
        }}
      }});
    }} else {{
      // JSON fallback
      const ta = document.createElement('textarea');
      ta.className = 'note-ta';
      ta.style.cssText = 'min-height:120px;font-family:monospace;font-size:.68rem;color:#6a9060';
      ta.value = JSON.stringify(item.props||{{}}, null, 2);
      ta.id = 'json-editor';
      sec.appendChild(ta);
    }}
    body.appendChild(sec);
  }}

  // Re-render buttons (graphic AND clip items)
  if (item.type === 'graphic' || item.type === 'clip') {{
    const sec = document.createElement('div');
    sec.className = 'insp-section';
    sec.innerHTML = `
      <div style="display:flex;gap:6px">
        <button class="render-btn" id="previewBtn" onclick="doPreviewRender()" style="flex:1;background:#222">
          ◉ Quick preview (PNG)
        </button>
        <button class="render-btn" id="renderBtn" onclick="doRerender()" style="flex:1">
          &#8635; ${{item.rendered ? 'Re-render mp4' : 'Render mp4'}}
        </button>
      </div>
      <div class="render-status" id="renderStatus"></div>`;
    body.appendChild(sec);
  }}

  // Transition out picker (clip and graphic items only)
  if (item.type === 'graphic' || item.type === 'clip') {{
    const curTrans = item.transition || 'none';
    const sec = document.createElement('div');
    sec.className = 'insp-section';
    sec.innerHTML = `<div class="insp-section-head">Transition out</div>
      <div class="trans-pick">
        ${{TRANS_TYPES.map(t => `<button class="trans-opt${{t===curTrans?' active':''}}" data-t="${{t}}"
          onclick="setItemTransition('${{item.id}}','${{t}}')">${{t}}</button>`).join('')}}
      </div>`;
    body.appendChild(sec);
  }}

  // Clip footage search hint + splice
  if (item.type === 'clip') {{
    const yt = `https://www.youtube.com/results?search_query=${{encodeURIComponent(item.content||item.tag_text||'')}}`;
    const sec = document.createElement('div');
    sec.className = 'insp-section';
    sec.innerHTML = `
      <div class="insp-section-head">Footage needed</div>
      <div style="font-size:.72rem;color:#555;line-height:1.6;margin-bottom:6px">${{esc(item.full_text||item.content||item.tag_text||'')}}</div>
      <div style="font-size:.65rem;color:#3a3a3a;margin-bottom:6px">Duration: ${{item.duration||'?'}}s</div>
      <a href="${{yt}}" target="_blank" class="yt-link">Search YouTube &#8599;</a>`;
    body.appendChild(sec);

    // Splice section
    const splSec = document.createElement('div');
    splSec.className = 'insp-section';
    splSec.innerHTML = `
      <div class="insp-section-head">Splice</div>
      <div style="font-size:.65rem;color:#444;margin-bottom:4px">Split this clip into two at a timecode</div>
      <div class="splice-row">
        <input id="spliceInput" class="splice-input" type="number" min="0.1"
          max="${{item.duration||8}}" step="0.5" placeholder="seconds"
          value="${{Math.round((item.duration||8)/2)}}" />
        <button class="splice-btn" onclick="spliceItem('${{item.id}}')">&#9144; Splice</button>
      </div>`;
    body.appendChild(splSec);
  }}
}}

function _starterProps(item) {{
  // Parse starter props from tag text when props is empty
  const t = item.tag_text || '';
  const comp = item.composition || '';
  if (comp === 'HeroBigStat') {{
    const parts = t.split(',').map(s=>s.trim());
    return {{ stat: parts[0]||'', unit: parts[1]||'', label: parts[2]||'', context: parts[3]||'', bgColor:'#f0ece4', accentColor:'#C8102E' }};
  }}
  if (comp === 'CareerTimeline') {{
    const player = t.split('·')[0]?.trim() || t;
    return {{ playerName: player, bgColor:'#f0ece4', events:[] }};
  }}
  if (comp === 'HeroIntro') {{
    return {{ subtitle: t, bgColor:'#f0ece4' }};
  }}
  if (comp === 'AttackingRadar') {{
    const parts = t.split(',').map(s=>s.trim());
    return {{ entityName: parts[0]||t, club: parts[1]||'', competition: parts[2]||'', season: parts[3]||'', bgColor:'#f0ece4', metrics:[] }};
  }}
  if (comp === 'PlayerTrio') {{
    const parts = t.replace(/^the debate,\s*/i,'').split(/\s+vs\s+/i);
    return {{ title:'the debate', bgColor:'#f0ece4',
      players: parts.map(n=>({{name:n.trim(),image:'',club:'',clubColor:'#C8102E',stat:'',statLabel:''}})) }};
  }}
  if (comp === 'TopScorersTable' || comp === 'PremierLeagueTable') {{
    const parts = t.split(',').map(s=>s.trim());
    return {{ competition: parts[0]||'', season: parts[1]||'', bgColor:'#f0ece4', players:[] }};
  }}
  if (comp === 'TeamLineup') {{
    const parts = t.split(',').map(s=>s.trim());
    return {{ teamName: parts[0]||'', formation:'4-3-3', opposition: parts[1]||'', date: parts[2]||'', teamColor:'#C8102E', bgColor:'#f0ece4', players:[] }};
  }}
  if (comp === 'MatchResult') {{
    const parts = t.split(',').map(s=>s.trim());
    return {{ homeTeam: parts[0]||'', awayTeam: parts[1]||'', homeScore:0, awayScore:0, competition:'', date: parts[2]||'', homeColor:'#C8102E', awayColor:'#034694', bgColor:'#f0ece4' }};
  }}
  if (comp === 'HeroFormRun') {{
    const parts = t.split(',').map(s=>s.trim());
    return {{ teamName: parts[0]||'', teamColor:'#C8102E', label:'last 10 matches', results:[], bgColor:'#f0ece4' }};
  }}
  if (comp === 'HeroClipSingle') {{
    // Tag text format: "description, label" or just "description"
    const parts = t.split(',').map(s=>s.trim());
    return {{ clip:'', label: parts[parts.length-1]||'', title: parts[0]||'', bgColor:'#0d0d0d' }};
  }}
  if (comp === 'HeroClipCompare') {{
    const parts = t.split(',').map(s=>s.trim());
    return {{ clipLeft:'', clipRight:'', labelLeft: parts[0]||'', labelRight: parts[1]||'', title: parts[0]||'', bgColor:'#0d0d0d' }};
  }}
  // ── No-builder compositions: scaffolds matching _COMPOSITION_SCHEMAS so the
  //    inspector hands the user an editable, schema-valid payload. Re-render
  //    succeeds against Remotion's Zod schemas with these defaults. The user
  //    edits the meaningful fields before clicking Re-render.
  if (comp === 'ArticleHeadline') {{
    return {{ headline: t || 'Headline', publication:'', category:'', author:'', byline:'', date:'', edition:'', lede:'', imageSrc:'', imageCaption:'', highlightColor:'#C8102E', accentColor:'#C8102E', bgColor:'#f0ece4' }};
  }}
  if (comp === 'CountdownReveal') {{
    return {{ title: t || 'Title', subtitle:'', accentColor:'#C8102E', teamColor:'#C8102E', bgColor:'#f0ece4', darkMode:false, dwellFrames:'' }};
  }}
  if (comp === 'ScoutReport') {{
    return {{ playerName: t || 'Player', playerImageSlug:'', origin:'', league:'', competition:'', playerAge:'', signingFee:'', signingYear:'', headline:'', dateline:'', source:'', badgeSlug:'', clubColor:'#C8102E', accentColor:'#C8102E', bgColor:'#f0ece4' }};
  }}
  if (comp === 'TrioFeature') {{
    return {{ bgColor:'#f0ece4', players:[] }};
  }}
  if (comp === 'AnnotatedImage') {{
    return {{ imageSrc:'', caption: t || '', accentColor:'#C8102E', bgColor:'#f0ece4' }};
  }}
  if (comp === 'HeroNewsFeed') {{
    return {{ title: t || 'News', subtitle:'', accentColor:'#C8102E', bgColor:'#0d0d0d', headlines:[] }};
  }}
  if (comp === 'HeroPlayerRevealTrio') {{
    return {{ title: t || '', subtitle:'', accentColor:'#C8102E', bgColor:'#0d0d0d', players:[] }};
  }}
  if (comp === 'HeroTransferProfit') {{
    return {{ title: t || 'Transfer profit', subtitle:'', accentColor:'#C8102E', bgColor:'#0d0d0d', rows:[] }};
  }}
  if (comp === 'MapCallout') {{
    return {{ title: t || '', accentColor:'#C8102E', bgColor:'#f0ece4' }};
  }}
  if (comp === 'PortraitStatHero') {{
    const parts = t.split(',').map(s=>s.trim());
    return {{ playerName: parts[0]||'', playerImage:'', stat: parts[1]||'', statLabel: parts[2]||'', accentColor:'#C8102E', bgColor:'#f0ece4' }};
  }}
  if (comp === 'PortraitWithBars') {{
    return {{ playerName: t || '', playerImage:'', title:'', accentColor:'#C8102E', bgColor:'#f0ece4', bars:[] }};
  }}
  if (comp === 'StatPulse') {{
    const parts = t.split(',').map(s=>s.trim());
    return {{ stat: parts[0]||'', label: parts[1]||'', accentColor:'#C8102E', bgColor:'#f0ece4' }};
  }}
  if (comp === 'TimelineScroll') {{
    return {{ title: t || '', accentColor:'#C8102E', bgColor:'#f0ece4', events:[] }};
  }}
  if (comp === 'ValueCurve') {{
    return {{ playerName: t || '', accentColor:'#C8102E', bgColor:'#f0ece4', points:[] }};
  }}
  // Generic fallback (compositions whose Zod schemas are fully optional with
  // .default() — covers HeroDualPanel, HeroPhotoReel, HeroContactSheet,
  // HeroGoalRush, HeroHeadlineStack, and any new template not yet enumerated).
  return {{ bgColor:'#f0ece4' }};
}}

function renderInspectorJSON(item, body) {{
  const hasProps = item.props && Object.keys(item.props).length > 0;
  const allProps = hasProps ? Object.assign({{}}, item.props) : _starterProps(item);

  const meta = document.createElement('div');
  meta.className = 'insp-section';

  const headerNote = hasProps
    ? 'edit &rarr; Apply, then Re-render'
    : '&#9888; No props from original render &mdash; starter props generated from tag';

  meta.innerHTML = `
    <div class="insp-section-head" style="display:flex;align-items:baseline;gap:6px">
      Full Props JSON
      <span style="font-size:.55rem;color:${{hasProps?'#2a2a2a':'#8a6a20'}};font-weight:400">${{headerNote}}</span>
    </div>
    ${{!hasProps?`<div style="font-size:.6rem;color:#3a3a3a;margin-bottom:6px;padding:5px 6px;background:#120f00;border:1px solid #2a1f00;border-radius:3px">
      Tag: <span style="color:#6a5a30;font-family:monospace">${{esc(item.tag||item.tag_text||'')}}</span>
    </div>`:''}}
  `;

  const ta = document.createElement('textarea');
  ta.className = 'json-ta';
  ta.id = 'jsonPropsTA';
  ta.value = JSON.stringify(allProps, null, 2);
  meta.appendChild(ta);

  // When the original render produced no manifest entry, ask the server to
  // rebuild props from the tag text via _PAYLOAD_BUILDERS. Covers every
  // composition with a server-side builder — lookup-driven (TournamentBracket)
  // and LLM-driven alike. If the builder returns nothing (rate limit, lookup
  // miss, no API key), we keep whatever _starterProps() generated as fallback.
  const SERVER_RESOLVED = new Set([
    'TournamentBracket',
    'HeroStatBars', 'HeroFormRun', 'HeroTactical', 'HeroBigStat',
    'HeroLeagueGraph', 'HeroTransferRecord', 'HeroIntro', 'HeroOutro',
    'HeroQuote', 'HeroChapterWord', 'HeroConceptCard', 'HeroClipCompare',
    'HeroScatterPlot', 'HeroShotMap', 'HeroMatchTimeline', 'HeroAwardsList',
    'HeroComparisonRadar', 'HeroSeasonTimeline',
    'PlayerTrio', 'PlayerStats', 'AttackingRadar', 'MatchResult',
    'Transfer', 'Trophy', 'CareerTimeline', 'SeasonComparison',
    'TeamLineup', 'DisciplinaryRecord', 'QuoteCard',
    'PremierLeagueTable', 'StandingsTable', 'TopScorersTable', 'TopAssistsTable',
  ]);
  if (!hasProps && SERVER_RESOLVED.has(item.composition)) {{
    fetch('/resolve-tag-props', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        composition: item.composition,
        tag_text:    item.tag_text || item.content || ''
      }})
    }}).then(r => r.json()).then(j => {{
      if (j && j.ok && j.props) {{
        ta.value = JSON.stringify(j.props, null, 2);
      }}
    }}).catch(() => {{}});
  }}

  const errDiv = document.createElement('div');
  errDiv.className = 'json-err';
  errDiv.id = 'jsonPropsErr';
  meta.appendChild(errDiv);

  const applyBtn = document.createElement('button');
  applyBtn.className = 'json-apply-btn';
  applyBtn.innerHTML = '&#10003; Apply JSON';
  applyBtn.onclick = () => {{
    try {{
      const parsed = JSON.parse(ta.value);
      item.props = parsed;
      errDiv.textContent = '';
      applyBtn.innerHTML = '&#10003; Applied';
      markUnsaved();
      scheduleSave();
      setTimeout(()=>applyBtn.innerHTML='&#10003; Apply JSON', 1500);
    }} catch(ex) {{
      errDiv.textContent = 'Invalid JSON: ' + ex.message;
    }}
  }};

  // Ctrl+S inside textarea also applies
  ta.addEventListener('keydown', e => {{
    if ((e.ctrlKey||e.metaKey) && e.key==='s') {{ e.preventDefault(); applyBtn.click(); }}
  }});

  meta.appendChild(applyBtn);
  body.appendChild(meta);
}}

// playerIdx: when set, writes to item.props.players[playerIdx][key] via updatePlayerProp
//            when null/undefined, writes to item.props[key] via updateProp
function buildPropField(props, field, itemId, playerIdx) {{
  const val = props ? (props[field.key] ?? '') : '';
  const div = document.createElement('div');
  div.className = 'prop-field';

  const isPlayer = playerIdx != null;
  const updateFn = isPlayer
    ? `updatePlayerProp(${{playerIdx}},'${{field.key}}',`
    : `updateProp('${{field.key}}',`;

  if (field.type === 'text') {{
    div.innerHTML = `<div class="prop-label">${{field.label}}</div>
      <input class="prop-input" type="text" value="${{esc(String(val))}}"
             oninput="${{updateFn}}this.value)">`;
  }} else if (field.type === 'color') {{
    div.innerHTML = `<div class="prop-label">${{field.label}}</div>
      <div class="prop-color-row">
        <input type="color" value="${{val||'#C9A84C'}}"
               oninput="${{updateFn}}this.value);this.nextSibling.style.background=this.value">
        <div class="color-swatch" style="background:${{val||'#C9A84C'}}"></div>
        <input class="prop-input" style="flex:1" type="text" value="${{val||''}}"
               oninput="${{updateFn}}this.value)">
      </div>`;
  }} else if (field.type === 'boolean') {{
    div.innerHTML = `<div style="display:flex;align-items:center;gap:6px">
        <input type="checkbox" ${{val?'checked':''}} style="accent-color:var(--gold)"
               onchange="${{updateFn}}this.checked)">
        <span class="prop-label" style="margin:0">${{field.label}}</span>
      </div>`;
  }} else if (field.type === 'image') {{
    div.innerHTML = `<div class="prop-label">${{field.label}}</div>
      <div class="slot-img-row">
        ${{val?`<img class="slot-thumb" id="img-${{field.key}}"
             src="/player-thumb/${{encodeURIComponent(val+(val.includes('.')?'':'.png'))}}"
             onerror="this.style.display='none'">`:`<div class="slot-thumb-empty" id="img-${{field.key}}">?</div>`}}
        <span class="slot-img-slug" id="slug-${{field.key}}">${{val||'(none)'}}</span>
        <button class="swap-btn" onclick="openImgModal(null,'${{field.key}}',null)">Swap</button>
      </div>`;
  }} else if (field.type === 'clip_file') {{
    const isVideo = val && (val.endsWith('.mp4')||val.endsWith('.webm')||val.endsWith('.mov'));
    div.innerHTML = `<div class="prop-label">${{field.label}}</div>
      <div class="clip-file-row" id="cliprow-${{field.key}}">
        ${{val
          ? isVideo
            ? `<video class="clip-thumb" src="/remotion-public/${{encodeURIComponent(val)}}" muted loop autoplay playsinline></video>`
            : `<img class="clip-thumb" src="/remotion-public/${{encodeURIComponent(val)}}" onerror="this.style.display='none'">`
          : `<div class="clip-thumb-empty">no clip</div>`}}
        <div class="clip-file-info">
          <span class="slot-img-slug" id="clipslug-${{field.key}}">${{val||'(none)'}}</span>
          <label class="upload-zone" style="margin-top:4px" title="Upload footage">
            <input type="file" accept="video/mp4,video/webm,video/quicktime,image/*" style="display:none"
                   onchange="handleClipUpload(this,'${{field.key}}')">
            &#8679; Upload footage
          </label>
        </div>
      </div>`;
  }}
  return div;
}}

function buildPlayerListField(item, field) {{
  const players = (item.props && item.props.players) || [
    {{name:'Player 1',image:'',club:'',clubColor:'#C8102E',stat:'',statLabel:''}},
    {{name:'Player 2',image:'',club:'',clubColor:'#004D98',stat:'',statLabel:''}},
    {{name:'Player 3',image:'',club:'',clubColor:'#DA291C',stat:'',statLabel:''}},
  ];
  // Ensure item.props.players is initialised so updatePlayerProp has a target
  if (!item.props) item.props = {{}};
  if (!item.props.players) item.props.players = players.map(p => Object.assign({{}}, p));

  const div = document.createElement('div');
  div.innerHTML = `<div class="prop-label" style="margin-bottom:6px">${{field.label}}</div>`;
  players.forEach((p, pi) => {{
    const slug = p.image || '';
    const slot = document.createElement('div');
    slot.className = 'player-slot';
    slot.innerHTML = `<div class="slot-num">Player ${{pi+1}}</div>
      <div class="slot-img-row">
        ${{slug?`<img class="slot-thumb" id="pthumb-${{pi}}"
             src="/player-thumb/${{encodeURIComponent(slug+(slug.includes('.')?'':'.png'))}}"
             onerror="this.style.display='none';this.nextSibling.style.display='flex'">`:''}}<div class="slot-thumb-empty" id="pempty-${{pi}}" style="display:${{slug?'none':'flex'}}">?</div>
        <span class="slot-img-slug" id="pslug-${{pi}}">${{slug||'(none)'}}</span>
        <button class="swap-btn" onclick="openImgModal(null,null,${{pi}})">Swap</button>
      </div>`;
    // Pass playerIdx so sub-field updates go to players[pi] not props root
    (field.subfields||[]).filter(sf=>sf.key!=='image').forEach(sf => {{
      slot.appendChild(buildPropField(p, sf, item.id + '-p' + pi, pi));
    }});
    div.appendChild(slot);
  }});
  return div;
}}

// ── Prop updates ──────────────────────────────────────────────────────────────
function getSelectedItem() {{
  return timelineItems.find(i=>i.id===selectedId);
}}

function markUnsaved() {{
  _unsaved = true;
  const b = document.getElementById('autosaveBadge');
  if (b) {{ b.textContent = '● unsaved'; b.classList.remove('saved'); }}
}}

function markSaved() {{
  _unsaved = false;
  const b = document.getElementById('autosaveBadge');
  if (b) {{ b.textContent = '✓ saved'; b.classList.add('saved'); setTimeout(()=>{{ b.classList.remove('saved'); b.textContent='· saved'; }}, 2000); }}
}}

function scheduleSave() {{
  clearTimeout(_autosaveTimer);
  _autosaveTimer = setTimeout(async () => {{
    const res = await fetch('/timeline-save/'+SAFE_NAME, {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{items: timelineItems}})
    }}).then(r=>r.json());
    if (res.ok) markSaved();
  }}, 1500);
}}

function updateProp(key, value) {{
  const item = getSelectedItem();
  if (!item) return;
  if (!item.props) item.props = {{}};
  item.props[key] = value;
  markUnsaved();
  scheduleSave();
}}

function updatePlayerProp(pi, key, value) {{
  const item = getSelectedItem();
  if (!item) return;
  if (!item.props) item.props = {{}};
  if (!item.props.players) item.props.players = [{{}},{{}},{{}}];
  if (!item.props.players[pi]) item.props.players[pi] = {{}};
  item.props.players[pi][key] = value;
  markUnsaved();
  scheduleSave();
}}

function setApproval(val) {{
  const item = getSelectedItem();
  if (!item) return;
  item.approved = val;
  document.getElementById('app-yes').classList.toggle('active', val);
  document.getElementById('app-no').classList.toggle('active', !val);
  // Update timeline item appearance
  const el = document.querySelector(`.tl-item[data-id="${{item.id}}"]`);
  if (el) {{
    el.classList.toggle('rejected', !val);
    const bar = el.querySelector('.tl-approved-bar');
    if (bar) {{ bar.className = 'tl-approved-bar ' + (val?'bar-approved':'bar-rejected'); }}
  }}
  // Persist immediately for approval changes
  markUnsaved();
  scheduleSave();
  fetch('/timeline-item-update/'+SAFE_NAME, {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{id:item.id, filename:item.filename||'', patch:{{approved:val}}}})
  }});
}}

function saveNote(text) {{
  const item = getSelectedItem();
  if (!item) return;
  item.note = text;
  markUnsaved();
  scheduleSave();
  fetch('/timeline-item-update/'+SAFE_NAME, {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{id:item.id, filename:item.filename||'', patch:{{note:text}}}})
  }});
}}

function changeComp(comp) {{
  const item = getSelectedItem();
  if (!item) return;
  item.composition = comp;
  renderInspector(item);
  showToast(`Template &#8594; ${{comp}}`, 'ok');
}}

// ── Quick PNG preview re-render (Option A) ───────────────────────────────────
async function doPreviewRender() {{
  const item = getSelectedItem();
  if (!item) return;
  const btn    = document.getElementById('previewBtn');
  const status = document.getElementById('renderStatus');

  const je = document.getElementById('json-editor');
  if (je) {{
    try {{ item.props = JSON.parse(je.value); }}
    catch(e) {{ status.textContent='Invalid JSON'; return; }}
  }}

  if (!item.filename) {{
    const slug = (item.tag_text||item.composition||'item').toLowerCase().replace(/[^a-z0-9]+/g,'_').slice(0,35);
    item.filename = `${{(item.composition||'graphic').toLowerCase()}}_${{slug}}.mp4`;
  }}

  btn.disabled = true; btn.textContent = '◉ Preview…';
  status.textContent = 'Rendering still…'; status.style.color = 'var(--gold)';

  const res = await fetch('/re-render-preview', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      safe_name:   SAFE_NAME,
      filename:    item.filename,
      composition: item.composition || '',
      props:       item.props || {{}}
    }})
  }}).then(r=>r.json());

  if (res.error) {{
    status.textContent = res.error; status.style.color = 'var(--red)';
    btn.disabled = false; btn.textContent = '◉ Quick preview (PNG)'; return;
  }}

  item.preview_filename = res.preview_filename;
  const jobKey = res.job_key;

  const poll = setInterval(async () => {{
    const s = await fetch(`/job-status/${{jobKey.split('/').map(encodeURIComponent).join('/')}}`).then(r=>r.json());
    if (s.status === 'done') {{
      clearInterval(poll);
      item.preview_rendered = true;
      item._cacheBust = Date.now();
      status.textContent = '✓ Preview ready'; status.style.color = 'var(--green)';
      btn.disabled = false; btn.textContent = '◉ Quick preview (PNG)';
      updatePreview(item);
      saveTimeline(true);
    }} else if (s.status === 'failed') {{
      clearInterval(poll);
      status.textContent = 'Preview failed'+(s.error?': '+s.error:'');
      status.style.color = 'var(--red)';
      btn.disabled = false; btn.textContent = '◉ Quick preview (PNG)';
    }}
  }}, 1000);
}}

// ── Re-render ─────────────────────────────────────────────────────────────────
async function doRerender() {{
  const item = getSelectedItem();
  if (!item) return;
  const btn    = document.getElementById('renderBtn');
  const status = document.getElementById('renderStatus');

  // Try JSON editor fallback
  const je = document.getElementById('json-editor');
  if (je) {{
    try {{ item.props = JSON.parse(je.value); }}
    catch(e) {{ status.textContent='Invalid JSON'; return; }}
  }}

  btn.disabled = true; btn.textContent = '&#8635; Rendering&#8230;';
  status.textContent = 'Queued&#8230;'; status.style.color = 'var(--gold)';

  // Generate filename if needed
  if (!item.filename) {{
    const slug = (item.tag_text||item.composition||'item').toLowerCase().replace(/[^a-z0-9]+/g,'_').slice(0,35);
    item.filename = `${{(item.composition||'graphic').toLowerCase()}}_${{slug}}.mp4`;
  }}

  const res = await fetch('/re-render', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      safe_name: SAFE_NAME,
      filename:  item.filename,
      type:      item.track === 'graphics' ? (typeMap[item.tag_key?.toLowerCase().replace(/ /g,'_')] ? item.tag_key?.toLowerCase().replace(/ /g,'_') : 'graphic') : item.type,
      composition: item.composition || '',
      props: item.props || {{}}
    }})
  }}).then(r=>r.json());

  if (res.error) {{
    status.textContent = res.error; status.style.color = 'var(--red)';
    btn.disabled = false; btn.textContent = '&#8635; Re-render'; return;
  }}

  const poll = setInterval(async () => {{
    const s = await fetch(`/re-render-status/${{SAFE_NAME}}/${{item.filename}}`).then(r=>r.json());
    if (s.status === 'done') {{
      clearInterval(poll);
      item.rendered = true;
      status.textContent = '&#10003; Done'; status.style.color = 'var(--green)';
      btn.disabled = false; btn.textContent = '&#8635; Re-render';
      // Update timeline item dot
      const el = document.querySelector(`.tl-item[data-id="${{item.id}}"]`);
      if (el) {{
        el.classList.remove('unrendered');
        const dot = el.querySelector('.tl-item-dot');
        if (dot) {{ dot.className = 'tl-item-dot dot-rendered'; }}
      }}
      // Cache-bust so browser loads the fresh re-render
      item._cacheBust = Date.now();
      updatePreview(item);
      // Auto-save props so they persist on restart
      saveTimeline(true);
      showToast('Render complete — props saved', 'ok');
    }} else if (s.status === 'failed') {{
      clearInterval(poll);
      status.textContent = 'Failed'+(s.error?': '+s.error:'');
      status.style.color = 'var(--red)';
      btn.disabled = false; btn.textContent = '&#8635; Re-render';
    }}
  }}, 2000);
}}

// ── Position number editing ────────────────────────────────────────────────────
function _startPosEdit(itemEl, itemId, trackName, currentPos) {{
  const badge = itemEl.querySelector('.tl-num');
  if (!badge) return;

  const trackItems = timelineItems.filter(i => i.track === trackName);
  const max = trackItems.length;

  const input = document.createElement('input');
  input.type    = 'number';
  input.min     = 1;
  input.max     = max;
  input.value   = currentPos;
  input.className = 'tl-num-input';
  badge.replaceWith(input);
  input.focus();
  input.select();

  const commit = () => {{
    const n = parseInt(input.value);
    if (!isNaN(n) && n >= 1 && n <= max && n !== currentPos) {{
      _moveItemToPos(itemId, trackName, n);
    }} else {{
      renderTimeline();
    }}
  }};
  input.addEventListener('blur',    commit);
  input.addEventListener('keydown', e => {{
    if (e.key === 'Enter')  {{ e.preventDefault(); input.blur(); }}
    if (e.key === 'Escape') {{ input.removeEventListener('blur', commit); renderTimeline(); }}
    e.stopPropagation();
  }});
}}

function _moveItemToPos(itemId, trackName, newPos) {{
  const srcIdx = timelineItems.findIndex(i => i.id === itemId);
  if (srcIdx === -1) return;
  const [moved] = timelineItems.splice(srcIdx, 1);
  moved.track = trackName;

  // Find the global index of the item currently at newPos in this track (after splice)
  const trackItems = timelineItems.filter(i => i.track === trackName);
  const target = trackItems[newPos - 1]; // 0-indexed
  let insertIdx;
  if (target) {{
    insertIdx = timelineItems.findIndex(i => i.id === target.id);
  }} else {{
    // Past end — insert after last item in track
    let last = -1;
    timelineItems.forEach((item, idx) => {{ if (item.track === trackName) last = idx; }});
    insertIdx = last + 1;
  }}
  timelineItems.splice(insertIdx, 0, moved);
  renderTimeline();
  selectItem(moved.id);
  markUnsaved();
  scheduleSave();
  showToast(`Moved to ${{trackName.toUpperCase()}} position ${{newPos}}`, 'ok');
}}

// ── Drag — presets to timeline ─────────────────────────────────────────────────
function presetDragStart(e) {{
  const el = e.currentTarget;
  _dragSrc = {{
    type:        'preset',
    track:       el.dataset.track,
    itemType:    el.dataset.type,
    tagKey:      el.dataset.tagKey || '',
    tagText:     el.dataset.tagText || '',
    composition: el.dataset.composition || '',
    label:       el.dataset.label || '',
  }};
  e.dataTransfer.effectAllowed = 'copy';
  e.dataTransfer.setData('text/plain', JSON.stringify(_dragSrc));
}}

// ── Drag — timeline item reorder ───────────────────────────────────────────────
function itemDragStart(e, id) {{
  _dragSrc = {{type:'item', id}};
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', id);
  e.stopPropagation();
}}

function clearDropIndicators() {{
  document.querySelectorAll('.tl-item.drop-before,.tl-item.drop-after').forEach(el => {{
    el.classList.remove('drop-before','drop-after');
  }});
  _dropTargetId = null;
  _dropSide = null;
}}

function _trackRowEl(trackName) {{
  // graphics and clips share the video row in the DOM
  if (trackName === 'graphics' || trackName === 'clips') return document.getElementById('track-video');
  return document.getElementById('track-'+trackName);
}}

function trackDragOver(e, trackName) {{
  e.preventDefault();
  e.dataTransfer.dropEffect = _dragSrc?.type === 'preset' ? 'copy' : 'move';
  const row = _trackRowEl(trackName);
  row.classList.add('drag-over');
  _dragOverTrack = trackName;

  // Position-aware indicator: find which item the cursor is hovering near
  if (_dragSrc?.type === 'item') {{
    const items = row.querySelectorAll('.tl-item:not(.dragging)');
    let newTarget = null, newSide = 'after';
    for (const el of items) {{
      const rect = el.getBoundingClientRect();
      if (e.clientX <= rect.right) {{
        newTarget = el.dataset.id;
        newSide = e.clientX < rect.left + rect.width / 2 ? 'before' : 'after';
        break;
      }}
    }}
    if (newTarget !== _dropTargetId || newSide !== _dropSide) {{
      clearDropIndicators();
      _dropTargetId = newTarget;
      _dropSide = newSide;
      if (newTarget) {{
        document.querySelector(`.tl-item[data-id="${{newTarget}}"]`)
          ?.classList.add('drop-' + newSide);
      }}
    }}
  }}
}}

function trackDragLeave(e, trackName) {{
  const row = _trackRowEl(trackName);
  if (!row.contains(e.relatedTarget)) {{
    row.classList.remove('drag-over');
    clearDropIndicators();
  }}
}}

function trackDrop(e, trackName) {{
  e.preventDefault();
  const row = _trackRowEl(trackName);
  row.classList.remove('drag-over');
  clearDropIndicators();

  if (!_dragSrc) return;

  if (_dragSrc.type === 'preset') {{
    const newItem = {{
      id:          'new_' + Date.now(),
      type:        _dragSrc.itemType,
      track:       trackName,
      act:         'ADDED',
      tag:         _dragSrc.tagKey ? `[${{_dragSrc.tagKey}}: ${{_dragSrc.tagText||''}}]` : '',
      tag_key:     _dragSrc.tagKey || '',
      tag_text:    _dragSrc.tagText || '',
      composition: _dragSrc.composition || '',
      filename:    null,
      rendered:    false,
      approved:    true,
      note:        '',
      content:     _dragSrc.label || _dragSrc.composition || '…',
      full_text:   '',
      words:       0,
      duration:    8,
      props:       {{}},
      position:    timelineItems.length,
    }};

    let insertIdx;
    if (trackName === 'transitions') {{
      // For transitions: use cursor X → time → insert before the seq item at that time
      insertIdx = _calcTransitionInsertIdx(e);
    }} else {{
      insertIdx = _calcInsertIdx(trackName, _dropTargetId, _dropSide);
    }}
    timelineItems.splice(insertIdx, 0, newItem);
    renderTimeline();
    selectItem(newItem.id);
    showToast(`Added ${{_dragSrc.label}} &#8212; fill props and render`, 'ok');

  }} else if (_dragSrc.type === 'item') {{
    const srcIdx = timelineItems.findIndex(i => i.id === _dragSrc.id);
    if (srcIdx === -1) {{ _dragSrc = null; return; }}

    const [moved] = timelineItems.splice(srcIdx, 1);
    moved.track = trackName;

    // Re-find insert position after splice
    let insertIdx = _calcInsertIdx(trackName, _dropTargetId, _dropSide);
    timelineItems.splice(insertIdx, 0, moved);

    renderTimeline();
    selectItem(moved.id);
    markUnsaved();
    scheduleSave();

    // Descriptive toast
    const finalIdx = timelineItems.findIndex(i=>i.id===moved.id);
    const trackItems = timelineItems.filter(i=>i.track===trackName);
    const posInTrack = trackItems.findIndex(i=>i.id===moved.id) + 1;
    const neighbour  = trackItems[posInTrack] || trackItems[posInTrack-2];
    const nLabel     = neighbour ? (neighbour.content||neighbour.tag_text||'').slice(0,22) : '';
    showToast(`Moved to position ${{posInTrack}}${{nLabel?' (near '+nLabel+')':''}}`, 'ok');
  }}

  _dragSrc = null;
}}

function _calcTransitionInsertIdx(dropEvent) {{
  // Convert cursor X to a timeline time, then insert transition before
  // the seq item (graphics/clips) whose start time is nearest/after the cursor.
  const scroll = document.getElementById('timelineScroll');
  const inner  = document.getElementById('timelineInner');
  if (!inner || !scroll) return timelineItems.length;

  const rect    = inner.getBoundingClientRect();
  const cursorX = dropEvent.clientX - rect.left + scroll.scrollLeft;
  const dropSec = cursorX / _pxPerSec;

  // Find the seq item whose start time is closest to dropSec
  const seqItems = timelineItems.filter(i => i.track === 'graphics' || i.track === 'clips');
  if (!seqItems.length) return timelineItems.length;

  // Pick the seq item that the cursor is over or just after
  let target = seqItems[0];
  for (const item of seqItems) {{
    if ((item._startTime ?? 0) <= dropSec) target = item;
    else break;
  }}

  // Insert just before `target` in the global array
  const targetIdx = timelineItems.findIndex(i => i.id === target.id);
  return targetIdx >= 0 ? targetIdx : timelineItems.length;
}}

function _calcInsertIdx(trackName, targetId, side) {{
  if (!targetId) {{
    // Drop on empty space → append after all items in this track
    let last = -1;
    timelineItems.forEach((item, idx) => {{ if (item.track === trackName) last = idx; }});
    return last + 1;
  }}
  const destIdx = timelineItems.findIndex(i => i.id === targetId);
  if (destIdx === -1) return timelineItems.length;
  return side === 'before' ? destIdx : destIdx + 1;
}}

// ── Image picker ──────────────────────────────────────────────────────────────
function openImgModal(itemId, field, playerIdx) {{
  modalCtx = {{itemId: itemId||selectedId, field, playerIdx}};
  _selSlug = _selPath = null;
  document.getElementById('modalSearch').value = '';
  document.getElementById('modalSelLbl').textContent = 'None selected';
  populateModalGrid(allImages);
  document.getElementById('imgModal').classList.add('open');
}}

function closeModal() {{
  document.getElementById('imgModal').classList.remove('open');
  modalCtx = _selSlug = _selPath = null;
}}

function filterModalImages(q) {{
  q = q.toLowerCase();
  populateModalGrid(q ? allImages.filter(i=>i.slug.includes(q)||i.name.toLowerCase().includes(q)) : allImages);
}}

function populateModalGrid(images) {{
  const grid = document.getElementById('modalGrid');
  if (!images.length) {{ grid.innerHTML='<div style="grid-column:1/-1;color:#222;text-align:center;padding:24px">None found</div>'; return; }}
  grid.innerHTML = images.map(img=>`
    <div class="img-opt" onclick="selectModalImg(this,'${{img.slug}}','${{img.file}}')" data-slug="${{img.slug}}">
      <img src="/player-thumb/${{encodeURIComponent(img.file)}}"
           onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2280%22 height=%22110%22><rect fill=%22%230f0f0f%22 width=%2280%22 height=%22110%22/><text fill=%22%23222%22 x=%2240%22 y=%2260%22 text-anchor=%22middle%22 font-size=%2220%22>?</text></svg>'"
           loading="lazy">
      <div class="img-opt-lbl">${{img.name}}</div>
    </div>`).join('');
}}

function selectModalImg(el, slug, file) {{
  document.querySelectorAll('.img-opt').forEach(e=>e.classList.remove('selected'));
  el.classList.add('selected');
  _selSlug = slug;
  _selPath = file ? file.replace(/[.][^./]+$/, '') : slug;
  document.getElementById('modalSelLbl').textContent = _selPath;
}}

function confirmModal() {{
  if (!_selPath || !modalCtx) {{ closeModal(); return; }}
  const item = getSelectedItem();
  if (!item) {{ closeModal(); return; }}
  if (!item.props) item.props = {{}};

  const imgData = allImages.find(i=>i.slug===_selSlug);
  const thumbSrc = imgData ? `/player-thumb/${{encodeURIComponent(imgData.file)}}` : '';

  if (modalCtx.playerIdx !== null && modalCtx.playerIdx !== undefined) {{
    if (!item.props.players) item.props.players = [{{}},{{}},{{}}];
    if (!item.props.players[modalCtx.playerIdx]) item.props.players[modalCtx.playerIdx] = {{}};
    item.props.players[modalCtx.playerIdx].image = _selPath;
    const pi = modalCtx.playerIdx;
    const thumbEl = document.getElementById('pthumb-'+pi);
    const emptyEl = document.getElementById('pempty-'+pi);
    const slugEl  = document.getElementById('pslug-'+pi);
    if (thumbEl && thumbSrc) {{ thumbEl.src=thumbSrc; thumbEl.style.display=''; }}
    if (emptyEl) emptyEl.style.display='none';
    if (slugEl)  slugEl.textContent = _selPath;
  }} else if (modalCtx.field) {{
    item.props[modalCtx.field] = _selPath;
    const thumbEl = document.getElementById('img-'+modalCtx.field);
    const slugEl  = document.getElementById('slug-'+modalCtx.field);
    if (thumbEl && thumbSrc) {{ thumbEl.src=thumbSrc; thumbEl.style.display=''; }}
    if (slugEl) slugEl.textContent = _selPath;
  }}

  closeModal();
  showToast('Image set &#8212; click &#8635; Render to apply', 'ok');
}}

// ── Image upload ──────────────────────────────────────────────────────────────
async function handleUpload(input) {{
  const file = input.files[0];
  if (!file) return;
  const name = prompt('Image slug (e.g. firmino):', file.name.replace(/[.][^.]+$/,'').replace(/[ ]+/g,'_').toLowerCase());
  if (!name) return;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('name', name);
  const res = await fetch('/upload-image',{{method:'POST',body:fd}}).then(r=>r.json());
  if (res.ok) {{
    allImages.push({{slug:res.slug,file:res.file,name:res.slug.replace(/_/g,' ').replace(/\\b./g,c=>c.toUpperCase())}});
    filterModalImages(document.getElementById('modalSearch').value);
    showToast(`'${{res.slug}}' added`, 'ok');
  }} else showToast('Upload failed', 'err');
  input.value='';
}}

// ── Clip upload ───────────────────────────────────────────────────────────────
async function handleClipUpload(input, propKey) {{
  const file = input.files[0];
  if (!file) return;
  const defaultName = file.name.replace(/[.][^.]+$/,'').replace(/[ ]+/g,'_').toLowerCase();
  const name = prompt('Clip filename slug:', defaultName);
  if (!name) return;

  // Probe video duration client-side before uploading
  let durationFrames = null;
  if (file.type.startsWith('video/')) {{
    durationFrames = await new Promise(resolve => {{
      const vid = document.createElement('video');
      vid.preload = 'metadata';
      vid.onloadedmetadata = () => {{
        resolve(Math.max(1, Math.round(vid.duration * 30)));
        URL.revokeObjectURL(vid.src);
      }};
      vid.onerror = () => {{ resolve(null); URL.revokeObjectURL(vid.src); }};
      vid.src = URL.createObjectURL(file);
    }});
  }}

  const fd = new FormData();
  fd.append('file', file);
  fd.append('name', name);
  if (durationFrames) fd.append('duration_frames', String(durationFrames));
  const res = await fetch('/upload-clip',{{method:'POST',body:fd}}).then(r=>r.json());
  if (!res.ok) {{ showToast('Upload failed: '+(res.error||''), 'err'); input.value=''; return; }}
  // Set clip path prop
  updateProp(propKey, res.file);
  // Store durationInFrames in props so render uses the clip's actual length
  if (res.duration_frames) {{
    updateProp('durationInFrames', res.duration_frames);
    // Also update the timeline item's visible duration (seconds)
    const item = getSelectedItem();
    if (item) {{
      item.duration = Math.round(res.duration_frames / 30);
      renderTrack('clips', timelineItems.filter(i=>i.track==='clips'));
    }}
  }}
  // Refresh inspector to show the clip thumbnail
  const item = getSelectedItem();
  if (item) renderInspector(item);
  showToast(`Clip uploaded: ${{res.file}}${{res.duration_frames ? ' · '+Math.round(res.duration_frames/30)+'s' : ''}}`, 'ok');
  input.value='';
}}

// ── Save & export ─────────────────────────────────────────────────────────────
async function saveTimeline(silent) {{
  clearTimeout(_autosaveTimer);
  const res = await fetch('/timeline-save/'+SAFE_NAME, {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{items: timelineItems}})
  }}).then(r=>r.json());
  if (res.ok) {{ markSaved(); if (!silent) showToast('Timeline saved', 'ok'); }}
}}

async function rerunFailedRenders() {{
  const btn = document.getElementById('rerunFailedBtn');
  if (btn) {{ btn.textContent = '⏳ Running…'; btn.disabled = true; }}
  try {{
    const res = await fetch('/rerun-failed', {{
      method: 'POST', headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{safe_name: SAFE_NAME}})
    }}).then(r=>r.json());
    if (res.error) showToast('Error: ' + res.error, 'err');
    else if (res.count === 0) showToast('No failed renders found.', 'ok');
    else showToast(`Re-rendering ${{res.count}} failed graphic${{res.count!==1?'s':''}} in background…`, 'ok');
  }} catch(e) {{
    showToast('Re-run failed: ' + e.message, 'err');
  }} finally {{
    if (btn) {{ btn.textContent = '&#8635; Re-run Failed'; btn.disabled = false; }}
  }}
}}

// ── Option A: Render Approved ────────────────────────────────────────────────
async function renderApproved() {{
  const btn    = document.getElementById('renderApprovedBtn');
  const status = document.getElementById('renderApprovedStatus');
  if (btn) {{ btn.disabled = true; btn.textContent = '⏳ Queuing…'; }}
  try {{
    const res = await fetch('/render-batch', {{
      method: 'POST', headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{safe_name: SAFE_NAME}})
    }}).then(r=>r.json());
    if (res.error)        {{ showToast('Error: ' + res.error, 'err'); return; }}
    if (res.queued === 0) {{ showToast(res.message || 'Nothing to render.', 'ok'); return; }}
    showToast(`Rendering ${{res.queued}} mp4${{res.queued!==1?'s':''}} in background…`, 'ok');
    const poll = setInterval(async () => {{
      const s = await fetch('/render-batch-status/'+SAFE_NAME).then(r=>r.json());
      if (s.status === 'idle' || s.status === 'unknown') {{ clearInterval(poll); status.textContent=''; return; }}
      status.textContent = `${{s.done}}/${{s.total}} done · ${{s.failed}} failed${{s.current?' · '+s.current:''}}`;
      if (s.status === 'done' || s.status === 'done_with_errors') {{
        clearInterval(poll);
        showToast(`Batch render complete · ${{s.done}}/${{s.total}}${{s.failed?` · ${{s.failed}} failed`:''}}`, s.failed? 'err' : 'ok');
        // Force a timeline reload so newly rendered mp4s replace PNG stills
        if (typeof loadTimeline === 'function') loadTimeline();
      }}
    }}, 1500);
  }} catch(e) {{
    showToast('Render approved failed: ' + e.message, 'err');
  }} finally {{
    if (btn) {{ btn.disabled = false; btn.textContent = '▶ Render Approved'; }}
  }}
}}

async function doExport() {{
  const res = await fetch('/studio-export', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{safe_name:SAFE_NAME}})
  }}).then(r=>r.json());
  if (res.ok) showToast(`Exported ${{res.count}} renders &#8594; ${{res.path}}`, 'ok');
  else showToast('Export failed', 'err');
}}

// ── Misc helpers ──────────────────────────────────────────────────────────────
function toggleSection(id) {{
  const el = document.getElementById('sec-'+id);
  if (el) el.style.display = el.style.display==='none' ? '' : 'none';
}}

function _fmtSec(sec) {{
  const m = Math.floor(sec/60), s = Math.floor(sec%60);
  return `${{String(m).padStart(2,'0')}}:${{String(s).padStart(2,'0')}}`;
}}

function updateTimecode() {{
  // Only update if not currently playing
  const tc = document.getElementById('timecode');
  if (tc.classList.contains('playing')) return;
  const gfxItems = timelineItems.filter(i=>i.track==='graphics');
  const totalDur = gfxItems.reduce((s,i)=>s+(i.duration||8),0);
  tc.textContent = `~${{_fmtSec(totalDur)}} est.`;
}}

function esc(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

// ── Sequence preview ──────────────────────────────────────────────────────────
let _seqPlaylist  = [];
let _seqIdx       = 0;
let _seqPlaying   = false;
let _seqAudioOn   = true;
let _seqTotalDur  = 0;
let _seqScrubbing = false;
let _seqGapRaf    = null;
let _seqInline    = false;   // true = plays in editor panel; false = fullscreen overlay

// ── Element accessors (switch between inline and fullscreen targets) ───────────
function _svEl()     {{ return document.getElementById(_seqInline ? 'previewVideo'      : 'seqVideo'); }}
function _sPhEl()    {{ return document.getElementById(_seqInline ? 'previewPlaceholder': 'seqPlaceholder'); }}
function _sPhLbl()   {{ return _seqInline
    ? document.querySelector('#previewPlaceholder .preview-placeholder-label')
    : document.getElementById('seqPhLabel'); }}
function _sLblEl()   {{ return document.getElementById(_seqInline ? 'inlineItemLabel'   : 'seqLabel'); }}
function _sTcEl()    {{ return document.getElementById(_seqInline ? 'inlineTimecode'     : 'seqTimecode'); }}
function _sScrEl()   {{ return document.getElementById(_seqInline ? 'inlineScrubber'     : 'seqScrubber'); }}
function _sPlayBtn() {{ return document.getElementById(_seqInline ? 'inlinePlayBtn'      : 'seqPlayBtn'); }}
function _sAudioBtn(){{ return document.getElementById(_seqInline ? 'inlineAudioBtn'     : 'seqAudioBtn'); }}

function _buildSeqPlaylist() {{
  return timelineItems
    .filter(i => i.approved !== false && (i.track === 'graphics' || i.track === 'clips'))
    .map(i => {{
      let src = null;
      if (i.track === 'clips' && i.props && i.props.clip) {{
        src = `/remotion-public/${{i.props.clip}}`;
      }} else if (i.track === 'graphics' && i.filename) {{
        src = `/video/${{SAFE_NAME}}/${{encodeURIComponent(i.filename)}}`;
      }}
      return {{
        src,
        durationSec:   i.duration || 8,
        trimInFrames:  i.trimIn  || 0,
        trimOutFrames: i.trimOut || null,
        label:         i.content || i.tag_text || i.composition || '',
        tlId:          i.id,   // link back to timeline item for highlighting
      }};
    }});
}}

function _seqGlobalSecBeforeIdx(idx) {{
  let t = 0;
  for (let i = 0; i < idx && i < _seqPlaylist.length; i++) t += _seqPlaylist[i].durationSec;
  return t;
}}

function _seqIdxForGlobal(globalSec) {{
  let rem = globalSec;
  for (let i = 0; i < _seqPlaylist.length; i++) {{
    if (rem < _seqPlaylist[i].durationSec) return {{idx:i, offsetSec:rem}};
    rem -= _seqPlaylist[i].durationSec;
  }}
  const last = _seqPlaylist.length - 1;
  return {{idx: Math.max(0, last), offsetSec: last >= 0 ? _seqPlaylist[last].durationSec : 0}};
}}

function _seqUpdateScrubber(globalSec) {{
  const scr = _sScrEl();
  if (scr && !_seqScrubbing) {{
    scr.value = globalSec;
    const pct = _seqTotalDur > 0 ? (globalSec / _seqTotalDur * 100) : 0;
    scr.style.setProperty('--pct', pct.toFixed(2) + '%');
  }}
  const tc = _sTcEl();
  if (tc) tc.textContent = _fmtSec(globalSec) + ' / ' + _fmtSec(_seqTotalDur);
}}

function _seqHighlightActive(idx) {{
  // Remove previous highlight
  document.querySelectorAll('.tl-item.seq-active').forEach(el => el.classList.remove('seq-active'));
  if (idx < 0 || idx >= _seqPlaylist.length) return;
  const tlId = _seqPlaylist[idx]?.tlId;
  if (!tlId) return;
  const el = document.querySelector(`.tl-item[data-id="${{tlId}}"]`);
  if (el) {{
    el.classList.add('seq-active');
    el.scrollIntoView({{behavior:'smooth', block:'nearest', inline:'nearest'}});
  }}
}}

function openSeqPreview(inline) {{
  _seqInline   = !!inline;
  _seqPlaylist = _buildSeqPlaylist();
  if (!_seqPlaylist.length) {{ showToast('No rendered items in timeline', 'err'); return; }}
  _seqTotalDur = _seqPlaylist.reduce((s, i) => s + i.durationSec, 0);

  const scr = _sScrEl();
  if (scr) {{ scr.max = _seqTotalDur; scr.value = 0; scr.style.setProperty('--pct','0%'); }}

  _seqIdx     = 0;
  _seqPlaying = true;   // set BEFORE _seqLoadItem so it plays immediately
  updatePlayheadPos(0);
  const lb = _sLblEl(); if (lb) lb.textContent = '';
  const pb = _sPlayBtn(); if (pb) pb.innerHTML = '&#9646;&#9646;';

  if (_seqInline) {{
    document.getElementById('inlineSeqCtrl').classList.add('open');
  }} else {{
    document.getElementById('seqOverlay').classList.add('open');
  }}

  // Start narration audio (must be in user-gesture context — no setTimeout!)
  const audio = document.getElementById('seqAudio');
  audio.src = `/narration-audio/${{SAFE_NAME}}`;
  audio.currentTime = 0;
  audio.play().catch(()=>{{}});

  _seqLoadItem(0);
  _startPlayheadRaf();
}}

function closeSeqPreview() {{
  document.getElementById('seqOverlay').classList.remove('open');
  stopInlineSeq();
}}

function stopInlineSeq() {{
  _seqPlaying = false;
  cancelAnimationFrame(_seqGapRaf);
  _stopPlayheadRaf();
  const video = _svEl();
  if (video) {{ video.pause(); video.src = ''; video.style.display = 'none'; }}
  const audio = document.getElementById('seqAudio');
  audio.pause(); audio.src = '';
  document.getElementById('inlineSeqCtrl').classList.remove('open');
  document.getElementById('seqOverlay').classList.remove('open');
  _seqHighlightActive(-1);
  // Restore placeholder in inline mode
  if (_seqInline) {{
    const ph = document.getElementById('previewPlaceholder');
    if (ph) ph.style.display = 'flex';
  }}
}}

function _seqLoadItem(idx) {{
  cancelAnimationFrame(_seqGapRaf);

  if (idx >= _seqPlaylist.length) {{
    _seqPlaying = false;
    const pb = _sPlayBtn(); if (pb) pb.innerHTML = '&#9654;';
    _seqHighlightActive(-1);
    _stopPlayheadRaf();
    return;
  }}

  _seqIdx = idx;
  _seqHighlightActive(idx);

  const item     = _seqPlaylist[idx];
  const tlItem   = timelineItems.find(i => i.id === item.tlId);
  const baseTime = tlItem?._startTime ?? _seqGlobalSecBeforeIdx(idx);
  const video    = _svEl();
  const ph       = _sPhEl();
  const lbl      = _sLblEl();

  if (lbl) lbl.textContent = (idx + 1) + ' / ' + _seqPlaylist.length + ' — ' + item.label;

  if (item.src) {{
    // ── Has a video file — show it and play ──────────────────────────────────
    if (ph) ph.style.display = 'none';
    video.style.display = 'block';

    // Clear old listeners before loading new src
    video.onloadedmetadata = null;
    video.ontimeupdate     = null;
    video.onended          = null;

    video.src = item.src;
    video.load();   // explicit load() ensures the browser starts fetching

    const doPlay = () => {{
      video.currentTime = (item.trimInFrames || 0) / 30;
      if (_seqPlaying) video.play().catch(()=>{{}});
    }};

    if (video.readyState >= 2) {{
      doPlay();
    }} else {{
      video.addEventListener('canplay', doPlay, {{once: true}});
    }}

    video.ontimeupdate = () => {{
      if (_seqScrubbing) return;
      const localSec  = Math.max(0, video.currentTime - (item.trimInFrames || 0) / 30);
      const globalSec = baseTime + localSec;
      _seqUpdateScrubber(globalSec);
      updatePlayheadPos(globalSec);
      if (item.trimOutFrames && video.currentTime * 30 >= item.trimOutFrames) _seqNextItem();
    }};

    video.onended = () => _seqNextItem();

  }} else {{
    // ── No video file (not rendered yet) — show placeholder, advance by timer ─
    video.pause(); video.src = ''; video.style.display = 'none';
    if (ph) {{
      ph.style.display = 'flex';
      const lbl2 = ph.querySelector('.preview-placeholder-label');
      if (lbl2) lbl2.textContent = item.label || 'Not rendered';
    }}

    if (_seqPlaying) {{
      const start = performance.now();
      const tick  = (now) => {{
        if (!_seqPlaying) return;
        const elapsed   = (now - start) / 1000;
        const globalSec = baseTime + Math.min(elapsed, item.durationSec);
        _seqUpdateScrubber(globalSec);
        updatePlayheadPos(globalSec);
        if (elapsed >= item.durationSec) {{ _seqNextItem(); return; }}
        _seqGapRaf = requestAnimationFrame(tick);
      }};
      _seqGapRaf = requestAnimationFrame(tick);
    }}
  }}
}}

function _seqNextItem() {{
  _seqIdx++;
  _seqLoadItem(_seqIdx);
}}

function toggleSeqPlay() {{
  const audio = document.getElementById('seqAudio');
  if (_seqPlaying) {{
    // ── Pause ──────────────────────────────────────────────────────────────────
    _seqPlaying = false;
    const pb = _sPlayBtn(); if (pb) pb.innerHTML = '&#9654;';
    const video = _svEl(); if (video) video.pause();
    audio.pause();
    cancelAnimationFrame(_seqGapRaf);
    _stopPlayheadRaf();
  }} else {{
    // ── Resume ─────────────────────────────────────────────────────────────────
    _seqPlaying = true;
    const pb = _sPlayBtn(); if (pb) pb.innerHTML = '&#9646;&#9646;';
    const video = _svEl();
    if (video && video.src) {{
      video.play().catch(()=>{{}});
    }} else {{
      // Gap item or end of sequence — reload current item
      _seqLoadItem(_seqIdx);
    }}
    if (audio.src) audio.play().catch(()=>{{}});
    _startPlayheadRaf();
  }}
}}

function onSeqScrub(value) {{
  const globalSec = parseFloat(value);
  const pct = _seqTotalDur > 0 ? (globalSec / _seqTotalDur * 100) : 0;
  const scr = _sScrEl(); if (scr) scr.style.setProperty('--pct', pct.toFixed(2) + '%');
  _seqUpdateScrubber(globalSec);

  const {{idx, offsetSec}} = _seqIdxForGlobal(globalSec);
  const audio = document.getElementById('seqAudio');
  if (audio.src) audio.currentTime = globalSec;

  const wasPlaying = _seqPlaying;
  _seqPlaying = false;
  cancelAnimationFrame(_seqGapRaf);

  const loadAndSeek = (targetIdx, off) => {{
    const video = _svEl();
    const item  = _seqPlaylist[targetIdx];
    if (!item?.src || !video) {{
      if (wasPlaying) {{ _seqPlaying = true; _seqLoadItem(targetIdx); }}
      return;
    }}
    const seekTo = (item.trimInFrames / 30) + off;
    const doSeek = () => {{
      video.currentTime = seekTo;
      if (wasPlaying) {{ _seqPlaying = true; video.play().catch(()=>{{}}); audio.play().catch(()=>{{}}); }}
    }};
    if (targetIdx !== _seqIdx) {{
      _seqIdx = targetIdx;
      _seqLoadItem(targetIdx);
      video.addEventListener('loadedmetadata', doSeek, {{once:true}});
    }} else {{
      doSeek();
    }}
  }};

  loadAndSeek(idx, offsetSec);
}}

function toggleSeqAudio() {{
  _seqAudioOn = !_seqAudioOn;
  const audio = document.getElementById('seqAudio');
  const btn   = _sAudioBtn();
  if (_seqAudioOn) {{
    if (btn) {{ btn.innerHTML = '&#128266;'; btn.classList.remove('active'); }}
    if (_seqPlaying) audio.play().catch(()=>{{}});
  }} else {{
    if (btn) {{ btn.innerHTML = '&#128263;'; btn.classList.add('active'); }}
    audio.pause();
  }}
}}

// ── Delete item ───────────────────────────────────────────────────────────────
function deleteItem(id) {{
  const item = timelineItems.find(i => i.id === id);
  if (!item) return;
  // Block deletion of HERO INTRO
  if ((item.tag_key||'').toUpperCase() === 'HERO INTRO') {{
    showToast('Cannot delete HERO INTRO', 'err');
    return;
  }}
  const index = timelineItems.findIndex(i => i.id === id);
  _deletedItems.push({{item: {{...item}}, index}});
  timelineItems.splice(index, 1);
  if (selectedId === id) {{
    selectedId = null;
    document.getElementById('inspBody').innerHTML = '';
    document.getElementById('inspType').textContent = '';
    document.getElementById('inspName').textContent = '';
  }}
  renderTimeline();
  markUnsaved();
  scheduleSave();
  showToast('Item deleted &nbsp;<span style="color:var(--gold);cursor:pointer" onclick="undoDelete()">Undo</span>', 'ok');
}}

function undoDelete() {{
  if (!_deletedItems.length) return;
  const {{item, index}} = _deletedItems.pop();
  const clampedIdx = Math.min(index, timelineItems.length);
  timelineItems.splice(clampedIdx, 0, item);
  renderTimeline();
  selectItem(item.id);
  markUnsaved();
  scheduleSave();
  showToast('Restored', 'ok');
}}

// ── Splice clip ───────────────────────────────────────────────────────────────
function spliceItem(id) {{
  const item = timelineItems.find(i => i.id === id);
  if (!item || item.type !== 'clip') return;

  const input = document.getElementById('spliceInput');
  const atSec = parseFloat(input ? input.value : 0);
  const totalSec = item.duration || 8;

  if (isNaN(atSec) || atSec <= 0 || atSec >= totalSec) {{
    showToast('Splice point must be between 0 and ' + totalSec + 's', 'err');
    return;
  }}

  const atFrames   = Math.round(atSec * 30);
  const totalFrames = item.duration_frames || Math.round(totalSec * 30);
  const existingTrimIn = item.trimIn || 0;

  const idA = 'sp_' + Date.now() + 'a';
  const idB = 'sp_' + Date.now() + 'b';

  const itemA = {{
    ...item,
    id:              idA,
    content:         (item.content||'') + ' [1/2]',
    duration:        atSec,
    duration_frames: atFrames,
    trimIn:          existingTrimIn,
    trimOut:         existingTrimIn + atFrames,
    props:           {{...(item.props||{{}}), trimIn: existingTrimIn, trimOut: existingTrimIn + atFrames}},
  }};
  const itemB = {{
    ...item,
    id:              idB,
    content:         (item.content||'') + ' [2/2]',
    duration:        totalSec - atSec,
    duration_frames: totalFrames - atFrames,
    trimIn:          existingTrimIn + atFrames,
    trimOut:         existingTrimIn + totalFrames,
    props:           {{...(item.props||{{}}), trimIn: existingTrimIn + atFrames, trimOut: existingTrimIn + totalFrames}},
  }};

  const idx = timelineItems.findIndex(i => i.id === id);
  timelineItems.splice(idx, 1, itemA, itemB);
  renderTimeline();
  selectItem(idA);
  markUnsaved();
  scheduleSave();
  showToast('Clip spliced at ' + atSec + 's', 'ok');
}}

// ── Transition on clip/graphic items ──────────────────────────────────────────
const TRANS_TYPES = ['none','push','flash','letterbox','paper','dataLine','grain'];

function setItemTransition(id, type) {{
  const item = timelineItems.find(i => i.id === id);
  if (!item) return;
  item.transition = type;
  // Refresh the picker UI without full re-render
  document.querySelectorAll('.trans-opt').forEach(el => {{
    el.classList.toggle('active', el.dataset.t === type);
  }});
  markUnsaved();
  scheduleSave();
}}

// ── Export video modal ────────────────────────────────────────────────────────
// ── Narration Sync ────────────────────────────────────────────────────────────

async function applySyncMap() {{
  const btn = document.getElementById('syncBtn');
  if (btn) {{ btn.disabled = true; btn.textContent = '⏳ Syncing…'; }}
  try {{
    const res  = await fetch('/sync-map/' + encodeURIComponent(SAFE_NAME));
    const data = await res.json();
    if (data.error) {{ alert('Sync error: ' + data.error); return; }}

    const scenes = data.scenes || [];
    if (!scenes.length) {{ alert('No sync data found — run the pipeline first.'); return; }}

    // Build a map: tag + content → narration_start_seconds
    // We match by tag name + first 30 chars of content
    const syncIndex = {{}};
    for (const s of scenes) {{
      if (s.narration_start_seconds == null) continue;
      const k = (s.tag + ':' + (s.content || '').substring(0, 30)).toLowerCase();
      syncIndex[k] = s.narration_start_seconds;
    }}

    // Apply to timeline items
    let applied = 0;
    for (const item of timelineItems) {{
      const tpl = (item.tag_key || item.template || '').toUpperCase().replace(/[\[\]]/g, '');
      const cnt = (item.content || item.tag_text || '').substring(0, 30);
      const k   = (tpl + ':' + cnt).toLowerCase();
      if (syncIndex[k] !== undefined) {{
        item._syncedStart = syncIndex[k];   // seconds into narration
        applied++;
      }}
    }}

    // Re-sort the video track items by _syncedStart where available
    // Insert gap items to pad timing so graphics land at the right moment
    _applySyncToTimeline(syncIndex, data.total_narration_duration_seconds || 0);

    const isEstimated = data.estimated ? ' (estimated — no voice timestamps yet)' : '';
    if (btn) {{ btn.textContent = `✓ Synced ${{applied}} scenes${{isEstimated}}`; }}
    setTimeout(() => {{ if (btn) {{ btn.disabled = false; btn.textContent = '⏱ Sync to Narration'; }} }}, 4000);

  }} catch(e) {{
    alert('Sync failed: ' + e.message);
    if (btn) {{ btn.disabled = false; btn.textContent = '⏱ Sync to Narration'; }}
  }}
}}

function _applySyncToTimeline(syncIndex, totalNarrDuration) {{
  // Get all approved video items (graphics + clips) in current timeline order
  const videoItems = timelineItems.filter(i =>
    (i.track === 'graphics' || i.track === 'clips') && i.approved !== false
  );
  if (!videoItems.length) return;

  // For each item that has sync data, set its _startTime to its narration offset
  // For items without sync data, keep their relative position between synced neighbours
  for (const item of videoItems) {{
    const tpl = (item.tag_key || item.template || '').toUpperCase();
    const cnt = (item.content || item.tag_text || '').substring(0, 30);
    const k   = (tpl + ':' + cnt).toLowerCase();
    if (syncIndex[k] !== undefined) {{
      item._startTime = syncIndex[k];
    }}
  }}

  // Re-build timeline with updated positions
  _buildSeqTimeline();
  renderTimeline();
  saveTimeline(true);
}}

function openExportModal() {{
  // Count what will be exported
  const count = timelineItems.filter(i =>
    i.approved !== false &&
    (i.track === 'graphics' || i.track === 'clips') &&
    i.composition
  ).length;
  const sc = document.getElementById('exportSceneCount');
  if (sc) sc.textContent = count + ' scene' + (count!==1?'s':'') + ' will be included';
  document.getElementById('exportProgress').textContent = 'Ready to export.';
  document.getElementById('exportBar').style.width = '0%';
  const res = document.getElementById('exportResult');
  if (res) {{res.style.display='none'; res.textContent='';}}
  const btn = document.getElementById('exportStartBtn');
  if (btn) {{btn.disabled=false; btn.textContent='&#9654; Start Export';}}
  document.getElementById('exportModal').style.display = 'flex';
}}

function closeExportModal() {{
  document.getElementById('exportModal').style.display = 'none';
  clearInterval(_exportPollT);
}}

async function startExportVideo() {{
  const btn = document.getElementById('exportStartBtn');
  if (btn) {{btn.disabled=true; btn.textContent='Exporting…';}}
  document.getElementById('exportProgress').textContent = 'Queuing render…';
  document.getElementById('exportBar').style.width = '5%';

  // Save current timeline first
  await fetch('/timeline-save/'+SAFE_NAME, {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{items: timelineItems}})
  }});

  const res = await fetch('/export-video', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{safe_name: SAFE_NAME}})
  }}).then(r=>r.json());

  if (res.error) {{
    document.getElementById('exportProgress').textContent = '&#10007; ' + res.error;
    if (btn) {{btn.disabled=false; btn.textContent='&#9654; Start Export';}}
    return;
  }}

  // Poll for status
  let dots = 0;
  _exportPollT = setInterval(async () => {{
    const s = await fetch('/export-video-status/'+SAFE_NAME).then(r=>r.json());
    dots = (dots+1)%4;
    const spinner = '.'.repeat(dots+1);

    if (s.status === 'running') {{
      document.getElementById('exportProgress').textContent = (s.progress||'Working') + spinner;
      document.getElementById('exportBar').style.width = '40%';
    }} else if (s.status === 'done') {{
      clearInterval(_exportPollT);
      document.getElementById('exportProgress').textContent = '&#10003; Export complete!';
      document.getElementById('exportBar').style.width = '100%';
      const res = document.getElementById('exportResult');
      if (res) {{
        res.style.display = 'block';
        res.textContent   = s.output || 'export/final.mp4';
      }}
      if (btn) {{btn.disabled=false; btn.textContent='&#9654; Start Export';}}
      showToast('Video exported &#8594; export/final.mp4', 'ok');
    }} else if (s.status === 'failed') {{
      clearInterval(_exportPollT);
      document.getElementById('exportProgress').textContent = '&#10007; Failed: ' + (s.error||'unknown error');
      document.getElementById('exportBar').style.width = '0%';
      if (btn) {{btn.disabled=false; btn.textContent='&#9654; Start Export';}}
    }}
  }}, 2500);
}}

let _toastT;
function showToast(msg, type) {{
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show ' + (type==='ok'?'ok':type==='err'?'err':'');
  clearTimeout(_toastT);
  _toastT = setTimeout(()=>el.className='toast', 3200);
}}

init();
</script>
</body>
</html>"""
    return html


# ─────────────────────────────────────────────────────────────────────────────
# Track A — Server Core (gates, registry validation, boot invariants)
# ─────────────────────────────────────────────────────────────────────────────

import re as _re_track_a
from collections import defaultdict as _defaultdict_track_a
from dataclasses import dataclass as _dataclass_track_a, field as _field_track_a
from typing import Optional as _Optional_track_a

from templates.templateRegistry import (
    ACT_TEMPLATE_WHITELIST,
    TEMPLATE_DATA_KIND,
    BANNED_AUTOGEN,
)

REMOTION_ROOT_TSX = str(REMOTION_DIR / "src" / "Root.tsx")
REMOTION_GEN_DIR  = str(REMOTION_DIR / "src" / "gen")

# Numeric-only safety zone. Templates here MUST come from a trusted numeric source.
DATA_TEMPLATES = frozenset({
    "HeroStatBars", "HeroLeagueGraph", "HeroBigStat",
    "PlayerStats", "AttackingRadar", "TopScorersTable",
    "HeroAwardsList", "HeroComparisonRadar",
    "StandingsTable", "TopAssistsTable", "SeasonComparison",
    "HeroScatter", "HeroShotMap", "HeroMatchTimeline",
})

# Numeric-trusted sources only. LLM is explicitly NOT in this set.
TRUSTED_SOURCES = frozenset({
    "footballdata", "fbref", "wikipedia", "manual_curated", "stock_formation",
})

# Per-template payload contracts.
DATA_CONTRACTS = {
    "HeroStatBars": {
        "required": ["stats"],
        "rule": lambda p: 2 <= len(p["stats"]) <= 8 and all(
            isinstance(s.get("valueA"), (int, float)) for s in p["stats"]
        ),
    },
    "HeroLeagueGraph": {
        "required": ["series"],
        "rule": lambda p: all(len(s.get("points", [])) >= 3 for s in p["series"]),
    },
    "HeroBigStat": {
        "required": ["stat", "unit"],
        "rule": lambda p: p.get("stat") not in (None, ""),
    },
    "PlayerStats": {
        "required": ["stats"],
        "rule": lambda p: len(p["stats"]) >= 3,
    },
    "AttackingRadar": {
        "required": ["metrics"],
        "rule": lambda p: len(p["metrics"]) >= 4,
    },
    "TopScorersTable": {
        "required": ["rows"],
        "rule": lambda p: 3 <= len(p["rows"]) <= 12,
    },
    "HeroAwardsList": {
        "required": ["items"],
        "rule": lambda p: len(p["items"]) >= 1,
    },
    "HeroComparisonRadar": {
        "required": ["metrics"],
        "rule": lambda p: len(p["metrics"]) >= 4,
    },
    "StandingsTable": {
        "required": ["rows"],
        "rule": lambda p: 4 <= len(p["rows"]) <= 24,
    },
    "TopAssistsTable": {
        "required": ["rows"],
        "rule": lambda p: 3 <= len(p["rows"]) <= 12,
    },
    "SeasonComparison": {
        "required": ["seasons"],
        "rule": lambda p: len(p["seasons"]) >= 2,
    },
    "HeroScatter": {
        "required": ["points"],
        "rule": lambda p: len(p["points"]) >= 3,
    },
    "HeroShotMap": {
        "required": ["shots"],
        "rule": lambda p: len(p["shots"]) >= 1,
    },
    "HeroMatchTimeline": {
        "required": ["events"],
        "rule": lambda p: len(p["events"]) >= 1,
    },
}

ACT_BUDGET = {1: 2, 2: 3, 3: 4, 4: 3, 5: 2}
GLOBAL_BUDGET = 12


class RegistryDriftError(RuntimeError):
    """Raised at boot when Root.tsx compositions and _COMPOSITION_SCHEMAS disagree."""


@_dataclass_track_a(frozen=True)
class RenderRequest:
    template_id: str
    payload: dict
    scene_id: str


# ── Boot invariants ──────────────────────────────────────────────────────────

def ensure_remotion_dirs() -> None:
    """Make sure /src/gen exists before any render is attempted."""
    os.makedirs(REMOTION_GEN_DIR, exist_ok=True)


_SCHEMA_EXEMPT_COMPOSITIONS = {
    "VideoSequence",  # master sequencer — props built by export, not edited in studio
    "Thumbnail",      # thumbnail render path — separate from documentary pipeline
}


def validate_composition_registry() -> None:
    """Cross-check Root.tsx <Composition id="..."/> entries vs _COMPOSITION_SCHEMAS.
    Raises RegistryDriftError on any mismatch so boot aborts loudly."""
    try:
        with open(REMOTION_ROOT_TSX, "r", encoding="utf-8") as fh:
            root_src = fh.read()
    except FileNotFoundError as e:
        raise RegistryDriftError(f"Root.tsx not found at {REMOTION_ROOT_TSX}") from e

    declared = set(_re_track_a.findall(r'<Composition\s+id\s*=\s*"([^"]+)"', root_src)) - _SCHEMA_EXEMPT_COMPOSITIONS
    schemas  = set(_COMPOSITION_SCHEMAS.keys())

    missing  = declared - schemas   # in Root.tsx, no schema in server
    extra    = schemas - declared   # has schema, not registered in Root.tsx

    if missing or extra:
        raise RegistryDriftError(
            f"Composition registry drift detected. "
            f"missing_schema={sorted(missing)} extra_schema={sorted(extra)}"
        )


# ── Gate 1: DataGate (numeric-only hard fail) ────────────────────────────────

def data_gate(template_id: str, payload: dict) -> bool:
    """Hard fail. Numeric templates MUST have trusted source + valid contract.
    Non-numeric templates pass through unconditionally."""
    if template_id not in DATA_TEMPLATES:
        return True
    if not payload:
        return False
    if payload.get("_source") not in TRUSTED_SOURCES:
        return False
    spec = DATA_CONTRACTS.get(template_id)
    if not spec:
        return False  # numeric template missing a contract = misconfigured, fail closed
    if any(not payload.get(f) for f in spec["required"]):
        return False
    try:
        return bool(spec["rule"](payload))
    except Exception:
        return False


# ── Gate 2: ShouldRenderGate (classification + budget + role pacing) ─────────

def should_render(scenes: list) -> set:
    """Single eligibility gate. Returns the set of scene IDs that should render.
    Inputs (NOT separate systems): classification, role pacing, explicit_request, budget."""
    eligible = []
    prev_classes = []
    for s in scenes:
        if s.get("type") not in (None, "graphic"):
            continue  # only graphic scenes are gated
        cls = s.get("classification", "SHOULD_NOT_VISUALISE")
        if cls == "SHOULD_NOT_VISUALISE" and not s.get("explicit_request"):
            continue
        # SHOULD_VISUALISE pacing penalty: skip if same class appeared in prior 2
        if cls == "SHOULD_VISUALISE" and cls in prev_classes[-2:]:
            continue
        eligible.append(s)
        prev_classes = (prev_classes + [cls])[-3:]

    # Per-act budget (MUST first, then SHOULD by narration density)
    keep = set()
    by_act = _defaultdict_track_a(list)
    for s in eligible:
        by_act[int(s.get("actIndex", 0))].append(s)

    for act, lst in by_act.items():
        lst.sort(key=lambda s: (
            0 if s.get("classification") == "MUST_VISUALISE" else 1,
            -len(str(s.get("narration", "")).split()),
        ))
        cap = ACT_BUDGET.get(act + 1, 2)
        for s in lst[:cap]:
            keep.add(s["id"])

    # Global cap
    if len(keep) > GLOBAL_BUDGET:
        ranked = sorted(
            [s for s in scenes if s.get("id") in keep],
            key=lambda s: 0 if s.get("classification") == "MUST_VISUALISE" else 1,
        )
        keep = {s["id"] for s in ranked[:GLOBAL_BUDGET]}

    return keep


# ── Gate 3: TemplateResolver (deterministic whitelist lookup) ────────────────

def resolve_template(scene: dict) -> _Optional_track_a[str]:
    """Whitelist lookup. No filtering elsewhere."""
    cls  = scene.get("classification")
    act  = int(scene.get("actIndex", 0)) + 1
    kind = scene.get("data_kind", "none")

    candidates = ACT_TEMPLATE_WHITELIST.get(act, {}).get(cls, [])
    candidates = [
        t for t in candidates
        if TEMPLATE_DATA_KIND.get(t) == kind or TEMPLATE_DATA_KIND.get(t) == "copy"
    ]
    candidates = [
        t for t in candidates
        if t not in BANNED_AUTOGEN or scene.get("explicit_request")
    ]
    if not candidates:
        return None
    prev = scene.get("_prev_template")
    for t in candidates:
        if t != prev:
            return t
    return candidates[0]


# ── Render request packaging ─────────────────────────────────────────────────

def build_render_request(scene: dict) -> RenderRequest:
    """Package a scene into a frozen RenderRequest.
    Payload-build is owned by Track B (graphics_agent.build_payload) and is
    expected to have populated scene['_payload'] before this is called."""
    return RenderRequest(
        template_id=scene["template"],
        payload=scene.get("_payload") or {},
        scene_id=scene["id"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# End Track A
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    ensure_remotion_dirs()
    validate_composition_registry()
    print("  Documentary Engine — Context Server")
    print("  Open: http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
