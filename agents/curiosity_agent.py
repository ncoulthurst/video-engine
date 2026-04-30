"""
Curiosity Agent — generates ranked YouTube video ideas using proven viral hook formulas.

Reference: "How the Masters Really Works" — 1.4M views in 2 weeks on a brand-new channel.
Two-part system: thumbnail = shocking specific fact, title = the frame ("you don't really know this").

Key title insight: shorter is stronger.
  WEAK:  "How Brentford's Attacking Signings Really Work"
  GOOD:  "How Brentford Keep Finding Elite Attackers"
  GOOD:  "Why Brentford Never Miss on Strikers"
Active verbs beat "Really Works". Scan time on YouTube is ~0.3s — every word must earn its place.

Ranking: curiosity × clarity × timeliness
  - curiosity: how far is the gap between what viewer expects and what they'll learn?
  - clarity:   is the hook instantly understandable and believable?
  - timeliness: how relevant right now?

Output includes Core Question, Narrative Shape, and Visual Anchors (mapped to engine templates)
so each idea connects directly to the production pipeline.
"""

import json
from utils.llm_utils import ask_gemini, ask_llm


_HOOK_FORMULAS = """
FORMULA 8 — GEOPOLITICAL SPORTS  ★ MASSIVELY UNDERUSED, EXTREMELY HIGH CEILING ★
Football as a lens on money, power, ideology, and nations.
This formula works because it elevates the story beyond sport — a viewer who doesn't
even watch football will click because the real subject is geopolitics, wealth, or power.

Why it works: "Saudi Football: The £600B Gamble"
  → What does a sovereign wealth fund want from football? (power question)
  → Is it working? (stakes question)
  → What does it mean for the sport? (consequence question)
Three non-sport questions fire simultaneously. The viewer is curious even if they
have never watched a Saudi league match in their life.

Subject areas to mine:
  STATE OWNERSHIP   — Saudi, UAE, Qatar, US private equity, Chinese investment waves
  FINANCIAL DOPING  — PSG, Manchester City FFP saga, the rules that bend or break
  LEVERAGE & POWER  — how clubs use football for soft power, tourism, or nation-branding
  CORRUPTION        — FIFA scandals, dodgy transfers, referee fixing, agent networks
  IDEOLOGY          — how authoritarian regimes use sport (World Cup hosting, player silence)
  MIGRATION         — why players from X country always end up in Y league
  ECONOMICS         — what a £1B wage bill actually buys, what it doesn't

Rules:
  1. The football is the VEHICLE, not the point — the real subject is money/power/ideology
  2. The number must imply SCALE THAT SHOCKS — not £150M (normal now), but £600B (sovereign fund)
  3. The title should work for someone who has NEVER watched football
  4. One concrete human story anchors it — a player, a deal, a moment — so it doesn't feel like a lecture

Examples:
  "Saudi Football: The £600B Gamble"
  "How Qatar Bought The World Cup"
  "Why Abu Dhabi Needed Manchester City"
  "The Country That Owns European Football"
  "How American Money Broke The Transfer Market"
  "Why China's Football Dream Failed"

Thumbnail: the scale number alone, or a flag vs a trophy — pure visual contrast

FORMULA 1 — HOW IT REALLY WORKS
Proven: "How the Masters Really Works" → 1.4M views in 2 weeks, new channel.
Thumbnail: single shocking specific number. Title: short active frame ("how/why [subject] [verb]").
The "really" or implied "really" signals the viewer's existing model is incomplete.
TITLE RULE: do NOT default to "Really Works". Find the sharpest active verb instead.
  WEAK:  "How Brentford's Attacking Signings Really Work"
  GOOD:  "How Brentford Keep Finding Elite Attackers"
  GOOD:  "Why Brentford Never Miss on Strikers"
  GOOD:  "How the Premier League Actually Makes Money"
Thumbnail: the single most surprising specific number tied to the subject

FORMULA 2 — COUNTERINTUITIVE MONEY
Subject has a surprising relationship with money: refused it, lost it, made it in an unexpected way.
Number must be specific. The surprise must be real — not just "they spent a lot".
  GOOD: "How Brentford Made £400M Without a Single Star"
  GOOD: "Why Ajax Left £500M on the Table"
Thumbnail: the number alone, massive

FORMULA 3 — THE REAL REASON
The accepted explanation is wrong or incomplete. Something more interesting is underneath.
The hook implies the viewer has been misled — not aggressively, just quietly.
  GOOD: "Why Suárez Really Left Liverpool"
  GOOD: "The Real Reason Ronaldo Went to Saudi Arabia"
Thumbnail: "THE REAL REASON" or a short contrasting claim

FORMULA 4 — SPECIFIC STAT
One number reframes the subject completely. The viewer's mental model is wrong.
The number must be counterintuitive — if they knew it they'd say "wait, seriously?"
  GOOD: "The Stat That Proves Suárez Was Better Than Messi"
  GOOD: "31 Goals. The Number English Football Forgot"
Thumbnail: the number alone, stripped of context

FORMULA 5 — AGAINST EXPECTATIONS
Subject did the opposite of rational — and it worked or failed spectacularly. The why drives clicks.
  GOOD: "Why Liverpool Refused £1 + 1p for Suárez"
  GOOD: "How Atlético Won La Liga by Spending Nothing"
Thumbnail: the unexpected action ("REFUSED", "£0", "FREE")

FORMULA 6 — TIMING ANCHOR
Only makes sense right now. Urgency: watch before the moment passes.
  GOOD: "Why [Player Currently Linked] Becomes £200M Next Season"
Thumbnail: current claim tied to the news cycle

FORMULA 7 — PATTERN OBSERVATION  ★ HIGHLY UNDERUSED, EXTREMELY CLICKABLE ★
A specific real-world pattern that triggers multiple simultaneous "why?" questions.
No number required. The OBSERVATION ITSELF is the hook.
This is the most underrated formula — it feels like a genuine question, not a sales pitch.

Why it works: "Brazil used to produce world-class fullbacks"
  → Why fullbacks specifically? (not strikers, not goalkeepers)
  → Why Brazil? (the most attacking nation in football)
  → Why "used to"? (what changed? when? is it fixable?)
  → Who were they? (Cafu, Roberto Carlos, Marcelo, Dani Alves — instantly vivid)
Four questions fire simultaneously. The viewer has to click to resolve the tension.

Rules for a strong Pattern Observation:
  1. SPECIFIC — not "Brazil produces great players" (too broad), must name a position/era/trait
  2. TRIGGERS MULTIPLE QUESTIONS — at least 2 "why?" questions must fire from the title alone
  3. IMPLIES CHANGE — "used to", "stopped", "no longer", "suddenly" adds a temporal mystery
  4. COUNTERINTUITIVE for the subject — the most attacking nation specialising in defensive positions

Pattern types to look for:
  TEMPORAL:    "Why [Country/Club] Stopped Producing [Type of Player]"
               "Why [Era] Produced More [Position] Than Any Other"
  GEOGRAPHIC:  "Why [Country/City/Region] Produces More [Position/Type] Than Anywhere Else"
  POSITIONAL:  "Why [Country/Club] Always Gets [Position] Right (And [Other Position] Wrong)"
  ERA:         "Why The [Decade] Was The Golden Age of [Position/Style]"
  STRUCTURAL:  "Why [League/Club] Develops [Type] But Never [Other Type]"

Examples:
  "Why Brazil Stopped Producing Fullbacks"
  "Why England Never Develops Strikers"
  "Why Spanish Clubs Always Produce Goalkeepers"
  "Why Ajax Produces a Generation Every Decade"
  "Why South American Stars Always Leave at 18"
  "Why the Premier League Can't Develop Wingers"
  "Why German Football Forgot How to Score"

Thumbnail: the pattern stated as a bold claim or a side-by-side comparison (1998 vs 2024 names)
Title format: "Why [Subject] [Pattern]" or "How [Subject] [Lost/Found/Changed] [Thing]"
"""

