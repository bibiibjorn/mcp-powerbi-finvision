# Interactivity

## Principle

Interactivity serves exploration, not decoration. Every slicer, cross-filter, drill-through, and bookmark is an *affordance* — a promise to the reader that "you can do something here." Unused affordances are noise; affordances that promise something the report can't deliver are worse than noise (they break trust). A static page for an audience that wants a snapshot is the right answer as often as an interactive page for an audience that wants to explore. Pick based on the reader's task.

## Why it works

Shneiderman's "visual information-seeking mantra" (Shneiderman, *The Eyes Have It*, 1996) organises interactivity as: **overview first → zoom/filter → details-on-demand**. The overview answers "what's going on?"; filters answer "what about this subset?"; details-on-demand answer "tell me more about this specific thing." These are three distinct tasks, and each maps to different interactive elements — slicers for filter, cross-filter for zoom, drill-through for detail. A report that confuses the levels (e.g. drill-through where filtering would do) makes the interaction more expensive than reading the page would have been.

Direct manipulation (Shneiderman, 1983) is the mental model: the reader expects that clicking a bar filters the other visuals, that clicking a slicer updates the page, that a tooltip appears on hover. Power BI's interaction model is largely direct-manipulation by default — which means any deviation from the default confuses readers who've used other dashboards. "Cross-filter off for this visual" is a legitimate choice but must be justified; the default is that everything cross-filters.

The cost of exploration is real. Each slicer adds a cognitive decision ("do I need to set this filter, or is it already right?"), a visual element (space on the page), and a potential state ambiguity (the reader is looking at filtered data without realising it). Each drill-through adds a hidden page the reader has to discover. Each bookmark adds a mode the reader has to remember. Affordances scale non-linearly — 3 slicers feel lightweight, 12 feel like a configuration panel.

Working memory (Cowan, 2001, ~4 chunks) caps the number of active affordances the reader can hold. 12 slicers wall means the reader can't remember what they've set. 5 bookmarks means the reader can't remember what each one does. Designing for interactivity is designing for the reader's memory as much as for the report's flexibility.

The failure mode that justifies this file's existence: agents that reflexively add affordances because "interactivity is good." A dashboard with 15 slicers, 4 drill-through targets, 8 bookmarks, and custom tooltips on every visual is not a great interactive report — it's a form the reader must fill out to see anything. Restraint is the design choice.

## How to apply

### Step 1 — Identify the reader's task

Before adding any interactive affordance, ask: what does the reader actually do with this page?

- **Glance / monitor.** Reader checks the page daily to verify status. No filtering needed; the page is already scoped to "today" or "this quarter." Static page with maybe one slicer for time zoom.
- **Review.** Reader works through the page weekly, comparing periods or segments. 2-4 slicers for the dimensions they'll cut by (time, business unit, region).
- **Explore.** Reader investigates a question, doesn't know the answer in advance. Rich slicers (4-8), cross-filter, drill-through to detail pages.
- **Drill-to-detail.** Reader starts at an overview page, right-clicks to see details for one entity. Drill-through is the whole point; slicers on the detail page are minimal.
- **Present.** Reader (or an exec) uses the page in a meeting. Bookmarks for preset views ("Q4 YTD", "Top 10 by risk"). Minimal slicers during presentation to avoid accidental state changes.

A page can serve one of these tasks well — rarely two, never three. If you catch yourself adding every interactive affordance "just in case," the page is trying to serve too many tasks. Split the page.

### Step 2 — Slicers: fewer, named, scoped

Slicer rules of thumb:

- **Max 3-5 slicers per page** (the rules-layer skill says 3; experience says up to 5 is fine if they're clearly grouped). Beyond 5 the reader spends more time on slicers than on visuals.
- **Top or left edge.** Readers expect slicers there. Slicers scattered through the page fight the layout.
- **Labelled obviously.** "Date" not "Dim1_Date". Slicer labels are UI copy; readers read them as commands.
- **Scoped to the page.** If a slicer makes the current page meaningless, it should either be removed or the page should be restructured.

Pick the slicer visual type based on what it's filtering:

- **Small enum (≤ 5 values, mutually exclusive).** Buttons (`slicer` with button/tile mode) or a dropdown. Readers see all options, pick one.
- **Medium enum (5-30 values).** Dropdown `slicer` or `advancedSlicerVisual` for search.
- **Large enum (30+).** `advancedSlicerVisual` with search; never a scrollable list.
- **Date range.** Between-mode slicer or relative-date slicer.
- **Hierarchy.** `advancedSlicerVisual` with hierarchical mode, or a matrix with "Include Filter Context."

If a slicer is mostly left unchanged (reader uses the default 99 % of the time), consider moving it to the filter pane instead. The filter pane costs no page real estate and signals "this is available but not front-and-centre."

### Step 3 — Cross-filter: understand what you're claiming

The default Power BI behaviour is that clicking a bar in one visual filters other visuals on the page. This is powerful and usually correct — it's what readers expect. But consider:

- **Totals shouldn't be filtered.** If the page has a "Total NAV" card, clicking a category bar filters the card to that category. The card now shows "Total NAV filtered" not "Total NAV" — the reader may not notice, and then the total is wrong in their mental model. Disable cross-filter on totals; they remain fixed.
- **Filtered-by-default slicers should stay set.** If the page is scoped to "current quarter," clicking a bar shouldn't clear the quarter filter. Check the cross-filter interactions.
- **Reciprocal filter on drill visuals.** If the top visual filters a detail matrix, the matrix probably shouldn't filter back (clicking a matrix row shouldn't change the top visual — the top visual is the overview, the matrix is detail-on-demand).

