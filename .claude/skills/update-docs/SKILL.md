---
name: update-docs
description: Update all project documentation after code changes. Updates CLAUDE.md class map, MEMORY.md, README.md version info, and cross-references.
user-invocable: true
disable-model-invocation: false
---

# Update Documentation

After code changes, update all relevant documentation to keep everything in sync.

## What to Update

### 1. CLAUDE.md (always)
- Version number, line counts, architecture table, project structure tree, TODO lists

### 2. MEMORY.md (always)
- Version number, class map line numbers, changes section, TODO
- Keep under 200 lines

### 3. README.md (on version bumps only)
- Version number, feature list, version history table

### 4. docs/guides/SETUP_GUIDE.md (on setup flow changes only)
- Setup wizard steps, prerequisites, file list

## Files to NEVER Auto-Update
- docs/plans/ -- historical (frozen)
- docs/business/ -- business case (frozen)
- docs/releases/VERSION_2_RELEASE_NOTES.md -- historical (frozen)
- archive/ -- deprecated (frozen)
