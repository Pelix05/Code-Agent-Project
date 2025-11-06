import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from prompts import BUG_FIX_PROMPT

# === Load env ===
load_dotenv()

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
QWEN_KEY = os.getenv("QWEN_API_KEY")
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "deepseek-coder")

# === Clients ===
gemini_client = None
if GEMINI_KEY:
    gemini_client = OpenAI(
        api_key=GEMINI_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
    )

qwen_client = None
if QWEN_KEY:
    qwen_client = OpenAI(
        api_key=QWEN_KEY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )

ollama_client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",
)

# === Folders ===
BASE_DIR = Path(__file__).resolve().parent
SNIPPETS_DIR = BASE_DIR / "snippets"
PATCHES_DIR = BASE_DIR / "patches"
PATCHES_DIR.mkdir(exist_ok=True)

REPORT_FILE = BASE_DIR / "analysis_report_cpp.txt"
SNIPPET_FILE = SNIPPETS_DIR / "bug_snippets_cpp.txt"
PATCH_FILE = PATCHES_DIR / "all_patches.diff"


def ask_llm(prompt: str) -> str:
    # Gemini first
    if gemini_client:
        try:
            resp = gemini_client.chat.completions.create(
                model="gemini-1.5-flash",
                messages=[{"role": "user", "content": prompt}]
            )
            text = resp.choices[0].message.content
            if text.strip().startswith("diff --git"):
                print("[+] Patch from Gemini")
                return text
            print("[!] Gemini invalid format, fallback...")
        except Exception as e:
            print(f"[!] Gemini failed: {e} → fallback to Qwen...")

    # Qwen second
    if qwen_client:
        try:
            resp = qwen_client.chat.completions.create(
                model="qwen1.5-14b-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
            )
            text = resp.choices[0].message.content
            if text.strip().startswith("diff --git"):
                print("[+] Patch from Qwen")
                return text
            print("[!] Qwen invalid format, fallback...")
        except Exception as e:
            print(f"[!] Qwen failed: {e} → fallback to Ollama...")

    # Ollama last
    try:
        resp = ollama_client.chat.completions.create(
            model=LOCAL_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.choices[0].message.content
        return text
    except Exception as e:
        return f"[!] Ollama failed: {e}"


def run_pipeline():
    if not REPORT_FILE.exists() or not SNIPPET_FILE.exists():
        print("[!] Report or snippet not found.")
        return

    report = REPORT_FILE.read_text(encoding="utf-8")
    snippets = SNIPPET_FILE.read_text(encoding="utf-8").split("--- ")

    print(f"[*] Found {len(snippets) - 1} snippets to process")

    with open(PATCH_FILE, "w", encoding="utf-8") as f:
        for i, snippet in enumerate(snippets[1:], start=1):
            print(f"🔧 Processing snippet {i}...")
            prompt = BUG_FIX_PROMPT.format(
                code_snippet=snippet.strip(),
                analysis=report
            )
            patch = ask_llm(prompt)

            if patch.strip().startswith("diff --git"):
                f.write(f"\n\n=== PATCH {i} ===\n")
                f.write(patch.strip())
                f.write("\n" + "=" * 50 + "\n")
                print(f"[+] Patch {i} appended to {PATCH_FILE}")
            else:
                print(f"[!] Skipping snippet {i}, invalid diff format")


if __name__ == "__main__":
    run_pipeline()
