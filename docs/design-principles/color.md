# Color

## Principle

Color encodes meaning. If it does not encode, it does not belong. The theme is the default palette; any visual-level color override requires a justification that is a sentence long, not a vibe. Red and green are reserved for **variance semantic** (bad/good, loss/gain) — using them for categorical encoding costs the reader the ability to interpret a genuine variance signal elsewhere in the report. Accessibility is a hard floor, not a post-hoc adjustment: contrast ratio ≥ 4.5 : 1 for text (WCAG AA); colour is always paired with a non-colour redundant signal when the signal matters.

## Why it works

Colour is preattentive (Ware, ch. 4) — detected in ~200 ms before any reading happens. This is why colour is such a powerful encoding: it carries meaning without costing the reader attention. The catch is that the reader's brain picks up *every* coloured region preattentively, so any colour variation on the page reads as a signal whether it is one or not. Decorative colour isn't neutral — it is noise the reader has to mentally filter.

Hue, saturation, and value are three separate channels. Hue is what most people mean by "colour" (red vs blue). Saturation is how vivid it is. Value is how light/dark. Each channel carries roughly one bit of signal independently; combining them over-encodes. The practical rule: use hue for category, saturation for emphasis, and leave value mostly alone (dark text on light background is the default; changing value is for backgrounds, not data).

About 8 % of men and 0.5 % of women have red-green colour-vision deficiency (CVD) — the most common type. That means a page relying on red-green to carry a signal is illegible to roughly one in twelve male readers. WCAG 2.1 codifies the rule: "color is not used as the only visual means of conveying information." Pairing colour with a sign, arrow, icon, or text label solves this; unpaired red/green does not.

Cultural semantics: in Western financial contexts, red = loss/negative and green = gain/positive. This mapping is strong enough that using red for "highest performer" will confuse readers even when the legend explicitly says so. The reader's preattentive system overrides the legend. Don't fight the semantic — use it when you mean it and avoid it when you don't.

IBCS (International Business Communication Standards) codifies variance colour semantics beyond red/green: solid fills = actual, hollow = plan/budget, hatched = forecast. This gives you three channels of meaning without adding saturated colours. Most corporate reports don't use IBCS, but the underlying logic — patterned fills carry a second channel of meaning — is sound and borrowable.

The theme is where colour lives because themes are reusable. A visual-level colour override:

- Hard-codes a choice that might change when the brand updates.
- Is invisible to BPA (you can't audit against the theme if the theme isn't being used).
- Creates drift across the report: identical visuals end up with slightly different colours because they were authored by different tools.

So the rule is: theme first, visual override only with a named reason, brand colours in the theme rather than per-visual.

## How to apply

### Step 1 — Use the theme's palette, always start there

Every report should use a theme that differs from the default Power BI themes. The theme defines:

- **`dataColors`** — the ordered palette for data series. `dataColors[0]` is the primary/accent; `dataColors[1..N]` are the secondary categorical colours.
- **`foreground` / `background`** — text and panel colours.
- **Semantic sentiment colours** — `good`, `bad`, `neutral` (theme-level or extension-measure bindings).
- **`visualStyles`** — defaults that apply to all visuals of a given type, and overrides per-wildcard.

When you need a colour, use `ThemeDataColor` references rather than hex literals. A hex literal buried in a visual is technical debt:

```json
// Preferred — theme-linked
"expr": {"ThemeDataColor": {"ColorId": 1, "Percent": 0}}

// Avoid — hex literal in a visual
"expr": {"Literal": {"Value": "'#118DFF'"}}
```

Hex literals are acceptable in *extension measures* (where the colour is computed from data: `IF([Delta] >= 0, "good", "bad")`). They are not acceptable as defaults for a visual's fill; that belongs in the theme.

### Step 2 — Default to muted, reserve saturation for emphasis

A page with eight saturated colours looks loud and reduces the reader's ability to notice what matters. Muted greys, pale blues, soft tones are fine as a default palette. Saturation is then available as an emphasis channel: one saturated accent bar in a sea of muted bars says *this one matters* without a label.

Guidance:

- **Category charts** — default to muted tones (saturation ~40-60 %). One accent colour for the series that matters most.
- **Background panels** — neutral (white, near-white, or theme background). Decorative gradients are not backgrounds; they are distractions.
- **Text** — dark on light (ratio ≥ 4.5 : 1) by default. Light-on-dark themes are fine if the theme is consistent; ad-hoc dark panels inside a light theme are not.
- **Borders** — subtle (1 px, 10-20 % opacity) or absent. Heavy borders compete with the data.

Saturation escalates deliberately:

