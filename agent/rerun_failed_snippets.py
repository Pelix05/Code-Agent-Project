"""
Re-run LLM generation for snippets that produced invalid or corrupt patches.
This script will:
- scan `agent/patches_py_fixed` for repaired_*.diff that failed git checks (or original .diff files)
- for each failed snippet it will construct a strict prompt asking the LLM to RETURN ONLY a unified diff
  (no explanation, no fences) and save the raw response to raw_resp_rerun_<n>.txt and sanitized
  patch to rerun_patch_<n>.diff
- It will rely on the existing `ask_llm` helper in `lc_pipeline.py` so this script must be run from the `agent/` folder.
"""
from pathlib import Path
import time
import re
from lc_pipeline import ask_llm, clean_patch_output, validate_patch

BASE = Path(__file__).resolve().parent
PATCH_DIR = BASE / 'patches_py_fixed'
REPORT_PY = BASE / 'analysis_report_py.txt'
SNIPPETS_PY = BASE / 'snippets' / 'bug_snippets_py.txt'

STRICT_INSTRUCTION = (
    "You must return ONLY a unified diff (the output of git diff -U3 or similar). "
    "Do NOT include any prose, explanation, markdown fences, or code fences. "
    "Ensure headers use '--- a/<path>' and '+++ b/<path>' and include @@ hunk headers."
)


def find_failed():
    failed = []
    for p in sorted(PATCH_DIR.glob('*.diff')):
        # choose repaired_*.diff if present otherwise original
        if p.name.startswith('repaired_'):
            ok = False
            # run git apply check
            import subprocess
            proc = subprocess.run(['git', 'apply', '--check', str(p)], cwd=str(BASE.parent / 'python_repo'), capture_output=True, text=True)
            if proc.returncode != 0:
                failed.append(p)
        else:
            # if not repaired, skip ones that passed earlier
            pass
    return failed


def run_rerun():
    failed = find_failed()
    if not failed:
        print('[*] No failed repaired patches found to rerun')
        return

    out_list = []
    for i, rp in enumerate(failed, start=1):
        print(f"[*] Re-running LLM for {rp.name} ({i}/{len(failed)})")
        # build snippet-specific prompt: include the analysis report and the snippet
        report = REPORT_PY.read_text(encoding='utf-8') if REPORT_PY.exists() else ''
        # try to get corresponding snippet by index from filename
        snippet_text = Path(SNIPPETS_PY).read_text(encoding='utf-8') if SNIPPETS_PY.exists() else ''
        prompt = STRICT_INSTRUCTION + "\n\nAnalysis:\n" + report + "\n\nSnippet:\n" + snippet_text
        raw = ask_llm(prompt, 'original_code.py', 'patched_code.py')
        ts = int(time.time())
        raw_path = PATCH_DIR / f'raw_resp_rerun_{i}_{ts}.txt'
        raw_path.write_text(raw or '', encoding='utf-8')
        # sanitize
        cleaned = clean_patch_output(raw or '')
        if not validate_patch(cleaned):
            # attempt aggressive sanitize from lc_pipeline if available
            try:
                from lc_pipeline import aggressive_sanitize
                cleaned = aggressive_sanitize(raw or '')
            except Exception:
                pass
        out_patch = PATCH_DIR / f'rerun_patch_{i}_{ts}.diff'
        out_patch.write_text(cleaned or '', encoding='utf-8')
        ok = validate_patch(cleaned)
        out_list.append((rp.name, out_patch.name, ok))
        print(f" -> wrote {out_patch.name} (valid={ok})")
    # write summary
    with (PATCH_DIR / 'rerun_summary.txt').open('w', encoding='utf-8') as fh:
        for orig, new, ok in out_list:
            fh.write(f"{orig}\t{new}\t{ok}\n")
    print('[*] Rerun complete; summary written to rerun_summary.txt')


if __name__ == '__main__':
    run_rerun()
