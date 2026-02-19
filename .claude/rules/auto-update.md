# Auto-Update Rules

These rules define when and how documentation must be updated automatically.
Follow these rules after EVERY code change -- do not wait for user to request it.

## After Editing tempo_automation.py or tray_app.py

1. **Update class map line numbers in CLAUDE.md** (Architecture section)
   - Check if any class/method moved and update the line references
2. **Update total line count** in CLAUDE.md header
3. **Update MEMORY.md class map** if line numbers changed significantly

## After Adding a New Feature

1. **Update "What's Working" list** in CLAUDE.md
2. **Update TODO list** -- move item from TODO to completed
3. **Bump version number** in CLAUDE.md header if warranted
4. **Update MEMORY.md** version and changes section

## After a Version Bump

1. **Update README.md** version number and feature list
2. **Update docs/guides/SETUP_GUIDE.md** if the setup flow changed
3. **Add entry to version history** in CLAUDE.md and README.md

## After Fixing a Bug

1. **Add to Debugging Quick Ref** in MEMORY.md if it's a pattern others might hit
2. **Update CLAUDE.md debugging section** if relevant

## After Moving/Renaming Files

1. **Update project structure tree** in CLAUDE.md
2. **Update any cross-references** in README.md and SETUP_GUIDE.md
3. **Update .gitignore** if new file patterns needed

## What NOT to Update

- Don't update docs for trivial whitespace or comment-only changes
- Don't update README.md for internal refactors that don't change user behavior
- Don't update line numbers for changes under 5 lines of shift
