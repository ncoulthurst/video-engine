# VISUAL GRAMMAR

## Transition Tags (assembly instructions — not rendered as graphics)

Transitions are placed at act breaks to signal tonal shifts. They are applied at assembly time in VideoSequence.tsx.
Place on its own line in the script: `[TRANSITION: type]`

| Type | Effect | Use for |
|------|--------|---------|
| `letterbox` | Cinematic black-bar crush (~1s) | Chapter breaks, act transitions |
| `push` | Horizontal slide with motion blur (~0.7s) | Forward momentum, rise sections |
| `grain` | Noise burst overexposure (~0.4s) | Chaos, dark moments, controversy |
| `paper` | Dissolve through paper texture (~0.9s) | Legacy, reflection, fading eras |
| `dataLine` | Accent-coloured wipe line (~0.9s) | Stats moments, data transitions |
| `flash` | Film-burn cut (~0.3s) | High-energy, goal moments |
| `evolve` | Same-world evolution (~1.7s) | **Default between consecutive HERO scenes in the same act** — outgoing scene holds then drops, incoming emerges from same space. REQUIRES both scenes to share the same `bgColor`. |
| `worldPan` | Horizontal pan with parallax (~0.7s) | World-shift between eras or locations — same mechanical feel as push but with deeper depth. |

### Transition direction

`push` and `worldPan` support a `transitionDirection` field: `"left"` (backward/retrospective) or `"right"` (forward in time, default). `"forward"` is treated as `"right"`.

Direction defaults per act (set automatically by server.py):
| Act | Direction |
|-----|-----------|
| 0 (Cold Open) | forward |
| 1 | right |
| 2 | right |
| 3 | forward |
| 4 | left |
| 5 | forward |

`emotional_beat` role scenes always invert their act's direction.

---

## Scene Metadata Fields

These fields are assigned by server.py Layer 2 post-processing — **do not set them in the LLM prompt**.

| Field | Values | Set by | Purpose |
|-------|--------|--------|---------|
| `role` | `anchor` / `evidence` / `emotional_beat` / `transition_support` / `context` | server.py | Narrative function of the scene (see retention_patterns.md) |
| `world_id` | string — era label (e.g. `"1980s"`) | server.py | Groups scenes in the same visual world; consecutive same-world graphics get `evolve` transition |
| `flow_hint` | `evolve` / `cut` / `push` | server.py | Transition hint passed to VideoSequence — evolve for same-world graphics, cut at act boundaries, push otherwise |
| `flow_direction` | `left` / `right` / `forward` | server.py | Directional hint for push/worldPan transitions |
| `hero_visual` | `true` / absent | server.py | Marks scene for bespoke motion_agent render instead of standard template |
| `visual_rationale` | string | storyboard_agent | LLM-provided reason this visual choice serves the story (for human review) |

## Clip Tags (require footage — listed in production sheet for manual sourcing)

### CLIP SINGLE — the most used tag in the script
`[CLIP SINGLE: description, Xs, label]`
A large cinematic clip frame. Use for any specific moment that needs footage — emotional beats, iconic goals, reactions, celebrations, arrivals, controversies.

- **description** — exactly what the clip should show (clear enough to find on YouTube)
- **Xs** — duration in seconds (e.g. `7s`, `8s`, `10s`)
- **label** — text shown below the frame (date, context, or caption)

Use LIBERALLY. Every act should have at least one. This is the most human element of the video — the thing that makes it feel real, not like a stats explainer.

Examples:
```
[CLIP SINGLE: Suárez breaks down crying on the Selhurst Park pitch, Liverpool players consoling him, 8s, 11 May 2014 — Selhurst Park]
[CLIP SINGLE: Suárez biting Chiellini's shoulder at the 2014 World Cup, referee not seeing it, 6s, Brazil 2014]
[CLIP SINGLE: Suárez scoring his 31st league goal against Norwich, wheeling away in celebration, 7s, Season's defining moment]
```