Configure cross-filter interactions explicitly (Edit interactions toolbar in Desktop, or `visualInteractions` in `page.json`). The default is usually right but not always; a 30-second pass through the interactions model is worth the time.

### Step 4 — Drill-through: commit to detail pages

Drill-through is "right-click → see this entity on a dedicated page." The affordance is powerful because it hides detail until the reader needs it. But it requires commitment:

- **The detail page must exist and make sense standalone.** Drill-through to a blank page, or to a page that only works in the drill-through context, is a broken affordance.
- **The detail page must scope to the drill-through entity.** Auto-wire the `drillthrough` filter on the detail page so that arriving via drill-through populates it correctly.
- **Minimum 3 drill-through targets to justify the affordance.** If you have one detail page, just use a report-level filter; drill-through adds a mental model for no gain. Drill-through shines when the reader has "drill to customer", "drill to product", "drill to order" all available from the same overview.
- **Breadcrumb or back navigation on the detail page.** The reader needs a cheap way back. A back button (`actionButton` with "Back" action) costs nothing and is expected.

### Step 5 — Bookmarks: preset views, not interactive slideshows

Bookmarks save a page state (filters, visibility, selection). Legitimate uses:

- **Preset views.** "YTD", "Q4", "Top 10 by risk" — a bookmark per named scenario the reader returns to.
- **Before/after snapshots.** A bookmark showing the state before a scenario is applied and another after.
- **Reset.** A bookmark that clears all filters back to page default. Genuinely useful when the reader has filtered deep.

The anti-pattern is using bookmarks to toggle visibility of visuals — "click bookmark 1 to see chart A, bookmark 2 to see chart B." This is the "interactive slideshow" trap; it's slower to use than tabs and confuses the reader about what they're looking at.

Rules:

- **Name bookmarks clearly.** "YTD Actuals" not "Bookmark 1".
- **Bookmark navigator visual** (`bookmarkNavigator`) lets the reader see all the bookmarks as buttons. If the page has > 3 bookmarks, use the navigator — don't rely on readers finding them in the bookmark pane.
- **Don't reflexively bookmark.** If you catch yourself making bookmarks because "interactivity," ask whether a tab or a slicer is simpler.

### Step 6 — Tooltips: context or nothing

Tooltips appear on hover. Default tooltips show the data-point values. Custom tooltips (a dedicated tooltip page) can show anything — a chart, a matrix, a narrative.

Rules:

- **Default tooltips are fine for most visuals.** The value on hover is usually all the reader needs.
- **Custom tooltip pages** are worth the build when the reader benefits from substantial extra context (e.g. hover on a region bar shows a small trend chart of that region over time). Not when the extra content is decorative.
- **Tooltip content should be fast to render.** Heavy custom tooltips lag; the reader hovers somewhere else; the tooltip chases them.

### Step 7 — Buttons, navigation, action visuals

Buttons (`actionButton`) and page navigators (`pageNavigator`) are interactivity for wayfinding, not filtering. They work well when:

- **There are multiple pages and the reader needs to move between them.** A page navigator across the top or left edge is expected.
- **A specific action is worth highlighting.** "Export this matrix" or "Open documentation" — a labelled button is clearer than expecting the reader to find the feature.

Avoid:

- **Buttons that duplicate existing UI.** If the reader can achieve the action from the standard Power BI toolbar, adding a button is noise.
- **Buttons styled to look like other visuals.** The reader needs to know it's clickable; a button without a button-shaped affordance fails to communicate interactivity.

## Worked examples

### Example 1 — R0101 `DIR PARTICIPATIONS - DETAIL` slicer set

**User intent.** Relationship manager opens a specific participation's detail page. Needs to scope by currency, date range, and potentially by sub-entity.

**Decision.** One `slicer` (`01ac57dcbb86262a9c60`, 210 × 75) plus bookmark navigators (`26828da190d1ebeae9ed`, `5cfd92c9d12d4135265a`, `664e38d181637a51a2e2`) for preset views. The slicer count is 1 — not 5, not 12.

