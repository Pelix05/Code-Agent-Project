from pathlib import Path
import subprocess

BASE = Path(__file__).resolve().parent
PATCH_DIR = BASE / 'patches_py_fixed'
PY_REPO = Path(__file__).resolve().parent.parent / 'python_repo'

results = []
for p in sorted(PATCH_DIR.glob('repaired_*.diff')):
    try:
        proc = subprocess.run(['git', 'apply', '--check', str(p)], cwd=str(PY_REPO), capture_output=True, text=True)
        ok = proc.returncode == 0
        out = (proc.stdout or '') + (proc.stderr or '')
    except Exception as e:
        ok = False
        out = str(e)
    results.append((p.name, ok, out.strip()))
    print(f"{p.name}\t{'OK' if ok else 'FAIL'}\t{(out.splitlines()[0] if out else '')}")

summary = f"Summary: {sum(1 for r in results if r[1])}/{len(results)} OK\n"
print(summary)
with (PATCH_DIR / 'repaired_check_report.txt').open('w', encoding='utf-8') as fh:
    fh.write(summary)
    for name, ok, out in results:
        fh.write(f"{name}\t{'OK' if ok else 'FAIL'}\t{out}\n")
print('Wrote repaired_check_report.txt')