### CLIP COMPARE — two footage frames side by side
`[CLIP COMPARE: left description | right description, Xs, left label | right label]`
Two clip frames side by side with a vertical divider. Use for before/after, player A vs B in two systems, or two contrasting moments.

Examples:
```
[CLIP COMPARE: Suárez pressing relentlessly at Anfield | Suárez in tight spaces at Camp Nou, 9s, Liverpool 2013/14 | Barcelona 2014/15]
[CLIP COMPARE: Young Suárez celebrating wildly at Ajax | Veteran Suárez in Nacional colours, 8s, Ajax 2010 | Nacional 2022]
```

---

## Automated Infographic Tags (auto-rendered by the graphics engine)

All tags below are rendered automatically as 1920×1080 MP4 clips using Frequency visual style.

### League Table
`[STANDINGS TABLE: Premier League YYYY/YY - Top 6 Final Standings]`
Renders a cinematic animated league table. Always include the 4-digit start year and 2-digit end year.
Example: `[STANDINGS TABLE: Premier League 2013/14 - Top 6 Final Standings]`

### Top Scorers
`[TOP SCORERS: Premier League YYYY/YY]`
Renders the top 5 goal scorers with bar chart and goal count-up animation.
Example: `[TOP SCORERS: Premier League 2013/14]`

### Top Assists
`[TOP ASSISTS: Premier League YYYY/YY]`
Same layout as Top Scorers but for assists.
Example: `[TOP ASSISTS: Premier League 2013/14]`

### Player Season Stats
`[PLAYER STATS: Player Name YYYY/YY]`
Feature card showing 6 key stats for a single player's season.
Example: `[PLAYER STATS: Luis Suárez 2013/14]`

### Match Result
`[MATCH RESULT: Home Team N-N Away Team, DD Mon YYYY]`
Score card with badge panels, count-up score, and goal scorer list.
Example: `[MATCH RESULT: Liverpool 5-1 Arsenal, 09 Feb 2014]`

### Transfer Announcement
`[TRANSFER: Player Name from Club A to Club B, YYYY, £Xm]`
Cinematic FROM → TO club panel with fee pill.
Example: `[TRANSFER: Luis Suárez from Liverpool to Barcelona, 2014, £75m]`

### Trophy / Honour
~~`[TROPHY: ...]`~~ — **DEPRECATED. Do not use.** Use `[HERO BIG STAT: ...]` for title wins or `[TRANSFER: ...]` for the season a player joined a trophy-winning club.

### Career Timeline — PRIMARY club transition graphic
`[CAREER TIMELINE: Player Name]` or `[CAREER TIMELINE: Player Name - Focus: Club Name]`
Horizontal timeline of all career clubs. Use the `- Focus` variant whenever narrating a club arrival or highlighting a specific era. The focused club is highlighted at 1.08× scale with full opacity; others fade to 45%.

**Use multiple times** — once per major club transition:
- First professional club: `[CAREER TIMELINE: Luis Suárez - Focus: Nacional]`
- Ajax arrival: `[CAREER TIMELINE: Luis Suárez - Focus: Ajax]`
- Liverpool arrival: `[CAREER TIMELINE: Luis Suárez - Focus: Liverpool]`
- Barcelona arrival: `[CAREER TIMELINE: Luis Suárez - Focus: Barcelona]`

This is the primary graphic for club transitions. **Prefer this over a bare `[TRANSFER: ...]` card** — the timeline shows the full journey, not just one move.

### Player Trio (Peer Comparison)
`[PLAYER TRIO: comparison title, Player1 vs Player2 vs Player3]`
Three-column editorial layout — each player's image, club, and key stat. Use once per documentary in ACT 3 to show where the subject ranks among their peers.

- **comparison title** — short punchy label (e.g. "the debate", "the contenders")
- **Players** — subject + two elite contemporaries from the same era
- Requires player images in `/remotiontest/public/` (name: `playername.png`)

