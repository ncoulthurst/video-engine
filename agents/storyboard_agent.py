"""
storyboard_agent — Layer 1 of the two-layer storyboard system.

Responsibility: narrative intent only.
  - Build the LLM prompt from blueprint, facts, and retention brief
  - Call the LLM (Gemini) for scene generation
  - Return raw scenes as a list of dicts (with classification + data_kind
    + entity fields, coerced to safe defaults)

Does NOT:
  - Assign final clip types, roles, world_id, or flow_hint (server.py does that)
  - Post-process or enforce structural rules (server.py does that)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.llm_utils import ask_gemini


# ── Track D: SceneClassification schema validators ──────────────────────────
# Coerce LLM-emitted classification + data_kind into safe enums. Defaults are
# deliberately conservative — unknown classification → SHOULD_NOT_VISUALISE
# (so the downstream ShouldRenderGate filters the scene out unless explicit).

VALID_CLASSIFICATIONS = frozenset({
    "MUST_VISUALISE", "SHOULD_VISUALISE", "SHOULD_NOT_VISUALISE",
})

VALID_DATA_KINDS = frozenset({
    "stat", "timeline", "formation", "quote", "ranking",
    "entity", "comparison", "copy", "none",
})

VALID_EVIDENCE_MODES = frozenset({
    "STAT", "PORTRAIT", "TACTICAL", "CLIP", "NARRATIVE",
})


def _validate_classification(scene: dict) -> dict:
    """Coerce scene['classification'] to a known enum value. Default = SHOULD_NOT_VISUALISE."""
    raw = scene.get("classification")
    if isinstance(raw, str):
        norm = raw.strip().upper().replace("-", "_").replace(" ", "_")
        if norm in VALID_CLASSIFICATIONS:
            scene["classification"] = norm
            return scene
    scene["classification"] = "SHOULD_NOT_VISUALISE"
    return scene


def _validate_data_kind(scene: dict) -> dict:
    """Coerce scene['data_kind'] to a known enum value. Default = 'none'."""
    raw = scene.get("data_kind")
    if isinstance(raw, str):
        norm = raw.strip().lower()
        if norm in VALID_DATA_KINDS:
            scene["data_kind"] = norm
            return scene
    scene["data_kind"] = "none"
    return scene


def _normalize_evidence_mode(scene: dict) -> dict:
    """Coerce scene['evidence_mode'] to a known enum value. Default = 'STAT'.

    Five-way bucket used by the LLM (and downstream observability) to
    alternate visual modalities and avoid 3+ pure-data scenes back-to-back:
      STAT       — pure data graphic (radar, stat bars, league graph, top scorers)
      PORTRAIT   — player/manager imagery dominant (player trio, portrait stat hero, intro)
      TACTICAL   — pitch / lineup / formation (team lineup, hero tactical)
      CLIP       — sourced footage placeholder (clip single, clip compare)
      NARRATIVE  — narration / quote / chapter / context (quote card, chapter word, narration)
    """
    raw = scene.get("evidence_mode")
    if isinstance(raw, str):
        norm = raw.strip().upper().replace("-", "_").replace(" ", "_")
        if norm in VALID_EVIDENCE_MODES:
            scene["evidence_mode"] = norm
            return scene
    scene["evidence_mode"] = "STAT"
    return scene


def _check_evidence_mode_runs(scenes: list) -> None:
    """Observability helper: warn (do NOT reorder) when 3+ consecutive scenes
    share the same evidence_mode. Reordering is _break_data_runs's job in
    server.py — this is a pure observability hook so the operator can re-prompt.
    """
    if not scenes:
        return
    run_mode = None
    run_start = 0
    for i, s in enumerate(scenes):
        mode = s.get("evidence_mode", "STAT")
        if mode == run_mode:
            continue
        if run_mode is not None:
            run_len = i - run_start
            if run_len >= 3:
                print(
                    f"[Storyboard] WARNING: {run_len} consecutive {run_mode} "
                    f"scenes detected at indices {run_start}-{i - 1} — "
                    f"consider re-prompting"
                )
        run_mode = mode
        run_start = i
    # Final run
    run_len = len(scenes) - run_start
    if run_len >= 3 and run_mode is not None:
        print(
            f"[Storyboard] WARNING: {run_len} consecutive {run_mode} "
            f"scenes detected at indices {run_start}-{len(scenes) - 1} — "
            f"consider re-prompting"
        )
# ── End Track D validators ──────────────────────────────────────────────────


def generate_scenes(topic, entity, blueprint, checked_facts,
                    wiki="", context="", retention_brief=None, director_override=""):
    """Generate the raw storyboard scenes via LLM.

    Returns a list of scene dicts (un-post-processed).
    Returns [] on failure.
    """
    acts_text = ""
    for act in blueprint.get("acts", []):
        acts_text += f"\n{act['name']} ({act['timeRange']}):\n"
        for e in act.get("events", []):
            acts_text += f"  - {e}\n"
        acts_text += "  Tags planned:\n"
        for t in act.get("tags", []):
            acts_text += f"    [{t['type']}: {t.get('content', '')}]\n"

    facts_list = (
        "\n".join(f"• {f}" for f in checked_facts[:15])
        if checked_facts else "None specified."
    )

    # Pre-compute director override block to avoid nested f-string inside prompt
    _director_block = (
        "\nDIRECTOR'S LATE-STAGE OVERRIDE (highest priority — treat every line as a MUST INCLUDE):\n"
        + director_override.strip()
        + "\nThese override notes were added after the blueprint. Every topic, player, or act mentioned here"
        + "\nMUST be represented in the storyboard. Add new scenes or acts as needed.\n"
    ) if director_override and director_override.strip() else ""

    # Build retention brief injection block
    retention_block = ""
    if retention_brief and isinstance(retention_brief, dict) and not retention_brief.get("error"):
        cf  = retention_brief.get("contrast_frame", {})
        anc = retention_brief.get("anchor_character", {})
        rfs = retention_brief.get("act_reframes", [])
        retention_block = f"""
