# Design Rules — Frequency Visual Grammar

Machine-readable contract for all Remotion templates (hand-authored and generated).
Last updated: 2026-04-21

---

## 1. Scene Identity

Every scene MUST render a `ContextChip` identifying its type.

- Position: `top: 48, left: 100` (absolute, inside AbsoluteFill)
- Opacity: tied to the scene's header/title entry spring
- Light backgrounds: omit `color` prop (defaults to `COLORS.muted` = #999)
- Dark backgrounds: `color="rgba(255,255,255,0.35)"`

Standard labels by composition type:

| Composition | Label |
|-------------|-------|
| HeroBigStat | "Stats" |
| CareerTimeline | "Career" |
| HeroStatBars | "Stats" |
| AttackingRadar | "Radar" |
| HeroTactical | "Tactics" |
| HeroLeagueGraph | "League" |
| HeroFormRun | "Form" |
| HeroMatchTimeline | "Timeline" |
| HeroShotMap | "Shot Map" |
| DisciplinaryRecord | "Discipline" |
| PlayerTrio | "The Debate" |
| TeamLineup | "Lineup" |
| TopScorersTable | "Scorers" |
| PlayerStats | "Player" |
| HeroTransferRecord | "Transfer" |
| HeroQuote | "Quote" |
| HeroIntro | "Documentary" |
| HeroSeasonTimeline | "Season" |

---

## 2. Section Dividers

Use `RuleLine` from `shared.tsx` for horizontal rules between content sections.
Never use a plain `<div style={{ height: 1, background: ... }}>` border directly.

```tsx
<RuleLine color={COLORS.primary} opacity={0.10} progress={dividerProgress} />
// With label:
<RuleLine color={teamColor} opacity={0.20} label="Attack" progress={dividerProgress} />
```

---

## 3. Club Badges

Always use `BadgeTreatment` from `shared.tsx`. Never use raw `<SmartImg>` directly for badge display.

```tsx
<BadgeTreatment src={`badges/${badgeSlug}`} size={56} glowColor={teamColor} />
```

---

## 4. Image Frames (dwell > 2s)

Any image or video frame that stays on screen for more than 2 seconds (60 frames at 30fps)
MUST have a `FrameGlow` treatment. Position the FrameGlow inside a `position: "relative"` wrapper.

```tsx
<div style={{ position: "relative" }}>
  <SmartImg src={src} style={{ width: CLIP_W, height: CLIP_H }} />
  <FrameGlow w={CLIP_W} h={CLIP_H} delay={24} />
</div>
```

---

## 5. Animation (skipIntro)

Every entry spring MUST be conditioned on `skipIntro`:

```tsx
// CORRECT
const titleProg = skipIntro ? 1 : spring({ frame, fps, config: { damping: 24, stiffness: 55 } });

// WRONG — always animates from zero, breaks evolve/worldPan continuations
const titleProg = spring({ frame, fps, config: { damping: 24, stiffness: 55 } });
```

Schema requirement:
```ts
skipIntro: z.boolean().optional().default(false),
```

---

## 6. World State (camera continuity)

Every template schema MUST include `worldState`:

```ts
worldState: WorldStateSchema.optional(),
```

Never apply cameraX/cameraY transforms inside the component.
`VideoSequence.tsx` wraps every scene in `<WorldStateRoot>` — that is the single authority.

---

## 7. Z-index Sandwich

All templates follow this stacking order:

| Layer | z-index | Element |
|-------|---------|---------|
| Background | 0 | `<PaperBackground />` or `<DarkBackground />` |
| Side image | 1 | Player/club photography |
| Grain | 2 | `<Grain />` |
| Content | 10 | All text, charts, overlays |
| ContextChip | 10 | Same layer as content |

---

## 8. Typography Scale

| Role | Font | Size | Weight |
|------|------|------|--------|
| Display title | Playfair Display | 64–96px | 900 |
| Section heading | Playfair Display | 36–56px | 900 |
| Body / label | Inter | 14–22px | 500–700 |
| Overline / chip | Inter | 10–13px | 700, tracking 3px |
| Stat number | Playfair Display or Inter | 80–140px | 900 |

---

## 9. Background Surface

- Light documentary: `<PaperBackground color="#f0ece4" />`
- Dark cinematic: `<DarkBackground color="#111111" />`
- Custom accent: pass explicit `bgColor` prop, keep it consistent with `canonical_bgColor` from the world state

Always follow with `<Grain />` on the same level.

---

## 10. Imports (generated templates)

Import only from:
- `"remotion"`
- `"react"`
- `"zod"`
- `"./shared"` — for all design tokens, motif components, schemas

Required named imports from `"./shared"`:
```ts
import {
  fontFamily, serifFontFamily,
  COLORS, SPRINGS,
  PaperBackground, DarkBackground, Grain, SmartImg,
  WorldStateSchema,
  RuleLine, ContextChip, FrameGlow, BadgeTreatment,
} from "./shared";
```
