# cardVisual

## Overview

<!-- MANUAL: overview -->
`cardVisual` is the modern KPI card introduced with the new card visual in Power BI. Unlike the legacy `card` type, it supports multiple values in one card, rich formatting (callout size, reference labels, accent bars, conditional icons), and small-multiples layouts. The single `Data` role accepts one or many columns/measures; each becomes a separate row in the card when multi-row layout is enabled.

Its internal structure has **two compounding font dials** — `value.fontSize` and `layout.calloutSize` — which multiply at render time. This is the #1 cause of card overflow issues (see Gotchas).
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `cardVisual` when:

- Displaying a **single headline KPI** per card (NAV, total revenue, count of active clients).
- Building a **row of 3–6 summary cards** at the top of an overview page.
- You want **reference labels** (prior period, target, variance) rendered beside the main value without writing a Deneb spec.
- You need **accent bars** or **conditional icons** driven by a measure.

Prefer `card` (legacy) when: you need the lowest-complexity rendering path, or the report is locked to the classic theme. Prefer `multiRowCard` when you have >6 values in a single card and want them auto-laid-out as a scrollable list. Prefer a Deneb spec when you need custom SVG glyphs or bespoke layouts.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Data` | yes | yes | Column, Measure |

## Containers

- **background** (3 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (5 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (7 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (1 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **lockAspect** (1 properties) — documented inline below
- **padding** (8 properties) — see [./_shared/containers-padding.md](./_shared/containers-padding.md)
- **spacing** (2 properties) — documented inline below
- **stylePreset** (1 properties) — documented inline below
- **subTitle** (11 properties) — documented inline below
- **title** (12 properties) — see [./_shared/containers-title.md](./_shared/containers-title.md)
- **visualHeader** (25 properties) — see [./_shared/containers-visualHeader.md](./_shared/containers-visualHeader.md)
- **visualHeaderTooltip** (13 properties) — documented inline below
- **visualLink** (13 properties) — documented inline below
- **visualTooltip** (17 properties) — documented inline below
- **accentBar** (6 properties) — documented inline below
- **cardCalloutArea** (15 properties) — documented inline below
- **cardImage** (30 properties) — documented inline below
- **fillCustom** (18 properties) — documented inline below
- **glowCustom** (10 properties) — documented inline below
- **grid** (5 properties) — documented inline below
- **image** (27 properties) — documented inline below
- **label** (16 properties) — documented inline below
- **layout** (59 properties) — documented inline below
- **outline** (5 properties) — documented inline below
- **overFlow** (2 properties) — documented inline below
- **referenceLabel** (17 properties) — documented inline below
- **referenceLabelDetail** (17 properties) — documented inline below
- **referenceLabelLayout** (11 properties) — documented inline below
- **referenceLabelTitle** (12 properties) — documented inline below
- **referenceLabelValue** (14 properties) — documented inline below
- **rotation** (3 properties) — documented inline below
- **shadowCustom** (10 properties) — documented inline below
- **shapeCustomRectangle** (14 properties) — documented inline below
- **smallMultiplesAccentBar** (5 properties) — documented inline below
- **smallMultiplesBorder** (5 properties) — documented inline below
- **smallMultiplesCellBackGround** (17 properties) — documented inline below
- **smallMultiplesGrid** (6 properties) — documented inline below
- **smallMultiplesHeader** (36 properties) — documented inline below
- **smallMultiplesLayout** (14 properties) — documented inline below
- **smallMultiplesOuterShape** (6 properties) — documented inline below
- **smallMultiplesOverFlow** (2 properties) — documented inline below
- **value** (15 properties) — documented inline below

## Properties

### background

See [./_shared/containers-background.md](./_shared/containers-background.md).

### border

See [./_shared/containers-border.md](./_shared/containers-border.md).

### divider

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `dividerColor` | `object` | — | — | no | Color of dividers between sections (used in some visual types instead of divider.color). |
| `dividerIgnorePadding` | `boolean` | — | — | no | Ignore padding |
| `dividerLineStyle` | `string` | — | — | no | Line style |
| `dividerTransparency` | `number` | — | — | no | Transparency |
| `dividerWidth` | `number` | — | — | no | Width |
| `show` | `boolean` | — | — | no | Show |

### dropShadow

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Angle |
| `color` | `object` | — | — | no | Color of the drop shadow. Note: drop shadows create accessibility issues for users with vestibular disabilities -- prefer flat layout with background contrast. |
| `position` | `string` | — | — | no | Shadow position |
| `preset` | `string` | — | — | no | Preset |
| `shadowBlur` | `number` | — | — | no | Blur |
| `shadowDistance` | `number` | — | — | no | Distance |
| `shadowSpread` | `number` | — | — | no | Size |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### general

See [./_shared/containers-general.md](./_shared/containers-general.md).

### lockAspect

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `show` | `boolean` | — | — | no | Show |

### padding

See [./_shared/containers-padding.md](./_shared/containers-padding.md).

### spacing

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `verticalSpacing` | `integer` | — | — | no | Space between label and value |

### stylePreset

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `name` | `string` | — | — | no | Style |

### subTitle

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `alignment` | `string` | — | — | no | Horizontal alignment of the subtitle text. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no | Font color for the subtitle. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for the subtitle text (6-45pt). |
| `heading` | `string` | — | — | no | Set the heading tag for your visual titles to help with screen reader accessibility. This won't impact the visual styling of your titles. |
| `italic` | `boolean` | — | — | no | Italic |
| `show` | `boolean` | — | — | no | Show |
| `text` | `string` | — | — | no | The name of the visual |
| `titleWrap` | `boolean` | — | — | no | Word wrap |
| `underline` | `boolean` | — | — | no | Underline |

### title

See [./_shared/containers-title.md](./_shared/containers-title.md).

### visualHeader

See [./_shared/containers-visualHeader.md](./_shared/containers-visualHeader.md).

### visualHeaderTooltip

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `background` | `object` | — | — | no | Background color for the visual header tooltip popup. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontFamily` | `string` | — | — | no | Font family for the tooltip |
| `fontSize` | `number` | — | — | no | Font size for visual header tooltip text. |
| `italic` | `boolean` | — | — | no | Italic |
| `section` | `string` | — | — | no | Choose the page you want to use for your tooltip |
| `text` | `string` | — | — | no | Write the custom tooltip that will show up when a report page tooltip hasn't been chosen or isn't supported |
| `themedBackground` | `object` | — | — | no | Theme-aware background color for header tooltips. |
| `themedTitleFontColor` | `object` | — | — | no | Theme-aware font color for header tooltip title. |
| `titleFontColor` | `object` | — | — | no | Font color for header tooltip title text. |
| `transparency` | `number` | — | — | no | Set transparency for the tooltip background |
| `type` | `string` | — | — | no | Type |
| `underline` | `boolean` | — | — | no | Underline |

