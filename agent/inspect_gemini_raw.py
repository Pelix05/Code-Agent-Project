import sys, os, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()

try:
    from agent import lc_pipeline
except Exception:
    import lc_pipeline

OUT = Path(__file__).resolve().parent / 'patches_py_fixed'
OUT.mkdir(exist_ok=True)

prompts = [
    "Say YES",
    "Return exactly the word YES with no other text",
    "Return NO_PATCH if you cannot produce a unified diff for this snippet",
]

client = lc_pipeline.gemini_llm
if not client:
    print('Gemini client not initialized')
    sys.exit(1)

for i, p in enumerate(prompts, start=1):
    try:
        print('Calling Gemini with prompt:', p)
        resp = client.invoke([lc_pipeline.HumanMessage(content=p)])
        # Try to serialize the response object: capture content attribute if present
        saved = {}
        saved['repr'] = repr(resp)
        saved['content'] = getattr(resp, 'content', None)
        # Some clients may attach additional attributes; capture __dict__ if available
        try:
            saved['dict'] = getattr(resp, '__dict__', None)
        except Exception:
            saved['dict'] = None
        outp = OUT / f'raw_gemini_test_{i}.json'
        outp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Saved', outp)
    except Exception as e:
        print('Call failed', e)
        errp = OUT / f'raw_gemini_test_{i}_error.txt'
        errp.write_text(str(e), encoding='utf-8')
        print('Saved error file', errp)