RETENTION MECHANICS — these are MANDATORY structural constraints, not suggestions:

CORE CONTRAST LOOP: {cf.get('loop_sentence','')}
  Past state label:    {cf.get('past_label','')}
  Present state label: {cf.get('present_label','')}
  This loop sentence must recur at EVERY act transition — the narrator echoes it in different words.

ANCHOR CHARACTER: {anc.get('name','')}
  Framing: {anc.get('framing','')}
  Introduce: {anc.get('first_appears','')}
  Act 5 closing line: {anc.get('closing_line','')}
  This character must appear as a named subject in at least 3 acts.

CLOSING PROVOCATION (Act 5 final scene narration): {retention_brief.get('closing_question','')}
  The video MUST NOT end with a conclusion. It ends with this open question.

ACT PURPOSE — each act must answer its assigned question:
{chr(10).join(f"  • {rf.get('act','')}: {rf.get('question','')} → payoff: {rf.get('payoff','')}" for rf in rfs)}
"""

    prompt = f"""Generate a scene-by-scene storyboard for this football documentary.

VIDEO TITLE: {topic}
SUBJECT: {entity}

WIKIPEDIA BACKGROUND:
{wiki[:1500] if wiki else ""}

ACTS & PLANNED CONTENT:
{acts_text}

DIRECTOR'S BRIEF — read carefully, every specific moment mentioned here MUST appear as a named scene with real date and opponent:
{context[:2000] if context else "None provided."}

{f"CRITICAL — the following specific scenes from the Director's Brief MUST be included (extract from context above): scan the brief for any match results, incidents, or named moments and include each as a dedicated clip or graphic scene." if context else ""}