- Muted base everywhere.
- One saturated accent colour per page for emphasis.
- Semantic red/green reserved for variance (see Step 3).
- Alert red (bright, saturated) reserved for "something is wrong" callouts — used sparingly or the reader tunes it out.

### Step 3 — Semantic colour only for variance

Red, green, and orange carry hard-wired meaning in financial reports:

- **Green** — gain, positive delta, on target, healthy.
- **Red** — loss, negative delta, off target, alert.
- **Orange / amber** — warning, watch list, trending wrong.

Using these colours for anything else costs the reader the ability to decode actual variance signals. A page where red = "region 1" instead of "loss" teaches the reader to ignore red. Then when a KPI delta genuinely turns red, the reader doesn't react.

Rules for semantic colour:

- **Apply to variance / delta, not to the primary value.** The primary value is always shown; colour signals the judgment about it. See [cards-and-kpis.md](cards-and-kpis.md) for the KPI-specific pattern.
- **Pair with a redundant signal.** Sign (+/-), arrow (↑/↓), icon (✓/✗), or text label. This is the WCAG accessibility floor; it is also just better UX because signed deltas read faster than colour alone.
- **Be consistent across the report.** If positive delta is green on page 1, it is green on every page. Not "this chart used blue for positive because the page was already blue-dominant."
- **Calibrate intensity.** Semantic red/green should be saturated enough to be preattentive (ordinary theme reds/greens are fine) — not muted, not neon.

### Step 4 — CVD-friendly alternatives when the theme supports them

If the theme uses blue/orange as the sentiment pair instead of green/red, the report is CVD-friendly by default. Blue for positive, orange for negative, both saturated, both WCAG-clearing contrast. This is a good default for corporate reports where accessibility is an audit requirement.

Whether the theme uses red/green or blue/orange, the redundant-signal rule still applies: the sign or arrow does the work for any reader — with or without CVD — who is scanning quickly.

### Step 5 — Categorical colour: fewer, more muted

For categorical encoding (e.g. bars by product line, lines by region):

- **Maximum 5-7 categories per chart.** More and readers can't map each colour to its category without the legend.
- **Use theme `dataColors` in order.** Theme-designed palettes have already been balanced for saturation and hue distance.
- **If more than 5 categories are needed, fall back to a sorted chart + "Other" bucket** rather than adding more colours. If the user truly needs all 12 categories distinguishable, consider small multiples.

Avoid the rainbow palette (red → orange → yellow → green → blue → purple). Readers can't extract ordinal meaning from hue — hue is a categorical channel, not an ordinal one. Use sequential (single-hue light-to-dark) or diverging (two-hue) palettes for ordinal/quantitative encoding.

### Step 6 — Conditional formatting with purpose

Conditional formatting (CF) applies colour based on data. It is powerful and frequently abused. Rules:

- **Apply CF only for variance, threshold, or sentiment.** Not for decoration.
- **Pair CF with a scale or legend.** The reader needs to know what the colour means — "red ≤ target" or "colour scale: low→high."
- **Apply to one column, not all.** A matrix with CF on every column becomes a heatmap with no focal point. CF the one column that carries the variance; leave the rest plain.
- **Use data bars for magnitude, colour scales for variance.** Data bars (horizontal bars inside a cell) encode magnitude on a common scale — Cleveland-McGill rank #1. Colour scales encode variance — powerful but imprecise. Pick the right tool.

### Step 7 — Backgrounds, borders, shadows

These are the places where decorative colour creeps in:

- **Backgrounds.** Neutral. Theme-defined. A gradient wallpaper is almost always wrong — it competes with the data.
- **Borders.** 1 px, subtle. Heavy coloured borders on every visual make the page feel like a form.
- **Drop shadows.** Off. WCAG 2.1 flags shadows as a vestibular-issue concern; the rules-layer skill already disables them in the theme wildcard.

## Worked examples

### Example 1 — R0101 `STRATEGIC ALLOCATION` waterfallChart, red/green for signed variance

**User intent.** Show signed contributions to allocation variance (positive steps = bought more than target; negative steps = bought less than target).

**Decision.** Waterfall chart with semantic red for negative steps, green for positive, neutral grey for the total bar. The start bar is also neutral grey. Saturation is strong enough to be preattentive.

**Why this is correct.** The colour *is* the variance semantic — the whole point of the chart is "what moved in which direction." Red/green here isn't decorative; it is the encoded meaning. The sign of the bar (above or below the baseline) is the redundant signal — readers with CVD still see the direction without needing the colour. Combined, the two channels communicate at a glance.

**What the agent should reproduce.** For waterfall charts: use theme-linked sentiment colours (good / bad) on positive / negative bars, neutral for baseline and total. Do not override with ad-hoc hex codes.

