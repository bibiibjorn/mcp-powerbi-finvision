# Design Principles — Agent Reasoning Layer

> **For the autonomous design agent (sub-project E).** This corpus gives you the *why* behind Power BI design decisions — reasoning you can apply to novel situations, not just rules you follow blindly.
>
> **Load pattern:** read this INDEX first, identify which decisions you are about to make, then load only the 1–2 relevant topic files. Do not read the full corpus upfront — it is designed for on-demand retrieval.
>
> **See also:** companion mechanical reference at [`../visual-catalog/INDEX.md`](../visual-catalog/INDEX.md) (schema, roles, properties, templates) and rules-layer skills `reports:pbi-report-design`, `reports:modifying-theme-json`.

## Topics (priority order)

| Priority | Topic | File | When to load |
|----------|-------|------|--------------|
| 1 | Page structure & information hierarchy | [page-structure.md](page-structure.md) | First decision on any new page or report redesign |
| 2 | Chart selection | [chart-selection.md](chart-selection.md) | Picking visual type per data shape / user intent |
| 3 | Information density | [density.md](density.md) | Deciding how much goes on one page; tiebreaker when layout is crowded |
| 4 | Cards & KPIs | [cards-and-kpis.md](cards-and-kpis.md) | Designing the top-row summary band; any single-number display |
| 5 | Color | [color.md](color.md) | Semantic vs decorative color; emphasis; variance encoding |
| 6 | Interactivity | [interactivity.md](interactivity.md) | Slicers, cross-filter, drill-through, bookmarks |
| 7 | Typography & spacing | [typography-spacing.md](typography-spacing.md) | Font choices, size hierarchy, alignment grid |

## When to load what — decision matrix

| Agent is about to … | Load these file(s) |
|---------------------|--------------------|
| Decide overall page layout for a new page | `page-structure.md`, `density.md` |
| Pick a chart type for a specific visual | `chart-selection.md` |
| Place the top-row KPI band | `cards-and-kpis.md`, `page-structure.md` |
| Decide how many visuals to cram on a page | `density.md` |
| Decide whether to use red/green variance encoding | `color.md` |
| Apply conditional formatting | `color.md`, `chart-selection.md` (detail tables) |
| Configure a cardVisual's fontSize / calloutSize | `cards-and-kpis.md`, `typography-spacing.md` |
| Add or remove a slicer | `interactivity.md` |
| Set up drill-through or bookmarks | `interactivity.md` |
| Choose font sizes for a page | `typography-spacing.md`, `cards-and-kpis.md` (if KPIs) |
| Align visuals on a grid | `typography-spacing.md` |
| Decide what goes on page N vs page N+1 | `page-structure.md`, `density.md` |
| Decide whether to build a custom tooltip | `interactivity.md` |
| Evaluate an existing page for slop | Load every topic's `## Self-audit checklist` section; run each against the page |
| Run end-of-build self-audit | Re-read `## Self-audit checklist` section of each topic file touched during the build |

## Cross-topic tensions

These conflicts come up often enough to warrant a standing resolution.

### Donut chart — `chart-selection.md` vs. `color.md`

