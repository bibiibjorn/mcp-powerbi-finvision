# listSlicer

## Overview

<!-- MANUAL: overview -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Tooltips` | no | yes | Column, Measure |
| `Values` | yes | yes | Column |

## Containers

- **background** (5 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (4 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (5 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (2 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **lockAspect** (1 properties) — documented inline below
- **padding** (6 properties) — see [./_shared/containers-padding.md](./_shared/containers-padding.md)
- **spacing** (1 properties) — documented inline below
- **stylePreset** (1 properties) — documented inline below
- **subTitle** (11 properties) — documented inline below
- **title** (12 properties) — see [./_shared/containers-title.md](./_shared/containers-title.md)
- **visualHeader** (25 properties) — see [./_shared/containers-visualHeader.md](./_shared/containers-visualHeader.md)
- **visualHeaderTooltip** (13 properties) — documented inline below
- **visualLink** (13 properties) — documented inline below
- **visualTooltip** (17 properties) — documented inline below
- **accentBar** (6 properties) — documented inline below
- **actionState** (5 properties) — documented inline below
- **data** (1 properties) — documented inline below
- **expansionIcon** (7 properties) — documented inline below
- **fillCustom** (17 properties) — documented inline below
- **glowCustom** (9 properties) — documented inline below
- **icon** (10 properties) — documented inline below
- **image** (23 properties) — documented inline below
- **label** (16 properties) — documented inline below
- **layout** (57 properties) — documented inline below
- **outline** (5 properties) — documented inline below
- **overFlow** (2 properties) — documented inline below
- **rotation** (3 properties) — documented inline below
- **selection** (4 properties) — documented inline below
- **selectionIcon** (11 properties) — documented inline below
- **shadowCustom** (9 properties) — documented inline below
- **shapeCustomRectangle** (14 properties) — documented inline below
- **value** (17 properties) — documented inline below

## Properties

### background

See [./_shared/containers-background.md](./_shared/containers-background.md).

### border

See [./_shared/containers-border.md](./_shared/containers-border.md).

### divider

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no | Color of the divider line between header and content areas. |
| `ignorePadding` | `boolean` | — | — | no | Ignore padding |
| `show` | `boolean` | — | — | no | Show |
| `style` | `string` | — | — | no | Line style |
| `width` | `number` | — | — | no | Width |

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

### actionState

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `IsButtonsAdvanced` | `boolean` | — | — | no | Advanced |
| `isCalloutValuesAdvanced` | `boolean` | — | — | no | Advanced |
| `isExpansionIconAdvanced` | `boolean` | — | — | no | Advanced |
| `isImageAdvanced` | `boolean` | — | — | no | Advanced |
| `isSelectionIconAdvanced` | `boolean` | — | — | no | Advanced |

### data

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `isInvertedSelectionMode` | `boolean` | — | — | no | Invert selection -- exclude selected items instead of including them. |

### expansionIcon

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `color` | `object` | — | — | no |  |
| `position` | `string` | — | — | no | Position |
| `size` | `integer` | — | — | no | Size |
| `spacing` | `integer` | — | — | no | Spacing |
| `transparency` | `number` | — | — | no | Transparency |
| `verticalAlignment` | `string` | — | — | no |  |

### fillCustom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `blur` | `integer` | — | — | no | Blur |
| `contrast` | `integer` | — | — | no | Contrast |
| `effects` | `boolean` | — | — | no | Image effects |
| `exposure` | `integer` | — | — | no | Exposure |
| `field` | `string` | — | — | no | Field |
| `fillColor` | `object` | — | — | no |  |
| `fit` | `string` | — | — | no | Image fit |
| `height` | `integer` | — | — | no | Image height |
| `image` | `object` | — | — | no |  |
| `imageShow` | `boolean` | — | — | no | Add image |
| `imageSplitTransparency` | `boolean` | — | — | no |  |
| `imageTransparency` | `number` | — | — | no | Background color transparency |
| `saturation` | `integer` | — | — | no | Saturation |
| `show` | `boolean` | — | — | no | Show |
| `sourceType` | `string` | — | — | no | Image source |
| `transparency` | `number` | — | — | no | Background color transparency |
| `url` | `string` | — | — | no | Image URL |

### glowCustom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Angle |
| `color` | `object` | — | — | no |  |
| `glowDistance` | `number` | — | — | no | Distance |
| `glowPositionPreset` | `string` | — | — | no | Preset |
| `glowSpread` | `number` | — | — | no | Size |
| `position` | `string` | — | — | no | Offset |
| `shadowBlur` | `number` | — | — | no | Blur |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### icon

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `altText` | `string` | — | — | no | Alt text |
| `icon` | `object` | — | — | no |  |
| `iconType` | `string` | — | — | no | Icon type |
| `iconUrl` | `string` | — | — | no | Image URL |
| `padding` | `integer` | — | — | no | Padding |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `size` | `integer` | — | — | no | Size |
| `transparency` | `number` | — | — | no | Background color transparency |

### image

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `altText` | `string` | — | — | no | Alt text |
| `blur` | `integer` | — | — | no | Blur |
| `contrast` | `integer` | — | — | no | Contrast |
| `effects` | `boolean` | — | — | no | Image effects |
| `enhancedBlur` | `integer` | — | — | no | Blur |
| `enhancedFit` | `string` | — | — | no | Image fit |
| `enhancedSaturation` | `integer` | — | — | no | Saturation |
| `exposure` | `integer` | — | — | no | Exposure |
| `height` | `integer` | — | — | no | Image height |
| `ignorePadding` | `boolean` | — | — | no | Image ignores buttons padding and reaches the edges. |
| `image` | `string` | — | — | no | Field |
| `imageAreaSize` | `integer` | — | — | no | Image area size |
| `imageFile` | `object` | — | — | no |  |
| `imageFit` | `string` | — | — | no | Image fit |
| `padding` | `integer` | — | — | no | Space between image and callout |
| `position` | `string` | — | — | no | Position |
| `saturation` | `integer` | — | — | no | Saturation |
| `setAsBackGround` | `boolean` | — | — | no | Set as background |
| `show` | `boolean` | — | — | no | Show |
| `sourceType` | `string` | — | — | no | Image source |
| `transparency` | `number` | — | — | no | Background color transparency |
| `url` | `string` | — | — | no | Image URL |

### label

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `alignBaselines` | `boolean` | — | — | no | Align baselines |
| `bold` | `boolean` | — | — | no | Bold |
| `field` | `string | integer` | — | — | no | Field |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `heading` | `string` | — | — | no | Heading |
| `horizontalAlignment` | `string` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `matchValueAlignment` | `boolean` | — | — | no | Match value alignment |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `textWrap` | `boolean` | — | — | no | Text wrap |
| `transparency` | `number` | — | — | no | Transparency |
| `underline` | `boolean` | — | — | no | Underline |

### layout

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `alignment` | `string` | — | — | no |  |
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
| `columnCount` | `integer` | — | — | no | Columns |
| `columnPadding` | `integer` | — | — | no | Space between columns |
| `containerIndentation` | `boolean` | — | — | no | Indent button container |
| `customizeLines` | `boolean` | — | — | no | Customize lines |
| `customizePadding` | `boolean` | — | — | no | Customize spacing |
| `fixedButtonsCount` | `boolean` | — | — | no | Fixed number of buttons |
| `fixedHeight` | `integer` | — | — | no | Height |
| `fixedWidth` | `integer` | — | — | no | Width |
| `gridlineColor` | `object` | — | — | no |  |
| `gridlineStyle` | `string` | — | — | no | Line style |
| `gridlineTransparency` | `number` | — | — | no | Transparency |
| `gridlineWidth` | `number` | — | — | no | Width |
| `indentation` | `integer` | — | — | no | Indentation |
| `leftOuterMargin` | `integer` | — | — | no | Left padding |
| `lineColor` | `object` | — | — | no |  |
| `lineStyle` | `string` | — | — | no | Line style |
| `lineTransparency` | `number` | — | — | no | Transparency |
| `lineWidth` | `number` | — | — | no | Width |
| `maxTiles` | `integer` | — | — | no | Max cards shown |
| `orientation` | `integer` | — | — | no | Arrangement |
| `rectangleRoundedCurve` | `integer` | — | — | no | Rounded corners |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Individual corners |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |
| `reverseIndentation` | `boolean` | — | — | no | Reverse indentation |
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

### rotation

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Rotation |
| `shapeAngle` | `integer` | — | — | no | Shape rotation |
| `textAngle` | `integer` | — | — | no | Text rotation |

### selection

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `restrictToLeafNodes` | `boolean` | — | — | no | Enable this option to allow selection only at the last level (leaf nodes) of the hierarchy tree. This ensures that users can only select the most specific items, preventing selection of intermediate categories |
| `selectAllCheckboxEnabled` | `boolean` | — | — | no | Show "Select all" as an option in the slicer. This selects all the values in the slicer so you can unselect them one by one to create an "is not" type filter. |
| `singleSelect` | `boolean` | — | — | no | Single select |
| `strictSingleSelect` | `boolean` | — | — | no | Force Selection ensures only one item can be chosen at a time. If no item is selected, the first available will be automatically chosen. |

### selectionIcon

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `color` | `object` | — | — | no |  |
| `overlayPosition` | `string` | — | — | no | Position |
| `overlayToggle` | `boolean` | — | — | no | Overlay |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `showIconByState` | `boolean` | — | — | no | Show |
| `size` | `integer` | — | — | no | Size |
| `spacing` | `integer` | — | — | no | Horizontal gap |
| `transparency` | `number` | — | — | no | Transparency |
| `verticalAlignment` | `string` | — | — | no |  |

### shadowCustom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
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
| `text` | `string` | — | — | no | Text |
| `textWrap` | `boolean` | — | — | no | Text wrap |
| `transparency` | `number` | — | — | no | Transparency |
| `underline` | `boolean` | — | — | no | Underline |
| `verticalAlignment` | `string` | — | — | no |  |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "listSlicer",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-listslicer](../GUIDE.md#cli-listslicer)
- JSON format: [../GUIDE.md#json-listslicer](../GUIDE.md#json-listslicer)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
TODO: fill in during enrichment.
<!-- /MANUAL -->
