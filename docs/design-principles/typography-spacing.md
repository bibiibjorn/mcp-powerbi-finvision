# Typography & spacing

## Principle

Typography and spacing are mostly rules, not judgment — but the judgment is knowing when the default grid fights the content. Default to the theme's typography classes and a strict 16-px grid; override only when the content genuinely fights the grid, and document why. Misalignment is the single most visible signal that a report wasn't designed, even at 4-pixel offsets. The rules feel mechanical; the impact on reader trust is not.

## Why it works

Gestalt proximity and alignment (Wertheimer and the Gestalt school; summarised in Ware, ch. 6): the human visual system groups things that are close, and separates things by consistent distance. A row of cards with one card 8 px off-grid reads as "something is wrong" before the reader consciously sees the misalignment. The perceptual system notices 2-4 px differences reliably. That is why grid math matters more than it seems: small imprecisions read as sloppiness, and sloppiness costs reader trust in the data.

Typography in information displays follows a different logic from typography in editorial design. In editorial, expressive typography is a goal — different weights, sizes, styles carry voice. In a dashboard, typography is *infrastructure*; it should be invisible. The best dashboards have typography the reader never thinks about. That means: few font sizes (≤ 3 per page), one font family, no decorative weights, no italicised-for-emphasis, no uppercase-for-titling. Typography that calls attention to itself is competing with the data.

Font availability is a distribution constraint unique to Power BI. Reports render in Desktop, in the Service (browser), and in embedded contexts — and the font has to work everywhere. Segoe UI is the safe default because it ships with Windows and is web-friendly. Custom fonts (Helvetica Neue, Proxima Nova, corporate brand fonts) don't render reliably: they may substitute to the browser's default, they may render inconsistently between Desktop and Service, they may fail on a user's phone. The result is a report that looks different for different users — which destroys the consistency that typography is meant to provide.

The 16-px grid is not arbitrary. Power BI's default canvas is 1280 × 720 or 1920 × 1080; both divide cleanly by 16. Visuals positioned on a 16-px grid snap together at the pixel level; visuals positioned at arbitrary pixel offsets (12, 21, 37) drift against each other when rendered at different DPIs. The grid is the unit of composition the render pipeline is calibrated against.

Finally, font size for `cardVisual` and other measurement-sensitive visuals: two font properties compound at render time (`value.fontSize × calloutSize`). Declaring a font size is not the same as rendering a font size. The reasoning layer needs to name this specifically — rendered verification is not optional, it's the only way to confirm text fits. See [cards-and-kpis.md](cards-and-kpis.md) for the full pattern.

## How to apply

### Step 1 — Use Segoe UI everywhere

- **Segoe UI Regular** — body text, axis labels, data labels, card labels.
- **Segoe UI Semibold** — titles, emphasis where needed.
- **Segoe UI Light** — acceptable at larger sizes (≥ 24 pt) for page titles or large KPI numbers; breaks at small sizes.

Nothing else. No Segoe UI Black (it reads as too heavy), no Segoe UI Italic (reserve italic for genuine emphasis, rarely needed in dashboards), no custom brand fonts. If brand compliance requires a specific font, verify it renders correctly in Desktop + Service + mobile — usually it doesn't, and "approximately the brand font in rendered output" is worse than "Segoe UI reliably."

### Step 2 — Limit to 3 font sizes per page

The rule of thumb is ≤ 3 font sizes per page. A common pattern:

- **Large (24-48 pt).** Page title, KPI primary numbers.
- **Medium (12-14 pt).** Chart titles, card labels, axis labels if called out.
- **Small (9-10 pt).** Data labels, footnotes, axis tick labels.

4+ font sizes on one page creates a typography mini-hierarchy that competes with the visual hierarchy. If you need more than 3 sizes, reconsider — probably one of the sizes can be dropped or merged.

For KPI cards specifically, the rules-layer skill's three-element hierarchy (primary number > delta/verdict > supporting context) maps to three size tiers. Those same three tiers serve the rest of the page; don't add a fourth tier for chart titles that differs from card labels.

### Step 3 — Text classes via the theme

The theme defines **text classes** (`textClasses` in the theme JSON):

