import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agent.lc_pipeline import run_pipeline, REPORT_PY, SNIPPETS_PY
from pathlib import Path

def write_single_snippet(src_snippets: Path, dst: Path):
    txt = src_snippets.read_text(encoding='utf-8')
    parts = txt.split('--- ')
    if len(parts) <= 1:
        print('[!] No snippets found in', src_snippets)
        return False
    first = parts[1].strip()
    # write a file with the same separator so run_pipeline will parse it
    dst.write_text('--- ' + first + '\n', encoding='utf-8')
    return True

if __name__ == '__main__':
    base = Path(__file__).resolve().parent
    src = SNIPPETS_PY
    dst = base / 'snippets' / 'one_snippet_py.txt'
    dst.parent.mkdir(parents=True, exist_ok=True)
    ok = write_single_snippet(src, dst)
    if not ok:
        sys.exit(1)
    print('[*] Running pipeline for single snippet...')
    run_pipeline(REPORT_PY, dst, lang='py')