Examples:
```
[PLAYER TRIO: the debate, Luis Suárez vs Lionel Messi vs Cristiano Ronaldo]
[PLAYER TRIO: Premier League's finest, Suárez vs Rooney vs van Persie]
```

### Season Comparison
`[SEASON COMPARISON: Player Name YYYY/YY vs YYYY/YY]`
Side-by-side bar chart comparison of two seasons.
Example: `[SEASON COMPARISON: Luis Suárez 2012/13 vs 2013/14]`

### Team Lineup — use for EVERY named match
`[TEAM LINEUP: Team Name N-N-N vs Opposition, DD Mon YYYY]`
Tactical formation on a pitch diagram. **Every match you name in the script should have a lineup graphic.** This is heavily underused and is one of the most engaging templates.

Examples:
```
[TEAM LINEUP: Liverpool 4-3-3 vs Arsenal, 09 Feb 2014]
[TEAM LINEUP: Uruguay 4-4-2 vs Ghana, 02 Jul 2010]
[TEAM LINEUP: Barcelona 4-3-3 vs Real Madrid, 22 Mar 2015]
```

### Disciplinary Record
`[DISCIPLINARY RECORD: Player Name]`
Animated timeline of a player's bans and incidents, colour-coded by severity.
Example: `[DISCIPLINARY RECORD: Luis Suárez]`

### Quote Card
`[QUOTE CARD: "Quote text" — Attribution, Context]`
Full-screen cinematic quote with large text, accent line, and context caption.
Example: `[QUOTE CARD: "I apologise to Giorgio Chiellini" — Luis Suárez, FIFA World Cup 2014]`

## Hero Style Templates (cinematic, full-bleed)

These templates follow a modern cinematic documentary style, using Playfair Display serifs, paper/dark backgrounds, and film grain.

### hero Stat Bars
`[HERO STAT BARS: Title, Subtitle, Team A, Team B]`
Animated head-to-head stat comparison bars.
Example: `[HERO STAT BARS: head to head, Liverpool vs Arsenal, Liverpool, Arsenal]`

### hero Form Run — NARRATIVE ARC TOOL
`[HERO FORM RUN: Team Name, Label]`
Animated coloured squares showing a run of results (W/D/L). This is your primary tool for showing momentum — use it for title races, relegation run-ins, or any stretch of form central to the story. Use once per team if narrating a two-horse race.

Examples:
```
[HERO FORM RUN: Liverpool, the 2013/14 title run-in]
[HERO FORM RUN: Manchester City, final 8 games 2011/12]
[HERO FORM RUN: Ajax, Eredivisie run to the title 2010/11]
```

For a title race, always show BOTH teams:
```
[HERO FORM RUN: Liverpool, the 2013/14 title run-in]
[HERO FORM RUN: Manchester City, final 8 games 2013/14]
```
→ MANDATORY for title races: emit one tag per team involved

### hero Tactical Map
`[HERO TACTICAL: Concept | Team | Formation | Description]`
AI-generated pitch diagram. The engine uses an LLM to reason about the tactical concept and automatically place all 11 players in the correct formation with movement/pressing arrows drawn on top.

- **Concept** — the tactical idea (e.g. `gegenpressing`, `high press`, `back three build-up`, `low block`)
- **Team** — club name for correct brand colour (e.g. `Liverpool`, `Manchester City`)
- **Formation** — tactical shape (e.g. `4-3-3`, `4-2-3-1`, `3-4-3`)
- **Description** — short subtitle shown on the graphic

Arrows are automatically generated:
- **Solid** = pressing/attacking runs (bright, prominent)
- **Dashed** = cover/support movements

Examples:
```
[HERO TACTICAL: gegenpressing | Liverpool | 4-3-3 | Klopp's coordinated high press]
[HERO TACTICAL: low block | Atletico Madrid | 4-4-2 | Simeone's defensive shape]
[HERO TACTICAL: tiki-taka | Barcelona | 4-3-3 | positional play and triangles]
[HERO TACTICAL: counter-attack | Leicester City | 4-4-2 | Ranieri's transition game]
```