_NARRATIVE_SHAPES = """
NARRATIVE SHAPES — pick the one that fits the story:
  MYSTERY    — something happened that nobody has properly explained. The video is the investigation.
  SYSTEM     — there is a repeatable process behind a surprising outcome. The video reveals the machine.
  RISE/FALL  — a journey from one state to another. Classic arc, works for players and clubs.
  PARADOX    — two things that shouldn't coexist, do. The tension is the video.
"""

_VISUAL_ANCHOR_TEMPLATES = """
VISUAL ANCHOR → ENGINE TEMPLATE MAPPING:
When suggesting visual anchors, use these exact engine template names where possible:
  "career timeline / transfer history"  → CareerTimeline
  "season-by-season stats"              → HeroGoalRush
  "transfer profit/loss table"          → HeroTransferProfit
  "head-to-head stat comparison"        → HeroStatBars
  "scatter plot (value vs output)"      → HeroScatterPlot
  "form run comparison"                 → HeroFormRun
  "league position across a season"     → HeroLeagueGraph
  "player radar (per-90 stats)"         → AttackingRadar
  "tactical formation/pressing system"  → HeroTactical
  "editorial headline reveal"           → HeroHeadlineStack
  "big single stat (record/milestone)"  → HeroBigStat
  "news feed / reaction montage"        → HeroNewsFeed
  "player trio comparison"              → PlayerTrio
  "match timeline"                      → HeroMatchTimeline
List 2-4 visual anchors per idea. Each anchor = [description, EngineTemplate].
"""


