# Cards & KPIs

## Principle

A KPI card answers one question: *how are we doing?* That question is unanswerable without context — a bare number is a number, not a KPI. Every card needs, in priority order, a **primary number**, a **reference** (target / prior / peer), a **delta** (absolute AND %), and a **label**. Font size must be measured at render, not declared — two multiplicative font properties compound in Power BI's `cardVisual`, so "14D × 8D" on paper can be 26 pt on screen.

## Why it works

Human cognition judges magnitude through comparison, not in isolation. A revenue number of €518 M means nothing by itself; €518 M versus a €483 M target means +7.3 %, on plan, good news. This is intrinsic to how the brain converts data into information: the reader needs an anchor to interpret the number. Few (*Information Dashboard Design*, ch. 6) calls this the single most-violated rule on real dashboards — bare numbers displayed prominently, with no reference — and the most-fixable.

Preattentive delta encoding (Ware, ch. 5; IBCS notation): sign, colour, and arrow glyphs are perceived in ~200 ms before any reading happens. A gap of "+35 M (+7.3 %)" rendered green with an up-arrow communicates "we beat the target" before the reader has processed the words. That is the whole reason to put a delta on the card. A card that shows only the primary number forces the reader to mentally compute the comparison — and they won't; they'll skip the card.

Working-memory bounds (Cowan, 2001: ~4 chunks) cap the useful KPI count per band. A row of 4-6 cards is readable; a row of 10+ cards is not (see `density.md`). Hierarchy within the band — one headline card larger than the others — gives the eye a landing point and frees up cognitive budget for the supporting cards.

Power BI's `cardVisual` has a mechanical quirk that the reasoning layer has to address explicitly: two font multipliers, `value.fontSize` and `layout.calloutSize`, compound at render time. A card declared as `value.fontSize = 18D` with `calloutSize = 14D` renders at approximately 26 pt on screen — enough to overflow a 130 × 440 card width, which produces the silent clipping failure mode that is hard to debug from the JSON alone. Agents that treat `fontSize` as the render size and skip the verification step reliably ship broken cards. This is documented as a project scar; the self-audit demands a rendered verification, not a declared check.

## How to apply

### Step 1 — Decide the card's question

Before placing a card, state the question it answers in the form *"How are we doing on X, compared to Y, as of Z?"* If you cannot complete that sentence, the card has no purpose. Examples:

- "How is NAV tracking versus last month's close?"
- "How much variance is there on strategic allocation versus target?"
- "How are returns this quarter compared to the prior quarter?"

A card without a comparison is a data point, not a KPI. If the user hasn't specified a reference, propose one (prior period is the default; budget if the model has one; rolling average for volatile metrics) — don't silently ship a bare number.

### Step 2 — Pick the visual type

Three native options for single-value displays:

- **`card`** — legacy; shows a value with optional label. Minimal visual affordance for context.
- **`cardVisual`** — modern; supports multiple referenceLabels (target, prior), callout style, inline delta. Preferred for KPIs that need context on the card itself.
- **`kpi`** — built-in Indicator + Goal + TrendLine data roles, built-in status colour. Preferred when a true target measure exists in the model.
- **`multiRowCard`** — multiple rows of label + value pairs. Use for compact parameter readouts (e.g. entity identity: name / legal form / country), not for KPIs.

When a target measure exists in the model and the comparison is clean (above/below target = good/bad), `kpi` is the strongest choice — it encodes status semantically without needing an extension measure. When the comparison is more nuanced (prior period with no fixed target, variance versus benchmark with colour rules), `cardVisual` plus extension measures gives you full control.

### Step 3 — Structure the card's content

Inside the card, elements have a fixed priority and a fixed size hierarchy:

1. **Primary number** — largest, boldest, highest contrast. Round aggressively ("518M" not "517,893,412" — precision belongs in detail tables).
2. **Label** — identifies the number. Smaller than the number; above OR below, never competing with it.
3. **Reference value** — the comparison anchor (target / prior / peer). Small, muted, labelled ("Target: 483M", "1YP: 480M") so the reader knows what they're being compared against.
4. **Delta** — the verdict. Both absolute and percentage. Signed. Coloured semantically (green/red). Paired with a non-colour redundant signal (arrow, icon) for accessibility.
5. **Trend sparkline** (optional) — adds 300-level detail at the 3-level glance. A small SVG sparkline or a KPI-visual TrendLine works; don't put a full line chart beside the card.

Omit anything below the card's purpose. A card answering "how much NAV?" doesn't need a 90-day sparkline if the question is about the current value; adding one dilutes the headline.

### Step 4 — Size the band

