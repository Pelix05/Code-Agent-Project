"""
Automated evaluation harness for the agent.

Reads a dataset of bug cases, spins up the analyzers/dynamic tester for each
workspace, and records detection/repair metrics. Designed to be lightweight so
it can run on both the student's own project and selected OSS issues.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

# Reuse existing helpers when available
from lc_pipeline import run_iterative_fix_py, run_iterative_fix_cpp  # type: ignore

AGENT_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = AGENT_DIR / "eval_dataset.json"


@dataclass
class BugCase:
    id: str
    language: str  # "py" or "cpp"
    workspace: str  # relative path to workspace root
    description: str = ""


@dataclass
class BugResult:
    id: str
    language: str
    workspace: str
    detection_log: str
    dynamic_log: str
    auto_fix_summary: str
    detected: bool
    tests_passed: bool
    repair_successful: bool
    duration_seconds: float


def run_cmd(cmd: str, cwd: Optional[Path] = None) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return (result.stdout or "") + (result.stderr or "")


def run_case(case: BugCase) -> BugResult:
    workspace_path = AGENT_DIR / case.workspace
    if not workspace_path.exists():
        raise FileNotFoundError(f"Workspace {case.workspace} does not exist.")

    repo_dir = workspace_path / ("python_repo" if case.language == "py" else "cpp_project")
    if case.language == "cpp":
        repo_dir = repo_dir / "puzzle-2"

    start = time.time()
    static_cmd = f'py -3 -u analyzer_{"py" if case.language == "py" else "cpp"}.py --repo-dir "{repo_dir}"'
    dynamic_cmd = (
        f'py -3 -u dynamic_tester.py --py --py-repo "{repo_dir}"'
        if case.language == "py"
        else f'py -3 -u dynamic_tester.py --cpp --cpp-repo "{repo_dir}"'
    )

    static_log = run_cmd(static_cmd, cwd=AGENT_DIR)
    dynamic_log = run_cmd(dynamic_cmd, cwd=AGENT_DIR)

    if case.language == "py":
        auto_fix_result = run_iterative_fix_py(repo_dir=str(repo_dir), max_iters=3)
    else:
        auto_fix_result = run_iterative_fix_cpp(repo_dir=str(repo_dir), max_iters=3)

    duration = time.time() - start

    detected_flag = "error" in static_log.lower() or "bug" in static_log.lower()
    tests_passed_flag = "FAIL" not in dynamic_log
    repair_flag = isinstance(auto_fix_result, dict) and auto_fix_result.get("success")

    return BugResult(
        id=case.id,
        language=case.language,
        workspace=case.workspace,
        detection_log=static_log,
        dynamic_log=dynamic_log,
        auto_fix_summary=json.dumps(auto_fix_result, ensure_ascii=False) if isinstance(auto_fix_result, dict) else str(auto_fix_result),
        detected=detected_flag,
        tests_passed=tests_passed_flag,
        repair_successful=bool(repair_flag),
        duration_seconds=duration,
    )


def load_dataset(path: Path) -> List[BugCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [BugCase(**item) for item in data]


def main():
    parser = argparse.ArgumentParser(description="Run automated evaluation across multiple bug cases.")
    parser.add_argument("--dataset", type=str, default=str(DEFAULT_DATASET), help="Path to eval dataset JSON.")
    parser.add_argument("--output", type=str, default=str(Path("reports") / "eval_results.json"), help="Where to write results.")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise SystemExit(f"Dataset file not found: {dataset_path}")

    bug_cases = load_dataset(dataset_path)
    results = []
    for case in bug_cases:
        try:
            result = run_case(case)
            results.append(result)
        except Exception as exc:
            results.append(
                BugResult(
                    id=case.id,
                    language=case.language,
                    workspace=case.workspace,
                    detection_log=str(exc),
                    dynamic_log="",
                    auto_fix_summary="",
                    detected=False,
                    tests_passed=False,
                    repair_successful=False,
                    duration_seconds=0.0,
                )
            )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] Evaluation results saved to {output_path}")


if __name__ == "__main__":
    main()
