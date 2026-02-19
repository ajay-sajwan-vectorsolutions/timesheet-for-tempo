---
name: review-code
description: Review Python code against Tempo project coding standards. Checks ASCII output, API patterns, config access, error handling, and logging.
user-invocable: true
disable-model-invocation: false
argument-hint: [file-path]
---

# Review Tempo Code

Review code files against Tempo Automation project standards.

## Checklist

### 1. ASCII-Only Output
- FAIL: Any Unicode symbols in print() statements
- PASS: `print("[OK] Sync complete")` -- only [OK], [FAIL], [!], [->], [INFO], [SKIP]

### 2. API Call Patterns
Every `requests.get/post/put/delete` must have: `timeout=30`, `raise_for_status()`, logger call

### 3. Config Access
- FAIL: `config['section']['key']`
- PASS: `config.get('section', {}).get('key', 'default')`

### 4. No Credentials in Logs
No `logger.*token`, `logger.*password`, `print.*token` patterns

### 5. Error Handling
Specific exceptions first, generic Exception last, all except blocks log the error

### 6. PEP 8
Max 100 char lines, f-strings, type hints, docstrings on public methods

### 7. Windows Compatibility
No Unix-specific calls, pathlib for paths, pythonw.exe safe (no sys.stdout assumption)

### 8. pystray Thread Safety (tray_app.py only)
Callbacks return quickly (< 100ms), heavy work in daemon threads

## Output Format
For each issue: file:line, standard violated, current code, suggested fix
