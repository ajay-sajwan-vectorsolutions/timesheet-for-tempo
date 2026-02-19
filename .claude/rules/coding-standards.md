# Coding Standards

## Output Rules
- ASCII only in print() -- no Unicode symbols (Windows cp1252 crashes on file redirect)
- ASCII only in .bat files -- use [OK]/[FAIL]/[!] instead of checkmarks/crosses
- Allowed replacements: checkmark -> [OK], cross -> [FAIL], warning -> [!], arrow -> [->], info -> [INFO]

## Python Style
- Follow PEP 8, max 100 character lines
- Use f-strings for string formatting
- Use type hints for function parameters and returns
- Docstrings for all classes and public methods

## Config Access
- Always use `.get()` with fallback: `config.get('section', {}).get('key', 'default')`
- Never use direct key access: `config['section']['key']` (raises KeyError)

## API Calls
- Always set `timeout=30` on all requests calls
- Always call `response.raise_for_status()` after API calls
- Always log: `logger.info(f"API call to {url}: {response.status_code}")`

## Error Handling
```python
try:
    result = api_call()
    logger.info("Success message")
    return result
except SpecificException as e:
    logger.error(f"Specific error: {e}")
except Exception as e:
    logger.error(f"Unexpected error: {e}", exc_info=True)
```

## Logging
- Log all meaningful operations but never log credentials
- Never log API tokens, passwords, or full config
- Use: logger.info() for success, logger.error() for failures
- Logs may contain: issue keys, time amounts, user emails

## Security
- Never commit API tokens or passwords
- DPAPI encryption for Windows credentials (CredentialManager class)
- Encrypted values stored as `ENC:<base64>` in config.json
- All API calls over HTTPS

## Windows Compatibility
- pythonw.exe: sys.stdout is None -- redirect to os.devnull
- pystray callbacks must return quickly -- heavy work in daemon threads
- UTF-8 stdout/stderr encoding forced at startup via io.TextIOWrapper
