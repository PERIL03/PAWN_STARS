# Visual QA Checklist

Use this checklist before demo day to quickly validate UI quality and responsive behavior.

## Setup

1. Start the app locally.
2. Open desktop and mobile views.
3. Run snapshot capture for baseline images:

```bash
bash scripts/capture-responsive.sh
```

## Navigation and Responsiveness

1. Confirm the top navigation is visible and readable on every page.
2. Confirm the mobile menu opens, closes, and collapses after clicking a nav item.
3. Confirm no horizontal overflow exists on 390px width.
4. Confirm all pages maintain spacing around cards and headings at mobile sizes.

## Motion and Visual Polish

1. Confirm reveal animations appear once as sections scroll into view.
2. Confirm hero parallax ornaments move smoothly on scroll (home page).
3. Confirm no jittering or layout shifts during scroll.
4. Confirm reduced-motion preference disables heavy motion effects.

## File Upload and Preview Flows

1. Encrypt/Decrypt page: drag-and-drop highlights properly and selected filename updates.
2. Live Preview page: controls and board remain visible on mobile.
3. Visualizer page: PGN upload works and moves list is clickable.

## Visualizer Playback Controls

1. Confirm Play starts auto-advancing moves.
2. Confirm Pause stops auto-advancing immediately.
3. Confirm Previous/Next updates board and highlighted move.
4. Confirm Play is disabled at the final move and Pause is disabled when not playing.

## Accessibility Spot Checks

1. Tab through nav and major form controls; focus ring should be visible.
2. Confirm button labels are understandable without icons.
3. Confirm text contrast is readable over gradients and glass cards.

## Demo Readiness

1. Compare new snapshots to previous baseline for regressions.
2. Verify there are no console errors on Home, Upload, Preview, Visualizer, About, Contact.
3. Verify all links and main actions are functional end-to-end.