### Example 2 — R0101 `PORFTOLIO - INSTRUMENT BREAKDOWN` pivotTable with conditional formatting

**User intent.** Show detailed instrument data and highlight cells where a KPI has crossed a threshold.

**Decision.** The matrix's primary measure columns stay plain (no CF). One variance column has a CF colour scale: positive → green, negative → red, zero → neutral. A small legend or column header indicates "variance vs benchmark."

**Why this is correct.** CF applied to one column makes that column the visual focus — the reader knows where to look. CF applied to every column turns the matrix into a heatmap where nothing stands out. Pairing the CF with a header label ("Variance") removes ambiguity about what the colours mean.

**What would be wrong.** CF on every measure column (reader can't tell which column is the variance signal). CF with no column label (reader has to guess). CF using a rainbow scale (ordinal meaning lost on a nominally categorical encoding).

### Example 3 — correctly broken: brand red on a single series

**Setup.** The theme uses muted blues and greys for neutral categorical encoding. The client's brand colour is a specific red (the same shade as a corporate logo). The client asks the branding to appear in the report.

**Wrong.** Apply the brand red everywhere — every bar chart, every line chart, every category. Result: brand red loses emphasis (because it's everywhere), and conflicts with semantic red (which still means "loss" in the reader's brain).

**Correct.** Add the brand red to the theme's `dataColors[0]` — it becomes the default accent. Use it sparingly, for the primary series on one or two key charts. Keep the semantic red (for variance) as a separate, distinct saturated red reserved for deltas and alerts. The two reds need to be visually distinguishable — ideally the brand red is slightly different saturation or hue from the variance red. Document both in the theme so later authors don't merge them.

The break from "use theme defaults without overrides" is legitimate because brand presence is a real user requirement. The mitigation is *one* accent use, not pervasive use.

### Example 4 — correctly broken the wrong way: heatmap-happy matrix

An agent produces a matrix with CF on every column: a "heatmap view." Each cell is coloured on a red-yellow-green gradient based on that column's value relative to its column mean.

**Wrong because** the reader can't tell which column is the point. Every cell is coloured; nothing stands out. The matrix has traded precision (reading numbers) for pattern visibility (reading colours) — but gained no clarity because there's no focal point.

**Correct.** CF only on the variance column (or the one column where colour encoding matters). Or, if the user genuinely wants a heatmap, use a dedicated heatmap visual (`matrixHeatMap` or Deneb) with explicit axes and legend — not a matrix that silently has colour in every cell.

## Anti-patterns

### "Rainbow categorical"

**What the agent is tempted to produce.** A chart with 8 categories, each a different saturated hue from across the colour wheel.

**Why it's wrong.** Hue is a categorical channel; 8 distinct hues means the reader has no sort order. The chart looks loud; information extraction is slow. At 8+ categories, the reader can't map hue → category reliably without the legend.

**What to do instead.** Reduce to 5-7 categories (collapse the tail into "Other"). Use theme `dataColors` in order. If truly 8+ categories matter, use small multiples, not more colours.

### "Heatmap without a scale"

**What the agent is tempted to produce.** Conditional formatting on a matrix column with no legend, no gradient key, no column-header indication of what the colour means.

**Why it's wrong.** Readers can't decode. They see "red means bad, green means good, probably" — probably isn't good enough for a business decision.

**What to do instead.** Every CF column has a legend or a clear column header naming the measure the colour encodes. If the colour is on a gradient, show the gradient scale explicitly.

### "Red/green only"

**What the agent is tempted to produce.** Delta coloured red for negative, green for positive, with no sign, no arrow, no text.

**Why it's wrong.** 8 % of male readers can't decode it. WCAG 2.1 AA failure. The signal is invisible to CVD readers who happen to be in your audience.

**What to do instead.** Always pair colour with a sign, arrow, or icon. Consider blue/orange as a theme-level alternative to red/green.

### "Brand color everywhere"

**What the agent is tempted to produce.** Every chart, every bar, every KPI coloured in the brand's primary colour because the user said "match our brand."

**Why it's wrong.** When everything is emphasised, nothing is. The brand colour loses its power; emphasis encoding becomes impossible because the emphasis colour is the default.

**What to do instead.** Add brand colours to the theme. Use the primary brand colour for *one* accent series (the one that matters most on each page). Keep everything else muted. The brand shows up meaningfully; emphasis remains available.

### "Low-contrast dark-on-dark"

**What the agent is tempted to produce.** Dark grey text on a dark blue panel because it looks sleek.

**Why it's wrong.** Fails WCAG 4.5 : 1 contrast. Readers with low vision, older readers, and readers in bright-room conditions can't read it. The sleekness is an aesthetic choice that excludes users.

**What to do instead.** Check every text-on-background pair for contrast ratio. WebAIM's contrast checker is 30 seconds. ≥ 4.5 : 1 for regular text, ≥ 3 : 1 for ≥ 18 pt text.

### "Gradient wallpaper"

**What the agent is tempted to produce.** Page background is a subtle gradient from light-blue to white because it looks polished.

**Why it's wrong.** The gradient is colour noise competing with data. Readers' preattentive system registers every gradient transition. It also fails contrast ratio across the page — text that passes against the light end fails against the dark end.

**What to do instead.** Flat, neutral background. If the report feels sterile, add texture through whitespace and typography — not through coloured fills.

## Self-audit checklist

- [ ] Is every colour used encoding something (category, variance, emphasis, alert)? Can you name what each colour means?
- [ ] Are theme colours used (ThemeDataColor references) before any hex overrides?
- [ ] If a hex override exists, is it in an extension measure (data-computed) or is it justified with a one-sentence reason?
- [ ] Is text-on-background contrast ≥ 4.5 : 1 everywhere (≥ 3 : 1 for text ≥ 18 pt)?
- [ ] If red/green is used for variance, is there a non-colour redundant signal (sign, arrow, icon)?
- [ ] Are there ≤ 3 saturated colours per page (muted default, one accent, one semantic)?
- [ ] Is semantic red/green reserved for variance? No categorical uses of red or green unrelated to variance?
- [ ] Is conditional formatting applied to at most one column per matrix, with a legend or clear header?
- [ ] Is the background neutral (white, theme background) — no decorative gradients, no coloured panels?
- [ ] Are borders subtle (1 px, low opacity, or absent)? Are drop shadows off?
- [ ] If the theme provides a CVD-friendly sentiment pair (blue/orange), is it used instead of red/green?

## 2026 platform notes

- **Theme canvas size (March 2026).** The theme JSON now supports a `canvas` key that sets the default page dimensions for all new pages. This belongs in the theme to ensure consistent canvas sizing — not set per page. Document the target canvas size in the theme along with colour decisions.
- **Power BI Q&A retires December 2026.** Reports using Q&A visuals will need a migration path. Q&A results often use default blue colours unconnected to the theme. When migrating Q&A to Copilot-driven or standard visuals, apply the theme palette explicitly to the replacement visuals.
- **High-contrast mode testing.** Power BI Service added a user-accessible high-contrast toggle (View → High contrast colors) in 2025. Test reports in both high-contrast black and white modes. In high-contrast mode, Power BI overrides most colours — verify that data labels and titles remain readable. Theme-defined colours override more gracefully than per-visual hex overrides in high-contrast mode.
- **Semantic model colour metadata.** As of 2026, measure `displayFolder` and `formatString` can be augmented with colour hints via calculation-group metadata — not yet a Power BI first-class feature, but used in some SVG measure patterns. Keep model-level colour decisions in the theme; don't embed them in measure `formatString` hacks.
- **Accessible colour — Power BI Accessibility Checker (2026).** The built-in Accessibility Checker in Power BI Desktop (2026 release) validates colour contrast, alt text, and tab order. It does not catch every CVD failure — supplement with the grayscale screenshot test and manual CVD simulation (Coblis, Colour Blindness Simulator). WCAG 2.1 criterion 1.4.1 (Use of Color) is the baseline; the Checker enforces AA contrast (1.4.3, 1.4.11).
- **Okabe-Ito palette for CVD-safe categorical encoding.** The empirically-tested CVD-safe palette is: Black (`#000000`), Orange (`#E69F00`), Sky blue (`#56B4E9`), Bluish green (`#009E73`), Yellow (`#F0E442`), Blue (`#0072B2`), Vermillion (`#D55E00`), Reddish purple (`#CC79A7`). When building a theme for an accessibility-critical report, consider using this as the `dataColors` array. Never use red + green in the same chart.

## See also

- [cards-and-kpis.md](cards-and-kpis.md) — semantic colouring on KPI deltas; CVD pairing.
- [chart-selection.md](chart-selection.md) — when colour-as-encoding is the right choice and when it isn't.
- [page-structure.md](page-structure.md) — colour as part of visual hierarchy.
- Visual catalog: `../visual-catalog/` per-type files — default `fillCustom`, `dataColors` role, CF object paths.
- Rules-layer skill: `reports:modifying-theme-json` — how to set sentiment colours, text classes, CF rules at theme level.
- Rules-layer skill: `reports:pbi-report-design`, references `visual-colors.md` — concrete patterns and theme examples.