A KPI band lives in the top-left region of an executive page (see `page-structure.md`). Size rules:

- **4-6 cards per row**, equal widths, equal heights. One row is the default; two rows of 4 is acceptable when the metrics naturally split into two groups.
- **Headline hierarchy.** If one card is the most important (e.g. "NAV" on a portfolio page), give it more width or a larger primary number than the supporting cards. The eye lands there first.
- **Card dimensions.** ~300 × 150 px for standard cards on a 1280-wide page; ~440 × 130 px for wider multi-measure cards. Proportional scaling for 1920-wide canvases.
- **Same typography across the row.** All primary numbers on the row use the same font size. If one number needs to be bigger, make that card bigger — don't mix sizes within the row.

### Step 5 — Configure the cardVisual font properties

For `cardVisual` specifically, both `value.fontSize` and `layout.calloutSize` apply multiplicatively at render. The conservative formula, tested against the overflow failure mode:

- **4 cards × 440 px wide, 130 px tall:** `value.fontSize = 14D`, `layout.calloutSize = 8D` → ~16 pt render, always fits.
- **2 cards × 700 px wide, 180 px tall (larger hero cards):** `value.fontSize = 20D`, `layout.calloutSize = 10D` → ~22 pt render.
- **Start conservative.** It is much easier to scale up from 14/8 after screenshotting than to debug an overflowing card on production.

Horizontal alignment matters too: `horizontalAlignment = 'center'` is the default for cards with content that might grow (multi-measure, long labels). `'left'` alignment makes overflow worse because the text has nowhere to go — it just extends past the right edge.

### Step 6 — Verify the render, always

The above compounding is not visible from the JSON. The only reliable way to confirm a card renders correctly is:

1. Close Power BI Desktop.
2. Apply the `pbir set` / `pbir_add_visuals` changes on disk.
3. Reopen Power BI Desktop.
4. Wait for render.
5. Screenshot the page.
6. Inspect the card at pixel level — is the number clipped? Is the delta visible? Is the label readable at zoom = 100%?

Agents that skip this loop and declare success based on JSON-level correctness ship overflowing cards routinely. Rendered verification is not optional for cardVisual.

### Step 7 — Colour and accessibility on deltas

Delta colouring (green/red) is a preattentive encoding — the reader decodes it without reading. Rules:

- **Apply to the gap / delta, not the primary value.** The primary value is always shown; the colour signals the *judgment* about the value.
- **Pair with a redundant signal.** An arrow (↑ / ↓), a sign (+/-), or an icon. Around 8 % of men and 0.5 % of women have some form of colour-vision deficiency; without redundancy, they can't read the signal.
- **Use blue/orange as an accessible alternative** to green/red when the theme supports it. The signal is retained for CVD readers.
- **Gray / neutral for "no change" or "not applicable".** Don't use a muted green for "flat"; the reader will interpret muted green as "weakly positive."

## Worked examples

### Example 1 — R0101 `DIR PARTICIPATIONS - DETAIL` wide multi-measure cardVisual

**User intent.** Relationship manager opens a participation's detail page; needs identity and a handful of KPIs visible in the header before any other content.

**Decision.** Two cardVisuals span the full page width as the identity header:
- `f208eccac1e0f3ecd659` at 850 × 110 (participation name, legal form, country)
- `c432543ba2fab21d4d51` at 1030 × 110 (NAV, ownership %, key dates)

**Why this works.** The cards use a field parameter pattern — one visual shows multiple measures as separate rows within the card, each with its own label, value, and delta. That is why the cards are wide (to fit multiple measures side by side) and short (110 px tall — text-dominated, not chart-dominated).

**Configuration that fits.** `value.fontSize` around 12-14 D, `calloutSize` around 7-9 D so the per-measure values fit in the narrow vertical space. Horizontal alignment left (not center) is acceptable here because the card is wide and the content is short — no overflow risk. Labels above each value, consistent sizing across all the measures.

**What the agent should reproduce.** Given *"Build an identity header for a single entity with key facts and KPIs,"* the agent should use a wide cardVisual (or a multiRowCard if values are pure readouts without deltas), fit 4-8 measures in one row, conservative font sizes, and rendered verification at the end.

### Example 2 — the 4-card, 130 × 440 pattern (project scars)

**Setup.** Four cards in a top-of-page KPI band. Each card is 130 px tall and 440 px wide. Metrics: NAV, YTD return, variance vs benchmark, top contributor.

