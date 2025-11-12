import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv()

try:
    from agent import lc_pipeline
except Exception:
    import lc_pipeline

import requests

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / 'patches_py_fixed'
OUT_DIR.mkdir(parents=True, exist_ok=True)

STAGE1_OUT = OUT_DIR / 'raw_resp_hf_llama_stage1.txt'
STAGE2_OUT = OUT_DIR / 'raw_resp_hf_llama_stage2.txt'
CLEAN_OUT = OUT_DIR / 'patch_hf_llama.diff'

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
    print('[!] No snippet found')
    sys.exit(1)

report = REPORT_PY.read_text(encoding='utf-8') if REPORT_PY.exists() else ''

# Build base prompt
try:
    base_prompt = lc_pipeline.BUG_FIX_PROMPT.format(code_snippet=snippet, analysis=report)
except Exception:
    base_prompt = f"Fix the following Python snippet.\n\nSnippet:\n{snippet}\n\nAnalysis:\n{report}"

HF_TOKEN = os.getenv('HUGGINGFACE_API_TOKEN') or os.getenv('HF_TOKEN')
if not HF_TOKEN:
    print('[!] No HF token in environment')
    sys.exit(1)

model = 'meta-llama/Llama-3.1-8B-Instruct'
url = 'https://router.huggingface.co/v1/chat/completions'
headers = {'Authorization': f'Bearer {HF_TOKEN}', 'Content-Type': 'application/json'}

# Stage 1: capability check (YES/NO)
stage1_prompt = (
    base_prompt
    + "\n\nBefore producing a patch, answer ONLY one word: YES if you can produce a valid unified-diff patch (git apply-compatible) for this snippet, or NO if you cannot. Reply exactly 'YES' or 'NO' with no extra text."
)

payload1 = {
    'model': model,
    'messages': [{'role': 'user', 'content': stage1_prompt}],
    'max_tokens': 32,
}

print('[*] Stage 1: capability check with model', model)
try:
    r1 = requests.post(url, headers=headers, json=payload1, timeout=60)
    print('[*] HTTP status stage1:', r1.status_code)
    r1.raise_for_status()
    d1 = r1.json()
except Exception as e:
    print('[!] Stage1 request failed:', e)
    try:
        print('resp text:', r1.text[:400])
    except Exception:
        pass
    sys.exit(1)

# extract
stage1_text = ''
if isinstance(d1, dict):
    try:
        stage1_text = d1.get('choices', [])[0].get('message', {}).get('content', '')
    except Exception:
        stage1_text = ''
if not stage1_text:
    stage1_text = json.dumps(d1, ensure_ascii=False)

STAGE1_OUT.write_text(stage1_text, encoding='utf-8')
print('[+] Saved stage1 response to', STAGE1_OUT)
print('[*] Stage1 reply preview:', stage1_text.strip()[:200])

normalized = stage1_text.strip().upper()
if normalized.startswith('YES'):
    print('[*] Model reported capability; proceeding to stage 2')
else:
    print('[!] Model reported NO or ambiguous; aborting stage2')
    sys.exit(0)

# Stage 2: request strict unified-diff-only patch
strict_suffix = (
    "\n\nIMPORTANT: Return ONLY a valid unified diff patch compatible with 'git apply'. "
    "Do NOT include any explanations, markdown fences, or extra text. If you cannot produce a valid patch, return exactly the string: NO_PATCH"
)
stage2_prompt = base_prompt + strict_suffix
payload2 = {
    'model': model,
    'messages': [{'role': 'user', 'content': stage2_prompt}],
    'max_tokens': 2048,
}

print('[*] Stage 2: requesting patch')
try:
    r2 = requests.post(url, headers=headers, json=payload2, timeout=120)
    print('[*] HTTP status stage2:', r2.status_code)
    r2.raise_for_status()
    d2 = r2.json()
except Exception as e:
    print('[!] Stage2 request failed:', e)
    try:
        print('resp text:', r2.text[:400])
    except Exception:
        pass
    sys.exit(1)

stage2_text = ''
if isinstance(d2, dict):
    try:
        stage2_text = d2.get('choices', [])[0].get('message', {}).get('content', '')
    except Exception:
        stage2_text = ''
if not stage2_text:
    stage2_text = json.dumps(d2, ensure_ascii=False)

STAGE2_OUT.write_text(stage2_text, encoding='utf-8')
print('[+] Saved stage2 response to', STAGE2_OUT)
print('[*] Stage2 reply preview:', stage2_text.strip()[:400])

# Handle NO_PATCH
if stage2_text.strip() == 'NO_PATCH':
    print('[!] Model returned NO_PATCH')
    sys.exit(0)

# Try sanitization
try:
    cleaned = lc_pipeline.clean_patch_output(stage2_text)
    valid = lc_pipeline.validate_patch(cleaned)
    print('[*] clean_patch_output length:', len(cleaned), 'valid:', valid)
    if valid:
        CLEAN_OUT.write_text(cleaned, encoding='utf-8')
        print('[+] Saved cleaned patch to', CLEAN_OUT)
        sys.exit(0)
    ag = lc_pipeline.aggressive_sanitize(stage2_text)
    print('[*] aggressive_sanitize length:', len(ag), 'valid:', lc_pipeline.validate_patch(ag))
    if lc_pipeline.validate_patch(ag):
        CLEAN_OUT.write_text(ag, encoding='utf-8')
        print('[+] Saved aggressive-sanitized patch to', CLEAN_OUT)
    else:
        print('[!] No valid patch extracted from stage2')
except Exception as e:
    print('[!] Sanitization failed:', e)

print('[*] Done')