**Why this works.** The page is a drill-through detail page; the primary filter (which participation) is already set by the drill-through. The one slicer handles a secondary filter (a time cut or a currency) that is occasionally needed but not front-and-centre. The bookmark navigators give the reader 3 preset views for common scenarios — "Q4 2024", "YTD", "All Time" or similar — which is faster than manually adjusting the slicer.

**What the agent should reproduce.** Drill-through detail pages need minimal slicers (the drill filter does the heavy lifting). Add one or two secondary slicers only if the user will actually use them. Offer preset views via bookmarks for common scenarios.

### Example 2 — R0101 `CASH FLOW & AVAILABILITY` bookmarkNavigator (correctly used)

**User intent.** Multiple views of cash flow data — gross vs net, weekly vs monthly vs quarterly, by currency.

**Decision.** A `bookmarkNavigator` (`197b743a9e56f5123568`, 530 × 36) at the top of the page offers the reader a row of named buttons. Each button triggers a bookmark that switches the page into that view. Also one `advancedSlicerVisual` (`2ca9278aec2b86ea53d8`) for a primary filter.

**Why this works.** Named preset views — "Gross Weekly", "Net Monthly", etc. — match the reader's mental model of the task. The reader doesn't configure filters manually; they pick a named view. The bookmark navigator visualises the available views so the reader doesn't have to remember them.

**What would be wrong.** The same bookmarks used as an interactive slideshow — e.g. "View 1 shows the chart", "View 2 hides the chart and shows the matrix". Bookmarks are not for showing/hiding visuals; they're for pre-configuring state.

### Example 3 — correctly static: `ANALYSIS - TIME SERIES` page

**User intent.** Analyst explores rank change over ~20 entities over time via a single ribbonChart.

**Decision.** Zero slicers on the page. The ribbonChart is the whole page.

**Why this works.** The audience is analyst/scratchpad; they came for the ribbon, not for filtering. Adding 3 slicers would steal pixels from the ribbon (which benefits from every pixel) and add cognitive load ("which filter is set?") when the reader wants to focus. If the analyst needs a filter, the filter pane is there. The page itself stays clean.

**What the agent should reproduce.** Scratchpad or dominant-visual pages don't need slicers on the page. Push secondary filters to the filter pane.

### Example 4 — correctly broken: disabling cross-filter on a total

**Setup.** A page has a "Total Return YTD" cardVisual at the top and a regional bar chart below. Default Power BI behaviour: clicking a region bar filters the card to that region.

**Wrong.** Default cross-filter. The card now reads "Total Return YTD for Region X"; the reader doesn't notice the filter and thinks the total dropped.

**Correct.** Disable cross-filter from the bar chart to the card ("Edit interactions" → None for the card). The card stays at "Total Return YTD" regardless of bar-chart selection. If the reader wants a filtered total, they use a slicer or a second card explicitly labelled "Filtered Return YTD."

**Why this is correct.** The total is a fixed anchor — the page's constant. The bars are the variable. Breaking the default cross-filter here restores the reader's mental model.

## Anti-patterns

### "12 slicers wall"

**What the agent is tempted to produce.** A top-of-page row of 12 slicers because "users might want to filter by all of these dimensions."

**Why it's wrong.** The reader has to read and assess 12 slicers before looking at any data. Working memory overflows on the slicer row alone. The page becomes a configuration form.

**What to do instead.** 3-5 slicers for the dimensions readers actually filter by. The rest go in the filter pane. If the user says "but I need all 12 to filter," the page is trying to serve multiple tasks — split it.

### "Drill-through nowhere"

**What the agent is tempted to produce.** Right-click menu with "Drill through to Customer Detail" on every visual, but there's no Customer Detail page.

**Why it's wrong.** Affordance promises a page that doesn't exist; breaks trust. Worse, the agent may produce the drill page too but bind it incorrectly — the reader arrives at a blank or wrongly-filtered page.

**What to do instead.** Commit to drill-through: build the destination page, wire the drill filter, test that right-click → drill-through → page arrives correctly filtered. If the destination page isn't worth building, don't offer the drill.

### "Interactive slideshow"

**What the agent is tempted to produce.** Four bookmarks on a page that toggle visibility of different visuals: bookmark 1 shows chart A, bookmark 2 shows chart B, etc.

**Why it's wrong.** The reader can't tell what's going on. Each bookmark is effectively a new page without the navigation affordance of being a page. Bookmarks aren't for pagination.

**What to do instead.** If you have 4 distinct views, use 4 pages (or 4 tabs via a page navigator). If the views share data and readers want to compare, use small multiples. Bookmarks are for preset *filter states*, not for preset *visibility states*.

### "Cross-filter misdirection"

