import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv
load_dotenv()

try:
    from agent import lc_pipeline
except Exception:
    import lc_pipeline

OUT_DIR = Path(__file__).resolve().parent / 'patches_py_fixed'
OUT_DIR.mkdir(exist_ok=True)

SNIPPETS_PY = lc_pipeline.SNIPPETS_PY
text = SNIPPETS_PY.read_text(encoding='utf-8')
snippet = text.split('--- ')[1].strip()
report = lc_pipeline.REPORT_PY.read_text(encoding='utf-8') if lc_pipeline.REPORT_PY.exists() else ''

base = lc_pipeline.BUG_FIX_PROMPT.format(code_snippet=snippet, analysis=report)
variants = {
    'minimal': "Fix the snippet below and return ONLY a unified diff patch (git apply-compatible). If you cannot produce a patch, return NO_PATCH.\n\n" + snippet,
    'short_strict': snippet + "\n\nIMPORTANT: Return ONLY a valid unified diff patch compatible with 'git apply'. Do NOT include explanations or fences. If you cannot, return NO_PATCH.",
    'verbose_strict': base + "\n\nIMPORTANT: Return ONLY a valid unified diff patch compatible with 'git apply'. Do NOT include explanations or fences. If you cannot, return NO_PATCH.",
}

client = lc_pipeline.gemini_llm
if not client:
    print('No Gemini client initialized')
    sys.exit(1)

for name, prompt in variants.items():
    try:
        print('=== Variant', name)
        resp = client.invoke([lc_pipeline.HumanMessage(content=prompt)])
        content = getattr(resp, 'content', str(resp))
        print('LEN', len(content))
        out = OUT_DIR / f'raw_resp_gemini_{name}.txt'
        out.write_text(content, encoding='utf-8')
        print('Saved', out)
    except Exception as e:
        print('Error for', name, e)
