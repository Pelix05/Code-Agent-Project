import os
import json
from dotenv import load_dotenv
import requests

load_dotenv()
token = os.getenv('HUGGINGFACE_API_TOKEN') or os.getenv('HF_TOKEN')
if not token:
    print('[!] No HUGGINGFACE_API_TOKEN/HF_TOKEN found in environment')
    raise SystemExit(1)

url = 'https://router.huggingface.co/v1/models'
headers = {'Authorization': f'Bearer {token}'}

print('[*] Querying Hugging Face Router models list...')
resp = requests.get(url, headers=headers, timeout=30)
if resp.status_code != 200:
    print('[!] Router models list request failed:', resp.status_code, resp.text)
    raise SystemExit(1)

data = resp.json()
print(f"[*] Found {len(data)} models registered with the router")
for m in data:
    # model entries can be simple strings or dicts
    if isinstance(m, str):
        print('---')
        print('id:', m)
        continue
    # print selected fields for readability
    mid = m.get('model', m.get('id', '<unknown>'))
    types = m.get('type') or m.get('capabilities') or ''
    desc = m.get('description', '')
    print('---')
    print('id:', mid)
    print('type/capabilities:', types)
    if desc:
        print('description:', desc[:200])

print('\n[*] Full JSON output saved to agent/hf_router_models.json')
with open('agent/hf_router_models.json', 'w', encoding='utf-8') as fh:
    json.dump(data, fh, indent=2)
