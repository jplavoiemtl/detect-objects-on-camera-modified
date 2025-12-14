---
name: live-footer-time
overview: Add footer date/time display matching existing format with minute updates.
todos:
  - id: locate-format
    content: Find existing date/time formatting logic in UI
    status: completed
  - id: add-footer
    content: Add footer element centered in live view
    status: completed
  - id: bind-timer
    content: Bind minute-level updater using existing format
    status: completed
  - id: style-footer
    content: Style footer for alignment and responsiveness
    status: completed
---

# Live Footer Time Update

## Approach

- Reuse the existing date/time format already used elsewhere in the app so the footer matches current display.
- Render the formatted date/time in the live view footer, positioned at the center of the bottom bar.
- Update the displayed time in the browser every minute (omit seconds) without requiring page reload.

## Key Files

- [assets/index.html](assets/index.html): Add footer container/markup for the live view if not present.
- [assets/app.js](assets/app.js): Format date/time using the existing format function/logic and set up a minute-level refresh on the footer element.
- [assets/style.css](assets/style.css): Adjust footer styling/placement so the centered date/time looks consistent with the live view layout.

## Implementation Notes

- Locate and reuse the current date/time formatting helper or pattern already used in the UI to ensure consistency.
- Use a timer (e.g., `setInterval` aligned to minute boundaries) to update the footer text without seconds.
- Ensure the footer remains responsive on different screen sizes and does not overlap existing controls.