### visualLink

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `bookmark` | `string` | — | — | no | Choose which bookmark will open when this element is selected. |
| `dataFunction` | `object` | — | — | no |  |
| `disabledTooltip` | `string` | — | — | no | Disabled tooltip |
| `drillthroughSection` | `string` | — | — | no | Select a single destination or use conditional formatting for custom destination logic, such as multi-destination support. |
| `enabledTooltip` | `string` | — | — | no | Enabled tooltip |
| `navigationSection` | `string` | — | — | no | Select a single destination or use conditional formatting for custom destination logic, such as multi-destination support. |
| `show` | `boolean` | — | — | no | Show |
| `showDefaultTooltip` | `boolean` | — | — | no | Show tooltips |
| `suppressDefaultTooltip` | `boolean` | — | — | no | No tooltips |
| `tooltip` | `string` | — | — | no | Write the custom tooltip that will show when users hover over this element. |
| `tooltipPlaceholderText` | `string` | — | — | no | Placeholder for tooltip |
| `type` | `string` | — | — | no | Choose what action will occur when this element is selected. |
| `webUrl` | `string` | — | — | no | Web URL |

### visualTooltip

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `actionFontColor` | `object` | — | — | no | Font color for tooltip action text (e.g., drill-through links). |
| `background` | `object` | — | — | no | Background color for the tooltip popup. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontFamily` | `string` | — | — | no | Font family for the tooltip |
| `fontSize` | `number` | — | — | no | Font size for tooltip text. |
| `italic` | `boolean` | — | — | no | Italic |
| `section` | `string` | — | — | no | Choose the page you want to use for your tooltip |
| `show` | `boolean` | — | — | no | Show |
| `showActionsInTooltips` | `boolean` | — | — | no | Add drill options within tooltips |
| `themedBackground` | `object` | — | — | no | Theme-aware background color for tooltips. |
| `themedTitleFontColor` | `object` | — | — | no | Theme-aware font color for tooltip title. |
| `themedValueFontColor` | `object` | — | — | no | Theme-aware font color for tooltip values. |
| `titleFontColor` | `object` | — | — | no | Font color for tooltip title text. |
| `transparency` | `number` | — | — | no | Set transparency for the tooltip background |
| `type` | `string` | — | — | no | Allows report pages to be used as tooltips for this visual |
| `underline` | `boolean` | — | — | no | Underline |
| `valueFontColor` | `object` | — | — | no | Font color for tooltip value text. |

### accentBar

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `color` | `object` | — | — | no |  |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |
| `width` | `number` | — | — | no | Width |

### cardCalloutArea

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backgroundFillColor` | `object` | — | — | no |  |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `paddingBottom` | `integer` | — | — | no | Bottom margin |
| `paddingIndividual` | `boolean` | — | — | no | Individual padding |
| `paddingLeft` | `integer` | — | — | no | Left margin |
| `paddingRight` | `integer` | — | — | no | Right margin |
| `paddingTop` | `integer` | — | — | no | Top margin |
| `paddingUniform` | `integer` | — | — | no | Uniform padding |
| `rectangleRoundedCurve` | `integer` | — | — | no | Corner radius |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Individual corners |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |
| `show` | `boolean` | — | — | no | Show |

