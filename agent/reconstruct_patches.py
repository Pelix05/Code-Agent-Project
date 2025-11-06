import re
from pathlib import Path
import difflib
import subprocess

BASE = Path(__file__).resolve().parent
PATCH_DIR = BASE / 'patches_py_fixed'
RAW_GLOB = 'raw_resp_*.txt'
REPAIRED_PREFIX = 'repaired_'
RECONSTRUCTED_PREFIX = 'reconstructed_'
PY_REPO = Path(__file__).resolve().parent.parent / 'python_repo'

CODE_FENCE_RE = re.compile(r'```(?:py|python)?\n([\s\S]+?)\n```', re.IGNORECASE)
FILENAME_RE = re.compile(r'^---\s+a/(.+)$', re.MULTILINE)
FILENAME_ALT_RE = re.compile(r'\b([\w\-_/\\]+\.py)\b')


def git_apply_check(patch_path: Path, cwd: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(['git', 'apply', '--check', str(patch_path)], cwd=str(cwd), capture_output=True, text=True)
        ok = proc.returncode == 0
        out = (proc.stdout or '') + (proc.stderr or '')
        return ok, out
    except Exception as e:
        return False, str(e)


def extract_code_blocks_from_raw(raw_text: str):
    blocks = []
    for m in CODE_FENCE_RE.finditer(raw_text):
        blocks.append(m.group(1))
    # fallback: find long contiguous non-diff code (lines without diff markers)
    if not blocks:
        lines = raw_text.splitlines()
        cur = []
        for ln in lines:
            if ln.startswith(('diff --git', '--- ', '+++ ', '@@ ', '+', '-', ' ')):
                if cur and len(cur) > 5:
                    blocks.append('\n'.join(cur))
                cur = []
                continue
            cur.append(ln)
        if cur and len(cur) > 5:
            blocks.append('\n'.join(cur))
    return blocks


def find_candidate_newtext(target_fname: str):
    # Search raw_resp files for a code block mentioning the filename or large code blocks
    candidates = []
    for rf in sorted(PATCH_DIR.glob(RAW_GLOB)):
        txt = rf.read_text(encoding='utf-8', errors='ignore')
        # prefer raw files that mention filename
        if target_fname and target_fname in txt:
            for b in extract_code_blocks_from_raw(txt):
                candidates.append((rf.name, b))
        else:
            for b in extract_code_blocks_from_raw(txt):
                # heuristic: code block that contains 'def ' or 'class ' and >20 lines
                if ('def ' in b or 'class ' in b) and len(b.splitlines()) > 10:
                    candidates.append((rf.name, b))
    return candidates


def reconstruct_for_repaired(repaired_path: Path):
    orig_name = repaired_path.name.removeprefix(REPAIRED_PREFIX)
    orig_path = PATCH_DIR / orig_name
    # try to get filename from repaired content
    cont = repaired_path.read_text(encoding='utf-8', errors='ignore')
    m = FILENAME_RE.search(cont)
    target_fname = None
    if m:
        target_fname = m.group(1).strip()
    else:
        # fallback to original .diff headers
        if orig_path.exists():
            t = orig_path.read_text(encoding='utf-8', errors='ignore')
            m2 = re.search(r'\b([\w\-_/\\]+\.py)\b', t)
            if m2:
                target_fname = m2.group(1)
    if not target_fname:
        # try any python filename in raw responses
        # take first candidate
        all_raw = ''.join([p.read_text(encoding='utf-8', errors='ignore') for p in sorted(PATCH_DIR.glob(RAW_GLOB))])
        m3 = FILENAME_ALT_RE.search(all_raw)
        if m3:
            target_fname = m3.group(1)
    if not target_fname:
        return False, 'no target filename found'

    # locate original file in PY_REPO; search recursively if needed
    orig_file = PY_REPO / target_fname
    if not orig_file.exists():
        # try at repo root (filename only)
        alt = PY_REPO / Path(target_fname).name
        if alt.exists():
            orig_file = alt
        else:
            # recursive search for filename
            candidates = list(PY_REPO.rglob(Path(target_fname).name))
            if candidates:
                orig_file = candidates[0]
            else:
                return False, f'original file not found: {target_fname}'

    orig_text = orig_file.read_text(encoding='utf-8', errors='ignore').splitlines()

    # find candidate new texts
    cands = find_candidate_newtext(target_fname)
    if not cands:
        return False, 'no candidate new text in raw responses'

    for src_name, newtext in cands:
        new_lines = newtext.replace('\r\n', '\n').splitlines()
        # If candidate contains diff markers, try to extract only file body
        if any(ln.startswith(('+++ ', '--- ', 'diff --git')) for ln in new_lines):
            # skip those; we prefer pure file content
            pass
        # build unified diff
        ud = '\n'.join(difflib.unified_diff(orig_text, new_lines, fromfile=f'a/{target_fname}', tofile=f'b/{target_fname}', lineterm='')) + '\n'
        if not ud.strip():
            continue
        out_path = PATCH_DIR / f'{RECONSTRUCTED_PREFIX}{repaired_path.name.removeprefix(REPAIRED_PREFIX)}'
        out_path.write_text(ud, encoding='utf-8')
        ok, out = git_apply_check(out_path, PY_REPO)
        if ok:
            return True, f'reconstructed using {src_name}'
        # else continue to next candidate
    return False, 'reconstruction attempts failed'


if __name__ == '__main__':
    # identify failed repaired files by running git apply --check
    failed = []
    for rp in sorted(PATCH_DIR.glob('repaired_*.diff')):
        ok, out = git_apply_check(rp, PY_REPO)
        if not ok:
            failed.append(rp)
    print(f'Found {len(failed)} failed repaired patches')
    summary = []
    for rp in failed:
        print('Attempting reconstruct for', rp.name)
        ok, info = reconstruct_for_repaired(rp)
        summary.append((rp.name, ok, info))
        print(' ->', 'OK' if ok else 'FAIL', info)

    # write summary
    with (PATCH_DIR / 'reconstruct_report.txt').open('w', encoding='utf-8') as fh:
        for name, ok, info in summary:
            status = 'OK' if ok else 'FAIL'
            fh.write(f"{name}\t{status}\t{info}\n")
    print('Wrote reconstruct_report.txt')