**What the agent is tempted to produce.** All visuals cross-filter by default; clicking anywhere filters the total KPI cards.

**Why it's wrong.** The total card now shows "total of what the reader happened to click on" instead of "total." Readers don't notice the filter; they misread the page.

**What to do instead.** Disable cross-filter on anchor visuals (totals, summary cards). Enable cross-filter between peer visuals (bar chart → trend chart, both scoped by the click).

### "Tooltip on everything"

**What the agent is tempted to produce.** Custom tooltip pages for every visual.

**Why it's wrong.** Most defaults are fine. Custom tooltips cost build time and introduce rendering lag. Readers rarely hover over visuals they already understand; the custom tooltip is wasted work.

**What to do instead.** Default tooltips for most visuals. Custom tooltips only where the added context materially helps (e.g. hover on a regional bar reveals a small trend; hover on an instrument row shows a mini-card of the instrument's key metrics).

### "Button instead of filter"

**What the agent is tempted to produce.** A row of 6 action buttons labelled with business-unit names; clicking a button filters the page to that business unit via bookmarks.

**Why it's wrong.** Duplicates a slicer's functionality with more friction. Readers know slicers filter; they have to learn that these buttons filter. The bookmark-button-filter pattern is also stateful in a way slicers aren't (you can't easily select multiple bookmarks).

**What to do instead.** A single slicer on the business-unit dimension. Multi-select works out of the box. Keep buttons for wayfinding (navigate to page) or specific actions (export) — not for filtering.

## Self-audit checklist

- [ ] Can you name the reader's task (glance / review / explore / drill-to-detail / present)?
- [ ] Does the interactivity level match the task (static for glance, rich slicers for explore, minimal for present)?
- [ ] Are there ≤ 5 slicers on the page? Can you state what question each slicer answers?
- [ ] Are slicers placed consistently (top or left edge)? Labelled in plain language?
- [ ] Do cross-filter interactions make sense (totals not filtered by category click; reciprocal filters considered)?
- [ ] If drill-through is configured, do the destination pages exist, and do they make sense as standalone pages?
- [ ] If drill-through exists, are there ≥ 3 meaningful destinations? If not, would a report-level filter be simpler?
- [ ] If bookmarks are used, are they preset *filter states* (not visibility toggles)? Are they named clearly?
- [ ] If bookmarks number > 3, is a `bookmarkNavigator` visual present to expose them?
- [ ] Are custom tooltips reserved for visuals where extra context materially helps?
- [ ] Do action buttons do things slicers don't (navigate, export, external link) — not duplicate slicer functionality?

## 2026 platform notes

- **Power BI Copilot replaces Q&A (retiring December 2026).** The Q&A visual (natural-language query UI) is being deprecated in favour of Copilot-driven exploration. If a report currently uses Q&A for exploration affordance, plan migration: replace Q&A visuals with Copilot-enabled pages or standard slicers + drillthrough. The interactivity design principles here (overview → filter → detail) remain unchanged; the mechanism shifts from Q&A to Copilot suggestions.
- **Slicer sync across pages is now cross-report-capable.** As of the 2025 feature wave, slicer synchronisation works across reports in a published workspace (via shared datasets). If two reports share a model and the user expects period selection to carry across, check whether the workspace sync setting is enabled. The principle — consistent slicer position across pages — now extends to: consistent slicer state across reports if the workspace config supports it.
- **WCAG 2.1 tooltip rule (1.4.13) is enforced in accessibility audits.** Microsoft's built-in Accessibility Checker (Desktop, 2026) now flags tooltips that contain content not accessible elsewhere. Apply the rule from this file: every tooltip-only data point must also appear in the visual's Show Data table or body.
- **Bookmark navigation in PBIR.** In PBIR format, bookmarks are stored in `Report/definition/bookmarks/` as individual JSON files. The `bookmarkNavigator` visual binds to them by ID. Adding or removing bookmarks via the `11_PBIP_Authoring` MCP op automatically regenerates the navigator's binding. Don't hand-edit bookmark IDs — use the op.

## See also

- [page-structure.md](page-structure.md) — where slicers live in the band layout.
- [density.md](density.md) — interactivity as a tool to reduce visual count (push detail into drill, push filters into slicers).
- [chart-selection.md](chart-selection.md) — when the visual itself is the affordance (e.g. matrix expand/collapse).
- Visual catalog: [`../visual-catalog/slicer.md`](../visual-catalog/slicer.md), [`../visual-catalog/advancedSlicerVisual.md`](../visual-catalog/advancedSlicerVisual.md), [`../visual-catalog/bookmarkNavigator.md`](../visual-catalog/bookmarkNavigator.md), [`../visual-catalog/actionButton.md`](../visual-catalog/actionButton.md).
- Rules-layer skill: `reports:pbi-report-design` (slicer rules).
