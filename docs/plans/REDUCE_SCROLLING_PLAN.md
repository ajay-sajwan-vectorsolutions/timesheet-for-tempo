# Plan: Reduce Scrolling on index.html (Dark Theme Site)

**Status:** IMPLEMENTED (March 12, 2026)

## Context
The site previously required ~5-6 full viewport scrolls to read completely. 22 feature cards, 10 timeline items, 9 roadmap cards, and several large sections were all fully expanded. Goal: reduce vertical height by ~40% while keeping all content accessible via tabs/collapsibles.

**Estimated savings: ~2,350px (from ~5,500px to ~3,200px = ~42% reduction)**

---

## Strategy: 3 Techniques

| Technique | Where Applied | How |
|-----------|--------------|-----|
| **Tabs** | Features+Roles, Problem+Solution, Screenshots+QuickStart | Show one group at a time, reuse existing `.tab-*` CSS |
| **Collapsible** | Business Case table, Architecture class table | Toggle button reveals detail on demand |
| **Compact** | Timeline, Roadmap | Reduce padding / convert cards to rows |

---

## Files Modified

| File | Changes |
|------|---------|
| `docs/site/index.html` | All HTML restructuring, JS updates |
| `docs/site/style.css` | ~25 lines of new CSS (toggle-btn, collapsible, compact, roadmap-row) |

---

## Section-by-Section Changes

### 1. Hero -- NO CHANGE (~300px)

### 2. Problem + Solution -- MERGED INTO TABS (saves ~350px)
- Wrapped both sections' content in a single `<section id="problem">` with a `.tab-container`
- Tab 1: "The Problem" (active) -- before/after comparison cards
- Tab 2: "How It Works" -- 3 flow steps + code block
- Deleted standalone `<section id="solution">`
- Added `<span id="solution"></span>` anchor to preserve bookmarks

### 3. Features + Roles -- TABBED GROUPS (saves ~850px)
- Grouped 20 feature cards into 4 category tabs + 1 Roles tab:

| Tab (default: first) | Cards |
|----------------------|-------|
| **Core Automation** | Daily Auto-Sync, Weekly Verification, Monthly Submission, Smart Distribution, Idempotent Sync |
| **Intelligence** | Overhead Stories, Schedule Guard, Tempo as Source of Truth, Early Submission, Monthly Reports |
| **Desktop App** | System Tray App, Toast Notifications, Welcome Toast, Tray Auto-Restart, Change Sync Time, Smart Exit Check |
| **Platform & Deploy** | Cross-Platform, Distribution Zips, DPAPI Encryption, Setup Wizard |
| **Roles** | Developer, Product Owner, Sales (existing role cards) |

- Each tab shows 4-6 cards in `.card-grid` (~2 rows visible)
- Deleted standalone `<section id="roles">`
- Added `<span id="roles"></span>` anchor

### 4. Business Case -- COLLAPSIBLE DETAILS (saves ~300px)
- Kept 3 ROI highlight boxes always visible
- Wrapped cost breakdown table + 4 stat cards in `<div id="biz-detail" class="collapsible">`
- Added toggle button: "Show detailed breakdown"

### 5. Timeline -- COMPACT PADDING (saves ~150px)
- Added `.compact` class to section (reduces padding from 5rem to 3rem)
- Added `.timeline-compact` class to timeline divs (reduces item padding from 2.5rem to 1.25rem)
- No structural HTML changes

### 6. Screenshots + Quick Start -- MERGED (saves ~300px)
- Renamed section to "Usage & Quick Start"
- Added 6th tab: "Quick Start" containing the 3 flow steps + commands code block
- Deleted standalone `<section id="quickstart">`
- Added `<span id="quickstart"></span>` anchor

### 7. Architecture -- COLLAPSIBLE TABLE (saves ~200px)
- Kept class diagram + API flow diagram visible
- Wrapped 9-row class table in `<div id="arch-detail" class="collapsible">`
- Added toggle button: "Show class details"

### 8. Roadmap -- COMPACT ROWS (saves ~200px)
- Replaced 9 card grid with compact row list inside a single card container
- Each row: icon + title + description + badge (single line per item)
- Used new `.roadmap-row` class

---

## Navigation Changes

Previous 10 links reduced to 7 links:

| Remove | Rename |
|--------|--------|
| Solution | Problem --> "Problem & Solution" |
| Roles | Features (unchanged, includes Roles tab) |
| Quick Start | Screenshots --> "Usage" |

---

## CSS Additions (~25 lines in style.css)

```css
/* Toggle button for collapsible sections */
.toggle-btn { ... }
.toggle-btn:hover { ... }

/* Collapsible content (hidden by default, .expanded shows it) */
.collapsible { display: none; }
.collapsible.expanded { display: block; }

/* Compact section padding */
section.compact { padding: 3rem 2rem; }

/* Compact timeline */
.timeline-compact .timeline-item { padding-bottom: 1.25rem; }

/* Roadmap compact rows */
.roadmap-row { display: flex; align-items: center; gap: 1rem; ... }
```

---

## JS Changes

### 1. Fixed `showTab()` to support multiple tab containers
Previous version used `document.querySelectorAll` globally -- broke when multiple tab containers existed. Replaced with `closest('.tab-container')` scoping.

### 2. Added `toggleSection()` for collapsibles
Toggles `.expanded` class and swaps "Show"/"Hide" in button text.

---

## Tab ID Convention (globally unique)
- Problem/Solution: `tab-prob-problem`, `tab-prob-solution`
- Features: `tab-feat-core`, `tab-feat-intel`, `tab-feat-desktop`, `tab-feat-platform`, `tab-feat-roles`
- Screenshots: kept existing `tab-cli`, `tab-schedule`, etc. + added `tab-quickstart`

---

## Verification Results
- All 3 tab groups switch correctly (Problem 2 tabs, Features 5 tabs, Usage 6 tabs)
- Both collapsibles toggle correctly (Business Case, Architecture)
- Toggle buttons swap "Show"/"Hide" text
- Nav links scroll to correct sections (7 links)
- Preserved anchor IDs (#solution, #roles, #quickstart) still work via `<span>` elements
- Roadmap displays as compact rows with badges
- Timeline uses compact padding