def generate_curiosity_ideas(topic: str, current_context: str = "", n_ideas: int = 12) -> dict:
    """
    Generate n_ideas curiosity-driven YouTube video ideas for the given football topic.

    Returns:
    {
      "topic": "Brentford FC",
      "ideas": [
        {
          "rank": 1,
          "title": "How Brentford Keep Finding Elite Attackers",
          "thumbnail_hook": "£400M PROFIT",
          "hook_formula": "HOW IT REALLY WORKS",
          "core_question": "Is this luck, or a system no one else understands?",
          "narrative_shape": "SYSTEM",
          "opening_line": "Since 2018, Brentford have sold four strikers for a combined...",
          "counterintuitive_element": "A Championship-era club has outperformed every top-six side on striker ROI",
          "visual_anchors": [
            ["transfer profit/loss table", "HeroTransferProfit"],
            ["career timeline / transfer history", "CareerTimeline"],
            ["head-to-head stat comparison", "HeroStatBars"]
          ],
          "curiosity_score": 9,
          "clarity_score": 8,
          "timeliness": 6,
          "combined_score": 432.0
        }
      ]
    }
    """
    context_block = f"\nCURRENT CONTEXT (use for timeliness scoring):\n{current_context}\n" if current_context else ""

    prompt = f"""You are a YouTube strategist specialising in curiosity-driven football content.
Generate {n_ideas} video ideas for the topic below using the 7 hook formulas.

TWO PROVEN REFERENCE POINTS:

1. "How the Masters Really Works" — 1.4M views in 2 weeks, new channel.
   Formula: thumbnail = shocking specific number, title = short active frame.

2. "Brazil used to produce world-class fullbacks" — this is a perfect PATTERN OBSERVATION hook.
   No number needed. The observation fires 4 questions at once:
   Why fullbacks? Why Brazil? Why "used to"? What changed?
   The viewer clicks to resolve the tension.
   This formula (Formula 7) is massively underused and often outperforms number-based hooks
   because it feels like a genuine question, not a sales pitch.

TITLE RULE: make it feel like a transformation OR an observation — never a description.
  DESCRIPTION (weak): "How Brentford Find Strikers"
  TRANSFORMATION (strong): "How Brentford Turn £10M Into £70M"
  OBSERVATION (strong): "Why Brentford Rarely Miss on Strikers"

TOPIC: {topic}
{context_block}

THE 6 HOOK FORMULAS:
{_HOOK_FORMULAS}

NARRATIVE SHAPES:
{_NARRATIVE_SHAPES}

VISUAL ANCHORS (map to engine templates):
{_VISUAL_ANCHOR_TEMPLATES}

SCORING — be honest, not generous. The most common mistake is OVER-SCORING STAT CARDS.

A STAT CARD is an idea built around a single number with no story beneath it.
  BAD EXAMPLE: "Bellingham: £150M Earned, 0 Lost"
  Why it scores LOW: the stat is the entire idea. There is no mystery, no system, no arc.
  A viewer who clicks learns... the stat they already read in the title. No payoff.
  Curiosity score for a stat card: MAX 5. Clarity MAX 7. These ideas feel like tweets, not documentaries.

A DOCUMENTARY IDEA needs a story ARC — a system to reveal, a mystery to solve, a transformation to trace.
  GOOD EXAMPLE: "Why Brazil Stopped Producing Playmakers" — reveals a structural shift; viewer learns WHY
  GOOD EXAMPLE: "Saudi Football: The £600B Gamble" — geopolitical story; viewer learns what money buys
  GOOD EXAMPLE: "How Ajax Produces a Generation Every Decade" — reveals a system; viewer learns HOW

Test for every idea before scoring: "If a viewer watches the full 15-minute video, what do they LEARN
that they didn't know from the title?" If the answer is "basically nothing — just more detail on the
stat", it's a stat card. Score it 4-5 on curiosity regardless of how punchy the number sounds.

  curiosity_score (1-10): gap between what viewer expects vs what they'll learn.
    10 = shatters mental model (geopolitical reveal, systemic secret, forgotten truth)
    7-8 = clear payoff beyond the hook (pattern explained, arc traced, system revealed)
    4-5 = stat card — punchy number, no depth beneath it
    1-3 = description, no gap

  clarity_score (1-10): is the hook instantly clear and believable to a casual viewer?
    Penalise: numbers that need context before they land, jargon, obscure names
    Reward: title works for someone who barely watches football

  timeliness (1-10): how relevant right now? 10 = directly tied to something this week.

COMBINED SCORE = curiosity × clarity × timeliness (rank highest first).

RULES:

TITLES (5-7 words max — every word earns its place):

PRIORITY 1 — TRANSFORMATION FRAME (strongest possible title)
Make the title feel like a transformation, not a description.
The viewer's brain asks "how?" the moment they see a before → after.
  ❌ "How Brentford Find Strikers"          (describes an action)
  ✅ "How Brentford Turn £10M Into £70M"    (shows the transformation)
  ❌ "Brentford's Striker Recruitment"      (describes a topic)
  ✅ "The Club That Turns Rejects Into Records"  (transformation + drama)
  ❌ "How Liverpool Develop Players"        (description)
  ✅ "How Liverpool Turn Academy Kids Into £100M Sales"  (transformation)
Pattern: [small/cheap/unexpected input] → [large/surprising output]
Use this frame for at least 3 of the {n_ideas} ideas.

PRIORITY 2 — ACTIVE VERB (when transformation doesn't fit)
- "Keep Finding", "Rarely Miss", "Turned Into", "Keep Getting Right"
- Avoid absolute claims ("never", "always", "0% failure") — they invite pushback.
  ❌ "Why Brentford Never Miss on Strikers"  (over-claim, one counter-example kills it)
  ✅ "Why Brentford Rarely Miss on Strikers"  (same punch, stays credible)

HOOKS (counterintuitive_element):
- Must contain CONTRAST or STAKES. Vague accurate statements are not hooks.
  ❌ "Brentford consistently acquire top attacking talent at good value"  (accurate, not gripping)
  ✅ "Brentford buy strikers for £10M and sell them for 10x — every time"  (contrast + stakes)
- Formula: [specific buy price] → [specific sell price or multiplier] / [small club] → [big outcome]

OPENING LINES:
- Must start with a SPECIFIC CONCRETE FACT. Never start with "Everyone talks about..." or
  "In modern football..." or any generic scene-setting phrase.
  ❌ "Everyone talks about big money signings in modern football..."
  ✅ "Brentford just sold another striker for £50 million. They paid £9.5 million for him."
- Rule: the first sentence must be something the viewer could verify. Date, fee, name, score.

CREDIBILITY:
- Do not claim 100%% rates, "never fails", "always works" — viewers will find the counter-example
- Use "rarely", "almost always", "keep", "consistently" — same punch, doesn't invite refutation

OTHER:
- At least 2 ideas must use Formula 1 (transformation title)
- At least 2 ideas must use Formula 7 (pattern observation — temporal, geographic, positional, structural)
- At least 1 idea must use Formula 8 (geopolitical sports) IF the topic has any connection to
  state ownership, money, power, or international football (most topics do)
- For Formula 7 ideas: no number required in thumbnail — the pattern statement IS the hook
- STRICTLY AVOID STAT CARDS: do not generate ideas where the entire concept is one number.
  "Player X: £YM fee" is a tweet, not a documentary. If you catch yourself writing one, replace it
  with a WHY or HOW frame that has a story beneath the number.
- Every number (where used) must be specific (£9.5M not "low fee", £50M not "big profit")
- For the opening_line: use the two-sentence structure — sentence 1 = specific fact,
  sentence 2 = the gap. "Brentford sold him for £50M. They paid £9.5M." Short. Visual. Done.
- core_question: one sentence the viewer genuinely needs answered
- visual_anchors: exactly 2-4 items, each as [description, EngineTemplateName]
- narrative_shape: exactly one of: MYSTERY / SYSTEM / RISE-FALL / PARADOX
- tension: what is surprising, unfair, or hard to believe — the reason this video has a right to exist

Return ONLY valid JSON:
{{
  "topic": "{topic}",
  "ideas": [
    {{
      "rank": 1,
      "title": "Short punchy title here",
      "thumbnail_hook": "SPECIFIC HOOK",
      "hook_formula": "HOW IT REALLY WORKS",
      "core_question": "One sentence — why should someone care?",
      "narrative_shape": "SYSTEM",
      "tension": "What is surprising, unfair, or hard to believe — e.g. 'A small club outperforms billion-pound teams'",
      "opening_line": "First sentence must be a specific verifiable fact — name, fee, date, score",
      "counterintuitive_element": "Contrast + stakes: buy price → sell price, or small club → big outcome",
      "visual_anchors": [
        ["description of visual", "EngineTemplateName"],
        ["description of visual", "EngineTemplateName"]
      ],
      "curiosity_score": 9,
      "clarity_score": 8,
      "timeliness": 6,
      "why_it_works": "One sentence on the psychological hook"
    }}
  ]
}}

Generate exactly {n_ideas} ideas. Rank by curiosity × clarity × timeliness (highest first).
"""

    raw = ask_gemini(prompt)
    if not raw:
        raw = ask_llm(prompt)

    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
    except Exception as e:
        print(f"    [!] Curiosity Agent: JSON parse failed — {e}")
        return {"topic": topic, "ideas": [], "error": str(e)}

    ideas = data.get("ideas", [])
    for idea in ideas:
        cs  = float(idea.get("curiosity_score") or 0)
        cl  = float(idea.get("clarity_score")   or 0)
        tl  = float(idea.get("timeliness")       or 0)
        idea["combined_score"] = round(cs * cl * tl, 1)

    ideas.sort(key=lambda x: x["combined_score"], reverse=True)
    for i, idea in enumerate(ideas, 1):
        idea["rank"] = i

    data["ideas"] = ideas
    return data