**Wrong configuration.** `value.fontSize = 18D`, `layout.calloutSize = 14D`. On paper that looks like 18 and 14 pt; in practice the multipliers compound to approximately 26 pt at render. A 4-character value like "+7.3%" fits; an 8-character value like "€1,245,678 M" overflows the 440 px width, clips silently (PBI doesn't warn), and the card reads as "€1,245..." with the critical digits lost. Left-aligned makes it worse; centre-aligned is also truncated but at both ends.

**Correct configuration.** `value.fontSize = 14D`, `layout.calloutSize = 8D` → ~16 pt at render. All expected values fit within 440 px. Verify by close-PBI → edit → reopen → screenshot before declaring done.

**What the agent should reproduce.** Given *"Add a top-of-page KPI band with four standard cards,"* the agent should start at 14D/8D and expand only if the screenshot shows visible empty space — never contract from 18D/14D and hope the content fits.

### Example 3 — small/dense multiRowCards on R0101 `DIR PARTICIPATIONS - DETAIL`

**User intent.** Show 3-5 small key-metrics readouts alongside the identity header cards, each tight and labelled.

**Decision.** Four multiRowCards (`0b3a027244a8ecb2b5ce` 60 × 35, `53c1be8b5bdea78bc1bf` 220 × 33, `de7bccc1feca95d8de9e` 137 × 35, `e12f776498835a6645c4` 221 × 35) arranged in the header strip.

**Why this works.** These aren't KPIs in the "how are we doing?" sense — they're parameter readouts (e.g. "Registration date: 2019-04-15"). No delta, no target, no judgment required. multiRowCard is the right visual: it's a label + value pair designed for exactly this pattern. Using a cardVisual here would waste space and imply a missing comparison.

**What the agent should learn.** Not every tile in the header is a KPI. Parameter readouts use multiRowCard; KPIs with deltas use cardVisual or kpi. Don't reach for cardVisual reflexively when the content doesn't need context.

### Example 4 — correctly broken: one giant card dominating the page

**User intent.** The headline answer of a particular page is one number — say, "total NAV across all portfolios, as of today." Everything else is context.

**Decision.** A single cardVisual at 300 × 1200 px in the top band, with the number rendered at ~60 pt, the reference ("1M ago: €1,218 M") beneath at ~14 pt, and a small sparkline to the right.

**Why this is correct.** The page's question has a single-number answer; the card *is* the page's primary content, not a supporting element. Sizing the card to match its importance (dominant, centered, readable from across the room) reinforces the hierarchy. The reference + sparkline supply context.

**What would be wrong.** Giving the same 300 × 1200 treatment to a non-dominant card (e.g. one of four equally-important KPIs) — that would break the hierarchy and confuse the reader about what matters most.

## Anti-patterns

### "Giant number, no context"

**What the agent is tempted to produce.** A card with the primary number rendered large and nothing else — no target, no delta, no comparison.

**Why it's wrong.** The reader can't tell if the number is good or bad. The card uses dashboard real estate for a data point that could have been a single cell in a detail table.

**What to do instead.** Add a reference + delta. If no target measure exists, ask the user what to compare against (prior period is the safest default). If the metric genuinely has no useful comparison, it probably isn't a KPI — move it to a detail table.

### "cardVisual overflow"

**What the agent is tempted to produce.** `value.fontSize = 18D`, `calloutSize = 14D`, horizontalAlignment left, 440 × 130 card. Based on the JSON, the font should be 18 pt.

**Why it's wrong.** `value.fontSize × calloutSize` compound at render (≈26 pt); the 440 px width can't hold "€1,245,678 M"; PBI silently truncates to "€1,245..."; the user sees a clipped card.

**What to do instead.** Start at `value.fontSize = 14D`, `calloutSize = 8D`, `horizontalAlignment = 'center'`. Close PBI → edit → reopen → screenshot → verify nothing is clipped. Only scale up if the card has visible empty space.

### "12 cards in a row"

**What the agent is tempted to produce.** A KPI band with 12 small cards because the brief listed 12 metrics.

**Why it's wrong.** 12 equally-weighted numbers = zero numbers. No hierarchy, nothing for the eye to land on, exceeds working memory.

**What to do instead.** Cut to 4-6 decision-grade KPIs (apply the "20 % change test" — if this metric moved 20 %, should someone act?). Move the rest to a detail matrix or a second page.

### "Delta without sign"

**What the agent is tempted to produce.** A delta shown as "1.2% (down)" or "1.2 % worse".

**Why it's wrong.** The sign is preattentive; the word is not. "-1.2 %" communicates in 200 ms; "1.2 % (down)" requires reading. Faster to produce, faster to misread.

**What to do instead.** Signed delta. "+1.2 %" / "-1.2 %". Coloured semantically. Arrow icon for redundancy.

### "Left-aligned big number in narrow card"

**What the agent is tempted to produce.** `horizontalAlignment = 'left'` on a 130 × 440 cardVisual with a large primary number.

**Why it's wrong.** Left alignment means overflow extends past the right edge — PBI truncates silently. Centre alignment truncates from both sides but usually retains the middle digits (which are what the reader is scanning).

**What to do instead.** `horizontalAlignment = 'center'` by default. Reserve `'left'` for cards wide enough that overflow isn't plausible.

### "Decorative icons per card"

**What the agent is tempted to produce.** Each card gets a decorative icon (a coin, a chart, a building) on the left.

**Why it's wrong.** The icon competes with the primary number for attention; it's not semantic. It adds cognitive work (the reader looks at the icon, decides it doesn't mean anything, then looks at the number).

**What to do instead.** Drop decorative icons. Use icons only when they carry meaning (up-arrow / down-arrow for delta direction; alert / checkmark for status).

### "Matching green for positive, muted red for negative"

**What the agent is tempted to produce.** A subtly-coloured green for positive deltas, subtly-coloured red for negative — both at low saturation to "look elegant."

**Why it's wrong.** Muted colours fail preattentive detection. The reader doesn't spot the signal at a glance — they have to read each delta. Also: low contrast may fail WCAG AA.

**What to do instead.** Saturated semantic colours (not neon, not muted) paired with signs and arrows. If the theme uses blue/orange for CVD friendliness, those colours should also be saturated enough to clear WCAG.

## Self-audit checklist

- [ ] Does each KPI card have a primary number, a reference, a delta (absolute AND %), and a label?
- [ ] Is the delta signed (+/-) and coloured semantically?
- [ ] Is there a non-colour redundant signal on the delta (arrow, icon)?
- [ ] Are there ≤ 6 cards per row, with visible hierarchy between the primary KPI and supporting ones?
- [ ] Do all cards in a row share the same width, height, and font sizes?
- [ ] For `cardVisual`: has the rendered font been verified via screenshot, not just the declared `fontSize`?
- [ ] For `cardVisual`: does `value.fontSize × calloutSize` produce a size that fits within the card's width for the widest expected value?
- [ ] Is `horizontalAlignment` `'center'` unless the card is wide enough that left-alignment can't overflow?
- [ ] Do numbers use display units consistent with the data magnitude ("518M" not "517,893,412")?
- [ ] Are decorative icons absent (only semantic icons allowed)?
- [ ] Can you point at any card and answer the user's question *"how are we doing on this?"* in 3 seconds, without reading detail text? If not, the card is missing something.

## 2026 platform notes

- **`cardVisual` is the current standard (2024+).** The old `card` visual is in maintenance mode. New KPI card work should use `cardVisual` unless the model already has a `kpi` visual with a matching `Indicator`/`Goal`/`TrendLine` binding. Do not migrate existing `card` visuals unless the page is being redesigned anyway.
- **Font-compounding bug is a known Power BI limitation.** As of May 2026, there is no fix from Microsoft for the `value.fontSize × calloutSize` compound rendering. The workaround documented in this file (conservative 14D/8D starting point + screenshot verification) remains the correct mitigation.
- **Visual calculations for card-like display (May 2026).** Visual calculations (`visualCalc`) can produce running totals or period comparisons without a model measure. They are appropriate for ad-hoc analysis pages but not for KPI bands that should be consistent across reports — model measures remain the right home for reusable KPI logic.
- **IBCS hollow-bar targets.** If the organisation uses IBCS notation: the `cardVisual` reference label plays the role of the "hollow bar" target — show it as a secondary label, not a chart element. For charts with explicit target bars (bullet chart pattern), see `reports:svg-visuals` for DAX bullet-chart implementations.

## See also

- [page-structure.md](page-structure.md) — where the KPI band sits and how it relates to the rest of the page.
- [color.md](color.md) — semantic delta colouring, accessibility palettes, redundant signals.
- [density.md](density.md) — why the KPI count ceiling is 4-6 per row.
- [typography-spacing.md](typography-spacing.md) — font size hierarchy within cards.
- [chart-selection.md](chart-selection.md) — when a card is the right answer vs. when a matrix is.
- Visual catalog: [`../visual-catalog/cardVisual.md`](../visual-catalog/cardVisual.md), [`../visual-catalog/kpi.md`](../visual-catalog/kpi.md), [`../visual-catalog/multiRowCard.md`](../visual-catalog/multiRowCard.md).
- Rules-layer skill: `reports:pbi-report-design` (reference `cards-and-kpis.md`) — concrete JSON structure, extension-measure patterns, goalText configuration.
