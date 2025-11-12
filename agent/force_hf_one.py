import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agent import lc_pipeline
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parent
SNIPPETS_PY = lc_pipeline.SNIPPETS_PY
REPORT_PY = lc_pipeline.REPORT_PY

def get_first_snippet(path: Path) -> str:
    txt = path.read_text(encoding='utf-8')
    parts = txt.split('--- ')
    if len(parts) <= 1:
        return ''
    return parts[1].strip()

def main():
    snippet = get_first_snippet(SNIPPETS_PY)
    if not snippet:
        print('[!] No snippet found')
        return
    report = REPORT_PY.read_text(encoding='utf-8') if REPORT_PY.exists() else ''
    prompt = lc_pipeline.BUG_FIX_PROMPT.format(code_snippet=snippet, analysis=report)
    # Append the strict unified-diff instruction as the pipeline does for routers
    prompt = prompt + "\n\nIMPORTANT: Return ONLY a valid unified diff patch compatible with 'git apply'. Do NOT include any explanations, markdown fences, or extra text."

    # Prefer the HuggingFace Router client (hf_router_llm) for hosted router models
    # since we set a router model with good providers; fall back to hf_llm (Inference API).
    hf = lc_pipeline.hf_router_llm or lc_pipeline.hf_llm
    if not hf:
        print('[!] No Hugging Face client initialized (hf_llm and hf_router_llm are None)')
        return

    try:
        print('[*] Invoking Hugging Face client directly...')
        resp = hf.invoke([lc_pipeline.HumanMessage(content=prompt)])
        content = getattr(resp, 'content', str(resp))
        print('[*] Response length:', len(content))
        out_dir = BASE / 'patches_py_fixed'
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / 'raw_resp_hf_forced.txt'
        out_path.write_text(content, encoding='utf-8')
        print(f'[+] Saved raw HF response to {out_path}')

        # Try to clean/validate immediately
        cleaned = lc_pipeline.clean_patch_output(content)
        if lc_pipeline.validate_patch(cleaned):
            fixed_path = out_dir / 'patch_hf_forced.diff'
            fixed_path.write_text(cleaned, encoding='utf-8')
            print(f'[+] Cleaned patch saved to {fixed_path}')
        else:
            print('[!] HF response did not contain a valid patch after cleaning')
    except Exception as e:
        print('[!] HF invoke failed:', e)

if __name__ == '__main__':
    main()