- `title` — page titles, visual titles.
- `label` — axis labels, data labels.
- `callout` — KPI numbers.
- `header` — table/matrix column headers.

Use the classes. A visual that overrides `fontSize` inline is drifting from the theme; when the theme changes (new font family, size bump), inline overrides don't update. Document the override with a comment if one is genuinely needed; otherwise, fix the theme.

The `modifying-theme-json` skill handles pushing formatting into the theme systematically. When reviewing a report, look at how many visuals have font-related overrides in their `objects` or `visualContainerObjects`; a high override count is a signal the theme doesn't fit the content and needs work.

### Step 4 — Grid math, always

Every visual's position derives from three numbers: margin, gap, page width/height. Never drag-to-position.

For a row of `n` visuals:

```
margin = 24 (or 32 for wide pages, or 16 for dense ops consoles)
gap    = 16 (24 if the page has room)
content_width = page_width - 2*margin
total_gaps    = gap * (n - 1)
visual_width  = (content_width - total_gaps) / n
x_i           = margin + i * (visual_width + gap)     for i in 0..n-1
```

Example: 4 equal visuals on a 1280 × 720 page with 24-px margins and 16-px gaps:

```
content_width = 1280 - 48 = 1232
total_gaps    = 16 * 3 = 48
visual_width  = (1232 - 48) / 4 = 296
x positions:    24, 336, 648, 960
```

For mixed-width rows (one KPI + one chart sharing a row), compute the gap between them explicitly and confirm it equals the gap between the other adjacent pairs on the page. A 4-px discrepancy is visible.

For vertical stacks: same logic on the y-axis. Same 16-px gap between stacked visuals within a band; 32-48-px gap between bands (this is the whitespace that creates band separation — see [density.md](density.md)).

### Step 5 — Vertical column alignment across bands

When visuals in different bands share a vertical split (e.g. two KPIs over two charts), the column boundaries must align. If band A has `visual_1 x=24, width=620` (ends at 644) and `visual_2 x=660, width=596`, then band B should use the same split: `visual_3 x=24, width=620`, `visual_4 x=660, width=596`. The widths of the two visuals in each band can differ from each other, but the *column gutter* stays in the same x position across bands.

Misaligned gutters break the grid visibly: the reader's eye follows vertical lines down the page, and when those lines jump 8-16 px between bands, the page feels "wobbly." Aligned gutters carry the eye down.

### Step 6 — Margins symmetric

Top / bottom / left / right margins of the page must all be equal (or, if asymmetric for a reason, the reason must be documented). 24 px on all four edges is the default. Asymmetric margins read as errors.

Inside visuals: padding between visual frame and content should also be consistent. Charts with 12-px left padding and 20-px right padding look off even though the reader can't articulate why.

### Step 7 — Shadows and effects

- **Drop shadows off.** The `pbi-report-design` skill sets `dropShadow.show: false` in the theme wildcard. Shadows add visual noise and are flagged by WCAG 2.1 as a vestibular-issue concern.
- **Borders subtle or absent.** 1-px borders at low opacity are fine as a visual-separation affordance when whitespace alone isn't enough. Heavy coloured borders on every visual make the page look like a spreadsheet.
- **Rounded corners.** Fine at small radii (4-8 px) if consistent across the report. Large radii (12+ px) look consumer-soft; generally inappropriate for business reports.

## Worked examples

### Example 1 — R0101 page title via `textbox` visual

**User intent.** Every page needs a title identifying what the page shows.

**Decision.** Textbox visual at `x=24, y=24`, width=400-600, height=48-64, font Segoe UI Semibold, size 20-24 pt. For R0101 specifically, there's a `textbox` (e.g. `45676d67e070b23b48c0` on NAVIGATOR at 190 × 71) serving as a page title element.

**Why this works.** Segoe UI Semibold reads as "heading" without being loud. 20-24 pt is large enough to be the clear page anchor, small enough not to dominate. Position at 24 / 24 is the standard top-left origin — readers expect titles there.

**What the agent should reproduce.** For every new page: add a textbox title at `x=24, y=24` with size 20-24 pt Segoe UI Semibold. Named consistently across pages ("Q4 Summary" not "Page 1"). Title is part of the page creation template, not an optional add-on.