- **Tension:** `chart-selection.md` prefers bar for share-of-total (Cleveland-McGill rank #1 beats donut at #4); `color.md` acknowledges donuts work for ≤ 3 slices when identity > precision.
- **Resolution:** Bar is the default for share-of-total. Donut is allowed only when all three hold: ≤ 3-4 slices AND the page needs visual variety (five bar charts elsewhere) AND precision doesn't matter. Breaking one of the three reverts to bar.

### Density vs. executive-audience rule — `density.md` vs. `page-structure.md`

- **Tension:** `density.md` calibrates visual count to audience; `page-structure.md` says executive pages follow 3-30-300 (implying ≤ 8 visuals).
- **Resolution:** These are consistent — executive audience implies the ≤ 8 bound. Conflict is only apparent. If the agent sees "exec audience + 12 visuals" it's wrong on the audience OR wrong on the count — usually the count (trim to 8).

### Color for emphasis vs. color for variance — `color.md` internal

- **Tension:** Emphasis colour = saturated accent (draws attention to *this one* visual); variance colour = red/green semantic (signals direction of delta). Both want saturated hues.
- **Resolution:** Variance wins red/green. Emphasis gets a non-red, non-green accent — theme `dataColors[0]` (usually blue or brand colour). Red/green are reserved for variance; misusing them costs the ability to read variance signals.

### KPI card count — `cards-and-kpis.md` vs. `density.md`

- **Tension:** `cards-and-kpis.md` caps KPI band at 6 per row; `density.md` caps executive-page total at 8 visuals.
- **Resolution:** Consistent. A 6-card KPI band leaves budget for 2 more visuals (a dominant trend chart + a detail table), which is exactly the 3-30-300 structure. If you find yourself wanting 8 KPIs + other visuals, you've exceeded the budget; cut KPIs to 4-5.

### Drill-through vs. density — `interactivity.md` vs. `density.md`

- **Tension:** Drill-through hides detail from the overview page (reduces density) but requires a separate detail page (adds page count).
- **Resolution:** Prefer drill-through when ≥ 3 distinct detail contexts exist (drill to customer, drill to product, drill to order) — the overview stays clean, the details are on-demand. If there's only one detail context, a report-level filter or a second page without drill-through is simpler.

### Grid math vs. content — `typography-spacing.md` vs. everything

- **Tension:** The 16-px grid is rigid; real content sometimes doesn't want to align (a 19-character measure name pushes a card wider than the grid slot).
- **Resolution:** Grid wins. Trim labels, use abbreviations, or redesign the card — don't break the grid to accommodate long labels. If the grid keeps fighting the content, the page is wrong, not the grid.

### Breaking 3-30-300 — `page-structure.md` internal

- **Tension:** The rule says exec pages follow 3-30-300; the "correctly break" section says some pages shouldn't.
- **Resolution:** Break only when the audience isn't arriving cold — drill-through detail pages, scratchpad pages, alarm consoles. All three have the property that the 3-second question is already answered or not applicable. For exec pages where a reader arrives without context, 3-30-300 is not optional.

## 2026 platform refreshes applied across this corpus

The following 2026-era facts have been incorporated into the individual topic files. This summary avoids re-reading those files when you only need a freshness check:

- **PBIR default format (March 2026).** Per-page JSON is now the default for new and migrated reports. Layout coordinates and band structure are unchanged. `page-structure.md` and `typography-spacing.md`.
- **Canvas size in theme JSON (March 2026).** `canvas` key in theme JSON sets default dimensions for all new pages. Set at project start. `page-structure.md`, `color.md`, `typography-spacing.md`.
- **Visual calculations GA (May 2026).** `visualCalc` expressions add running totals, rank, % of parent inside a visual without a model measure. Reduces supplementary-visual sprawl on analytical pages. `chart-selection.md`, `density.md`, `cards-and-kpis.md`.
- **R/Python visuals deprecated for Embed for your customers (May 2026).** Still work in standard Service/Desktop. Replace with Deneb or SVG DAX for embed scenarios. `chart-selection.md`.
- **Power BI Q&A retiring December 2026.** Migration path: Copilot-driven exploration or standard slicers + drillthrough. `interactivity.md`, `color.md`.
- **Slicer sync now cross-report-capable.** Slicer state can synchronise across reports sharing a published model (workspace sync setting). `interactivity.md`.
- **WCAG 2.1 tooltip enforcement (Power BI Accessibility Checker, 2026).** Microsoft's built-in checker now flags tooltip-only content. Tooltip rule from `interactivity.md` applies. `interactivity.md`.
- **Bookmark storage in PBIR.** Bookmarks live in `Report/definition/bookmarks/` as individual JSON files. Use `11_PBIP_Authoring` MCP op; do not hand-edit IDs. `interactivity.md`.
- **cardVisual standard (2024+, still current).** Old `card` is in maintenance mode. `cardVisual` is the platform-recommended KPI visual. Font-compounding bug (`value.fontSize × calloutSize`) unresolved as of May 2026. `cards-and-kpis.md`.
- **Segoe UI web-served in PBI Service (2024).** Segoe UI no longer requires Windows installation in Service. Recommendation unchanged; embedded/iframe contexts are still a risk. `typography-spacing.md`.
- **textClasses coverage complete (2025).** All 14 theme text class slots now apply consistently across native visuals. Audit theme before per-visual overrides. `typography-spacing.md`.
- **Power BI Q&A visual colours.** Q&A visuals use default blue unconnected to the theme; when migrating, apply theme palette explicitly. `color.md`.
- **High-contrast mode testing.** Power BI Service high-contrast toggle available (2025). Theme-defined colours override more gracefully than per-visual hex overrides. `color.md`.

## Accessibility guidance (WCAG 2.1) — cross-topic note

The `stream-4-report-design.md` research corpus (§11) contains a full WCAG 2.1 checklist for Power BI. The design-principles files apply WCAG rules throughout (contrast, colour-is-not-sole-encoding, alt text, tab order, tooltips). For a consolidated per-checklist view see `stream-4-report-design.md §11.6` or the `pbi-report-audit` skill's `design-audit-checklists.md`.

The four WCAG principles that most frequently apply in Power BI work:
- **Perceivable (§1):** contrast ≥ 4.5:1 text / ≥ 3:1 non-text; colour paired with shape/label.
- **Operable (§2):** keyboard navigation works; tab order configured; back buttons on drillthrough pages.
- **Understandable (§3):** no jargon; consistent slicer placement; informative visual titles.
- **Robust (§4):** alt text on all non-decorative visuals; "Show Data" table headers meaningful; tested in High Contrast mode.

## IBCS notation — cross-topic note

IBCS (International Business Communication Standards v1.2) is referenced in `chart-selection.md` (variance charts), `color.md` (solid = actual / hollow = plan / hatched = forecast), and `cards-and-kpis.md` (reference label as hollow-bar target). For full notation rules see `stream-4-report-design.md §12`. IBCS is optional but consistent use of its variance-colour semantics (even without formal certification) improves report legibility across pages.

## Citations

Reference sources used across the corpus. Short citations only — topic files cite these by name, not by full bibliographic entry.

- **Stephen Few** — *Information Dashboard Design* (2nd ed., 2013); *Now You See It* (2009). Dashboard attention model, gauge anti-patterns, sparklines, KPI bare-number critique.
- **Cleveland & McGill** — "Graphical Perception: Theory, Experimentation, and Application to the Development of Graphical Methods" (JASA 1984). Perceptual accuracy rank: position on common scale > length > angle > area > color.
- **Colin Ware** — *Information Visualization: Perception for Design* (3rd ed., 2012). Preattentive processing (ch. 4-5), gestalt grouping (ch. 6), colour encoding.
- **Edward Tufte** — *The Visual Display of Quantitative Information* (1983). Data-ink ratio, small multiples, chart-junk taxonomy.
- **IBCS** — *International Business Communication Standards*. Variance color semantics (solid = actual, hollow = plan, hatched = forecast), delta encoding.
- **Ben Shneiderman** — "The Eyes Have It" (VL 1996). Visual information-seeking mantra: overview → zoom/filter → details-on-demand. Direct-manipulation model (1983).
- **John Sweller** — "Cognitive Load During Problem Solving" (Cognitive Science 1988). Intrinsic / extraneous / germane load decomposition.
- **Nelson Cowan** — "The Magical Number 4 in Short-Term Memory" (BBS 2001). Working memory capacity refinement of Miller's 7±2.
- **Miles Tinker** — *Bases for Effective Reading* (1965). Reading speed comparisons (uppercase vs mixed case).
- **Jakob Nielsen / Kara Pernice** — Eye-tracking studies of dashboard layouts (NN/g publications). F-pattern and Z-pattern reading behaviour.
- **WCAG 2.1** — Web Content Accessibility Guidelines. Contrast ratio ≥ 4.5 : 1 for AA text (≥ 3 : 1 for ≥ 18 pt). Colour-is-not-the-only-means rule.
- **Max Wertheimer / Gestalt school** — Proximity, similarity, alignment principles.
- **SQLBI** — [sqlbi.com](https://www.sqlbi.com/) — Power BI–specific pattern library, DAX perf guidance.
- **Microsoft Report Design Best Practices** — [learn.microsoft.com report-design-tips](https://learn.microsoft.com/power-bi/guidance/report-design-tips).