def generate_proactive_ideas(current_date: str = "", context: str = "", n_ideas: int = 12) -> dict:
    """
    Generate curiosity-driven YouTube video ideas WITHOUT a user-supplied topic.

    The LLM reasons about football's story calendar — upcoming anniversaries,
    breakout players with no documentary yet, structural pattern observations,
    underdog/redemption arcs — and returns ideas ready to feed into the engine.

    Returns same format as generate_curiosity_ideas(), with "mode": "discovery".
    """
    import datetime
    if not current_date:
        current_date = datetime.date.today().strftime("%d %B %Y")

    context_block = f"\nCURRENT CONTEXT (reader-supplied news, events, or timing notes):\n{context}\n" if context else ""

    prompt = f"""You are a YouTube strategist specialising in curiosity-driven football content.
Today's date is {current_date}. Your job is to surface {n_ideas} video ideas that are ripe RIGHT NOW —
no topic has been given to you. You must choose the subjects yourself.

Think like an editor scanning the football world for stories that:
  1. Have a strong curiosity gap (viewer thinks they know, but they don't)
  2. Are perennially watchable OR tied to something happening soon/recently
  3. Map cleanly onto the engine's 5-act documentary format

SOURCES OF IDEAS TO CONSIDER:
  A. ANNIVERSARY / CALENDAR — are there famous events, transfers, or matches with round-number anniversaries soon?
     (e.g. 10 years since X, 25 years since Y — calculate from {current_date})
  B. BREAKOUT PLAYERS — players or managers who had exceptional recent seasons but have no proper documentary yet
  C. STRUCTURAL PATTERNS — underused Formula 7 angles: why does Country X stop producing Position Y?
     Why does Club X always get Z right? These are evergreen, high-curiosity, and require no news peg.
  D. UNDERDOG / REDEMPTION — clubs or players who defied the odds in a way that hasn't been fully explained
  E. MONEY & SYSTEM — clubs with a surprising transfer philosophy, wage structure, or development model
  F. FORGOTTEN GIANTS — top-tier players or eras that modern fans don't fully understand
  G. TIMING HOOKS — stories where the football calendar creates urgency (title race run-ins, relegation battles,
     cup finals, transfer window timing)
  H. GEOPOLITICAL SPORTS — football as a lens on money, power, and ideology. These stories have the highest
     ceiling because they attract viewers who don't even watch football. Mine:
     - State ownership (Saudi PIF, UAE, Qatar, US private equity, Chinese investment)
     - Financial doping and the rules that bend (PSG, Man City, FFP)
     - Soft power and nation-branding through sport
     - Corruption (FIFA, agents, referee fixing, transfer manipulation)
     - Migration economics (why players from X always go to Y)
     - World Cup hosting and what it actually costs/buys
{context_block}

THE 7 HOOK FORMULAS:
{_HOOK_FORMULAS}

NARRATIVE SHAPES:
{_NARRATIVE_SHAPES}

VISUAL ANCHORS (map to engine templates):
{_VISUAL_ANCHOR_TEMPLATES}

SCORING — be honest, not generous. CRITICAL: do NOT over-score stat cards.

A STAT CARD = an idea whose entire concept is one number. "Player X: £YM fee" is a tweet, not a
documentary. Score these MAX 5 on curiosity regardless of how punchy the number is. There is no
story payoff beyond the title — viewer learns nothing they didn't already read.

A strong idea has a story ARC: a system to reveal, a mystery to solve, a transformation to trace.
"Why Brazil Stopped Producing Playmakers" — structural shift, the viewer learns WHY.
"Saudi Football: The £600B Gamble" — geopolitical story, the viewer learns what money actually buys.

  curiosity_score (1-10):
    10 = shatters mental model (geopolitical reveal, hidden system, forgotten truth)
    7-8 = clear payoff: the video reveals something the viewer genuinely couldn't guess
    4-5 = stat card — punchy number, no narrative depth beneath it
    1-3 = description, no gap

  clarity_score (1-10): instantly clear to a casual viewer — no jargon, no setup required
  timeliness (1-10): how relevant right now given the date ({current_date})?

COMBINED SCORE = curiosity × clarity × timeliness (rank highest first).

TITLE RULES (same as always):
  - 5-7 words max
  - Transformation frame preferred: [small/cheap input] → [large/surprising output]
  - Active verbs over descriptions
  - "Rarely" not "Never", "Keep" not "Always"

SPREAD: across the {n_ideas} ideas, aim for variety:
  - At least 3 evergreen structural/pattern ideas (Formula 7) — these age well
  - At least 2 geopolitical sports ideas (Formula 8) — highest non-football audience crossover
  - At least 2 anniversary/calendar ideas if plausible from {current_date}
  - At least 2 breakout player/manager ideas from the last 2 seasons
  - At least 1 money/system idea (club or league-level)
  - Remaining: best opportunities across other categories
  - STRICTLY NO STAT CARDS in the final list. If you draft one, replace it.

Return ONLY valid JSON:
{{
  "mode": "discovery",
  "generated_date": "{current_date}",
  "ideas": [
    {{
      "rank": 1,
      "suggested_subject": "e.g. Brentford FC, Leandro Trossard, etc.",
      "category": "one of: ANNIVERSARY / BREAKOUT / PATTERN / UNDERDOG / MONEY / FORGOTTEN / TIMING",
      "title": "Short punchy title here",
      "thumbnail_hook": "SPECIFIC HOOK",
      "hook_formula": "HOOK FORMULA NAME",
      "core_question": "One sentence — why should someone care?",
      "narrative_shape": "SYSTEM",
      "tension": "What is surprising, unfair, or hard to believe",
      "opening_line": "First sentence must be a specific verifiable fact — name, fee, date, score",
      "counterintuitive_element": "Contrast + stakes",
      "visual_anchors": [
        ["description of visual", "EngineTemplateName"],
        ["description of visual", "EngineTemplateName"]
      ],
      "curiosity_score": 9,
      "clarity_score": 8,
      "timeliness": 6,
      "why_it_works": "One sentence on the psychological hook",
      "why_now": "Why is this story ripe specifically around {current_date}?"
    }}
  ]
}}

Generate exactly {n_ideas} ideas. Rank by combined_score (curiosity × clarity × timeliness) highest first.
"""

    raw = ask_gemini(prompt)
    if not raw:
        raw = ask_llm(prompt)

    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
    except Exception as e:
        print(f"    [!] Curiosity Agent (discover): JSON parse failed — {e}")
        return {"mode": "discovery", "ideas": [], "error": str(e)}

    ideas = data.get("ideas", [])
    for idea in ideas:
        cs  = float(idea.get("curiosity_score") or 0)
        cl  = float(idea.get("clarity_score")   or 0)
        tl  = float(idea.get("timeliness")       or 0)
        idea["combined_score"] = round(cs * cl * tl, 1)

    ideas.sort(key=lambda x: x["combined_score"], reverse=True)
    for i, idea in enumerate(ideas, 1):
        idea["rank"] = i

    data["ideas"] = ideas
    return data


if __name__ == "__main__":
    import sys
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "The Premier League"
    print(f"\nGenerating curiosity ideas for: {topic}\n")
    result = generate_curiosity_ideas(topic)

    for idea in result.get("ideas", []):
        print(f"\n{'─'*60}")
        print(f"#{idea['rank']:02d} [{idea['combined_score']:.0f}]  {idea['title']}")
        print(f"    Thumbnail:  {idea['thumbnail_hook']}")
        print(f"    Formula:    {idea['hook_formula']}")
        print(f"    Scores:     curiosity={idea.get('curiosity_score')} × clarity={idea.get('clarity_score')} × timeliness={idea.get('timeliness')}")
        print(f"    Core Q:     {idea.get('core_question','')}")
        print(f"    Tension:    {idea.get('tension','')}")
        print(f"    Shape:      {idea.get('narrative_shape','')}")
        print(f"    Anchors:    {idea.get('visual_anchors','')}")
        print(f"    Opening:    {idea['opening_line']}")
        print(f"    Why works:  {idea['why_it_works']}")
