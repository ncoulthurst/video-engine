"""
Retention Agent — second-pass analysis that wraps a blueprint in YouTube retention mechanics.

Takes the raw blueprint (essay-framed) and extracts:
  - Core visual contrast loop (before/after frame repeated through every act)
  - Narrative anchor character (one person who embodies the thesis)
  - Closing provocation (a question, not a conclusion)
  - Specific visual comparison ideas mapped to engine templates
  - Per-act reframes (what question each act answers)

Output flows into _generate_storyboard() as mandatory framing constraints.
Without this, scripts list events. With it, they build loops.

Reference gap (Brazil playmakers doc):
  Engine output: "a combination of factors including changes in culture..."
  Target output: "Brazil didn't just lose playmakers — they stopped producing them by design"
                 Anchor: Neymar as the last bridge
                 Closing: "After Neymar, what's left?"
"""

import json
import re
from utils.llm_utils import ask_gemini, ask_llm, _cached_infer


# ── Country/nationality scope detection ──────────────────────────────────────

_COUNTRY_SCOPE = [
    "brazil", "argentina", "france", "spain", "germany", "england", "italy",
    "portugal", "netherlands", "holland", "belgium", "croatia", "uruguay",
    "colombia", "chile", "mexico", "senegal", "nigeria", "ghana", "japan",
    "south korea", "ivory coast", "côte d'ivoire", "scotland", "denmark",
    "sweden", "norway", "switzerland", "austria", "poland", "ukraine",
]


_THEMATIC_SIGNALS = [
    "why did", "why brazil", "why argentina", "why france", "why spain", "why germany",
    "why england", "why italy", "story of", "history of", "decline of", "rise of",
    "death of", "end of", "how football", "evolution of", "the problem with",
    "what happened to", "where did", "stopped producing", "lost generation",
    "golden generation", "tactical revolution", "identity", "culture of",
    "system", "philosophy", "era of", "the fall of", "the rise of",
]

# Player-biography indicators — if any of these are in the entity, it's a player doc
# even if a country name appears later in the content
_BIOGRAPHY_SIGNALS = [
    "the german", "the french", "the spanish", "the english", "the italian",
    "the brazilian", "the argentinian", "the argentine", "the portuguese",
    "the dutch", "the belgian", "the croatian", "the chilean", "the colombian",
    "the mexican", "the senegalese", "the nigerian", "the ghanaian",
    "messi", "ronaldo",  # any "X Messi" / "X Ronaldo" nickname → biography
]


def _extract_subject_scope(entity: str, context: str, acts: list) -> dict:
    """
    Detect whether this is a national/systemic doc (about a country or system)
    vs a player biography that happens to mention a country.

    Rules:
    - Must have BOTH a country name AND a thematic signal to be national_doc
    - Entity containing "The German X" / "The Chilean X" etc. → biography, not national doc
    - Acts-only country match (no thematic signal) → NOT national doc

    Returns {"nationality": "Brazil", "is_national_doc": True} or
            {"nationality": None,     "is_national_doc": False}.
    """
    entity_lower = entity.lower()
    combined = (entity + " " + context[:400]).lower()

    # Biography guard: "The German Messi", "The French Ronaldo" etc. → player biography
    if any(sig in entity_lower for sig in _BIOGRAPHY_SIGNALS):
        return {"nationality": None, "is_national_doc": False}

    # Must have a thematic signal in the title or context to qualify as national doc
    has_thematic = any(sig in combined for sig in _THEMATIC_SIGNALS)
    if not has_thematic:
        # Check act names too (e.g. "THE MYTH OF JOGA BONITO" signals thematic)
        acts_text = " ".join(a.get("name", "") for a in acts[:3]).lower()
        has_thematic = any(sig in acts_text for sig in _THEMATIC_SIGNALS)

    if not has_thematic:
        return {"nationality": None, "is_national_doc": False}

    # Now check for country
    for country in _COUNTRY_SCOPE:
        if country in combined:
            return {"nationality": country.title(), "is_national_doc": True}

    # Country in act summaries (only valid if thematic already confirmed)
    acts_text = " ".join(
        a.get("name", "") + " " + a.get("summary", "") for a in acts[:3]
    ).lower()
    for country in _COUNTRY_SCOPE:
        if country in acts_text:
            return {"nationality": country.title(), "is_national_doc": True}

    return {"nationality": None, "is_national_doc": False}