Legacy format (also supported): `[HERO TACTICAL: Title, Description]`

### hero Big Stat
`[HERO BIG STAT: Stat, Unit, Label, Context]`
Massive oversized stat with impact animation.
Example: `[HERO BIG STAT: 31, goals, in a single Premier League season, Luis Suárez · 2013/14]`

### hero League Graph
`[HERO LEAGUE GRAPH: Team Name, Season]`
Animated SVG line chart showing league position over time.
Example: `[HERO LEAGUE GRAPH: Liverpool, 2013/14]`

### hero Transfer Record
`[HERO TRANSFER RECORD: Title, Subtitle]`
Horizontal bars showing a timeline of transfer fees.
Example: `[HERO TRANSFER RECORD: world record fees, the escalation of the market]`

### hero Intro
`[HERO INTRO: Video Title]`
Typewriter reveal of the channel name ("Frequency" — always hardcoded) followed by the video title as a subtitle. Use this at the start of every video.
Example: `[HERO INTRO: the greatest league season ever told]`

### hero Quote
`[HERO QUOTE: "Quote" — Attribution, Context]`
Cinematic quote with player image overlay.
Example: `[HERO QUOTE: "I am not a diver." — Luis Suárez, Jan 2013]`

### hero Chapter ~~(deprecated — use TRANSITION tags instead)~~
~~`[HERO CHAPTER: Word]`~~ — **Use `[TRANSITION: letterbox]` instead for act breaks.**
Act transitions are now handled by VideoSequence transition effects, not standalone cards.

### ~~hero Concept — DEPRECATED~~
~~`[HERO CONCEPT: Word Left, Word Right]`~~
**Do not use.** This tag was ambiguously named and was being misused for single abstract
concepts (e.g. "globalization of football tactics") which the template cannot render —
the underlying Remotion composition (`HeroClipCompare`) is a two-footage-frame side-by-side
comparison, NOT a text card. For two-item footage comparisons use `[CLIP COMPARE: ...]` above.
For a single abstract concept with a word, use `[HERO CHAPTER WORD: word]` or narration alone.

### hero Scatter Plot
`[HERO SCATTER: Axis X Label, Axis Y Label]`
Player data scatter plot with 4-quadrant labels.
Example: `[HERO SCATTER: speed, efficiency]`

### Player Radar (Attacking Stats)
`[PLAYER RADAR: Player Name, Club, Competition, Season]`
Cinematic radar chart showing a player's key attacking/creative metrics. Use once per documentary, ideally during the PEAK act when analysing the player at their best.
Example: `[PLAYER RADAR: Luis Suárez, Barcelona, La Liga, 2015/16]`

### hero Shot Map
`[HERO SHOT MAP: Player Name, Competition Season]`
xG-weighted shot location map on a half-pitch. Circles sized by xG value, coloured green (goal) / yellow (saved) / red (off target). Use for peak season shot analysis or a defining match.
Example: `[HERO SHOT MAP: Luis Suárez, Premier League 2013/14]`

### hero Match Timeline
`[HERO MATCH TIMELINE: Home Team N-N vs Away Team N-N, DD Mon YYYY]`
Minute-by-minute match events (goals, cards, subs) on a horizontal 0–90 timeline. Use for iconic individual matches.
Example: `[HERO MATCH TIMELINE: Liverpool 5-1 vs Arsenal 0-0, 09 Feb 2014]`
Short format also accepted: `[HERO MATCH TIMELINE: Liverpool vs Arsenal 5-1, 09 Feb 2014]`