### cardImage

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `addBackground` | `boolean` | — | — | no | Add background |
| `altText` | `string` | — | — | no | Alt text |
| `backgroundFillColor` | `object` | — | — | no |  |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `image` | `object` | — | — | no |  |
| `imageBlur` | `integer` | — | — | no | Blur |
| `imageContrast` | `integer` | — | — | no | Contrast |
| `imageData` | `string` | — | — | no | Data |
| `imageDataFit` | `string` | — | — | no | Image fit |
| `imageEffects` | `boolean` | — | — | no | Image effects |
| `imageExposure` | `integer` | — | — | no | Exposure |
| `imageHeight` | `integer` | — | — | no | Image height |
| `imageSaturation` | `integer` | — | — | no | Saturation |
| `imageTransparency` | `number` | — | — | no | Background image transparency |
| `imageType` | `string` | — | — | no | Image source |
| `imageUrl` | `string` | — | — | no | Image URL |
| `imageUrlFit` | `string` | — | — | no | Image fit |
| `paddingBottom` | `integer` | — | — | no | Bottom margin |
| `paddingIndividual` | `boolean` | — | — | no | Individual padding |
| `paddingLeft` | `integer` | — | — | no | Left margin |
| `paddingRight` | `integer` | — | — | no | Right margin |
| `paddingTop` | `integer` | — | — | no | Top margin |
| `paddingUniform` | `integer` | — | — | no | Uniform padding |
| `rectangleRoundedCurve` | `integer` | — | — | no | Corner radius |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Individual corners |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |
| `show` | `boolean` | — | — | no | Show |

### fillCustom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `fillColor` | `object` | — | — | no |  |
| `image` | `object` | — | — | no |  |
| `imageBlur` | `integer` | — | — | no | Blur |
| `imageContrast` | `integer` | — | — | no | Contrast |
| `imageData` | `string` | — | — | no | Data |
| `imageEffects` | `boolean` | — | — | no | Image effects |
| `imageExposure` | `integer` | — | — | no | Exposure |
| `imageFit` | `string` | — | — | no | Image fit |
| `imageHeight` | `integer` | — | — | no | Image height |
| `imageSaturation` | `integer` | — | — | no | Saturation |
| `imageShow` | `boolean` | — | — | no | Add image |
| `imageSplitTransparency` | `boolean` | — | — | no |  |
| `imageTransparency` | `number` | — | — | no | Background color transparency |
| `imageType` | `string` | — | — | no | Image source |
| `imageUrl` | `string` | — | — | no | Image URL |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Background color transparency |

### glowCustom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `angle` | `number` | — | — | no | Angle |
| `color` | `object` | — | — | no |  |
| `glowDistance` | `number` | — | — | no | Distance |
| `glowPositionPreset` | `string` | — | — | no | Preset |
| `glowSpread` | `number` | — | — | no | Size |
| `position` | `string` | — | — | no | Offset |
| `shadowBlur` | `number` | — | — | no | Blur |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### grid

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no | Color of gridlines in table/matrix visuals. |
| `show` | `boolean` | — | — | no | Show |
| `style` | `string` | — | — | no | Line style |
| `transparency` | `number` | — | — | no | Transparency |
| `width` | `number` | — | — | no | Width |

### image

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `altText` | `string` | — | — | no | Alt text |
| `blur` | `integer` | — | — | no | Blur |
| `contrast` | `integer` | — | — | no | Contrast |
| `exposure` | `integer` | — | — | no | Exposure |
| `fit` | `string` | — | — | no | Image fit |
| `fixedSize` | `boolean` | — | — | no | Image height |
| `horizontalAlignment` | `string` | — | — | no |  |
| `image` | `object` | — | — | no |  |
| `imageAreaSize` | `integer` | — | — | no | Image area size |
| `imageData` | `string` | — | — | no | Data |
| `imageEffects` | `boolean` | — | — | no | Image effects |
| `imageType` | `string` | — | — | no | Image type |
| `imageUrl` | `string` | — | — | no | Image URL |
| `padding` | `integer` | — | — | no | Space between image and callout |
| `position` | `string` | — | — | no | Position |
| `rectangleRoundedCurve` | `integer` | — | — | no | Corner radius |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Individual corners |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |
| `saturation` | `integer` | — | — | no | Saturation |
| `show` | `boolean` | — | — | no | Show |
| `size` | `integer` | — | — | no | Size |
| `transparency` | `number` | — | — | no | Background color transparency |
| `verticalAlignment` | `string` | — | — | no |  |

