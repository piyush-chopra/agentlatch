# Interface design: the AgentLatch workspace

## Context and review

AgentLatch is a React web app for people operating asynchronous agents. The screen's job is to make shared state, execution, and conflict recovery understandable at a glance. This redesign applies Apple HIG foundations and selected iPad-inspired patterns to the web; it does not claim to be a native iPadOS app or use Apple's actual Liquid Glass APIs.

The original interface used dense panels and several 7–10px utility labels. The redesign raises the base to 17px, uses a minimum text size of 0.6875rem (about 11.7px), gives primary controls at least 44px targets, and separates navigation from colorful content. The previous custom modal lacked browser-enforced background isolation; native HTML `dialog.showModal()` now supplies that behavior, focus management, Escape dismissal, and focus restoration.

## Design thesis

A calm workspace for independent minds: four colored state summaries and a small map of agents converging on one coordinator. The coordination map is the signature element because it explains the actual product, rather than decorating an unrelated dashboard. Times-like serif headings give the app a warmer editorial voice; platform system fonts keep controls familiar.

## Tokens

Text contrast ratios are calculated from the actual sRGB foreground/background hex values using WCAG relative luminance. They describe opaque token pairs, not a certification of every rendered pixel.

| Role | Light foreground / background | Ratio | Dark foreground / background | Ratio |
| --- | --- | --- | --- | --- |
| Primary content | #292538 / #FFFFFF | 14.83:1 | #F2ECFA / #24212C | 13.66:1 |
| Secondary content | #645E73 / #F5F3FA | 5.63:1 | #BCB3CD / #18161E | 8.93:1 |
| Primary action | #FFFFFF / #6741C0 | 6.79:1 | #291446 / #C4A6FA | 7.94:1 |
| Workflow / lavender | #59339D / #EAE2FC | 7.04:1 | #D8BFFF / #3C2D55 | 7.54:1 |
| Commit / mint | #226747 / #DDF3E8 | 5.83:1 | #ACE6C5 / #203E32 | 8.28:1 |
| Rejection / peach | #974C21 / #FFEADB | 5.35:1 | #FAC29E / #493125 | 7.59:1 |
| Resources / sky | #285E94 / #DEEDFC | 5.65:1 | #B5D9FF / #253951 | 8.04:1 |

Type:

- Display: `Times New Roman`, followed by Iowan Old Style, Palatino, Georgia, and generic serif. Used for titles, summary values, and resource names.
- Body/controls: the platform system sans-serif stack. Root 17px; explanatory text 0.8125–0.875rem; utility labels at least 0.6875rem.
- JSON and identifiers: system monospace.
- Headline: responsive 2.3–3.3rem; section title 1.65rem; summary value 3.6rem.
- No remote font requests or bundled Apple font files.

Layout: a collapsible sidebar beside a generous content canvas at regular widths, converting to labeled floating tabs at compact widths.

```text
Regular
┌──────────────┬─────────────────────────────────────────┐
│ AgentLatch   │ Sidebar toggle             New workflow│
│ Workspace    ├─────────────────────────────────────────┤
│              │ Your agents, in harmony.                 │
│ Overview     │ [Lavender] [Mint] [Peach] [Sky]          │
│ Workflows    │ [Scenario controls | Coordination map]  │
│ Resources    │ Recent workflows                        │
│ Activity     │ Activity journal                        │
│ Settings     │                                         │
└──────────────┴─────────────────────────────────────────┘
Compact
┌──────────────────────────┐
│ Overview       Settings +│
│ Your agents, in harmony. │
│ [Lavender] [Mint]        │
│ [Peach]    [Sky]         │
│ Scenario controls       │
│ Coordination map        │
│ Workflows / journal     │
│ [Overview Runs Data Log]│
└──────────────────────────┘
```

Navigation and toolbars alone use translucent materials. Content cards remain opaque. Dark appearance follows `prefers-color-scheme`; reduced motion and reduced transparency have dedicated CSS fallbacks. Color always accompanies text and/or an icon. Motion is restricted to the existing loading spinner, disabled for reduced-motion preferences.

## HIG references used

These headings refer to the local Apple design skill reference library; links point to Apple's source pages.

- `accessibility.md › Vision`: “Support larger text sizes.” Relative type sizing and fluid layouts replace tiny fixed text. [Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility)
- `accessibility.md › Mobility`: recommends 44×44pt default iPadOS controls; translated to a 44px web target floor.
- `layout.md › Best practices`: “Group related items to help people find the information they want.” [Layout](https://developer.apple.com/design/human-interface-guidelines/layout)
- `typography.md › Conveying hierarchy`: “Minimize the number of typefaces you use, even in a highly customized interface.” [Typography](https://developer.apple.com/design/human-interface-guidelines/typography)
- `color.md › Inclusive color`: avoid relying solely on color to communicate essential information. [Color](https://developer.apple.com/design/human-interface-guidelines/color)
- `sidebars.md › Best practices`: “Consider letting people hide the sidebar.” [Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars)
- `tab-bars.md › Best practices`: “Use a tab bar to support navigation, not to provide actions.” New workflow remains in the toolbar. [Tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars)
- `sheets.md › Platform considerations › Mobile`: favors centered page/form sheets on iPadOS. The web equivalent here is a native modal dialog with an explicit close action. [Sheets](https://developer.apple.com/design/human-interface-guidelines/sheets)
- `liquid-glass.md › The two layers`: the curated skill guide reserves glass for navigation and controls; content remains opaque.
- `dark-mode.md › Best practices`: respect system appearance rather than adding an independent appearance switch. [Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode)

## Critique and verification

The design deliberately retains the product's agent-to-coordinator map instead of an unrelated illustration. The old version badge and the technical footer stack were removed from the main visual hierarchy. The serif is limited to display/content roles so controls remain easy to scan. The principal trade-off is lower information density in exchange for touch comfort and clarity.

Verified in Chrome through the native accessibility interface: main layout, sidebar hide/show alternative, floating-tab resource navigation, workflow sheet, Escape dismissal with focus returning to New workflow, resource inspection, and an offline schema-race run completing with a rejected stale write and successful retry. Visually inspected the workspace and centered sheet. TypeScript and production build pass.

Small-screen breakpoints, dark mode, increased contrast, and reduced-motion/transparency behavior are implemented in CSS; every device/orientation and assistive-technology combination has not been exhaustively tested. This is an iPad-inspired web design, not an accessibility certification.
