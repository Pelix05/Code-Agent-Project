from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPORT_FILE = Path(__file__).resolve().parent / "analysis_report_py.txt"
SNIPPET_FILE = Path(__file__).resolve().parent / "snippets" / "bug_snippets_py.txt"
SNIPPET_FILE.parent.mkdir(exist_ok=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--repo-dir', type=str, help='Optional path to python repo to analyze')
    return p.parse_args()


def run_command(cmd, cwd=None):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return result.returncode, result.stdout + result.stderr


def analyze_python(repo_dir: str = None):
    # repo_dir overrides the default python_repo under project root
    if repo_dir:
        python_repo = Path(repo_dir)
    else:
        python_repo = BASE_DIR / "python_repo"
    print("[*] Running Python analysis (pylint + flake8 + bandit)...")
    # Prefer to run linters with the Windows Python launcher 'py -3' when available
    # so the same interpreter that has pygame gets used by the linters.
    launcher = "python -m"
    ret, _ = run_command("py -3 -c \"import sys\"", cwd=BASE_DIR)
    if ret == 0:
        launcher = "py -3 -m"

    # pylint: only errors and fatal (disable refactor, convention, warning)
    # Disable E1101 (no-member) globally for this analysis run to avoid false
    # positives coming from pygame's C extension members which static
    # analyzers can't always introspect.
    # Note: don't use --enable to avoid re-enabling E1101; rely on defaults and
    # explicitly disable noisy rules instead.
    cmd1 = f"{launcher} pylint --disable=R,C,W,E1101 --score=n --exit-zero --recursive=y ."
    ret1, output1 = run_command(cmd1, cwd=python_repo)

    # flake8: focus on syntax error, undefined name, unused import
    cmd2 = f"{launcher} flake8 --select=E9,F63,F7,F82 --show-source --statistics ."
    ret2, output2 = run_command(cmd2, cwd=python_repo)

    # bandit: security issue
    cmd3 = f"{launcher} bandit -r ."
    ret3, output3 = run_command(cmd3, cwd=python_repo)

    # Combine outputs
    return output1 + "\n" + output2 + "\n" + output3


def resolve_source_file(file_path: str, repo_root: Path | None) -> Path | None:
    """Try to locate the file referenced by the analyzer output."""
    candidate_paths = []
    path_obj = Path(file_path)
    if path_obj.is_absolute():
        candidate_paths.append(path_obj)
    if repo_root:
        candidate_paths.append((repo_root / path_obj).resolve())
        # Some linters output paths like repo_name/foo.py; also try stripping the leading folder name
        try:
            parts = path_obj.parts
            if parts and (repo_root / Path(*parts[1:])).exists():
                candidate_paths.append((repo_root / Path(*parts[1:])).resolve())
        except Exception:
            pass
    candidate_paths.append((BASE_DIR / file_path).resolve())
    candidate_paths.append((BASE_DIR / "python_repo" / file_path).resolve())
    for candidate in candidate_paths:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def extract_snippets(report_content, repo_root: Path | None = None):
    pattern = r"([^\s:]+\.py):(\d+):"
    matches = re.findall(pattern, report_content)
    print(f"[*] Found {len(matches)} Python issues")

    snippets = []
    for file_path, line_str in matches[:20]:
        try:
            line_num = int(line_str)
            source_file = resolve_source_file(file_path, repo_root)

            if source_file and source_file.exists():
                lines = source_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                start = max(0, line_num - 5)
                end = min(len(lines), line_num + 5)
                snippet = "\n".join(lines[start:end])
                entry = f"--- {file_path}:{line_num} ---\n{snippet}\n"
                snippets.append(entry)
        except Exception as e:
            print(f"[!] Failed to extract snippet from {file_path}:{line_str} -> {e}")

    if snippets:
        SNIPPET_FILE.write_text("\n\n".join(snippets), encoding="utf-8")
        print(f"[+] Python snippets saved to {SNIPPET_FILE}")


if __name__ == "__main__":
    args = parse_args()
    report = analyze_python(repo_dir=args.repo_dir)
    REPORT_FILE.write_text(report, encoding="utf-8")
    print(f"[+] Python analysis saved to {REPORT_FILE}")
    repo_root = Path(args.repo_dir).resolve() if args.repo_dir else None
    extract_snippets(report, repo_root=repo_root)