### label

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `alignBaselines` | `boolean` | — | — | no | Align baselines |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `heading` | `string` | — | — | no | Heading |
| `horizontalAlignment` | `string` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `matchValueAlignment` | `boolean` | — | — | no | Match value alignment |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `text` | `string` | — | — | no | Text |
| `textWrap` | `boolean` | — | — | no | Text wrap |
| `transparency` | `number` | — | — | no | Transparency |
| `underline` | `boolean` | — | — | no | Underline |

### layout

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `alignment` | `string` | — | — | no |  |
| `autoGrid` | `boolean` | — | — | no | Autogrid |
| `backgroundFillColor` | `object` | — | — | no |  |
| `backgroundImage` | `object` | — | — | no |  |
| `backgroundImageBlur` | `integer` | — | — | no | Blur |
| `backgroundImageContrast` | `integer` | — | — | no | Contrast |
| `backgroundImageData` | `string` | — | — | no | Data |
| `backgroundImageEffects` | `boolean` | — | — | no | Image effects |
| `backgroundImageExposure` | `integer` | — | — | no | Exposure |
| `backgroundImageFit` | `string` | — | — | no | Image fit |
| `backgroundImageHeight` | `integer` | — | — | no | Image height |
| `backgroundImageSaturation` | `integer` | — | — | no | Saturation |
| `backgroundImageShow` | `boolean` | — | — | no | Add image |
| `backgroundImageTransparency` | `number` | — | — | no | Image transparency |
| `backgroundImageType` | `string` | — | — | no | Image source |
| `backgroundImageUrl` | `string` | — | — | no | Image URL |
| `backgroundShow` | `boolean` | — | — | no | Show |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `borderColor` | `object` | — | — | no |  |
| `borderStyle` | `string` | — | — | no | Line style |
| `borderTransparency` | `number` | — | — | no | Transparency |
| `borderWidth` | `number` | — | — | no | Width |
| `bottomOuterMargin` | `integer` | — | — | no | Bottom padding |
| `calloutSize` | `number` | — | — | no | Callout size |
| `cellPadding` | `integer` | — | — | no | Space between cards |
| `collage` | `string` | — | — | no | Collage |
| `columnCount` | `integer` | — | — | no | Columns |
| `columnPadding` | `integer` | — | — | no | Space between columns |
| `contentOrder` | `string` | — | — | no | Order |
| `customizeLines` | `boolean` | — | — | no | Customize lines |
| `customizePadding` | `boolean` | — | — | no | Customize spacing |
| `fixedHeight` | `integer` | — | — | no | Height |
| `fixedWidth` | `integer` | — | — | no | Width |
| `gridlineColor` | `object` | — | — | no |  |
| `gridlineStyle` | `string` | — | — | no | Line style |
| `gridlineTransparency` | `number` | — | — | no | Transparency |
| `gridlineWidth` | `number` | — | — | no | Width |
| `leftOuterMargin` | `integer` | — | — | no | Left padding |
| `lineColor` | `object` | — | — | no |  |
| `lineStyle` | `string` | — | — | no | Line style |
| `lineTransparency` | `number` | — | — | no | Transparency |
| `lineWidth` | `number` | — | — | no | Width |
| `maxTiles` | `integer` | — | — | no | Max cards shown |
| `orientation` | `integer` | — | — | no | Arrangement |
| `paddingIndividual` | `boolean` | — | — | no | Individual padding |
| `paddingUniform` | `integer` | — | — | no | Uniform padding |
| `rectangleRoundedCurve` | `integer` | — | — | no | Rounded corners |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Individual corners |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |
| `rightOuterMargin` | `integer` | — | — | no | Right padding |
| `rowCount` | `integer` | — | — | no | Rows |
| `rowPadding` | `integer` | — | — | no | Space between rows |
| `showFixedSize` | `boolean` | — | — | no | Fixed size |
| `style` | `string` | — | — | no | Style |
| `topOuterMargin` | `integer` | — | — | no | Top padding |

### outline

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `lineColor` | `object` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Set transparency for line color |
| `weight` | `number` | — | — | no | Thickness of the outline in pixels |

### overFlow

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `overFlowDirection` | `integer` | — | — | no | Overflow direction |
| `overFlowStyle` | `integer` | — | — | no | Overflow style |