def _score_anchor(anchor: dict, acts: list, scope: dict, loop_sentence: str = "") -> int:
    """
    Score an anchor candidate. Higher = better.

    Weights (intentional — visual proof is the deciding factor):
    +4  visual_proof_strength (assessed via LLM against documentary thesis)
    +3  mentioned in final 2 acts
    +2  non-empty closing_line
    +2  bridge/transition framing language
    -2  peak-only framing (safe pick, not narrative proof)
    -4  nationality mismatch (hard disqualifier for national docs)
    """
    if not anchor or not anchor.get("name"):
        return -99

    score = 0
    name = anchor.get("name", "").lower()
    name_parts = [p for p in re.split(r"\s+", name) if len(p) > 2]
    framing = anchor.get("framing", "").lower()
    closing = anchor.get("closing_line", "").strip()

    # ── visual_proof_strength — most important factor ─────────────────────────
    # Does this player VISIBLY demonstrate the documentary's central contrast?
    # Not "are they famous" — "does footage of them prove the argument on screen?"
    thesis = loop_sentence or anchor.get("visual_proof", "")
    if thesis and anchor.get("name"):
        vp_raw = _cached_infer(
            f"Documentary thesis: '{thesis}'. "
            f"When shown in football clips, does {anchor['name']} VISIBLY demonstrate this contrast? "
            f"Consider: do they visibly struggle, show tension between styles, or embody the gap? "
            f"Reply with ONLY a single digit: 2 (strongly demonstrates), 1 (partially), 0 (does not).",
            expected_type="str",
            fallback="1",
        )
        try:
            vp = int(vp_raw.strip()) if vp_raw and vp_raw.strip() in ("0", "1", "2") else 1
            score += vp * 2  # 0, +2, or +4
        except (ValueError, AttributeError):
            score += 2  # neutral

    # ── Final-act presence ────────────────────────────────────────────────────
    final_acts = acts[max(0, len(acts) - 2):]
    final_text = " ".join(
        a.get("name", "") + " " + a.get("summary", "") + " " + " ".join(a.get("events", []))
        for a in final_acts
    ).lower()
    if any(p in final_text for p in name_parts):
        score += 3

    # ── Has closing line ──────────────────────────────────────────────────────
    if closing:
        score += 2

    # ── Bridge/transition framing ─────────────────────────────────────────────
    bridge_words = ["bridge", "last", "transition", "decline", "end of", "generation",
                    "symbol", "trapped", "caught", "isolated", "between", "gap"]
    if any(w in framing for w in bridge_words):
        score += 2

    # ── Peak-only penalty ─────────────────────────────────────────────────────
    peak_words = ["greatest", "best ever", "goat", "all-time", "dominant", "unrivalled"]
    if any(w in framing for w in peak_words):
        score -= 2

    # ── Nationality check ─────────────────────────────────────────────────────
    if scope.get("is_national_doc") and scope.get("nationality"):
        nationality = scope["nationality"]
        anchor_nat = _cached_infer(
            f"What nationality is the footballer '{anchor.get('name', '')}'? "
            f"Reply with ONLY the country name (e.g. 'Brazil', 'France').",
            expected_type="str",
            fallback=None,
        )
        if anchor_nat and nationality.lower() not in anchor_nat.lower():
            score -= 4  # hard disqualifier

    return score


