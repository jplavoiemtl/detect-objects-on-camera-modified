# UI Improvement Plan

**Goal:** Make the Object Detection UI more appealing with a dark blue theme, implement a dashboard layout for desktop, and improve the mobile experience with new features. The implementation will be rolled out in tested steps.

## Step 1: Base Aesthetics & Dark Blue Theme
- Introduce a refined Dark Blue color palette (e.g., slate/navy blues).
- Integrate modern typography (Google Fonts - Inter/Roboto).
- Replace old HTML symbol buttons with sleek SVG icons (Lucide/Heroicons).
- Add smooth transitions and micro-animations for interactive elements.

## Step 2: Desktop Dashboard Layout
- Restructure the UI grid on larger screens.
- Move the floating top and bottom control panels into a dedicated collapsible sidebar (dashboard-style).
- Allow the video feed to occupy the main remaining space unobstructed.

## Step 3: Interactive Features
- Add a **Fullscreen Mode** button to maximize the video view.
- Add a **Download/Share** button for the detection history images.
- Implement Toast Notifications for new detections when browsing history.

## Step 4: Mobile Responsiveness & Touch Gestures
- Adjust elements to use `dvh` to fix iOS Safari bottom navigation overlap.
- Ensure all touch targets are at least 44px by 44px.
- Implement Swipe Gestures on the saved image history (swipe left to go Forward, swipe right to go Back).

## Progress Tracker
- [x] Step 1: Base Aesthetics & Dark Blue Theme
- [x] Step 2: Desktop Dashboard Layout
- [x] Step 3: Interactive Features
- [x] Step 4: Mobile Responsiveness & Touch Gestures
