import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import json

# Ensure repo imports work
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

load_dotenv()

try:
    from agent import lc_pipeline
except Exception:
    # Fallback to direct import if package name isn't used
    import lc_pipeline

import requests

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / 'patches_py_fixed'
OUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_OUT = OUT_DIR / 'raw_resp_hf_forced_retry.txt'
CLEAN_OUT = OUT_DIR / 'patch_hf_forced_retry.diff'

# Load first snippet and report like the pipeline does
SNIPPETS_PY = lc_pipeline.SNIPPETS_PY
REPORT_PY = lc_pipeline.REPORT_PY

def get_first_snippet(path: Path) -> str:
    txt = path.read_text(encoding='utf-8')
    parts = txt.split('--- ')
    if len(parts) <= 1:
        return ''
    return parts[1].strip()

snippet = get_first_snippet(SNIPPETS_PY)
if not snippet:
    print('[!] No snippet found in', SNIPPETS_PY)
    sys.exit(1)

report = REPORT_PY.read_text(encoding='utf-8') if REPORT_PY.exists() else ''

# Build prompt using existing template if available
try:
    prompt = lc_pipeline.BUG_FIX_PROMPT.format(code_snippet=snippet, analysis=report)
except Exception:
    # Fallback simple prompt
    prompt = f"Fix the following Python snippet and return ONLY a unified diff patch (git apply compatible).\n\nSnippet:\n{snippet}\n\nAnalysis:\n{report}"

# Strong enforcement suffix
strict_suffix = (
    "\n\nIMPORTANT: Return ONLY a valid unified diff patch compatible with 'git apply'. "
    "Do NOT include any explanations, markdown fences, or extra text. If you cannot produce a valid patch, "
    "return exactly the string: NO_PATCH"
)
prompt = prompt + strict_suffix

# Router call params
HF_TOKEN = os.getenv('HUGGINGFACE_API_TOKEN') or os.getenv('HF_TOKEN')
if not HF_TOKEN:
    print('[!] No Hugging Face token found in environment (HUGGINGFACE_API_TOKEN or HF_TOKEN)')
    sys.exit(1)

model = 'openai/gpt-oss-20b'
url = 'https://router.huggingface.co/v1/chat/completions'
headers = {'Authorization': f'Bearer {HF_TOKEN}', 'Content-Type': 'application/json'}
payload = {
    'model': model,
    'messages': [
        {'role': 'user', 'content': prompt}
    ],
    'max_tokens': 2048,
}

print('[*] Invoking Hugging Face Router model', model)
try:
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    print('[*] HTTP status:', r.status_code)
    r.raise_for_status()
    data = r.json()
except Exception as e:
    print('[!] Request failed:', e)
    try:
        print('Response text preview:', r.text[:200])
    except Exception:
        pass
    sys.exit(1)

# Extract text from typical router response formats
content = ''
if isinstance(data, dict):
    # OpenAI-style: choices[0].message.content
    try:
        content = data.get('choices', [])[0].get('message', {}).get('content', '')
    except Exception:
        content = ''
# fallback: stringify
if not content:
    content = json.dumps(data, ensure_ascii=False, indent=2)

print('[*] Response length:', len(content))
RAW_OUT.write_text(content, encoding='utf-8')
print('[+] Saved raw response to', RAW_OUT)

# Try to clean/validate using pipeline helpers
try:
    cleaned = lc_pipeline.clean_patch_output(content)
    valid = lc_pipeline.validate_patch(cleaned)
    print('[*] clean_patch_output length:', len(cleaned), 'valid:', valid)
    if valid:
        CLEAN_OUT.write_text(cleaned, encoding='utf-8')
        print('[+] Saved cleaned patch to', CLEAN_OUT)
    else:
        # Try aggressive sanitize
        ag = lc_pipeline.aggressive_sanitize(content)
        print('[*] aggressive_sanitize length:', len(ag), 'valid:', lc_pipeline.validate_patch(ag))
        if lc_pipeline.validate_patch(ag):
            CLEAN_OUT.write_text(ag, encoding='utf-8')
            print('[+] Saved aggressive-sanitized patch to', CLEAN_OUT)
        else:
            print('[!] No valid patch extracted; saved raw response for inspection.')
except Exception as e:
    print('[!] Sanitization step failed:', e)

print('[*] Done')
