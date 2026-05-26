# Smart Ticket Selection & Description Generation

**Date:** 2026-05-13
**Status:** Brainstorm / Ideas
**Branch:** feature/fix-token-issue

---

## Problem Statement

The current app selects Jira tickets by status (`IN DEVELOPMENT` / `CODE REVIEW`) and splits hours equally across all active tickets. A ticket you didn't touch today gets the same logged hours as one you spent 6 hours on.

The goal is to pick the *right* Jira ID (epic/story) and generate a *meaningful* description for each tempo sync — based on what was actually worked on.

---

## Core Gap

| Current Behavior | Desired Behavior |
|---|---|
| Status-based ticket selection | Activity-signal-based selection |
| Equal hour distribution | Activity-weighted distribution |
| Rule-based description from ticket text | Description from actual work done (commits, PRs, comments) |
| No epic awareness | Epic-level fallback when no story activity exists |

---

## Bucket 1: Ticket Selection — Picking the Right Jira ID

### a) Git branch → ticket key matching
Parse the local git branch name or recent commit branches: `feat/genai-2800` → `GENAI-2800`. Strongest signal — if you've been committing to it today, you worked on it.

### b) Git commit log extraction
Scrape `git log --since="today" --oneline` for patterns like `GENAI-2800`, `LMS-1234` in commit messages. Ticket keys are often already in commit messages.

### c) Jira activity filter
Query for tickets where *you* commented or transitioned a status today:
```
commentAuthor = currentUser() AND updated >= "YYYY-MM-DD"
```
Catches tickets you updated in Jira but didn't commit code against (e.g., PR review, bug triage).

### d) Epic-level fallback
Two sub-ideas:
- If no story-level activity is found, fall back to the parent epic as the logging target.
- Allow explicit "log to epic" for cross-cutting work (code reviews, meetings about an initiative) where no single story applies.

### e) Hybrid scoring (status + activity)
Keep the current status filter as a *pool*, then rank within it by activity signals:
- Tickets with a commit or comment today → weight 3x
- Others → weight 1x

Feeds directly into the existing `distribution_weights` infrastructure — auto-populate it instead of requiring manual config.

---

## Bucket 2: Description Generation

### a) PR title + commit subjects
`Merged PR #87: Add token refresh logic` is far more accurate than the boilerplate first sentence of a ticket description. The external script (`weekly-tempo-logs.py`) does this; the current app does not.

### b) Per-day variation
Currently the same description repeats across multiple days on the same ticket. If commits are available per-day, each day's description can reflect that day's actual commits.

### c) Action-aware phrasing
Detect what *type* of activity happened and phrase accordingly:
- "Opened PR #87 …"
- "Merged PR #87 …"
- "Code review on …"
- "Fixed failing test in …"

Rather than generic "Worked on X."

### d) LLM-drafted summary (opt-in)
Shell out to `claude -p` to synthesize all signals (PRs + commits + Jira comments) into a description. Works but adds a dependency — recommended as opt-in via `--smart-descriptions` flag rather than the default path.

---

## Bucket 3: Workflow Improvements

### a) Activity-weighted auto-distribution
The `distribution_weights` config key already exists but requires manual tuning. Auto-populate it from today's git commits:
- 12 commits to GENAI-2800, 3 commits to LMS-99 → 80% / 20% split automatically.

### b) Interactive preview/edit before submit
Show a table of `date | ticket | hours | description` before writing anything to Jira. Let the user adjust inline. The `--dry-run` flag already previews but doesn't allow edits.

### c) Natural-language revision
Accept a freeform edit instruction ("add GENAI-2800 for 1h on Friday") and re-draft accordingly — same pattern as the external script's `confirm_draft` loop.

---

## Recommended Starting Points

| Priority | Idea | Why |
|---|---|---|
| High | Git commit log extraction (1b) | Zero new dependencies; keys are already in commit messages |
| High | Activity-weighted auto-distribution (3a) | Fills `distribution_weights` automatically; infra already exists |
| Medium | Epic-level fallback (1d) | Unique to this codebase; covers cross-cutting work with no story |
| Low | LLM-drafted descriptions (2d) | Most powerful but heaviest dependency |

---

## Reference

- External script analyzed: `c:\Users\Ajay.Sajwan\Downloads\weekly-tempo-logs.py`
- Existing infra: `distribution_weights` config key, `_generate_work_summary()` at line ~3500, `get_issues_in_status_on_date()` at line ~1947
