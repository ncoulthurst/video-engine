# Frequency Channel — Visual Design System
## Version 2.0

A unified design system for all automated infographic templates.
Every template is a full-bleed Remotion composition at 1920×1080, 30fps, ~9 seconds (270 frames).

---

## 1. Core Philosophy

Templates fill the entire frame — no cards floating on dark backgrounds, no outer chrome, no borders.
Content lives edge-to-edge with generous padding (~140px left/right). The composition IS the scene.

**Two visual modes:**
- **Light (primary):** Warm parchment — used for ~80% of compositions. Feels editorial, authoritative.
- **Dark (contrast):** Near-black — used for ~20% of compositions. Reserved for tactical diagrams,
  dramatic big-stat moments, or scatter/graph data. Creates breathing room between light scenes.

Never mix modes within a single composition. Alternate between them across a video for visual variety.

---

## 2. Design Tokens

### Typography

**Display titles** use **Playfair Display** (serif, @remotion/google-fonts/PlayfairDisplay).
Everything else uses **Inter** (sans-serif, @remotion/google-fonts/Inter).

| Role                | Font            | Size  | Weight | Letter-spacing | Colour       |
|---------------------|-----------------|-------|--------|----------------|--------------|
| Display title       | Playfair Display| 72px  | 900    | −3px           | #111 / #fff  |
| Overline / label    | Inter           | 13px  | 700    | +3px / UPPER   | #999 / rgba(255,255,255,0.5)|
| Subtitle / meta     | Inter           | 20px  | 500    | +0.5px         | #999 / rgba(255,255,255,0.4)|
| Row name (standard) | Inter           | 22px  | 600    | 0              | #111 / #fff  |
| Row name (champion) | Playfair Display| 30px  | 900    | −0.5px         | #111 / #fff  |
| Stat value (hero)   | Playfair Display| 34px  | 900    | −0.5px         | gold / #fff  |
| Stat value (normal) | Inter           | 24px  | 900    | −0.5px         | #111 / #fff  |
| Column header       | Inter           | 11px  | 700    | +2px / UPPER   | #555         |
| Column header (key) | Inter           | 11px  | 700    | +2px / UPPER   | #C9A84C      |
| Score (match)       | Playfair Display| 120px | 900    | −4px           | team colour  |
| Watermark           | Inter           | 22px  | 800    | −0.5px         | #111 @ 20% / #fff @ 20% |

**Fonts imported:** `loadFont()` from `@remotion/google-fonts/Inter` and `@remotion/google-fonts/PlayfairDisplay`
Both exported from `src/shared.tsx` as `fontFamily` and `serifFontFamily`.

---

### Colour Palettes

#### Light Theme (primary — ~80% of compositions)
| Token          | Value         | Usage                                    |
|----------------|---------------|------------------------------------------|
| `bgColor`      | #f0ece4       | Full-bleed background (flat or gradient) |
| `bgFrom`       | #f5f0e8       | Gradient start (alternative)             |
| `bgTo`         | #e9e2d8       | Gradient end (alternative)               |
| `primary`      | #111          | Main text, strong values                 |
| `secondary`    | #444          | Supporting text                          |
| `muted`        | #999          | Labels, column headers, meta             |
| `divider`      | rgba(0,0,0,0.08) | Row dividers, section lines           |
| `rowAlt`       | rgba(0,0,0,0.025) | Alternating row tint                 |
| `gold`         | #C9A84C       | #1 highlights, gold rule, trophy        |
| `gdPositive`   | #2d7a2d       | Positive goal difference                 |
| `gdNegative`   | #c0392b       | Negative goal difference                 |

#### Dark Theme (contrast — ~20% of compositions)
| Token          | Value         | Usage                                    |
|----------------|---------------|------------------------------------------|
| `bgColor`      | #1a1a1a       | Full-bleed background                    |
| `primary`      | #ffffff       | Main text                                |
| `secondary`    | rgba(255,255,255,0.6) | Supporting text                  |
| `muted`        | rgba(255,255,255,0.35) | Labels, meta                     |
| `divider`      | rgba(255,255,255,0.08) | Row dividers                     |
| `gold`         | #C9A84C       | Unchanged — same gold in both themes     |
| `accent`       | team colour   | Player dots, arrows, highlights          |

**When to use dark theme:**
- Tactical / pitch diagrams (`HeroTactical`)
- Data graphs and scatter plots (`HeroLeagueGraph`, `HeroScatterPlot`)
- Big dramatic stat moments when the previous scene was light
- Never back-to-back dark compositions

---

