"""
Small repair helper: try to clean sanitized patch files by removing stray non-diff lines
and ensuring reasonable hunk boundaries. This is conservative and non-destructive; it
writes repaired patches to patches/repaired_*.diff for manual inspection.

Usage: python agent/repair_patches.py --repo ../cpp_project/puzzle-2 --limit 3
"""
from pathlib import Path
import re
import subprocess
import argparse

PATCHES_DIR = Path(__file__).resolve().parent / "patches_py_fixed"
PY_REPO_DEFAULT = Path(__file__).resolve().parent.parent / 'python_repo'


def run(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as e:
        return False, str(e)


def repair_text(text: str) -> str:
    text = text.replace('\r\n', '\n')
    lines = text.splitlines()
    # find start of first diff-like header
    start_idx = 0
    for i, ln in enumerate(lines):
        if ln.startswith('diff --git') or ln.startswith('--- ') or ln.startswith('@@ '):
            start_idx = i
            break

    keep = []
    for ln in lines[start_idx:]:
        s = ln.strip()
        if s.startswith('```'):
            continue
        if s.startswith('Explanation:') or s.startswith('Note:'):
            break
        # drop clearly malformed index with placeholders
        if ln.startswith('index ') and ('<' in ln or '>' in ln):
            continue
        if ln.startswith(('diff --git', 'index ', '--- ', '+++ ')):
            keep.append(ln)
            continue
        if re.match(r'^@@ -\d+(,\d+)? \+\d+(,\d+)? @@', ln):
            keep.append(ln)
            continue
        if ln.startswith(('+', '-', ' ')):
            keep.append(ln)
            continue
        # ignore other lines (prose etc.)
    repaired = '\n'.join(keep).strip() + '\n' if keep else ''
    # ensure ---/+++ have a/ and b/ prefixes
    repaired = re.sub(r'^---\s+(?!a/)(.+)$', r'--- a/\1', repaired, flags=re.MULTILINE)
    repaired = re.sub(r'^\+\+\+\s+(?!b/)(.+)$', r'+++ b/\1', repaired, flags=re.MULTILINE)
    # if missing diff header, attempt to infer filename
    if 'diff --git' not in repaired:
        m = re.search(r"\b([\w\-_/\\]+\.py)\b", text)
        fname = m.group(1) if m else 'unknown.py'
        header = f'diff --git a/{fname} b/{fname}\n--- a/{fname}\n+++ b/{fname}\n'
        repaired = header + repaired
    # final sanity
    if not (repaired and '--- a/' in repaired and '+++ b/' in repaired):
        return ''
    return repaired


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=str, required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    files = sorted(PATCHES_DIR.glob("*.diff"))[: args.limit]
    if not files:
        print(f"No .diff files found in {PATCHES_DIR}")
        return

    for f in files:
        print(f"Processing {f.name}...")
        text = f.read_text(encoding="utf-8")
        repaired = repair_text(text)
        outp = PATCHES_DIR / f"repaired_{f.name}"
        outp.write_text(repaired, encoding="utf-8")
        print(f"Wrote {outp}")

        if not repaired:
            print("Repaired text empty, skipping git apply checks.")
            continue

        ok, out = run(["git", "apply", "--check", "-p1", str(outp)], cwd=repo)
        print("git apply --check -p1:", ok, out[:1000])
        if not ok:
            ok2, out2 = run(["git", "apply", "--check", "-p0", str(outp)], cwd=repo)
            print("git apply --check -p0:", ok2, out2[:1000])
            if ok2:
                ok3, out3 = run(["git", "apply", "-p0", str(outp)], cwd=repo)
                print("git apply -p0:", ok3, out3[:1000])
            else:
                print("Neither p1 nor p0 check succeeded.")
        else:
            ok3, out3 = run(["git", "apply", "-p1", str(outp)], cwd=repo)
            print("git apply -p1:", ok3, out3[:1000])


if __name__ == "__main__":
    main()