### referenceLabel

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backgroundColor` | `object` | — | — | no |  |
| `backgroundShow` | `boolean` | — | — | no | Show |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `formatString` | `string` | — | — | no |  |
| `paddingBottom` | `integer` | — | — | no | Bottom margin |
| `paddingIndividual` | `boolean` | — | — | no | Individual padding |
| `paddingLeft` | `integer` | — | — | no | Left margin |
| `paddingRight` | `integer` | — | — | no | Right margin |
| `paddingTop` | `integer` | — | — | no | Top margin |
| `paddingUniform` | `integer` | — | — | no | Uniform padding |
| `rectangleRoundedCurve` | `integer` | — | — | no | Corner radius |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Individual corners |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |
| `value` | `string | number | integer | boolean` | — | — | no | Add label |

### referenceLabelDetail

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `customFormatString` | `string` | — | — | no | Enter a custom number format for your callout. |
| `detailBackgroundColor` | `object` | — | — | no |  |
| `detailBold` | `boolean` | — | — | no | Bold |
| `detailDisplayUnits` | `integer` | — | — | no | Select the units (millions, billions, etc.) |
| `detailFontColor` | `object` | — | — | no |  |
| `detailFontFamily` | `string` | — | — | no | Font family |
| `detailFontSize` | `number` | — | — | no |  |
| `detailItalic` | `boolean` | — | — | no | Italic |
| `detailPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `detailTransparency` | `number` | — | — | no | Transparency |
| `detailUnderline` | `boolean` | — | — | no | Underline |
| `detailValue` | `string | number | integer | boolean` | — | — | no | Field |
| `formatString` | `string` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |
| `showBlankAs` | `string` | — | — | no | Show blank as |
| `textWrap` | `boolean` | — | — | no | Text wrap |

### referenceLabelLayout

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `arrangement` | `string` | — | — | no | Arrangement |
| `customizePadding` | `boolean` | — | — | no | Customize outer padding |
| `horizontalAlignment` | `string` | — | — | no |  |
| `labelSpace` | `number` | — | — | no | Space between labels |
| `outerPadding` | `number` | — | — | no | Outer padding |
| `paddingAfterDivider` | `number` | — | — | no | Padding after divider line |
| `paddingBeforeDivider` | `number` | — | — | no | Padding before divider line |
| `position` | `string` | — | — | no | Arrangement |
| `style` | `string` | — | — | no | Style |
| `valueArea` | `number` | — | — | no | Value area |
| `verticalAlignment` | `string` | — | — | no |  |

### referenceLabelTitle

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `show` | `boolean` | — | — | no | Show |
| `textWrap` | `boolean` | — | — | no | Text wrap |
| `titleBold` | `boolean` | — | — | no | Bold |
| `titleContentType` | `string` | — | — | no | Content |
| `titleFontColor` | `object` | — | — | no |  |
| `titleFontFamily` | `string` | — | — | no | Font family |
| `titleFontSize` | `number` | — | — | no |  |
| `titleItalic` | `boolean` | — | — | no | Italic |
| `titleText` | `string` | — | — | no | Text |
| `titleTransparency` | `number` | — | — | no | Transparency |
| `titleUnderline` | `boolean` | — | — | no | Underline |

### referenceLabelValue

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `customFormatString` | `string` | — | — | no | Enter a custom number format for your callout. |
| `show` | `boolean` | — | — | no | Show |
| `showBlankAs` | `string` | — | — | no | Show blank as |
| `textWrap` | `boolean` | — | — | no | Text wrap |
| `valueBold` | `boolean` | — | — | no | Bold |
| `valueDisplayUnits` | `integer` | — | — | no | Select the units (millions, billions, etc.) |
| `valueFontColor` | `object` | — | — | no |  |
| `valueFontFamily` | `string` | — | — | no | Font family |
| `valueFontSize` | `number` | — | — | no |  |
| `valueItalic` | `boolean` | — | — | no | Italic |
| `valuePrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `valueTransparency` | `number` | — | — | no | Transparency |
| `valueUnderline` | `boolean` | — | — | no | Underline |

### rotation

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Rotation |
| `shapeAngle` | `integer` | — | — | no | Shape rotation |
| `textAngle` | `integer` | — | — | no | Text rotation |

### shadowCustom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `angle` | `number` | — | — | no | Angle |
| `color` | `object` | — | — | no | Color of custom shadow effect. |
| `position` | `string` | — | — | no | Offset |
| `shadowBlur` | `number` | — | — | no | Blur |
| `shadowDistance` | `number` | — | — | no | Distance |
| `shadowPositionPreset` | `string` | — | — | no | Preset |
| `shadowSpread` | `number` | — | — | no | Size |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### shapeCustomRectangle

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `rectangleRoundedCurve` | `integer` | — | — | no | Rounded corners |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Customize style |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |
| `tabCutCornerSnipSizeBottom` | `integer` | — | — | no | Size of bottom snips |
| `tabCutCornerSnipSizeCustomStyle` | `boolean` | — | — | no | Customize style |
| `tabCutCornerSnipSizeLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `tabCutCornerSnipSizeLeftTop` | `integer` | — | — | no | Top left corner |
| `tabCutCornerSnipSizeRightBottom` | `integer` | — | — | no | Bottom right corner |
| `tabCutCornerSnipSizeRightTop` | `integer` | — | — | no | Top right corner |
| `tabCutCornerSnipSizeTop` | `integer` | — | — | no | Size of top snips |
| `tileShape` | `string` | — | — | no | Shape |