### Spacing
| Element              | Value            |
|----------------------|------------------|
| Content padding L/R  | 140px            |
| Content padding T/B  | centred (flex)   |
| Row height           | 82px             |
| Row divider          | 1px rgba(0,0,0,0.06) |
| Badge size (row)     | 36×36px          |
| Badge size (match)   | 110×110px        |
| Gold rule height     | 2px              |
| Gold bar (champion)  | 3px wide, left: −20px, inset top/bottom 16px |
| Watermark position   | bottom-right     |

---

## 3. Visual Overlays

Applied to every light-theme template. Dark templates use `Grain` only.

### `<PaperBackground color={bgColor} />`
Flat colour fill at `#f0ece4`. No gradient required — the grain provides all the texture needed.
Imported from `src/shared.tsx`.

### `<Grain />`
SVG `feTurbulence` fractalNoise overlay — dual layer:
- Static weave layer: seed=5, opacity 0.10
- Animated film grain layer: seed cycles `frame % 120`, opacity 0.14
Warm colour matrix applied (boosts warm channel, suppresses blue).
Applied to both light and dark themes.

### Vignette (optional)
`radial-gradient(circle at center, transparent 60%, rgba(0,0,0,0.05) 100%)`
Keep very subtle. Omit on dark themes — they have enough edge darkness naturally.

### Watermark — "Frequency"
- Always bottom-right
- 22px Inter 800
- Max 20% opacity
- Fades in after all data rows are visible
- Colour: `#111` on light, `#fff` on dark

---

## 4. Animation Principles

### Spring Physics Presets (defined in `SPRINGS` in `src/shared.tsx`)
| Name      | Damping | Stiffness | Character                    |
|-----------|---------|-----------|------------------------------|
| `header`  | 18      | 55        | Slow, smooth title slide-in  |
| `row`     | 13      | 140       | Snappy with slight overshoot |
| `cols`    | 20      | 80        | Medium, clean                |
| `feature` | 24      | 60        | Gentle float-in              |
| `brand`   | 24      | 50        | Very slow — last element     |
| `bounce`  | 10      | 180       | High-energy snap             |

For list templates not using presets, use `{ damping: 24, stiffness: 60 }` as the default row spring.

### Standard Cascade (list templates)
1. **Frame 0** — Title block fades in from −20px Y (header spring, damping 28, stiffness 55)
2. **Frame 10** — Gold rule animates width 0→100% (cols spring)
3. **Frame 20** — Column headers fade in
4. **Frame 20+** — Rows reveal with stagger (ROW_START=20, ROW_STAGGER=10)
   Each row: `opacity` 0→1 + `translateX` −36→0 via spring `{ damping: 24, stiffness: 60 }`
5. **After last row** — Watermark fades in

### Row Reveal Direction
Top-to-bottom for most lists (rank 1 first — lead with the hero).
Bottom-to-top only when building suspense to a reveal (e.g. countdown rankings).

### Row Anatomy
```
[ pos/trophy ][ badge 36×36 ][ name (flex:1) ][ stats... ][ key stat ]
     56px          60px                          varies       84px
```

---

## 5. Templates

---

### T1 — League Table (PremierLeagueTable)
**Remotion ID:** `PremierLeagueTable`
**Tag:** `[STANDINGS TABLE: Premier League 2013/14 - Top 6 Final Standings]`
**Theme:** Light

**Layout:** Full-bleed parchment. Serif title "premier league" (lowercase). Inter subtitle with season + "final standings". Gold rule. Column headers. Team rows.

**Champion row special treatments:**
- Gold vertical bar 3px, left: −20px (outside padding), with glow
- Trophy icon replaces position number
- Name in Playfair Display 30px (vs Inter 22px for others)
- Points in Playfair Display 34px gold (vs Inter 24px dark for others)

**Data structure:**
```json
{
  "season": "2013–14",
  "bgColor": "#f0ece4",
  "teams": [
    { "pos": 1, "name": "Manchester City", "color": "#6CABDD", "badgeSlug": "manchester-city.svg",
      "p": 38, "w": 27, "d": 5, "l": 6, "gd": 65, "pts": 86 }
  ]
}
```

---

### T2 — Top Scorers Table (TopScorersTable)
**Remotion ID:** `TopScorersTable`
**Tag:** `[TOP SCORERS: Premier League 2013/14]`
**Theme:** Light

**Layout:** Identical structure to T1. Overline shows competition in small caps. Serif title "top scorers". Same row animation.

**Top scorer special treatments:** Same as champion row — gold bar, serif name + serif goals value in gold.

**Data structure:**
```json
{
  "season": "2013–14",
  "competition": "Premier League",
  "statLabel": "Goals",
  "bgColor": "#f0ece4",
  "players": [
    { "pos": 1, "name": "Luis Suárez", "club": "Liverpool", "badgeSlug": "liverpool.svg",
      "clubColor": "#C8102E", "goals": 31, "assists": 12, "apps": 33 }
  ]
}
```

