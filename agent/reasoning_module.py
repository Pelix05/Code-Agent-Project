import os
from pathlib import Path
import traceback

try:
    from langchain_core.messages import HumanMessage
except ImportError:
    # Fallback minimal HumanMessage
    class HumanMessage:
        def __init__(self, content: str):
            self.content = content

try:
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None

REPORT_FILE = Path(__file__).resolve().parent.parent / "dynamic_analysis_report.txt"

# === Initialize LLM client (OpenAI / Qwen / Gemini) ===
API_KEY = os.getenv("OPENAI_API_KEY")  # or QWEN_API_KEY / GEMINI_API_KEY
llm_client = None
if ChatOpenAI and API_KEY:
    llm_client = ChatOpenAI(api_key=API_KEY, model="gpt-4", temperature=0.2)


def generate_fix_suggestion(error_log: str, language: str = "py") -> str:
    """
    Ask LLM to read a test failure log and suggest a possible patch/fix.
    """
    if llm_client is None:
        return "[Reasoning module skipped] LLM client not initialized."

    prompt = f"""
You are a software engineer assistant. A test has failed in a {language.upper()} project.
Below is the test output or error log:

{error_log}

Please suggest a possible fix. Focus on concrete code changes, not abstract ideas.
Provide the answer as a unified diff if possible, or as a short code snippet.
"""

    try:
        resp = llm_client.invoke([HumanMessage(content=prompt)])
        content = getattr(resp, "content", str(resp))
        return content.strip()
    except Exception as e:
        return f"[Reasoning module error] {e}\n{traceback.format_exc()}"


def run_reasoning_on_report(report_path: Path = REPORT_FILE, language: str = "py") -> None:
    """
    Parse the dynamic analysis report and generate suggestions for failed tests.
    """
    if not report_path.exists():
        print("[Reasoning] Report not found, skipping reasoning.")
        return

    report_text = report_path.read_text(encoding="utf-8")
    failed_sections = []
    current_fail = []
    in_fail = False
    for line in report_text.splitlines():
        if line.startswith("[-]"):
            in_fail = True
            current_fail = [line]
        elif in_fail and line.startswith("    "):
            current_fail.append(line)
        elif in_fail:
            # End of failure block
            failed_sections.append("\n".join(current_fail))
            in_fail = False
            current_fail = []

    # Catch last failure
    if in_fail and current_fail:
        failed_sections.append("\n".join(current_fail))

    # Generate suggestions
    print(f"[Reasoning] Found {len(failed_sections)} failed tests to analyze")
    for i, fail_text in enumerate(failed_sections, start=1):
        print(f"\n=== Reasoning on failure {i} ===")
        suggestion = generate_fix_suggestion(fail_text, language=language)
        print(suggestion)
        print("=== End suggestion ===")
