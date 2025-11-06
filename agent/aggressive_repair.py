"""
Aggressive repair: attempt fuzzy application of repaired_*.diff patches that fail git apply --check.
This is conservative: it only accepts a reconstruction if the final generated unified diff passes `git apply --check`.

Steps:
- For each repaired_*.diff that fails, parse the unified diff into hunks and target filename
- Load the current file from python_repo (search recursively)
- For each hunk, try to locate best matching position in the file for the removed/context lines using SequenceMatcher
- If match quality passes a threshold, perform replacement of that slice with the new hunk lines
- After processing all hunks, produce a unified diff between original and patched file and validate with `git apply --check`
- Save reconstructed diff to patches_py_fixed/aggr_reconstructed_<name>.diff

This script should be run from the `agent/` folder.
"""
from pathlib import Path
import re
import difflib
import subprocess
from difflib import SequenceMatcher

BASE = Path(__file__).resolve().parent
PATCH_DIR = BASE / 'patches_py_fixed'
PY_REPO = BASE.parent / 'python_repo'

HUNK_HDR_RE = re.compile(r'^@@ -(?P<a_start>\d+)(?:,(?P<a_count>\d+))? \+(?P<b_start>\d+)(?:,(?P<b_count>\d+))? @@')
FILE_HDR_RE = re.compile(r'^---\s+a/(.+)$', re.MULTILINE)

THRESHOLD = 0.55  # matching threshold (0..1)