### smallMultiplesAccentBar

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no |  |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |
| `width` | `number` | — | — | no | Width |

### smallMultiplesBorder

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `gridlineColor` | `object` | — | — | no |  |
| `gridlineShow` | `boolean` | — | — | no | Show |
| `gridlineStyle` | `string` | — | — | no | Line style |
| `gridlineTransparency` | `number` | — | — | no | Transparency |
| `gridlineWidth` | `number` | — | — | no | Width |

### smallMultiplesCellBackGround

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backgroundFillColor` | `object` | — | — | no |  |
| `backgroundImage` | `object` | — | — | no |  |
| `backgroundImageBlur` | `integer` | — | — | no | Blur |
| `backgroundImageContrast` | `integer` | — | — | no | Contrast |
| `backgroundImageData` | `string` | — | — | no | Data |
| `backgroundImageDataFit` | `string` | — | — | no | Image fit |
| `backgroundImageEffects` | `boolean` | — | — | no | Image effects |
| `backgroundImageExposure` | `integer` | — | — | no | Exposure |
| `backgroundImageHeight` | `integer` | — | — | no | Image height |
| `backgroundImageSaturation` | `integer` | — | — | no | Saturation |
| `backgroundImageShow` | `boolean` | — | — | no | Add image |
| `backgroundImageTransparency` | `number` | — | — | no | Background image transparency |
| `backgroundImageType` | `string` | — | — | no | Image source |
| `backgroundImageUrl` | `string` | — | — | no | Image URL |
| `backgroundImageUrlFit` | `string` | — | — | no | Image fit |
| `backgroundShow` | `boolean` | — | — | no | Show |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |

### smallMultiplesGrid

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `gridlineColor` | `object` | — | — | no |  |
| `gridlineRegion` | `string` | — | — | no | Gridlines |
| `gridlineShow` | `boolean` | — | — | no | Show |
| `gridlineStyle` | `string` | — | — | no | Line style |
| `gridlineTransparency` | `number` | — | — | no | Transparency |
| `gridlineWidth` | `number` | — | — | no | Width |

### smallMultiplesHeader

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backgroundColor` | `object` | — | — | no |  |
| `backgroundImage` | `object` | — | — | no |  |
| `backgroundImageBlur` | `integer` | — | — | no | Blur |
| `backgroundImageContrast` | `integer` | — | — | no | Contrast |
| `backgroundImageData` | `string` | — | — | no | Data |
| `backgroundImageEffects` | `boolean` | — | — | no | Image effects |
| `backgroundImageExposure` | `integer` | — | — | no | Exposure |
| `backgroundImageFit` | `string` | — | — | no | Image fit |
| `backgroundImageHeight` | `integer` | — | — | no | Image height |
| `backgroundImageSaturation` | `integer` | — | — | no | Saturation |
| `backgroundImageShow` | `boolean` | — | — | no | Add image |
| `backgroundImageTransparency` | `number` | — | — | no | Image transparency |
| `backgroundImageType` | `string` | — | — | no | Image source |
| `backgroundImageUrl` | `string` | — | — | no | Image URL |
| `backgroundShow` | `boolean` | — | — | no | Show |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `bold` | `boolean` | — | — | no | Bold |
| `bottomMargin` | `integer` | — | — | no | Bottom padding |
| `customFormatString` | `string` | — | — | no | Enter a custom number format for your callout. |
| `displayUnits` | `integer` | — | — | no | Select the units (millions, billions, etc.) |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `horizontalAlignment` | `string` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `leftMargin` | `integer` | — | — | no | Left padding |
| `paddingIndividual` | `boolean` | — | — | no | Individual padding |
| `paddingUniform` | `integer` | — | — | no | Uniform padding |
| `precision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `rightMargin` | `integer` | — | — | no | Right padding |
| `showBlankAs` | `string` | — | — | no | Show blank as |
| `textWrap` | `boolean` | — | — | no | Text wrap |
| `topMargin` | `integer` | — | — | no | Top padding |
| `transparency` | `number` | — | — | no | Transparency |
| `underline` | `boolean` | — | — | no | Underline |
| `verticalAlignment` | `string` | — | — | no |  |

### smallMultiplesLayout

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoGrid` | `boolean` | — | — | no | Autogrid |
| `cellPadding` | `integer` | — | — | no | Space between cards |
| `columnCount` | `integer` | — | — | no | Columns |
| `columnPadding` | `integer` | — | — | no | Space between columns |
| `customizePadding` | `boolean` | — | — | no | Customize spacing |
| `fixNumberOfTiles` | `boolean` | — | — | no | Autogrid |
| `headerPosition` | `string` | — | — | no | Position |
| `maxHeaderSize` | `integer` | — | — | no | The maximum percent of the visual allowed for the small multiple header |
| `maxSmallMultiples` | `integer` | — | — | no | Rows |
| `orientation` | `integer` | — | — | no | Arrangement |
| `rowCount` | `integer` | — | — | no | Rows |
| `rowPadding` | `integer` | — | — | no | Space between rows |
| `style` | `string` | — | — | no | Style |
| `titleOrientation` | `integer` | — | — | no | Orientation |