MUST INCLUDE THESE MOMENTS (confirmed by director):
{facts_list}
{_director_block}
{retention_block}
Generate a complete scene sequence — every scene in order from open to close, 70-90 scenes total. This is a 15-20 minute documentary — every act needs depth. Each act should have 3-5 narration scenes, 3-5 clip scenes, and 3-5 graphic scenes. Do not rush through events.
Mix narration, clip, and graphic scenes naturally — never put two graphics back-to-back without narration between them.

SCENE TYPES:
- "narration": 1-2 punchy, specific sentences of ACTUAL narration content. NOT GENERIC — must contain real names, real dates, real facts. No "iconic goal celebration", no "a moment of controversy" — write the actual words.
- "clip": specific footage description (searchable on YouTube) + label
- "graphic": which template + specific content (real scores, real player, real season)
- "transition": act break marker. types: letterbox, push, grain, paper, dataLine, flash

NARRATION VOICE RULES (apply to every "narration" scene's content field — these are voiced by TTS):
1. VOCABULARY — write like a smart 20-year-old football fan in a pub, NOT a Guardian long-read.
   BANNED words/phrases (never use): intrinsically, irrevocably, unparalleled, unprecedented, unequivocally,
   ostensibly, paradigm, zenith, nadir, juxtaposition, dichotomy, wellspring, lineage, embodied,
   exemplified, manifested, uninhibited, sublime, ineffable, audacious (overused), poetic, mesmerizing,
   symphony of, tapestry of, fabric of, essence of, soul of, beacon of hope, stark reminder,
   profound shift, irrevocably lost, hangs in the balance, era of (overused), realm of, landscape of,
   in perpetuity. Prefer plain words: stopped, big, mood, peak, showed.
2. PUNCTUATION FOR BREATH — narration is voiced. Prefer FULL STOPS over commas. Use em-dashes (—) for
   asides. Sentence FRAGMENTS allowed for rhythm: "Not slowly. All at once." Read it aloud in your head.
3. POETIC FLOURISH CAP — at most ONE lyrical/metaphorical line per ACT. Default register is declarative.
4. METAPHOR DISCIPLINE — pick ONE primary metaphor and stick with it. Do not pile up canvas + symphony +
   tapestry + fabric + soul.
5. NEVER end a narration scene with a question if it is the FINAL narration of the doc — end with a
   declarative statement that lands the thesis.
6. FIRST-PERSON PERSONALITY (1-2 times per act, NOT every paragraph) — drop in a "I remember…" or "I'd
   argue…" beat to break the AI-essay register. Don't do it every scene.

PER-SCENE CLASSIFICATION (Track D — every scene MUST include these three fields):
- "classification" — narrative necessity. One of:
    "MUST_VISUALISE"      — the beat IS a stat / formation / entity reveal; a graphic is the beat itself
    "SHOULD_VISUALISE"    — comparison or peak moment where a graphic strongly aids comprehension
    "SHOULD_NOT_VISUALISE" — myth, reflection, narration-led copy; no graphic needed
- "data_kind" — the kind of data the scene needs. One of:
    "stat" | "timeline" | "formation" | "quote" | "ranking" | "entity" | "comparison" | "copy" | "none"
- "entity" — the primary subject named in this scene (player, team, manager, or null if abstract)

The downstream ShouldRenderGate uses these to decide which scenes get a graphic. The LLM proposes intent
ONLY; it never picks a template directly. SHOULD_NOT_VISUALISE scenes are dropped from the render pipeline
unless explicitly flagged "explicit_request": true.

EVIDENCE MODE ALTERNATION: Each scene has an evidence_mode of STAT / PORTRAIT / TACTICAL / CLIP / NARRATIVE. Within any 4-scene window, do not let more than 2 scenes share the same evidence_mode. Prefer STAT → PORTRAIT → TACTICAL → CLIP → NARRATIVE rotations. NARRATIVE scenes are valid for transitions, quotes, chapter cards, and bridging narration. PORTRAIT scenes break up dense data sections — use them aggressively whenever a player is the subject.

Map templates to evidence_mode like this:
  STAT      → HERO BIG STAT, HERO STAT BARS, PLAYER RADAR, PLAYER STATS, TOP SCORERS, TOP ASSISTS, SEASON COMPARISON, league graphs
  PORTRAIT  → HERO INTRO, PLAYER TRIO, portrait stat hero, any player-image-dominant graphic
  TACTICAL  → TEAM LINEUP, HERO TACTICAL, formation/heatmap/pitch graphics
  CLIP      → CLIP SINGLE, CLIP COMPARE, footage placeholders
  NARRATIVE → narration scenes, TRANSITION, quote cards, chapter words, MATCH RESULT/TRANSFER/CAREER TIMELINE bridges

MANDATORY ACT STRUCTURE — use exactly these transitions:
ACT 1 (ORIGINS or THE MYTH): starts with TRANSITION letterbox
ACT 2 (RISE or THE SHIFT): starts with TRANSITION push
ACT 3 (PEAK or THE BREAK): starts with TRANSITION grain for thematic docs, letterbox for biography
ACT 4 (THE DEFINING EVENT or THE CONSEQUENCE): starts with TRANSITION grain (biography) or TRANSITION dataLine (thematic)
ACT 5 (REDEMPTION/LEGACY or THE QUESTION): starts with TRANSITION paper

CLIP COMPARE RULE: For evolution and thematic documentaries, CLIP COMPARE is the primary comparison tool.
Use it whenever showing how something changed between eras. Format:
[CLIP COMPARE: left description | right description, Xs, left label | right label]
Example: [CLIP COMPARE: Falcão dribbling through midfield 1982 | Casemiro defending deep 2022, 8s, Freedom | Structure]

SAME-NATIONALITY COMPARISON RULE: For national team identity documentaries, all PLAYER TRIO and SEASON COMPARISON
entries must compare players of the SAME nationality. Compare same role, different era. Never use a foreign player
as the primary contrast — the contrast is Brazil 1982 vs Brazil 2022, not Brazil vs Argentina.

SPATIAL VISUAL PREFERENCE: For thematic docs, prefer HERO TACTICAL over HERO STAT BARS wherever possible.
A heatmap showing where players operated tells the story without interpretation. Use HERO STAT BARS only
when the number itself is the point.

MANDATORY GRAPHICS — these MUST appear in the storyboard:
1. Scene 1: HERO INTRO — content must be the EXACT video title: "{topic}"
2. Every club arrival: CAREER TIMELINE with "Focus: [ClubName]" — use ONCE per major club only (not twice)
3. ACT 3 peak: PLAYER RADAR — "[Player], [Club], [Competition], [Season]"
4. ACT 3: HERO STAT BARS or HERO TACTICAL — head to head vs a specific rival (prefer HERO TACTICAL for movement/positional comparisons)
5. Every title race: HERO FORM RUN for BOTH teams — two separate scenes
6. Every specific named match: TEAM LINEUP — "[Team] [formation] vs [Opposition], [DD Mon YYYY]"

OPTIONAL — emit ONLY when there is a clear peer-comparison story:
- PLAYER TRIO — "the debate, [Subject] vs [Peer1] vs [Peer2]" — THREE INDIVIDUAL PLAYERS. Use ONLY for player biographies where the GOAT/era debate is central. SKIP for thematic, national-team, club, or systems-of-football documentaries.
- SEASON COMPARISON — emit per matchup ONLY when the Director's Brief explicitly requests player vs player comparison:
   SEASON COMPARISON: Luis Suárez vs Lionel Messi, La Liga 2015/16
   SEASON COMPARISON: Luis Suárez vs Cristiano Ronaldo, La Liga 2015/16

EXACT TAG FORMATS — use these exact formats, no variations:
- HERO INTRO: The Genius & Madness of Luis Suarez  (video title, nothing else — no "DOCUMENTARY:" prefix)
- CAREER TIMELINE: Luis Suárez - Focus: Liverpool  (player name, then " - Focus: ", then exact club name)
- PLAYER TRIO: the debate, Luis Suárez vs Lionel Messi vs Cristiano Ronaldo  (three individual players separated by " vs ")
- PLAYER RADAR: Luis Suárez, Liverpool, Premier League, 2013/14  (player, club, competition, season)
- HERO STAT BARS: goals per 90, Suárez vs Ronaldo 2013-14, Liverpool, Real Madrid  (title, subtitle, team A, team B)
- TEAM LINEUP: Liverpool 4-3-3 vs Stoke City, 12 Apr 2014  (include actual formation and date)
- HERO FORM RUN: Liverpool, the 2013/14 title run-in  (team, descriptive label)
- TOP SCORERS: Premier League 2013/14  (competition + season ONLY — no award names, no "Golden Boot")
- PLAYER STATS: Luis Suárez 2013/14  (player name + season — for season stat cards only, NOT for awards)
- HERO BIG STAT: 31, goals, in a single Premier League season, Luis Suárez · 2013/14  (for milestones/records/awards)
- SEASON COMPARISON: Luis Suárez vs Lionel Messi, La Liga 2015/16  (one tag per matchup — use when Director's Brief requests player comparison)

FACT DISCIPLINE — every named claim must be VERIFIABLE. No fabrication, no plausible-sounding invention.

ABSOLUTE RULES:
- Every CLIP SINGLE that names a specific match/score/opponent/stadium MUST reference an event that genuinely happened. If the RESEARCH section above doesn't confirm it and you're not 100% certain it's real, write a more general scene instead. Short and true beats long and false.
- Every CAREER TIMELINE focus club MUST be a club the player actually played for. Do NOT invent.
- Every HERO BIG STAT number MUST be sourced from research, not estimated or rounded.
- Every PLAYER RADAR (player, club, season) tuple MUST be a real season the player was at that club.
- Every TEAM LINEUP date MUST match a real fixture between those two teams.

KNOWN FABRICATION TRAPS — never make these mistakes:
- DO NOT put a player at a club they never played for. Examples of WRONG combos seen in past runs:
   "Ronaldinho - Focus: Chelsea" (he never played for Chelsea — Grêmio, PSG, Barcelona, AC Milan, Flamengo, Atlético-MG, Fluminense)
   "Pelé - Focus: Real Madrid" (he played Santos and NY Cosmos)
   "Messi - Focus: Manchester City" (Barcelona → PSG → Inter Miami)
- DO NOT invent matches. Cross-check the opponent + tournament + year. Examples of FAKE matches seen in past runs:
   "Brazil vs France 1982 World Cup" (they didn't play each other in '82)
   "Argentina vs Italy 1986 World Cup Final" (Argentina vs West Germany was the '86 final)
- DO NOT misstate World Cup totals: Brazil = 5 (1958, 1962, 1970, 1994, 2002). Italy = 4. Germany = 4. Argentina = 3 (1978, 1986, 2022). Uruguay = 2. France = 2. England = 1. Spain = 1.
- DO NOT use wrong dates for famous moments. Cross-check the year and month against research. Example trap: Rivaldo's bicycle kick vs Valencia was 17 Jun 2001, NOT December 2001; it was a bicycle kick, NOT a backheel.
- DO NOT include a player in a match they didn't play. Cross-check injury/suspension status. Example: Neymar did NOT play Brazil vs Netherlands in 2014 (injured by Zúñiga in the Colombia QF).
- If you're inventing a date or score to make a clip "sound real" — STOP. Drop the specific detail and write a more general scene instead.

When unsure: prefer a scene with NO specific date/score/stadium over a scene with a wrong one. The viewer notices wrong facts immediately and trust collapses.

STRICT RULES:
- STRICT CHRONOLOGICAL ORDER — earlier events first within each act, always
- The 2014 World Cup (biting incident) happened in JUNE 2014, while the subject was still at LIVERPOOL. It CAUSED the Barcelona move. It MUST appear between the Liverpool section and the Barcelona section — never after Barcelona.
- The 2010 World Cup (South Africa) happened in JUNE-JULY 2010 while the subject was at AJAX. It MUST appear in ACT 2 (Rise/Ajax era), NOT as the Defining Event in ACT 4.
- International tournaments go in the section covering their calendar year, not after the next club era
- Cold open narration must reference a SPECIFIC, NAMED moment with date and opponent — not "iconic goal" or "controversial moment"
- COLD-OPEN SURPRISE RULE: do NOT open with the single most-iconic moment for the subject. The viewer expects it; predictable hooks lose retention. Pick the SECOND-most-iconic or a counter-intuitive lesser-known moment that re-frames the thesis. Examples of what NOT to use as cold-open hooks:
   * Brazil documentary → NOT Ronaldinho 2002 World Cup, NOT Pelé 1970 (use a Barcelona-era Ronaldinho moment, or a Kaká 2007, or a counter-intuitive academy clip)
   * Messi documentary → NOT the 2022 World Cup final
   * Liverpool 2013/14 → NOT the Gerrard slip
   The most-iconic moment can appear LATER in the doc as the payoff, not the opener.
- PLAYER TRIO is OPTIONAL — only emit when the doc is a player biography with a real peer-rivalry story. Never emit for thematic/national/club docs (e.g. "Why Brazil Stopped Producing Playmakers" should NOT have a PLAYER TRIO).
- ACT 5 ENDING: do NOT end the doc with a question. End with a STATEMENT that completes the thesis. The final NARRATION scene must be a declarative line that lands the argument. Questions are allowed mid-act for rhythm, but the closing beat is always a statement.
- METAPHOR DISCIPLINE: pick ONE primary metaphor for the doc and stick with it (e.g. "canvas vs system/diagram"). Allow ONE secondary metaphor max ("artist", "machine"). Do NOT pile up "symphony", "tapestry", "fabric", "soul", "essence" in the same script — they cancel each other out.
- Never use PLAYER STATS for career totals, awards, or records — use HERO BIG STAT
- TOP SCORERS content must be competition + season ONLY (e.g. "Premier League 2013/14") — never "Golden Boot winner"

CONTENT FIELD FORMAT for each template (the "content" field in your JSON must match this exactly):
HERO INTRO     → "{topic}"
CAREER TIMELINE   → "Luis Suárez - Focus: Liverpool"  (Focus: must match real club name)
PLAYER TRIO       → "the debate, Luis Suárez vs Lionel Messi vs Cristiano Ronaldo"
PLAYER RADAR      → "Luis Suárez, Liverpool, Premier League, 2013/14"
HERO STAT BARS → "goals per 90, Suárez vs Ronaldo 2013/14, Liverpool, Real Madrid"
HERO FORM RUN  → "Liverpool, the 2013/14 title run-in"
TEAM LINEUP       → "Liverpool 4-3-3 vs Manchester City, 13 Apr 2014"
TOP SCORERS       → "Premier League 2013/14"   (NOT "Golden Boot winner (Player)")
TOP ASSISTS       → "La Liga 2015/16"
PLAYER STATS      → "Luis Suárez 2013/14"      (season stats card only — NOT awards)
HERO BIG STAT  → "31, goals, in a single Premier League season, Luis Suárez · 2013/14"
MATCH RESULT      → "Liverpool 3-2 Manchester City, 13 Apr 2014"
TRANSFER          → "Luis Suárez from Liverpool to Barcelona, 2014, £65m"
TRANSITION        → "letterbox"   (just the transition type — letterbox/push/grain/paper/dataLine)

Return ONLY valid JSON:
{{
  "scenes": [
    {{
      "id": "s001",
      "act": "COLD OPEN",
      "actIndex": 0,
      "type": "graphic",
      "template": "HERO INTRO",
      "content": "{topic}",
      "label": "",
      "duration": 8,
      "classification": "MUST_VISUALISE",
      "data_kind": "copy",
      "entity": "{entity}",
      "evidence_mode": "PORTRAIT"
    }},
    {{
      "id": "s002",
      "act": "COLD OPEN",
      "actIndex": 0,
      "type": "narration",
      "content": "Suárez's left foot connected with the ball on the edge of the box — 2 June 2014, Arena das Dunas, Nataly, Brazil. He turned and smiled. Nobody watching understood what was coming.",
      "label": "",
      "duration": 14,
      "classification": "SHOULD_NOT_VISUALISE",
      "data_kind": "none",
      "entity": "Luis Suárez",
      "evidence_mode": "NARRATIVE"
    }},
    {{
      "id": "s003",
      "act": "COLD OPEN",
      "actIndex": 0,
      "type": "clip",
      "template": "CLIP SINGLE",
      "content": "Luis Suárez biting Giorgio Chiellini's shoulder, referee not seeing it, Uruguay vs Italy 2014 World Cup",
      "label": "24 Jun 2014 — Arena das Dunas",
      "duration": 8,
      "classification": "MUST_VISUALISE",
      "data_kind": "none",
      "entity": "Luis Suárez",
      "evidence_mode": "CLIP"
    }},
    {{
      "id": "s004",
      "act": "ACT 1 — ORIGINS",
      "actIndex": 1,
      "type": "transition",
      "template": "TRANSITION",
      "content": "letterbox",
      "label": "",
      "duration": 2,
      "classification": "SHOULD_NOT_VISUALISE",
      "data_kind": "none",
      "entity": null,
      "evidence_mode": "NARRATIVE"
    }},
    {{
      "id": "s005",
      "act": "ACT 3 — PEAK",
      "actIndex": 3,
      "type": "graphic",
      "template": "PLAYER TRIO",
      "content": "the debate, Luis Suárez vs Lionel Messi vs Cristiano Ronaldo",
      "label": "2013/14 — the best in the world?",
      "duration": 12,
      "classification": "SHOULD_VISUALISE",
      "data_kind": "comparison",
      "entity": "Luis Suárez",
      "evidence_mode": "PORTRAIT"
    }},
    {{
      "id": "s006",
      "act": "ACT 3 — PEAK",
      "actIndex": 3,
      "type": "graphic",
      "template": "HERO BIG STAT",
      "content": "31, goals, in a single Premier League season, Luis Suárez · 2013/14",
      "label": "",
      "duration": 8,
      "classification": "MUST_VISUALISE",
      "data_kind": "stat",
      "entity": "Luis Suárez",
      "evidence_mode": "STAT"
    }},
    {{
      "id": "s007",
      "act": "ACT 3 — PEAK",
      "actIndex": 3,
      "type": "graphic",
      "template": "TEAM LINEUP",
      "content": "Liverpool 4-3-3 vs Manchester City, 13 Apr 2014",
      "label": "",
      "duration": 9,
      "classification": "SHOULD_VISUALISE",
      "data_kind": "formation",
      "entity": "Liverpool",
      "evidence_mode": "TACTICAL"
    }}
  ],
  "totalDuration": 1200
}}"""

    try:
        raw = ask_gemini(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        scenes = result.get("scenes", [])
        # Track D: coerce classification + data_kind + evidence_mode on every returned scene
        for s in scenes:
            _validate_classification(s)
            _validate_data_kind(s)
            _normalize_evidence_mode(s)
            if "entity" not in s:
                s["entity"] = None
            s.setdefault("explicit_request", False)
        # Priority 5: warn (do NOT reorder) on long evidence_mode runs
        _check_evidence_mode_runs(scenes)
        return scenes
    except Exception as e:
        print(f"  [storyboard_agent] Scene generation failed: {e}")
        return []
