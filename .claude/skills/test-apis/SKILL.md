---
name: test-apis
description: Test Jira and Tempo API connectivity and validate credentials. Run this before deployment or when debugging auth issues.
user-invocable: true
disable-model-invocation: true
---

# Test Tempo & Jira APIs

## Quick Test (All-in-one)
```bash
python tempo_automation.py --logfile test-api.log
```
If this succeeds, both APIs are working.

## Test Jira API
```python
python -c "
import requests, base64, json
with open('config.json') as f:
    config = json.load(f)
email = config['jira']['email']
token = config['jira']['api_token']
url = f\"https://{config['jira']['url']}/rest/api/3/myself\"
auth = base64.b64encode(f'{email}:{token}'.encode()).decode()
r = requests.get(url, headers={'Authorization': f'Basic {auth}'}, timeout=30)
print(f'[OK] {r.json().get(\"displayName\")}' if r.ok else f'[FAIL] {r.status_code}')
"
```

## Test Tempo API
```python
python -c "
import requests, json
with open('config.json') as f:
    config = json.load(f)
r = requests.get('https://api.tempo.io/4/user', headers={'Authorization': f'Bearer {config[\"tempo\"][\"api_token\"]}'}, timeout=30)
print(f'[OK] {r.json().get(\"displayName\")}' if r.ok else f'[FAIL] {r.status_code}')
"
```

## Health Check
```bash
python tempo_automation.py --verify-week --logfile health-check.log
```

## Note on DPAPI Encryption
If config.json has encrypted tokens (`ENC:`), use the main script instead -- CredentialManager handles decryption.