### Example 2 — R0101 dense multiRowCard strip (correctly compressing the grid)

**User intent.** A strip of 4 small multiRowCards on `DIR PARTICIPATIONS - DETAIL` — parameter readouts (entity identity fields). They're narrow (35-60 px tall) and tightly packed.

**Decision.** Gap between these cards is smaller than the page-wide 16-px default — approximately 8 px. Left-aligned labels, right-aligned values, uniform font size (~10-11 pt).

**Why this works and is a deliberate deviation.** Each card is small and unambiguous (label + value); the reader reads them as a group, not as separate visuals. The tight gap reinforces "these are one thing" (gestalt proximity). The page uses the standard 16-24 px gaps elsewhere, so the tight strip reads as deliberate, not as accidental.

**How to signal the deviation.** Match other visual properties across the strip — same background, same border style, same typography. The uniformity signals intent. If you had a tight strip plus a loose strip with no visual distinction, the reader would read "inconsistent"; with uniform strip formatting, they read "these are a unit."

**What the agent should reproduce.** When compressing gaps below the default, compress *all* the way within that group and keep the gaps consistent. Don't mix 8-px and 12-px gaps inside a strip. And restore the standard 16-24 px gap between the compressed strip and its neighbours.

### Example 3 — correctly broken: 5 sizes for a reason

**Setup.** A page has a headline KPI (48 pt), supporting KPIs (24 pt), a chart title (14 pt), axis labels (10 pt), and small footnote text (8 pt). Five sizes.

**Is this wrong?** Usually yes. Five sizes is more hierarchy than most pages need, and readers rarely benefit from the fifth tier. But if the page is specifically an executive dashboard where the hierarchy *is* the design (headline > KPIs > trend > detail labels > fine print disclaimers), and each size serves a distinct level the reader genuinely moves through, five can work. Document the rationale; this is the kind of case where typography overrides the default rule.

**What would be wrong.** Five sizes with no clear mapping to hierarchy levels — 14 pt for some labels, 13 pt for others, 12 pt for yet others, all playing a similar role. That's not hierarchy; that's drift. Consolidate.

## Anti-patterns

### "5 different font sizes on one page"

**What the agent is tempted to produce.** Chart titles at 14 pt, KPI labels at 13 pt, axis titles at 12 pt, data labels at 11 pt, footnote at 10 pt — five sizes, no clear role separation.

**Why it's wrong.** No visible hierarchy. The reader can't use size to decide what to read first. More typography work for the reader, not less.

**What to do instead.** Collapse to 3. Pick a large, medium, and small. Apply consistently via theme text classes. If a role doesn't map to one of the three, rethink whether the role needs typographic distinction.

### "Custom font"

**What the agent is tempted to produce.** A corporate brand font (Proxima Nova, Helvetica Neue) set in the theme because brand guidelines require it.

**Why it's wrong.** Custom fonts don't render reliably across Desktop / Service / mobile. Your report looks right on your screen and looks broken for a user on a different tenant. Brand consistency is lost *because* the font is custom.

**What to do instead.** Segoe UI. Document the deviation from brand if pushback arises — "Segoe UI is the only font that renders consistently across all Power BI surfaces; the brand font will fail for some users." If the brand team can't accept Segoe UI, consider whether the report needs to match brand typography or just brand colours (much easier to achieve via theme).

### "Eyeballed positions"

**What the agent is tempted to produce.** Visuals positioned by dragging in Desktop until they "look right." Widths 293, 296, 301 px. Gaps 14, 16, 19 px. Nothing quite aligns.

**Why it's wrong.** Misalignment is visible at 2-4 px. The page reads as sloppy even if the reader can't articulate why; the sloppiness reduces trust in the data.

**What to do instead.** Compute positions arithmetically from margin, gap, page dimensions. When using pbir CLI or MCP ops, pass computed values — don't accept whatever the last drag produced.

### "Uneven margins"

**What the agent is tempted to produce.** Left margin 24 px, right margin 32 px, top margin 16 px, bottom margin 40 px — because the visuals "needed" the extra space on two sides.

**Why it's wrong.** The reader's eye catches unequal margins immediately. It reads as "a mistake I can see but can't fix."