### hero Awards List
`[HERO AWARDS LIST: Award Name, Entity Name]`
Year-by-year award history (Ballon d'Or, Golden Boot, PFA Player of the Year etc.) revealing row by row. Entity's wins highlighted in gold. Use to show the scale of a trophy haul.
Example: `[HERO AWARDS LIST: Ballon d'Or, Lionel Messi]`

### hero Comparison Radar
`[HERO COMPARISON RADAR: Player A vs Player B, Competition, Season]`
Two overlapping radar polygons for a head-to-head stat comparison. Use in ACT 3 PEAK for the definitive GOAT debate or direct peer comparison.
Example: `[HERO COMPARISON RADAR: Lionel Messi vs Cristiano Ronaldo, La Liga, 2011/12]`

### Tournament Bracket — KNOCKOUT RUN TO GLORY
`[TOURNAMENT BRACKET: Tournament Name, Focus: Team Name]`

A full cinematic knockout bracket (R16 → QF → SF → Final). The camera opens on the whole bracket, then zooms and pans through the focus team's four matches in sequence, ending with a victory hero shot on the Final pill. Every pill shows both teams' scores. Flags render as proper SVG.

Use this ONCE per documentary, at the narrative peak when that team's tournament is the story — the knockout path is more informative than a single MATCH RESULT card because it shows context, opponents, and scorelines across the whole run.

Supported tournaments (exact-match names, case insensitive):
- `FIFA World Cup 2002` — Brazil's fifth-star run
- `FIFA World Cup 2022` — Argentina + Messi
- `Euro 2024` — Spain's clean knockout run

Examples:
```
[TOURNAMENT BRACKET: FIFA World Cup 2002, Focus: Brazil]
[TOURNAMENT BRACKET: FIFA World Cup 2022, Focus: Argentina]
[TOURNAMENT BRACKET: Euro 2024, Focus: Spain]
```

**When to use:** the Anchor scene of the act covering that tournament run, instead of a sequence of separate MATCH RESULT cards. If the tournament isn't on the supported list above, use MATCH RESULT per knockout game instead.

### hero Season Timeline — COLD OPEN / MANAGER CONTEXT
`[HERO SEASON TIMELINE: Subject Name | img:filename.png | season:pos, season:pos, ... | headline:word?]`

Deep-red grainy background with a B&W portrait (left) and a scrolling horizontal timeline (right) showing a subject's finish position or achievement per season. The most recent entry is larger and brighter. Use this as the **cold open graphic** for a manager or player documentary — it immediately establishes who they are, their journey, and current stakes.

- **Subject Name** — manager or player name (displayed small, bottom-left)
- **img:filename.png** — portrait image in `/remotiontest/public/` (rendered B&W automatically)
- **seasons** — comma-separated `season:positionLabel` pairs (e.g. `19/20:8th, 20/21:8th, 21/22:5th`)
  - Add `(PL)` or `(CL)` after the label for trophy icons: `25/26:1st(PL,FA)`
- **headline** — optional italic serif word shown bottom-right (e.g. `redemption.`, `quadruple?`, `legacy.`)

Examples:
```
[HERO SEASON TIMELINE: Mikel Arteta | img:arteta.png | 19/20:8th, 20/21:8th, 21/22:5th, 22/23:2nd, 23/24:2nd, 24/25:2nd, 25/26:1st(PL) | headline:quadruple?]
[HERO SEASON TIMELINE: Pep Guardiola | img:guardiola.png | 16/17:3rd, 17/18:1st(PL), 18/19:1st(PL), 19/20:2nd, 20/21:1st(PL), 21/22:1st(PL) | headline:dynasty.]
[HERO SEASON TIMELINE: Jürgen Klopp | img:klopp.png | 15/16:8th, 16/17:4th, 17/18:4th, 18/19:2nd, 19/20:1st(PL,CL), 20/21:3rd | headline:legacy.]
```

**When to use:** Always use as the cold open when the documentary subject is a **manager**. For player documentaries, prefer `[CAREER TIMELINE: ...]` for club transitions, but use this tag when the story is about a player's *season-by-season achievement arc* (e.g. a striker's golden years, a goalkeeper's peak run).