---

### T3 — Top Assists Table (TopAssistsTable)
**Remotion ID:** `TopAssistsTable`
**Tag:** `[TOP ASSISTS: Premier League 2013/14]`
**Theme:** Light

Identical to T2 but `statKey: "assists"`, `statLabel: "Assists"`. Uses same `TopScorersTable` component.

---

### T4 — Player Season Stats (PlayerStats)
**Remotion ID:** `PlayerStats`
**Tag:** `[PLAYER STATS: Luis Suárez 2013/14]`
**Theme:** Light

**Layout:** Two-panel full-bleed. Left (500px): badge (120×120), overline, player name (serif), club + season meta, accent bar. Right panel: 3×2 grid of stat tiles.

---

### T5 — Match Result (MatchResult)
**Remotion ID:** `MatchResult`
**Tag:** `[MATCH RESULT: Liverpool 5-1 Arsenal, 09 Feb 2014]`
**Theme:** Light

**Layout:** Full-bleed parchment. Subtle 4% team colour washes left/right halves. Centred layout: competition + date → badge | score | badge → scorer list.

**Score:** Playfair Display 120px/900. Winner's score in team colour; loser's score in `#111`. Score counts up from 0 during reveal.

**Scorer list:** Separated by a faint centred gold gradient rule. Home/away columns on either side of a hairline.

**Data structure:**
```json
{
  "homeTeam": "Liverpool", "awayTeam": "Arsenal",
  "homeBadgeSlug": "liverpool.svg", "awayBadgeSlug": "arsenal.svg",
  "homeColor": "#C8102E", "awayColor": "#EF0107",
  "homeScore": 5, "awayScore": 1,
  "date": "09 Feb 2014", "competition": "Premier League", "venue": "Anfield",
  "bgColor": "#f0ece4",
  "scorers": [
    { "name": "Suárez", "minute": "31", "team": "home" }
  ]
}
```

---

### T6 — Transfer Announcement (TransferAnnouncement)
**Remotion ID:** `TransferAnnouncement`
**Tag:** `[TRANSFER: Luis Suárez from Liverpool to Barcelona, 2014, £75m]`
**Theme:** Light

---

### T7 — Trophy Graphic (TrophyGraphic)
**Remotion ID:** `TrophyGraphic`
**Tag:** `[TROPHY: Premier League 2013/14 Manchester City]`
**Theme:** Light

---

### T8 — Career Timeline (CareerTimeline)
**Remotion ID:** `CareerTimeline`
**Tag:** `[CAREER TIMELINE: Luis Suárez]`
**Theme:** Light

---

### T9 — Season Comparison (SeasonComparison)
**Remotion ID:** `SeasonComparison`
**Tag:** `[SEASON COMPARISON: Luis Suárez 2012/13 vs 2013/14]`
**Theme:** Light

---

### T10 — Team Lineup (TeamLineup)
**Remotion ID:** `TeamLineup`
**Tag:** `[TEAM LINEUP: Liverpool 4-3-3 vs Arsenal, 09 Feb 2014]`
**Theme:** Light (parchment pitch — no green)

---

### HeroDualPanel — Shared World Dual Event Panel

**Remotion ID:** `HeroDualPanel`
**Tag:** `[HERO DUAL PANEL: Left Label | Right Label | leftTitle | rightTitle]`
**Theme:** Dark (both panels default to dark bg; shared bgColor shows as divider gap)
**Duration:** 270 frames

**Core concept:** The "same container" template. Shows two parallel events side-by-side on one shared canvas, divided by a hairline seam. Each panel has a context chip (`/ LABEL`) anchored to its top-left.

Use for: "Meanwhile..." | "By contrast..." | "Same day, different worlds"
e.g. player trains alone while club signs replacement; team wins title while relegated rivals drop.

**Props:**
- `leftLabel` / `rightLabel` — chip text (dates, event names, competition names)
- `leftChipColor` / `rightChipColor` — pill background: `#111` (formal), `#1a5c1a` (sport), `#8B0000` (scandal)
- `leftImage` / `rightImage` — full-bleed photo URLs (optional; panels go solid-dark without)
- `leftTitle` / `rightTitle` — optional headline over photo (serif 36px)
- `leftBody` / `rightBody` — optional caption under title (Inter 15px)
- `dividerColor` — seam line color (default `rgba(17,17,17,0.18)`)

**Chip color conventions (from reference video analysis):**
- `#111111` — formal meetings, transfers, legal/regulatory
- `#1a5c1a` — sport/action, outdoor, positive/active moments
- `#8B0000` — scandal, injury, controversy, relegation
- `#C9A84C` — trophies, records, peak moments

