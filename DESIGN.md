---
name: AI in Campaigns dashboard
description: A compact campaign analysis dashboard in a modern US government visual style.
colors:
  primary: "#315e71"
  navy: "#253746"
  header-edge: "#7babb8"
  neutral-bg: "#f6f7f9"
  surface: "#fff"
  text: "#172638"
  muted: "#647184"
  line: "#e0e5eb"
  ai: "#ff5630"
  mixed: "#f59e0b"
  ai-assisted: "#f59e0b"
  human: "#22c55e"
  democratic: "#4480c7"
  republican: "#c66b6b"
  focus: "#7090ef"
  map-low: "#22c55e"
  map-high: "#ff5630"
  no-analysis: "#e5e8ed"
typography:
  headline:
    fontFamily: "Public Sans, ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "27px"
    fontWeight: 700
    letterSpacing: "-0.03em"
  title:
    fontFamily: "Public Sans, ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 620
    letterSpacing: "-0.015em"
  body:
    fontFamily: "Public Sans, ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
  label:
    fontFamily: "Public Sans, ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 550
  metric:
    fontFamily: "Public Sans, ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "29px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.03em"
rounded:
  fine: "2px"
  control: "3px"
  segment: "4px"
  panel: "5px"
spacing:
  sm: "8px"
  md: "12px"
  mobile-gutter: "18px"
  panel: "20px"
  wide-panel: "24px"
  metric: "26px"
components:
  navigation:
    backgroundColor: "{colors.navy}"
    textColor: "{colors.surface}"
    height: "76px"
    padding: "0 4.3%"
  label-no-analysis:
    backgroundColor: "#f1f4f7"
    textColor: "#67778c"
    rounded: "{rounded.segment}"
    padding: "4px 7px"
  button-export:
    backgroundColor: "transparent"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "9px 13px"
  button-report:
    backgroundColor: "{colors.surface}"
    textColor: "#425877"
    rounded: "{rounded.control}"
    padding: "6px 9px"
  button-segment-active:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.fine}"
    padding: "7px 15px"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "#33445b"
    rounded: "{rounded.control}"
    height: "37px"
    padding: "9px 26px 9px 10px"
  panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
---

# Design System: AI in Campaigns dashboard

## Overview

**Creative North Star: "Modern public service dashboard"**

A familiar data dashboard following the user’s modern US government website reference. Self-hosted Public Sans, navy navigation and blue active controls establish the visual identity; white panels and compact native controls keep the data easy to scan.

Depth is restrained. Thin dividers organize ordinary surfaces, while the report drawer and preserved PDF receive the strongest separation.

**Key Characteristics:**

- Public Sans throughout, with tabular numerals for measurements.
- Navy navigation, blue actions, white data surfaces.
- Compact controls and a responsive report drawer.

## Colors

### Primary

- **Public service blue:** actions and active chamber controls.
- **Navigation navy:** header and headings; the brighter header edge separates navigation from content.
- **Pangram AI orange-red / Mixed amber / Human green:** semantic colors. Darker Pangram inks keep percentage text readable. AI-assisted composition uses amber while retaining its own label. Candidate colors use the saved provider classification.
- **Map green / amber / orange-red:** continuous low-to-high AI percentage scale. No-analysis gray remains distinct from zero.

### Secondary

- **Democratic blue / Republican red:** small party markers beside written party names.

### Neutral

- **Cool page / White surface:** page ground and data panels.
- **Ink / Muted slate:** primary text and secondary labels.
- **Divider:** panel outlines and internal rules.
- **Focus blue:** visible keyboard outlines.

**The Separate Encodings Rule.** Party identity and analysis composition retain separate color roles and written labels.

## Typography

Use the self-hosted Public Sans variable font from `src/fonts/PublicSans-Variable.ttf`, with the frontmatter fallback stack. The implementation supports normal weights from 100 through 900 and uses `font-display: swap`.

Headlines use the headline role; panel headings use title. Dense table cells and control text are generally 12px, and table headings and filter labels are 11px. The brand is 18px at weight 700. Report headings are 22px at weight 700. Metrics use tabular numerals, as do percentages and word counts. On screens at or below 760px, the main heading becomes 25px, report heading 21px and metrics 27px.

## Layout

The centered main area has a 1560px maximum width and 4.3% side padding. A 76px header precedes filters, a four-column statistics strip, a two-column overview and the full-width candidate table. The overview uses a 1.85:1 map/comparison split with a 270px minimum comparison column.

At 1100px and below, side gutters become 28px, search occupies a full filter row and the overview ratio becomes 1.6:1. At 760px and below, gutters become 18px, the header is 64px tall, chamber controls fill a row, filters wrap into two columns, statistics become a two-by-two grid and the overview stacks vertically. The Words column is hidden while the table remains horizontally scrollable.

The fixed report drawer occupies `min(800px, 68vw)`, changes to `min(800px, 80vw)` at 1100px and fills the viewport width at 760px. Its PDF region scrolls independently. The map height cap rises from 340px to 380px at 1600px; mobile removes the cap.

## Elevation & Depth

Data panels are flat white surfaces defined by thin borders. Shadows are reserved for the report drawer, PDF page, map tooltip and load-error notice. The report drawer slides 25px into place over 180ms with an ease-out curve. Map fills/strokes and comparison widths transition over 180ms. Reduced-motion preference removes animations and transitions.

## Shapes

Use small, controlled corners: controls use the control radius; data panels use the panel radius; the segmented container uses the segment radius and its buttons use the fine radius. Comparison bars have fine corners, party dots are circular and the geographic outlines remain unmodified.

## Components

- **Navigation:** navy header, white brand, pale blue icon and cycle text; transparent outlined Export CSV button. Hover gives export a darker blue fill.
- **Chamber control:** pale neutral segmented track, transparent inactive buttons and solid blue active button. Active state is represented with `aria-pressed`.
- **Fields:** native selects and a compact bordered search field. Shared keyboard focus uses a 3px outline with a 3px offset; search uses a 2px focus-within outline with a 1px offset.
- **Statistics:** one bordered white strip, internal vertical rules and large tabular values. The AI statistic uses Pangram’s darker AI ink.
- **Map and comparisons:** Pangram data scale, white state borders, outlined hover/focus/selection and labeled horizontal bars. Tooltip values are tabular. Missing values remain gray and display an em dash.
- **Candidate table:** pale header, thin row rules, subtle row hover, right-aligned numeric columns and compact View buttons. Missing reports show a small neutral No analysis label.
- **Report drawer:** white heading and breakdown area above a pale toolbar and gray PDF well. External links use outlined controls and an inline external-link icon. Close uses a square icon control. The PDF keeps its original visual treatment.

Primary-outcome and incumbency comparisons use two columns on desktop and one column on phone, with fixed 0–100% horizontal scales, AI orange-red data bars, numerical percentages and analyzed-candidate counts. The adjacent aggregation selector stays synchronized with the map selector.

## Do's and Don'ts

### Do:

- Do retain Public Sans and the navy header with its blue lower edge.
- Do keep AI, AI assisted and Human colors consistent across summaries and report breakdowns.
- Do pair party dots and data colors with written labels.
- Do preserve visible keyboard focus and reduced-motion behavior.

### Don't:

- Don’t use party colors to encode AI percentages.
- Don’t add decorative shadows to ordinary data panels.
- Don’t restyle the preserved PDF as dashboard typography.

The independent project uses slate navy navigation, a muted teal edge and an authored ballot-box mark. Pangram semantic colors remain unchanged.