def _validate_anchor(anchor: dict, acts: list, scope: dict = None) -> tuple[bool, str]:
    """
    Deterministic anchor validation. Returns (is_valid, rejection_reason).

    Rejects if:
    - no name
    - not found in final 2 acts
    - early-fade only (appears in acts 1-2, no closing line)
    - nationality mismatch for national-scope docs
    """
    if not anchor or not anchor.get("name"):
        return False, "no anchor name"

    scope = scope or {}
    name = anchor.get("name", "").lower()
    name_parts = [p for p in re.split(r"\s+", name) if len(p) > 2]

    if not acts:
        return True, ""

    # Final-act presence check
    final_acts = acts[max(0, len(acts) - 2):]
    final_text = " ".join(
        a.get("name", "") + " " + a.get("summary", "") + " " + " ".join(a.get("events", []))
        for a in final_acts
    ).lower()
    if not any(p in final_text for p in name_parts):
        return False, f"anchor '{anchor['name']}' not referenced in final acts"

    # Early-fade check
    first_appears = anchor.get("first_appears", "").upper()
    closing_line = anchor.get("closing_line", "").strip()
    if any(t in first_appears for t in ["ACT 1", "ACT 2", "ACT I", "ACT II"]) and not closing_line:
        return False, f"anchor '{anchor['name']}' fades out early (no closing line)"

    # Nationality check for national-scope docs
    if scope.get("is_national_doc") and scope.get("nationality"):
        nationality = scope["nationality"]
        anchor_nat = _cached_infer(
            f"What nationality is the footballer '{anchor.get('name', '')}'? "
            f"Reply with ONLY the country name (e.g. 'Brazil', 'France').",
            expected_type="str",
            fallback=None,
        )
        if anchor_nat and nationality.lower() not in anchor_nat.lower():
            return False, (
                f"anchor '{anchor['name']}' is {anchor_nat}, "
                f"not {nationality} — nationality mismatch"
            )

    return True, ""


def _fallback_anchor_prompt(entity: str, acts: list, rejected_name: str,
                             reason: str, scope: dict = None) -> str:
    """Build a focused re-prompt to fix anchor selection."""
    final_acts = acts[max(0, len(acts) - 2):]
    final_summaries = "\n".join(
        f"  {a.get('name','')}: {a.get('summary','')}" for a in final_acts
    )
    nationality_rule = ""
    if scope and scope.get("is_national_doc") and scope.get("nationality"):
        nationality_rule = (
            f"\nCRITICAL NATIONALITY RULE: The anchor MUST be {scope['nationality']}. "
            f"Do NOT choose a player from another country, no matter how relevant globally."
        )
    return f"""The anchor character '{rejected_name}' was rejected: {reason}.

SUBJECT: {entity}{nationality_rule}
FINAL ACT CONTENT:
{final_summaries}

Choose a NEW anchor character who:
- Is named or implied in the final act content above
- Is the BRIDGE between eras — not the best player, but the transitional one
- Has an unresolved arc (still active, recently retired, or represents an open question)
- Embodies the documentary's thesis in human form

Return ONLY valid JSON:
{{
  "name": "full name",
  "framing": "one sentence — their role as a bridge or symbol of transition",
  "first_appears": "act name",
  "closing_line": "exact narrator line about them in the final act"
}}"""

_VISUAL_TEMPLATES = """
AVAILABLE ENGINE TEMPLATES (use exact names):
  CareerTimeline         — club-by-club career arc
  HeroGoalRush        — season-by-season tally (goals, assists, appearances)
  HeroStatBars        — head-to-head stat comparison (2 subjects, side by side)
  HeroScatterPlot     — value vs output scatter
  HeroLeagueGraph     — league position across a season
  AttackingRadar         — per-90 stat radar (single player)
  HeroComparisonRadar — two-player radar overlay
  HeroTactical        — formation / pressing / positional heatmap
  HeroHeadlineStack   — editorial bold-text reveal (punchy statement)
  HeroBigStat         — single record or milestone number
  HeroFormRun         — form run (W/D/L sequence)
  HeroNewsFeed        — press reaction / headline montage
  PlayerTrio             — three-way comparison debate card
  HeroTransferRecord  — transfer fees in/out per club
  HeroTransferProfit  — buy-low/sell-high profit table
  HeroMatchTimeline   — match events on a timeline
"""