---

### worldPanTransition — The "Same Container" Transition

**Available in:** `ChapterTransition.tsx` as `worldPanTransition()` + `worldPanTiming()`
**Duration:** 42 frames (~1.4s)

At the peak of this transition, BOTH the outgoing and incoming scenes are visible simultaneously — outgoing on the left half, incoming on the right. A hairline divider appears at the seam. This communicates that all scenes exist on a single continuous horizontal canvas.

Use when: consecutive graphics in the same act, "before/after" sequences, any time scenes are spatially related.

Do NOT use: for act-break transitions (use letterbox), dramatic/dark moments (use grain), or when you want a clean restart (use flash).

---

### Hero Templates (I1–I11)

These share the same full-bleed philosophy. Most use Light theme with `bgColor: "#f0ece4"`.
Exceptions that use Dark theme by default:
- **HeroTactical** (`bgColor: "#1a1a1a"`) — dark pitch diagram
- **HeroLeagueGraph** (`bgColor: "#111111"`) — data graph
- **HeroScatterPlot** (`bgColor: "#111111"`) — scatter data

All Hero templates use serif titles where a display title exists.

**HeroTransferRecord** — the reference composition. All other templates should feel like siblings.
Key qualities to match: generous 140px padding, serif 72px title, Inter subtitle 20px muted, 2px gold rule, row-height 90px with `rgba(0,0,0,0.06)` dividers, Grain overlay.

---

## 6. Consistency Rules

1. **Full-bleed always.** No `DocumentaryFrame`, no cards floating on dark outer backgrounds, no outer chrome. Content fills 1920×1080 directly.
2. **Two fonts only.** Playfair Display for display titles and hero numbers. Inter for everything else. Never add a third font.
3. **Light is default. Dark is contrast.** Use `bgColor: "#f0ece4"` (light) for ~80% of compositions. Use `bgColor: "#1a1a1a"` (dark) for tactical, graph, and dramatic big-stat moments. Never two dark compositions back-to-back in a video.
4. **Gold is reserved for #1 / champions / trophies / key column headers.** `#C9A84C` appears for the best row, the gold rule, and trophy elements only.
5. **Team colours are accents, never full backgrounds.** Club colours appear in left gold bars, badge borders, bar fills, score numbers — never as full backgrounds.
6. **All primary reveals use spring physics.** No CSS transitions, no linear easing. Use the presets in `SPRINGS` or `{ damping: 24, stiffness: 60 }` as the base.
7. **Reveal order builds to the punchline.** Labels → supporting → data rows → hero stat. The most important element is always last.
8. **Watermark always last, always 20% opacity.** "Frequency" bottom-right. Never brighter, never earlier.
9. **Serif name + serif number = hero row.** Champion, top scorer, or featured subject: their name and key number both get Playfair Display to visually separate them from the rest.
10. **Copy stays minimal.** Names, numbers, short labels only. The narration carries context — the graphic carries impact.
11. **Hero images (sideImage) rules:** `zIndex: 1` behind grain. Opacity 0.75. Left edge faded via `WebkitMaskImage` gradient over ~350px. Foreground max-width reduced to ~520px to prevent overlap.
12. **Grain on everything.** Every template — light or dark — includes the `<Grain />` component from `src/shared.tsx`.
13. **Portrait blending: CSS mask, never a gradient overlay div.** When blending a cutout portrait into a background, use `WebkitMaskImage: "linear-gradient(to right, black 50%, transparent 92%)"` on the portrait container. **Never** place a sibling `<div>` with `background: linear-gradient(…${bgColor})` — it renders as a visible coloured rectangle box. Canonical implementation: `HeroPlayerRevealTrio.tsx`. Container has no background colour, `opacity: 0.88`, CSS mask on the container, bottom/top absolute vignette divs inside the container.

---

## 7. Adding New Templates

1. Create `src/NewTemplate.tsx` — import shared tokens from `./shared` (`fontFamily`, `serifFontFamily`, `Grain`, `PaperBackground`, `COLORS`, `SPRINGS`, `SmartImg`)
2. Define a Zod schema (`NewTemplatePropsSchema`) including `bgColor: z.string().default("#f0ece4")`
3. Export the component. Root element: `<AbsoluteFill>` with `<PaperBackground color={bgColor} />` and `<Grain />` as first children
4. Add a `<Composition>` entry in `src/Root.tsx`
5. Add a render function `render_new_template()` in `utils/remotion_renderer.py`
6. Add a tag regex and handler to `agents/graphics_agent.py`
7. Document the tag format in `templates/visual_grammar.md`