### smallMultiplesOuterShape

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `rectangleRoundedCurve` | `integer` | — | — | no | Rounded corners |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Individual corners |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |

### smallMultiplesOverFlow

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `overFlowDirection` | `integer` | — | — | no | Overflow direction |
| `overFlowStyle` | `integer` | — | — | no | Overflow style |

### value

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `bold` | `boolean` | — | — | no | Bold |
| `customFormatString` | `string` | — | — | no | Enter a custom number format for your callout. |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `horizontalAlignment` | `string` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `labelDisplayUnits` | `integer` | — | — | no | Select the units (millions, billions, etc.) |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `show` | `boolean` | — | — | no | Show |
| `showBlankAs` | `string` | — | — | no | Show blank as |
| `textWrap` | `boolean` | — | — | no | Text wrap |
| `transparency` | `number` | — | — | no | Transparency |
| `underline` | `boolean` | — | — | no | Underline |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "cardVisual",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — ANALYSIS - OWNERSHIP SIMULATION V3 CLEAN (visual delta-pct-cardVisual-c48c2f)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Data` = `[Net Asset Value Δ% (Simulation)]` — one scenario-delta measure.
- **Position:** `(1424, 10) 448×130` — top-right card in the 4-up overview strip at y=10.
- **vCO title:** `"Delta Pct"` (short label; the measure carries the Δ% formatting).
- **Objects present:** `value`, `layout`, `label`, `referenceLabel*` family, `categoryLabels`.
- **Value formatting:** explicit `fontSize`, `bold`, `fontColor`, `horizontalAlignment` — conservative, no callout blow-out. Consistent with the rest of the top-strip row.
- **No subtitle hide:** keeps the raw measure name visible as subtitle — matches the vCO.title being a short abbreviation, not a full KPI name.
<!-- /MANUAL -->

### R0101 — DIR PARTICIPATIONS - DETAIL (visual c432543ba2fab21d4d51)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Data` = 4 measures bound in a single cardVisual (`[Performance PV2 AbsVal]`, `[Return PV2 AbsVal]`, `[Performance PV3 AbsVal]`, `[Return PV3 AbsVal]`) — multi-measure card pattern, rendered as a 2×2 mini-grid.
- **Position:** `(850, 0) 1030×110` — right-side wide strip on the DIR PARTICIPATIONS - DETAIL header.
- **vCO title:** `"Net Asset Value"` (shared title across the 4 perf/return cells).
- **Rich container stack:** full chrome — `title`, `padding`, `background`, `border`, `visualHeader` — consistent with the repeat-detail pages.
- **Value formatting:** adds `labelDisplayUnits` on top of the standard `fontSize`/`bold`/`fontColor`/`horizontalAlignment` set — units abbreviation (K/M) for the absolute-value measures.
- **Advanced objects:** `shadowCustom`, `shapeCustomRectangle`, `outline`, `fillCustom`, `divider`, `image` — custom shape + divider between the 4 sub-cells; this is where the 2×2 layout visuals come from.
<!-- /MANUAL -->