def optimize_retention(script: str, out_dir: str) -> None:
    """Post-script retention pass — stub. Retention mechanics are now applied at the
    blueprint stage via generate_retention_brief(). This function exists for pipeline
    compatibility only."""
    pass


def generate_retention_brief(entity: str, blueprint: dict, context: str = "", wiki: str = "") -> dict:
    """
    Analyse a blueprint and return retention mechanics.

    Returns dict with: contrast_frame, anchor_character, closing_question,
                       visual_comparisons, act_reframes, loop_sentence
    """
    acts = blueprint.get("acts", [])
    scope = _extract_subject_scope(entity, context, acts)

    acts_text = ""
    for act in acts:
        acts_text += f"\n{act.get('name', '')}: {act.get('summary', '')}\n"
        for e in act.get("events", [])[:4]:
            acts_text += f"  • {e}\n"

    nationality_constraint = ""
    if scope["is_national_doc"] and scope["nationality"]:
        nationality_constraint = (
            f"\nCRITICAL — NATIONALITY RULE: This is a {scope['nationality']} documentary. "
            f"The anchor character MUST be {scope['nationality']}. "
            f"Do NOT choose players from other countries, no matter how globally relevant. "
            f"A non-{scope['nationality']} player as anchor is a system failure.\n"
        )

    prompt = f"""You are a YouTube retention strategist. A football documentary is being made:
{nationality_constraint}
SUBJECT: {entity}
DIRECTOR'S BRIEF: {context[:600] if context else "Not provided"}

BLUEPRINT:
{acts_text[:1500]}

Define the RETENTION MECHANICS — the structural devices that keep viewers watching 15-20 minutes.

1. CONTRAST FRAME — the single before/after loop the whole video repeats.
   - ONE visual comparison, not a list. Stated as a single provocative narrator sentence.
   - Example: "Brazil didn't just lose playmakers — they stopped producing them by design"
   - Example: "PSG didn't buy the best player in the world. They bought a symbol."
   - This sentence should be repeatable at every act transition.

2. ANCHOR CANDIDATES — give exactly 3 candidates, ranked best-first.
   For each: choose someone whose CLIPS VISUALLY PROVE THE THESIS — not the most famous, not the
   most successful, but the one who, when shown on screen, best demonstrates the central contrast.
   - Rank 1: the player who most visibly embodies the tension/gap/decline when shown in clips
   - Rank 2: alternative with different framing angle
   - Rank 3: the "safe/famous" pick (probably not the best choice, but worth showing)
   - CRITICAL: For thematic/national docs — choose bridge characters (exist in BOTH eras),
     not golden-era icons. A player who thrived in the old system only = nostalgia, not tension.
     A player who is visibly constrained by the new system = forward tension.
   - visual_proof: one sentence describing HOW their clips demonstrate the contrast on screen

3. CLOSING PROVOCATION — the final unanswered question. NOT a conclusion.
   - Creates forward tension. Viewer is left thinking, not satisfied.
   - "After Neymar, what's left?" beats "Brazil has declined in quality"

4. ACT REFRAMES — one sentence per act framing what question it answers.
   - Not "what happens" — "what does the viewer learn?"

{_VISUAL_TEMPLATES}

Return ONLY valid JSON:
{{
  "contrast_frame": {{
    "past_label": "short label for 'before' (e.g. 'Freedom')",
    "present_label": "short label for 'after' (e.g. 'System')",
    "loop_sentence": "narrator sentence repeatable at every act break",
    "thumbnail_angle": "how the contrast looks on a thumbnail"
  }},
  "anchor_candidates": [
    {{
      "name": "full name",
      "framing": "one sentence — their role in the thesis",
      "first_appears": "act name",
      "closing_line": "exact narrator line about them in Act 5",
      "visual_proof": "one sentence — how clips of them demonstrate the contrast on screen"
    }}
  ],
  "closing_question": "the final unanswered provocation",
  "loop_sentence": "the contrast as a bold one-liner",
  "act_reframes": [
    {{
      "act": "act name",
      "question": "what question does this act answer?",
      "payoff": "what the viewer learns by end of this act"
    }}
  ]
}}"""

    raw = ask_gemini(prompt)
    if not raw:
        raw = ask_llm(prompt)

    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
    except Exception as e:
        print(f"    [!] Retention Agent: JSON parse failed — {e}")
        return {
            "contrast_frame": {
                "past_label": "Before",
                "present_label": "After",
                "loop_sentence": f"The story of {entity} is a story of transformation.",
                "thumbnail_angle": "split screen: then vs now"
            },
            "anchor_character": {
                "name": entity, "framing": "", "first_appears": "ACT 1", "closing_line": ""
            },
            "closing_question": f"What did {entity} lose along the way?",
            "loop_sentence": f"This is how {entity} changed.",
            "act_reframes": [],
            "error": str(e)
        }

    # --- Score candidates, select best, expose all for UI ---
    # (acts and scope already computed above before the prompt)
    if scope["is_national_doc"]:
        print(f"    [Retention] National scope detected: {scope['nationality']}")

    loop_sentence = result.get("contrast_frame", {}).get("loop_sentence", "") or result.get("loop_sentence", "")
    candidates = result.get("anchor_candidates", [])

    # Backwards-compat: if LLM returned old schema with anchor_character instead of candidates
    if not candidates and result.get("anchor_character"):
        candidates = [result["anchor_character"]]

    # Score every candidate
    scored = []
    for c in candidates:
        s = _score_anchor(c, acts, scope, loop_sentence)
        scored.append({**c, "score": s})
        print(f"    [Retention] Candidate '{c.get('name','?')}' score={s}  visual_proof='{c.get('visual_proof','')[:60]}'")

    # Sort best-first
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Override rule: if highest-scored ≠ highest-prominence (first LLM pick), log it
    if scored and candidates and scored[0].get("name") != candidates[0].get("name"):
        print(f"    [Retention] Engine overrides LLM order: '{scored[0]['name']}' preferred over '{candidates[0]['name']}'")

    # Pick best valid anchor; fall back down the list if needed
    selected = None
    for candidate in scored:
        is_valid, reason = _validate_anchor(candidate, acts, scope)
        if is_valid:
            selected = candidate
            break
        else:
            print(f"    [!] Retention Agent: '{candidate.get('name')}' invalid — {reason}")

    # If no valid candidate, re-prompt with best candidate's rejection reason
    if not selected:
        top = scored[0] if scored else {}
        rejected_name = top.get("name", "unknown")
        _, reason = _validate_anchor(top, acts, scope) if top else (False, "no candidates")
        print(f"    [!] Retention Agent: all candidates invalid — re-prompting")
        fallback_prompt = _fallback_anchor_prompt(entity, acts, rejected_name, reason, scope)
        fallback_raw = ask_gemini(fallback_prompt) or ask_llm(fallback_prompt)
        try:
            if fallback_raw.startswith("```"):
                fallback_raw = fallback_raw.split("```")[1]
                if fallback_raw.startswith("json"):
                    fallback_raw = fallback_raw[4:]
            new_anchor = json.loads(fallback_raw.strip())
            new_anchor["score"] = _score_anchor(new_anchor, acts, scope, loop_sentence)
            selected = new_anchor
            scored.append(new_anchor)
            print(f"    [+] Retention Agent: fallback anchor → {new_anchor.get('name')}")
        except Exception as e2:
            print(f"    [!] Retention Agent: fallback anchor parse failed — {e2}")
            selected = scored[0] if scored else {"name": entity, "framing": "", "first_appears": "ACT 1", "closing_line": "", "score": 0}

    result["anchor_character"] = selected
    result["anchor_candidates"] = scored   # full ranked list for UI
    result["_scope"] = scope

    return result