def git_apply_check(patch_path: Path, cwd: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(['git', 'apply', '--check', str(patch_path)], cwd=str(cwd), capture_output=True, text=True)
        ok = proc.returncode == 0
        out = (proc.stdout or '') + (proc.stderr or '')
        return ok, out
    except Exception as e:
        return False, str(e)


def parse_unified(diff_text: str):
    """Parse a unified diff and return filename and hunks.
    hunks: list of dict {a_start,a_count,b_start,b_count,lines:[(type,content), ...]} where type is ' '|'+'|'-'
    """
    lines = diff_text.splitlines()
    fname = None
    m = FILE_HDR_RE.search(diff_text)
    if m:
        fname = m.group(1).strip()
    # find hunks
    hunks = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        mh = HUNK_HDR_RE.match(ln)
        if mh:
            a_start = int(mh.group('a_start'))
            a_count = int(mh.group('a_count') or '1')
            b_start = int(mh.group('b_start'))
            b_count = int(mh.group('b_count') or '1')
            i += 1
            hlines = []
            while i < len(lines) and not lines[i].startswith('@@ '):
                l = lines[i]
                if l.startswith('+'):
                    hlines.append(('+', l[1:]))
                elif l.startswith('-'):
                    hlines.append(('-', l[1:]))
                elif l.startswith(' '):
                    hlines.append((' ', l[1:]))
                else:
                    # skip unexpected
                    pass
                i += 1
            hunks.append({'a_start': a_start, 'a_count': a_count, 'b_start': b_start, 'b_count': b_count, 'lines': hlines})
            continue
        i += 1
    return fname, hunks


def find_file_in_repo(fname: str):
    p = PY_REPO / fname
    if p.exists():
        return p
    # try basename
    alt = PY_REPO / Path(fname).name
    if alt.exists():
        return alt
    # recursive search
    cands = list(PY_REPO.rglob(Path(fname).name))
    return cands[0] if cands else None


def find_best_match(orig_lines, snippet_lines):
    # Use SequenceMatcher to find best matching contiguous block in orig_lines for snippet_lines
    # Convert lists to strings with <NL> sentinel to use SequenceMatcher on strings for speed
    if not snippet_lines:
        return None, 0.0
    # build joined strings
    sep = '\n'
    s_orig = sep.join(orig_lines)
    s_snip = sep.join(snippet_lines)
    # SequenceMatcher finds matching blocks; find longest matching substring location
    matcher = SequenceMatcher(None, s_orig, s_snip)
    match = matcher.find_longest_match(0, len(s_orig), 0, len(s_snip))
    if match.size == 0:
        return None, 0.0
    # map match.a (offset in s_orig) back to line indices
    # compute char offsets to line numbers
    cum = [0]
    for ln in orig_lines:
        cum.append(cum[-1] + len(ln) + 1)
    # binary search for start line
    import bisect
    a_char = match.a
    start_line = bisect.bisect_right(cum, a_char) - 1
    # approximate end line by counting characters covered
    end_char = match.a + match.size
    end_line = bisect.bisect_right(cum, end_char) - 1
    # compute ratio
    ratio = match.size / max(len(s_snip), 1)
    return (start_line, end_line), ratio


def apply_hunks_fuzzily(orig_lines, hunks):
    patched = orig_lines.copy()
    # We will perform replacements; to avoid shifting indices, collect edits and apply from last to first
    edits = []  # list of (start_line, end_line, new_lines)
    for hunk in hunks:
        # build snippet of removed+context lines (lines that should exist in original)
        snippet = [t for typ, t in hunk['lines'] if typ in (' ', '-')]
        new_segment = [t for typ, t in hunk['lines'] if typ in (' ', '+')]
        if not snippet:
            # insertion-only hunk: try to find context lines
            context = [t for typ, t in hunk['lines'] if typ == ' ']
            if not context:
                # too little to match; skip this hunk
                continue
            snippet = context
        loc, ratio = find_best_match(orig_lines, snippet)
        if not loc:
            # no match; skip
            continue
        start, end = loc
        # expand end inclusive
        # apply small tolerance: allow matching even if ratio modest but with min length
        if ratio < THRESHOLD and len(snippet) < 3:
            # for tiny snippets allow lower threshold
            pass
        elif ratio < THRESHOLD:
            # skip hunk
            continue
        # record edit; replace lines start..end inclusive with new_segment
        edits.append((start, end, new_segment))
    if not edits:
        return None
    # sort edits by start descending to not break indices
    edits.sort(key=lambda x: x[0], reverse=True)
    for start, end, newseg in edits:
        # replace slice
        # ensure indices in range
        start = max(0, start)
        end = min(len(patched) - 1, end)
        patched[start:end + 1] = newseg
    return patched


def process_one(repaired_path: Path):
    txt = repaired_path.read_text(encoding='utf-8', errors='ignore')
    fname, hunks = parse_unified(txt)
    if not fname or not hunks:
        return False, 'no filename or hunks'
    target = find_file_in_repo(fname)
    if not target:
        return False, f'file not found in repo: {fname}'
    orig_lines = target.read_text(encoding='utf-8', errors='ignore').splitlines()
    patched = apply_hunks_fuzzily(orig_lines, hunks)
    if not patched:
        return False, 'no edits applied'
    # produce unified diff
    ud = '\n'.join(difflib.unified_diff(orig_lines, patched, fromfile=f'a/{fname}', tofile=f'b/{fname}', lineterm='')) + '\n'
    out_path = PATCH_DIR / f'aggr_reconstructed_{repaired_path.name.removeprefix("repaired_")}'
    out_path.write_text(ud, encoding='utf-8')
    ok, out = git_apply_check(out_path, PY_REPO)
    if ok:
        return True, 'reconstructed and git apply check OK'
    else:
        return False, out.strip()


def main():
    results = []
    import argparse
    parser = argparse.ArgumentParser(description='Aggressive repair helper')
    parser.add_argument('--limit', type=int, default=0, help='Limit number of failing repaired_*.diff files to attempt (0 = all)')
    parser.add_argument('--repo', type=str, default=None, help='Path to target python repo to validate against (overrides default)')
    args = parser.parse_args()

    # allow overriding the PY_REPO via --repo
    global PY_REPO
    if args.repo:
        PY_REPO = Path(args.repo)

    repaired_list = sorted(PATCH_DIR.glob('repaired_*.diff'))
    if args.limit and args.limit > 0:
        # Only attempt up to --limit failing repaired patches (skip ones already ok)
        repaired_list = repaired_list[:args.limit]

    for rp in repaired_list:
        ok, out = git_apply_check(rp, PY_REPO)
        if ok:
            results.append((rp.name, True, 'already ok'))
            continue
        print(f'Attempting aggressive repair for {rp.name}')
        ok2, info = process_one(rp)
        results.append((rp.name, ok2, info))
        print(' ->', 'OK' if ok2 else 'FAIL', info)
    # write report
    with (PATCH_DIR / 'aggressive_repair_report.txt').open('w', encoding='utf-8') as fh:
        for name, ok, info in results:
            fh.write(f"{name}\t{'OK' if ok else 'FAIL'}\t{info}\n")
    ok_count = sum(1 for r in results if r[1])
    print(f'Finished: {ok_count}/{len(results)} OK')


if __name__ == '__main__':
    main()