### R0101 — DIR PARTICIPATIONS - DETAIL (visual f208eccac1e0f3ecd659)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Data` = 4 NAV-family measures (`[Net Asset Value]`, `[NAV Debt - Endebted Asset]`, `[Debt Ratio - Endebted Asset]`, `[Net NAV (NAV - Debt) - Endebted Asset]`) — sibling to the perf/return 2×2 card on the same page.
- **Position:** `(0, 0) 850×110` — left-side wide strip, paired with the c432543… perf card to the right.
- **vCO title:** `"Net Asset Value"` — same title as its sibling; visual groups them.
- **Same advanced object stack** as c432543…: custom shape + divider + image for the 2×2 cell divisions.
- **Pattern:** pair these two cards side-by-side to form the page's KPI header row — do not author either one without the other, or the header becomes lopsided.
<!-- /MANUAL -->

### R0101 — PRIVATE FUNDS - DETAIL (visual 1c9f0642505458041108)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Data` = **18 measures** in a single cardVisual — the densest multi-measure pattern in R0101. Perf + return + DPI/RVPI/TVPI + calls/distributions + expected future + IRR + committed + max callable. Rendered as a 3×6 grid of mini-cards.
- **Position:** `(180, 0) 1700×210` — spans almost the full page width; double-height (210 px) to accommodate 3 rows of cell labels + values.
- **vCO title:** `"Net Asset Value"` (again — the private-funds detail page reuses the label but the measure set is PE-specific).
- **Cell sizing:** at 1700 × 210 with 18 cells in a 3×6 grid, each cell is ~283 × 70 — the layout calloutSize must be ≤ 8D to keep the value bold but not overflow.
- **Extract is 3643 lines** — dominated by the per-measure `properties[]` repetitions. Do NOT hand-author; copy the objects block wholesale from this extract if you need a sibling card.
- **Pattern warning:** the 18-measure dense grid is a R0101-specific pattern; for most reports a 4-card or 6-card row is more readable. Reserve this density for a PE detail page where the user expects to see the full J-curve metric family at a glance.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Two font multipliers that compound

`cardVisual` has two separate font dials that multiply at render time:

- `visual.objects.value[0].properties.fontSize` — declared font size (e.g. `"18D"`)
- `visual.objects.layout[0].properties.calloutSize` — scale multiplier (e.g. `"14D"`)

Observed render size ≈ `value.fontSize × (calloutSize / 10)` roughly. Setting `value.fontSize = "18D"` with `calloutSize = "14D"` produces ~25 pt on screen; both cranked up produces absurdly huge numbers that overflow the card.

Reference sizing for 4 cards in a row across 1920 px (each ~440 px wide, 130 px tall):

| Intent | `value.fontSize` | `layout.calloutSize` | Render |
|---|---|---|---|
| Conservative (always fits) | `"14D"` | `"8D"` | ~16 pt — safe for 4-digit + M suffix |
| Standard readable | `"16D"` | `"10D"` | ~20 pt — comfortable for 6-char values |
| Maximum before overflow | `"18D"` | `"12D"` | ~26 pt — test; often too big |
| Do not use | `"22D"+` with any callout | — | Overflow guaranteed |

Start conservative and screenshot to verify before assuming a size fits. `horizontalAlignment = "'left'"` compounds overflow risk because the number starts at the left edge and runs off the right — prefer `"'center'"` unless the longest possible value has been checked.

### Hide the subtitle when `visualContainerObjects.title` is set

`label[0].properties.show` defaults to `true`, which prints the measure's display name as a subtitle under the callout. If the `visualContainerObjects.title.text` already names the KPI (e.g. `"NAV Actual"`), the subtitle duplicates it with the raw measure name (e.g. `"Net Asset Value (Simul Switch)"`). Set `label.show = "false"` whenever `vCO.title` is set.

### KPI card value height requirement

For a value at 24 pt bold with a visible label subtitle, height must be ≥ 130 px. At 100–110 px, Power BI crops the value text at the bottom and only the top half of the number shows. Rule of thumb:

```text
min_height = 40 (title/label) + value_fontSize × 3.5 + 20 (padding)
```

For 24 pt value: min 130 px. For 18 pt value: min 110 px.

### `fontColor` hex vs. `ThemeDataColor`

Setting `fontColor` on `value[0].properties` via `ThemeDataColor` with `selector: {"id": "default"}` renders as **white** (theme colour 0 with the `default` selector is interpreted as foreground-on-data, not text colour). For reliable text colouring, use a hex literal with no selector:

```json
"fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#1A1A3E'"}}}}}
```
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-cardvisual](../GUIDE.md#cli-cardvisual)
- JSON format: [../GUIDE.md#json-cardvisual](../GUIDE.md#json-cardvisual)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- CLAUDE.md §5 — cardVisual sizing rules (two-dial compounding, 130×440 default floor).
- docs/design-principles/cards-and-kpis.md — KPI row composition and hierarchy.
- docs/GUIDE.md Part VI §6 — R0101 4-card top-strip layout as reference template.
<!-- /MANUAL -->