**What to do instead.** All four margins equal. If a visual genuinely needs more space on one side, *the visual* is sized wrong; resize the visual, don't pad the margin.

### "Italic for emphasis"

**What the agent is tempted to produce.** Italic text for the emphasised word in a chart title or label.

**Why it's wrong.** Italic Segoe UI at small sizes is hard to read; strains low-vision readers; doesn't reliably render across contexts. Italic in a dashboard is typography drawing attention to itself.

**What to do instead.** Emphasise through weight (Regular → Semibold), through size, through colour if semantic. Save italic for genuine editorial needs (a book title inside a footnote).

### "Uppercase titling"

**What the agent is tempted to produce.** All page titles and chart titles in UPPERCASE for visual weight.

**Why it's wrong.** Uppercase is 10-15 % slower to read (Tinker, *Bases for Effective Reading*, 1965). Dashboards need fast reading; uppercase trades speed for aesthetic weight. If the intent is emphasis, weight or size works without the reading penalty.

**What to do instead.** Sentence case (or title case for page names). Rely on weight/size for emphasis.

## Self-audit checklist

- [ ] Are only Segoe UI fonts in use across the report (Regular, Semibold, Light)?
- [ ] Are there ≤ 3 font sizes on any given page?
- [ ] Do visuals use theme `textClasses` rather than inline `fontSize` overrides (where possible)?
- [ ] Are all positions derivable from grid math (margin, gap, page dimensions)? No 2-4 px misalignments visible?
- [ ] Are all gaps between adjacent visuals within a band the same value?
- [ ] Are within-band gaps (16-24 px) visibly smaller than between-band gaps (32-48 px)?
- [ ] Do vertical column splits align across bands (gutters form continuous lines)?
- [ ] Are all four edge margins equal?
- [ ] Are drop shadows off? Are borders subtle (1 px low opacity) or absent?
- [ ] For `cardVisual` and similar measurement-sensitive visuals: has the rendered text been verified via screenshot (not just the declared `fontSize`)?
- [ ] Are titles in sentence/title case, not UPPERCASE?
- [ ] Where the theme is overridden inline, is there a stated reason?

## 2026 platform notes

- **Theme canvas size key (March 2026).** As of the March 2026 Desktop release, theme JSON supports a `canvas` block (`width`, `height`, `autoFitToPage`). Set this once in the project theme so every new page starts at the target resolution. This reinforces the "grid math from day 1" rule: if the canvas size is in the theme, all grid computations use the same baseline.
- **`textClasses` theme coverage is now complete.** All 14 text class slots (`title`, `label`, `callout`, `header`, `largeTitle`, `smallLabel`, etc.) are applied consistently across all native visuals as of 2025. Audit the theme `textClasses` section before doing per-visual font overrides — the override may simply be a gap in the theme, not a genuine content exception.
- **PBIR format and font paths.** In PBIR, per-visual font overrides live in `visual.objects.<container>[0].properties.fontSize` (internal rendering). These are the overrides that `reports:modifying-theme-json` and the `07_Visual_Operations:update_formatting` MCP op target. When reviewing typography consistency, count overrides in `visual.objects` (not `visualContainerObjects`); the container chrome objects control title/border typography, not chart content.
- **Segoe UI on Power BI Service (web rendering).** Segoe UI is served as a web font by Microsoft in the Service as of 2024. It no longer requires Windows installation to render. The recommendation to use Segoe UI exclusively remains correct, and the cross-platform rendering concern (Desktop vs Service vs mobile) is now reduced — but not eliminated for embedded/iframe contexts outside the standard Service.

## See also

- [page-structure.md](page-structure.md) — grid math as part of band layout.
- [cards-and-kpis.md](cards-and-kpis.md) — the cardVisual font-compounding rule (`value.fontSize × calloutSize`) and rendered-verification requirement.
- [density.md](density.md) — whitespace between bands as structural, not decorative.
- [color.md](color.md) — contrast ratios (text on background) that interact with typography choices.
- Rules-layer skill: `reports:modifying-theme-json` — how to push typography into the theme `textClasses`.
- Rules-layer skill: `reports:pbi-report-design`, reference `layout-guidelines.md` — concrete pixel values and sample layouts.
