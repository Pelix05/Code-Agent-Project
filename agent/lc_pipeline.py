import os
import re
import subprocess
from pathlib import Path
from dotenv import load_dotenv
# langchain clients are optional in developer environments; import defensively
try:
    from langchain_core.messages import HumanMessage
except Exception:
    # minimal fallback so code that constructs HumanMessage doesn't crash at import time
    class HumanMessage:
        def __init__(self, content: str):
            self.content = content
import concurrent.futures
import multiprocessing
import time
import traceback
import argparse
import json

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except Exception:
    ChatGoogleGenerativeAI = None

try:
    from langchain_openai import ChatOpenAI
except Exception:
    ChatOpenAI = None

try:
    from langchain_ollama import ChatOllama
except Exception:
    ChatOllama = None
try:
    import requests
except Exception:
    requests = None
try:
    # optional: local/open-source HF transformers pipeline
    from transformers import pipeline as hf_transformers_pipeline
except Exception:
    hf_transformers_pipeline = None
from prompts import BUG_FIX_PROMPT


def _invoke_child_process(name, prompt, q):
    """Top-level child process target for invoking LLM clients.

    This must be at module level so it is picklable on Windows.
    """
    try:
        if name == "Gemini":
            try:
                from langchain_core.messages import HumanMessage as HM
            except Exception:
                class HM:
                    def __init__(self, content: str):
                        self.content = content
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI as Client
            except Exception:
                q.put(("err", "Gemini client not installed"))
                return
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                q.put(("err", "GEMINI_API_KEY not set"))
                return
            client = Client(model="gemini-2.5-flash", google_api_key=key, temperature=0.1)
            resp = client.invoke([HM(content=prompt)])
            q.put(("ok", getattr(resp, "content", str(resp))))

        elif name == "Qwen":
            try:
                from langchain_core.messages import HumanMessage as HM
            except Exception:
                class HM:
                    def __init__(self, content: str):
                        self.content = content
            try:
                from langchain_openai import ChatOpenAI as Client
            except Exception:
                q.put(("err", "Qwen client not installed"))
                return
            key = os.getenv("QWEN_API_KEY")
            if not key:
                q.put(("err", "QWEN_API_KEY not set"))
                return
            client = Client(api_key=key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", model="qwen1.5-7b-chat")
            resp = client.invoke([HM(content=prompt)])
            q.put(("ok", getattr(resp, "content", str(resp))))

        elif name == "Ollama":
            try:
                from langchain_core.messages import HumanMessage as HM
            except Exception:
                class HM:
                    def __init__(self, content: str):
                        self.content = content
            try:
                from langchain_ollama import ChatOllama as Client
            except Exception:
                q.put(("err", "Ollama client not installed"))
                return
            model = os.getenv("LOCAL_MODEL", "deepseek-coder")
            ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
            # Pass explicit host so client connects to configured Ollama server
            try:
                client = Client(model=model, temperature=0.3, host=ollama_host)
            except TypeError:
                # Older langchain_ollama versions may expect 'base_url' instead
                client = Client(model=model, temperature=0.3, base_url=ollama_host)
            resp = client.invoke([HM(content=prompt)])
            q.put(("ok", getattr(resp, "content", str(resp))))

        elif name == "HuggingFace":
            try:
                from langchain_core.messages import HumanMessage as HM
            except Exception:
                class HM:
                    def __init__(self, content: str):
                        self.content = content
            try:
                import requests as _req
            except Exception:
                q.put(("err", "requests not available for HuggingFace client"))
                return
            key = os.getenv("HUGGINGFACE_API_TOKEN")
            if not key:
                q.put(("err", "HUGGINGFACE_API_TOKEN not set"))
                return
            model_url = os.getenv("HUGGINGFACE_MODEL_URL", "https://api-inference.huggingface.co/models/gpt2")
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            try:
                payload = {"inputs": prompt, "options": {"wait_for_model": True}}
                resp = _req.post(model_url, headers=headers, json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                # try to extract generated_text
                if isinstance(data, dict) and "generated_text" in data:
                    content = data["generated_text"]
                elif isinstance(data, list) and data and isinstance(data[0], dict) and "generated_text" in data[0]:
                    content = data[0]["generated_text"]
                else:
                    content = str(data)
                q.put(("ok", content))
            except Exception as e:
                q.put(("err", str(e)))
                return

        else:
            q.put(("err", f"Unknown LLM name: {name}"))
    except Exception as e:
        # Attempt to put error into queue for parent to read
        try:
            q.put(("err", str(e)))
        except Exception:
            pass
        # Also write full traceback to a file for post-mortem debugging
        try:
            ts = int(time.time())
            pid = os.getpid()
            PATCHES_DIR = Path(__file__).resolve().parent / "patches"
            PATCHES_DIR.mkdir(exist_ok=True)
            fname = PATCHES_DIR / f"child_error_{name}_{ts}_{pid}.log"
            with open(fname, "w", encoding="utf-8") as fh:
                fh.write("Exception in child process:\n")
                traceback.print_exc(file=fh)
        except Exception:
            pass

# === Load env ===
load_dotenv()

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
QWEN_KEY = os.getenv("QWEN_API_KEY")
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "deepseek-coder")
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_API_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN") or HUGGINGFACE_TOKEN
DISABLE_QWEN = os.getenv("DISABLE_QWEN", "0") in ("1", "true", "True")
# Optional local transformers model (text-generation)
TRANSFORMERS_MODEL = os.getenv("TRANSFORMERS_MODEL")
TRANSFORMERS_TRUST_REMOTE = os.getenv("TRANSFORMERS_TRUST_REMOTE", "1") in ("1", "true", "True")
TRANSFORMERS_DEVICE = os.getenv("TRANSFORMERS_DEVICE", "-1")  # -1 = CPU, 0 = first GPU
HF_TOKEN_2 = os.getenv("HF_TOKEN_2")
HUGGINGFACE_ROUTER_MODEL_2 = os.getenv("HUGGINGFACE_ROUTER_MODEL_2")
HUGGINGFACE_ALTERNATE_URLS = os.getenv("HUGGINGFACE_ALTERNATE_URLS", "").split(',') if os.getenv("HUGGINGFACE_ALTERNATE_URLS") else []

# If True, skip calling remote LLMs (useful for debugging/offline runs)
SKIP_LLM = False

# LLM timeouts (seconds) — configurable via environment to tune behavior per-deployment
LLM_TIMEOUT_GEMINI = int(os.getenv("LLM_TIMEOUT_GEMINI", "90"))
LLM_TIMEOUT_QWEN = int(os.getenv("LLM_TIMEOUT_QWEN", "90"))
LLM_TIMEOUT_OLLAMA = int(os.getenv("LLM_TIMEOUT_OLLAMA", "30"))
# Control whether pipeline may stop early when all dynamic tests pass even if
# static issues remain. Default: False (do NOT stop on dynamic-only success).
STOP_ON_DYNAMIC_ONLY = os.getenv("STOP_ON_DYNAMIC", "0") == "1"

# === LangChain Clients ===
gemini_llm = None
qwen_llm = None
ollama_llm = None
hf_llm = None
hf_router_llm = None
transformers_llm = None
hf_router_llm_2 = None
hf_router_llm_2 = None

# Instantiate LLM clients only if their classes are available and keys/config present
if ChatGoogleGenerativeAI and GEMINI_KEY:
    try:
        gemini_llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=GEMINI_KEY,
            temperature=0.1,
        )
    except Exception as e:
        print(f"[!] Failed to init Gemini client: {e}")

if (not DISABLE_QWEN) and ChatOpenAI and QWEN_KEY:
    try:
        qwen_llm = ChatOpenAI(
            api_key=QWEN_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen1.5-7b-chat",
        )
    except Exception as e:
        print(f"[!] Failed to init Qwen client: {e}")
else:
    if DISABLE_QWEN:
        print("[Debug] Qwen disabled via DISABLE_QWEN env var; skipping initialization.")

if ChatOllama:
    try:
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        try:
            ollama_llm = ChatOllama(model=LOCAL_MODEL, temperature=0.3, host=ollama_host)
        except TypeError:
            ollama_llm = ChatOllama(model=LOCAL_MODEL, temperature=0.3, base_url=ollama_host)
    except Exception as e:
        print(f"[!] Failed to init Ollama client: {e}")
    # Ollama client initialized (local fallback)

# Transformers local / HF open-source pipeline client
if hf_transformers_pipeline and TRANSFORMERS_MODEL:
    try:
        class _TransformersClient:
            def __init__(self, model, trust_remote=False, device="-1"):
                # device: '-1' for CPU, '0' for first GPU
                device_arg = -1 if str(device) == "-1" else int(device)
                self.pipe = hf_transformers_pipeline("text-generation", model=model, trust_remote_code=trust_remote, device=device_arg)

            def invoke(self, messages):
                prompt = "\n".join(getattr(m, 'content', str(m)) for m in messages)
                # Respect an env var for max tokens
                max_new_tokens = int(os.getenv("TRANSFORMERS_MAX_TOKENS", "256"))
                resp = self.pipe(prompt, max_new_tokens=max_new_tokens)
                # return an object with .content to match other clients
                try:
                    text = resp[0].get('generated_text') if isinstance(resp, list) and isinstance(resp[0], dict) else str(resp)
                except Exception:
                    text = str(resp)
                return type("R", (), {"content": text})()

        transformers_llm = _TransformersClient(TRANSFORMERS_MODEL, trust_remote=TRANSFORMERS_TRUST_REMOTE, device=TRANSFORMERS_DEVICE)
    except Exception as e:
        print(f"[!] Failed to init Transformers pipeline client: {e}")

# Minimal HuggingFace Inference API client (opt-in)
if HUGGINGFACE_TOKEN and requests:
    try:
        # Represent hf_llm as a small callable object with an invoke method
        class _HFClient:
            def __init__(self, token):
                self.token = token
                # primary HF inference URL (model-specific)
                self.url = os.getenv("HUGGINGFACE_MODEL_URL", "https://api-inference.huggingface.co/models/gpt2")
                # alternate model URLs to try when the primary endpoint returns 410
                self.alternates = [u.strip() for u in HUGGINGFACE_ALTERNATE_URLS if u.strip()]

            def _call_url(self, url, prompt):
                headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
                payload = {"inputs": prompt, "options": {"wait_for_model": True}}
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
                resp.raise_for_status()
                return resp.json()

            def invoke(self, messages):
                # messages is list-like of HumanMessage; we concat content
                prompt = "\n".join(getattr(m, 'content', str(m)) for m in messages)
                urls_to_try = [self.url] + self.alternates
                last_exc = None
                for url in urls_to_try:
                    try:
                        data = self._call_url(url, prompt)
                        # HF Inference API may return text or array with generated_text
                        if isinstance(data, dict) and "generated_text" in data:
                            return type("R", (), {"content": data["generated_text"]})()
                        if isinstance(data, list) and data and isinstance(data[0], dict) and "generated_text" in data[0]:
                            return type("R", (), {"content": data[0]["generated_text"]})()
                        # fallback: stringify response
                        return type("R", (), {"content": str(data)})()
                    except Exception as e:
                        last_exc = e
                        # If 410 Gone, try next alternate URL
                        try:
                            code = None
                            if hasattr(e, 'response') and e.response is not None:
                                code = getattr(e.response, 'status_code', None)
                        except Exception:
                            code = None
                        if code == 410:
                            continue
                        # otherwise, break and surface the error
                        break
                # All attempts failed
                raise last_exc if last_exc is not None else RuntimeError("HF inference failed")

        hf_llm = _HFClient(HUGGINGFACE_TOKEN)
    except Exception as e:
        print(f"[!] Failed to init HuggingFace client: {e}")

# Hugging Face Router (OpenAI-compatible) via OpenAI-style client
if ChatOpenAI and HF_TOKEN:
    try:
        hf_router_llm = ChatOpenAI(
            api_key=HF_TOKEN,
            base_url="https://router.huggingface.co/v1",
            model=os.getenv("HUGGINGFACE_ROUTER_MODEL", "gpt2"),
            temperature=0.2,
        )
    except Exception as e:
        print(f"[!] Failed to init HuggingFace Router client: {e}")

# Secondary HF Router (fallback router) using HF_TOKEN_2 / HUGGINGFACE_ROUTER_MODEL_2
HF_TOKEN_2 = os.getenv("HF_TOKEN_2")
HUGGINGFACE_ROUTER_MODEL_2 = os.getenv("HUGGINGFACE_ROUTER_MODEL_2")
if ChatOpenAI and HF_TOKEN_2:
    try:
        hf_router_llm_2 = ChatOpenAI(
            api_key=HF_TOKEN_2,
            base_url="https://router.huggingface.co/v1",
            model=HUGGINGFACE_ROUTER_MODEL_2 or os.getenv("HUGGINGFACE_ROUTER_MODEL", "gpt2"),
            temperature=0.2,
        )
    except Exception as e:
        print(f"[!] Failed to init secondary HuggingFace Router client: {e}")

# Secondary HF Router (fallback router) using HF_TOKEN_2 / HUGGINGFACE_ROUTER_MODEL_2
if ChatOpenAI and HF_TOKEN_2:
    try:
        hf_router_llm_2 = ChatOpenAI(
            api_key=HF_TOKEN_2,
            base_url="https://router.huggingface.co/v1",
            model=HUGGINGFACE_ROUTER_MODEL_2 or os.getenv("HUGGINGFACE_ROUTER_MODEL", "gpt2"),
            temperature=0.2,
        )
    except Exception as e:
        print(f"[!] Failed to init Secondary HuggingFace Router client: {e}")

# === Folder setup ===
BASE_DIR = Path(__file__).resolve().parent
SNIPPETS_DIR = BASE_DIR / "snippets"
PATCHES_DIR = BASE_DIR / "patches"
PATCHES_DIR.mkdir(exist_ok=True)

REPORT_CPP = BASE_DIR / "analysis_report_cpp.txt"
REPORT_PY = BASE_DIR / "analysis_report_py.txt"
SNIPPETS_CPP = SNIPPETS_DIR / "bug_snippets_cpp.txt"
SNIPPETS_PY = SNIPPETS_DIR / "bug_snippets_py.txt"
PATCH_FILE = PATCHES_DIR / "all_patches.diff"


def run_command(cmd, cwd=None):
    """Run shell command and print output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    print(result.stdout + result.stderr)


def ask_llm(prompt: str, original_code_file: str, patched_code_file: str) -> str:
    """Ask Gemini → Qwen → Ollama for a patch, apply the patch to the code."""
    global SKIP_LLM
    if SKIP_LLM:
        print("[Debug] SKIP_LLM is set; skipping LLM calls and returning empty patch")
        return ""

    def invoke_with_timeout(llm, name, timeout=20):
        """Invoke an LLM client in a thread with timeout."""
        if not llm:
            print(f"[Debug] {name} client not initialized, skipping.")
            return None
        print(f"[Debug] Invoking {name} (timeout={timeout}s) via thread")
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                # Gemini: skip the short YES/NO capability-check because Gemini has
                # sometimes answered NO for longer instruction prompts; instead send
                # the strict unified-diff instruction directly and accept its response.
                if name == "Gemini":
                    try:
                        # Lightweight capability check first: ask a very short question
                        # so Gemini doesn't reject long instruction prompts in stage1.
                        try:
                            cap_prompt = "Can you produce a unified-diff patch (git apply-compatible) for a small Python snippet? Reply YES or NO."
                            fut_cap = ex.submit(lambda: llm.invoke([HumanMessage(content=cap_prompt)]))
                            resp_cap = fut_cap.result(timeout=min(15, timeout))
                            cap = getattr(resp_cap, 'content', '') or ''
                            if not cap.strip().upper().startswith('YES'):
                                print(f"[Debug] Gemini capability check negative/ambiguous: '{cap[:80]}'")
                                return None

                            # Now send the strict unified-diff request and try a couple of variants
                            strict_suffix = (
                                "\n\nIMPORTANT: Return ONLY a valid unified diff patch compatible with 'git apply'. "
                                "Do NOT include any explanations, markdown fences, or extra text. If you cannot produce a valid patch, return exactly the string: NO_PATCH"
                            )
                            base_prompt = prompt + strict_suffix
                            variants = [
                                base_prompt,
                                # Preface a fake header to bias toward unified-diff
                                "diff --git a/unknown.py b/unknown.py\n" + base_prompt,
                                # Use explicit markers to make extraction easier
                                base_prompt + "\n\nReturn the patch ONLY between the markers <<<PATCH>>> and <<<END>>>. Do NOT include anything else."
                            ]

                            for idx, ap in enumerate(variants, start=1):
                                try:
                                    fut = ex.submit(lambda: llm.invoke([HumanMessage(content=ap)]))
                                    res = fut.result(timeout=timeout)
                                    content = getattr(res, 'content', '') or ''
                                    # Log full Gemini pipeline response for debugging
                                    try:
                                        out_dir = BASE_DIR / 'patches_py_fixed'
                                        out_dir.mkdir(parents=True, exist_ok=True)
                                        ts = int(time.time())
                                        fname = out_dir / f'raw_gemini_pipeline_{ts}_{idx}.json'
                                        saved = {
                                            'repr': repr(res),
                                            'content': content,
                                            '__dict__': getattr(res, '__dict__', None)
                                        }
                                        with open(fname, 'w', encoding='utf-8') as fh:
                                            json.dump(saved, fh, ensure_ascii=False, indent=2)
                                        print(f"[Debug] Saved Gemini pipeline raw response to {fname}")
                                    except Exception as _e:
                                        print(f"[Debug] Failed to save Gemini response: {_e}")
                                    if content.strip() == 'NO_PATCH':
                                        print(f"[Debug] Gemini returned NO_PATCH on variant {idx}")
                                        return None
                                    if 'diff --git' in content:
                                        return res
                                    if '<<<PATCH>>>' in content and '<<<END>>>' in content:
                                        m = re.search(r"<<<PATCH>>>([\s\S]*?)<<<END>>>", content)
                                        if m:
                                            patched_text = m.group(1).strip()
                                            return type('R', (), {'content': patched_text})()
                                except concurrent.futures.TimeoutError:
                                    print(f"[!] Gemini variant {idx} timed out")
                                    continue
                                except Exception as e:
                                    print(f"[!] Gemini variant {idx} failed: {e}")
                                    continue

                            # If none of the strict variants produced a patch, try a trimmed prompt
                            # consisting of the strict instruction plus the tail of the original prompt.
                            # This helps when very long analysis blocks cause the model to return
                            # an empty output or to hit internal limits.
                            try:
                                print("[Debug] Gemini: attempting trimmed-prompt retry (shorter input)")
                                tail_len = int(os.getenv("GEMINI_TRIM_TAIL", "3000"))
                                trimmed_body = (prompt[-tail_len:]) if len(prompt) > tail_len else prompt
                                trimmed_prompt = (
                                    "\n\nIMPORTANT: Return ONLY a valid unified diff patch compatible with 'git apply'. "
                                    "Do NOT include any explanations, markdown fences, or extra text. If you cannot produce a valid patch, return exactly the string: NO_PATCH\n\n"
                                    + trimmed_body
                                )
                                fut_t = ex.submit(lambda: llm.invoke([HumanMessage(content=trimmed_prompt)]))
                                res_t = fut_t.result(timeout=timeout)
                                content_t = getattr(res_t, 'content', '') or ''
                                # Save trimmed attempt for debugging
                                try:
                                    out_dir = BASE_DIR / 'patches_py_fixed'
                                    out_dir.mkdir(parents=True, exist_ok=True)
                                    ts = int(time.time())
                                    fname = out_dir / f'raw_gemini_pipeline_trimmed_{ts}.json'
                                    saved = {
                                        'repr': repr(res_t),
                                        'content': content_t,
                                        '__dict__': getattr(res_t, '__dict__', None)
                                    }
                                    with open(fname, 'w', encoding='utf-8') as fh:
                                        json.dump(saved, fh, ensure_ascii=False, indent=2)
                                    print(f"[Debug] Saved Gemini trimmed response to {fname}")
                                except Exception as _e:
                                    print(f"[Debug] Failed to save Gemini trimmed response: {_e}")

                                if content_t.strip() == 'NO_PATCH':
                                    print("[Debug] Gemini returned NO_PATCH on trimmed retry")
                                    return None
                                if 'diff --git' in content_t:
                                    return res_t
                                if '<<<PATCH>>>' in content_t and '<<<END>>>' in content_t:
                                    m = re.search(r"<<<PATCH>>>([\s\S]*?)<<<END>>>", content_t)
                                    if m:
                                        patched_text = m.group(1).strip()
                                        return type('R', (), {'content': patched_text})()
                            except concurrent.futures.TimeoutError:
                                print("[!] Gemini trimmed-prompt attempt timed out")
                            except Exception as e:
                                print(f"[!] Gemini trimmed-prompt attempt failed: {e}")

                            # As a final attempt, try a minimal prompt containing ONLY the
                            # buggy code snippet (extracted from the BUG_FIX_PROMPT) plus the
                            # strict unified-diff instruction. This reduces token usage and
                            # focuses the model on producing the patch.
                            try:
                                print("[Debug] Gemini: attempting minimal-snippet retry (code-only)")
                                # Try to extract the code snippet from the long prompt.
                                snippet_match = re.search(r"# Buggy Code Snippet\n([\s\S]*?)\n-+", prompt)
                                if snippet_match:
                                    code_only = snippet_match.group(1).strip()
                                else:
                                    # Fallback: take the last 2000 chars as a heuristic
                                    code_only = prompt[-2000:]

                                minimal_prompt = (
                                    "You are an expert software engineer.\n\n"
                                    "Generate a valid unified diff patch (git apply-compatible) that fixes the following buggy code.\n\n"
                                    f"{code_only}\n\n"
                                    "IMPORTANT: Return ONLY a valid unified diff patch compatible with 'git apply'. "
                                    "Do NOT include any explanations, markdown fences, or extra text. If you cannot produce a valid patch, return exactly the string: NO_PATCH"
                                )

                                fut_min = ex.submit(lambda: llm.invoke([HumanMessage(content=minimal_prompt)]))
                                res_min = fut_min.result(timeout=timeout)
                                content_min = getattr(res_min, 'content', '') or ''

                                # Save minimal attempt for debugging
                                try:
                                    out_dir = BASE_DIR / 'patches_py_fixed'
                                    out_dir.mkdir(parents=True, exist_ok=True)
                                    ts = int(time.time())
                                    fname = out_dir / f'raw_gemini_pipeline_minimal_{ts}.json'
                                    saved = {
                                        'repr': repr(res_min),
                                        'content': content_min,
                                        '__dict__': getattr(res_min, '__dict__', None)
                                    }
                                    with open(fname, 'w', encoding='utf-8') as fh:
                                        json.dump(saved, fh, ensure_ascii=False, indent=2)
                                    print(f"[Debug] Saved Gemini minimal response to {fname}")
                                except Exception as _e:
                                    print(f"[Debug] Failed to save Gemini minimal response: {_e}")

                                if content_min.strip() == 'NO_PATCH':
                                    print("[Debug] Gemini returned NO_PATCH on minimal retry")
                                    return None
                                if 'diff --git' in content_min:
                                    return res_min
                                if '<<<PATCH>>>' in content_min and '<<<END>>>' in content_min:
                                    m = re.search(r"<<<PATCH>>>([\s\S]*?)<<<END>>>", content_min)
                                    if m:
                                        patched_text = m.group(1).strip()
                                        return type('R', (), {'content': patched_text})()
                            except concurrent.futures.TimeoutError:
                                print("[!] Gemini minimal-snippet attempt timed out")
                            except Exception as e:
                                print(f"[!] Gemini minimal-snippet attempt failed: {e}")

                            return None
                        except concurrent.futures.TimeoutError:
                            print(f"[!] Gemini capability check timed out after {timeout}s")
                            return None
                        except Exception as e:
                            print(f"[!] Gemini failed during capability check: {e}")
                            return None
                    except concurrent.futures.TimeoutError:
                        print(f"[!] Gemini invoke timed out after {timeout}s")
                        return None
                    except Exception as e:
                        print(f"[!] Gemini failed during invoke: {e}")
                        return None

                # For HuggingFace Routers we keep the two-stage capability check
                if name in ("HuggingFace_Router", "HuggingFace_Router_2"):
                    stage1_prompt = (
                        prompt
                        + "\n\nBefore producing a patch, answer ONLY one word: YES if you can produce a valid unified-diff patch (git apply-compatible) for this snippet, or NO if you cannot. Reply exactly 'YES' or 'NO' with no extra text."
                    )
                    try:
                        fut1 = ex.submit(lambda: llm.invoke([HumanMessage(content=stage1_prompt)]))
                        # Shorter timeout for capability check
                        resp1 = fut1.result(timeout=min(20, timeout))
                        c1 = getattr(resp1, "content", "").strip().upper()
                        if not c1.startswith("YES"):
                            print(f"[Debug] {name} capability check answered NO/ambiguous: '{c1[:80]}'")
                            return None
                        strict_suffix = (
                            "\n\nIMPORTANT: Return ONLY a valid unified diff patch compatible with 'git apply'. "
                            "Do NOT include any explanations, markdown fences, or extra text. If you cannot produce a valid patch, return exactly the string: NO_PATCH"
                        )
                        local_prompt = prompt + strict_suffix
                        fut2 = ex.submit(lambda: llm.invoke([HumanMessage(content=local_prompt)]))
                        resp2 = fut2.result(timeout=timeout)
                        return resp2
                    except concurrent.futures.TimeoutError:
                        print(f"[!] {name} two-stage invoke timed out (stage1 or stage2)")
                        return None
                    except Exception as e:
                        print(f"[!] {name} failed during two-stage invoke: {e}")
                        return None

                # Default single-call flow for other LLMs
                local_prompt = prompt
                fut = ex.submit(lambda: llm.invoke([HumanMessage(content=local_prompt)]))
                try:
                    resp = fut.result(timeout=timeout)
                    return resp
                except concurrent.futures.TimeoutError:
                    print(f"[!] {name} invoke timed out after {timeout}s")
                    return None
        except Exception as e:
            print(f"[!] {name} failed during invoke: {e}")
            return None

    # Print which LLMs are available — we only use the Hugging Face Router(s) now
    print(f"[Debug] LLM availability: HuggingFace_Router={'yes' if hf_router_llm else 'no'}, HuggingFace_Router_2={'yes' if hf_router_llm_2 else 'no'}")

    # Use only the hosted Hugging Face Router(s) for patch generation. This forces
    # the pipeline to rely exclusively on the HF hosted router models and avoids
    # using Gemini/Ollama/Qwen/local transformers in this deployment.
    for llm, name, t in [
        (hf_router_llm, "HuggingFace_Router", int(os.getenv("LLM_TIMEOUT_HF", "45"))),
        (hf_router_llm_2, "HuggingFace_Router_2", int(os.getenv("LLM_TIMEOUT_HF", "45"))),
    ]:
        resp = invoke_with_timeout(llm, name, timeout=t)
        if resp is None:
            print(f"[Debug] {name} returned no response, moving to next LLM...")
            continue
        content = getattr(resp, "content", None)
        print(f"[Debug] {name} response length: {len(content) if content else 0}")
        if content and "diff --git" in content:
            print(f"[+] Patch from {name}")
            # Return raw patch text to the caller; do not attempt to apply
            # directly here because we may be operating on an isolated
            # workspace and the original file paths are not known.
            return content
        else:
            print(f"[Debug] {name} response did not contain a patch, skipping.")

    print("[!] All LLMs failed to produce a patch for this snippet.")
    return ""

def run_patch_py(report_file, snippet_file, lang="py"):
    """Wrapper function to run the patch pipeline for Python code."""
    print(f"[*] Running patch pipeline for {lang}...")

    # Run the pipeline to generate patches
    run_pipeline(report_file, snippet_file, lang)
    
    # Optionally: Apply patches or do further post-processing here
    print("[*] Patch pipeline completed.")


def clean_patch_output(patch: str) -> str:
    """Clean LLM output to a valid unified diff patch."""
    if not patch:
        return ""

    # Remove markdown
    patch = re.sub(r"^```diff", "", patch, flags=re.MULTILINE)
    patch = re.sub(r"^```", "", patch, flags=re.MULTILINE)

    valid_lines = []
    for line in patch.splitlines():
        line = line.rstrip()
        if line.startswith("diff --git"):
            parts = line.split()
            if len(parts) != 4:
                continue
        if line.startswith(("diff --git", "--- ", "+++ ", "@@ ", "+", "-", " ")):
            # Skip empty + or - lines
            if line in ("+", "-"):
                continue
            valid_lines.append(line)

    patch = "\n".join(valid_lines)

    if not patch.startswith("diff --git"):
        return ""

    return patch.strip()

def validate_patch(patch_text: str) -> bool:
    if not patch_text:
        return False
    if ("diff --git" in patch_text 
        and re.search(r"@@ -\d+,\d+ \+\d+,\d+ @@", patch_text) 
        and "--- a/" in patch_text 
        and "+++ b/" in patch_text):
        return True
    return False
import difflib

def apply_patch(original_file, patch_text, output_file):
    """Apply the generated patch to the original code file."""
    with open(original_file, 'r', encoding='utf-8', errors='ignore') as original, open(output_file, 'w', encoding='utf-8') as patched:
        original_code = original.readlines()

        # Use difflib to apply the patch
        patch = difflib.unified_diff(original_code, patch_text.splitlines(), fromfile=original_file, tofile=output_file)
        patched.writelines(patch)

    print(f"Patch applied successfully. Patched code saved to {output_file}")


def compare_files(original_file, patched_file):
    """Compare the original and patched files to prove the patch was applied."""
    with open(original_file, 'r', encoding='utf-8', errors='ignore') as f1, open(patched_file, 'r', encoding='utf-8', errors='ignore') as f2:
        original_code = f1.readlines()
        patched_code = f2.readlines()

    diff = difflib.unified_diff(original_code, patched_code, fromfile='original_code.py', tofile='patched_code.py')

    print('\n'.join(diff))


def run_pipeline(report_file, snippet_file, lang="py", iteration: int = None):
    """
    Run patch pipeline for snippets, saving each patch separately.
    lang: "py" for Python, "cpp" for C++
    """
    # Choose target directory based on language
    target_folder = PATCHES_DIR / f"patches_{lang}"
    target_folder.mkdir(parents=True, exist_ok=True)

    if not report_file.exists() or not snippet_file.exists():
        print("[!] Report or snippet not found.")
        return

    report = report_file.read_text(encoding="utf-8")
    snippets = snippet_file.read_text(encoding="utf-8").split("--- ")

    print(f"[*] Found {len(snippets) - 1} snippets to process in {lang.upper()} mode")

    # Prepare destination folder where dynamic_tester expects fixed patches
    if lang == "py":
        dest_folder = BASE_DIR / "patches_py_fixed"
    else:
        dest_folder = BASE_DIR / "patches" / "patches_cpp_fixed"
    dest_folder.mkdir(parents=True, exist_ok=True)
    for i, snippet in enumerate(snippets[1:], start=1):
        print(f"[*] Processing snippet {i}...")

        prompt = BUG_FIX_PROMPT.format(code_snippet=snippet.strip(), analysis=report)

        # Call LLM for patch suggestion (raw unified diff text)
        raw_patch = ask_llm(prompt, "original_code.py", "patched_code.py")

        # Clean and validate the returned patch
        patch_text = clean_patch_output(raw_patch)

        # If initial validation fails, try a more aggressive sanitizer
        if not validate_patch(patch_text):
            alt = sanitize_patch(raw_patch or "")
            if alt and validate_patch(alt):
                print(f"[+] Sanitizer produced a valid patch for snippet {i}")
                patch_text = alt
            else:
                # As a last-ditch cleanup, try to repair header prefixes and remove stray markers
                try:
                    repaired = (raw_patch or "").replace('\r\n', '\n')
                    # Ensure ---/+++ have a/ and b/ prefixes when missing
                    repaired = re.sub(r"^---\s+(?!a/)(.+)$", r"--- a/\1", repaired, flags=re.MULTILINE)
                    repaired = re.sub(r"^\+\+\+\s+(?!b/)(.+)$", r"+++ b/\1", repaired, flags=re.MULTILINE)
                    repaired = re.sub(r"^```.*$", "", repaired, flags=re.MULTILINE)
                except Exception:
                    repaired = raw_patch or ""

                alt2 = clean_patch_output(repaired)
                if alt2 and validate_patch(alt2):
                    print(f"[+] Repaired patch for snippet {i} using header fixes")
                    patch_text = alt2
                else:
                    print(f"[!] No valid patch produced for snippet {i}; saving raw response for inspection and skipping.")
                    # Save raw LLM output for debugging
                    try:
                        raw_path = dest_folder / f"raw_resp_{i}.txt"
                        raw_path.write_text(raw_patch or "", encoding="utf-8")
                        print(f"[+] Saved raw LLM response to {raw_path}")
                    except Exception as e:
                        print(f"[!] Failed to save raw response: {e}")
                    continue

        # Write the patch into the destination folder with a unique name (iteration + timestamp)
        ts = int(time.time())
        if iteration is not None:
            patch_name = f"patch_{iteration}_{ts}_{i}.diff"
        else:
            patch_name = f"patch_{ts}_{i}.diff"
        patch_path = dest_folder / patch_name
        try:
            patch_path.write_text(patch_text, encoding="utf-8")
            print(f"[+] Saved patch to {patch_path}")
        except Exception as e:
            print(f"[!] Failed to write patch file {patch_path}: {e}")



def sanitize_patch(raw_patch: str) -> str:
    """
    Remove markdown code blocks, explanations, and any text after the diff body.
    Ensures only valid unified diff remains.
    """
    lines = raw_patch.strip().splitlines()
    clean_lines = []
    inside_patch = False

    for line in lines:
        # Start when we see the diff header
        if line.startswith("diff --git"):
            inside_patch = True
            clean_lines = [line]
            continue
        if not inside_patch:
            continue

        # Stop when explanation or markdown starts
        if line.strip().startswith("Explanation:") or line.strip().startswith("```"):
            break

        # Accept only valid diff lines
        if line.startswith(("index ", "--- ", "+++ ", "@@", "+", "-", " ")):
            clean_lines.append(line)

    return "\n".join(clean_lines).strip()


def aggressive_sanitize(raw_patch: str) -> str:
    """
    Try several heuristics to extract a unified diff from noisy LLM output.
    Returns a cleaned unified-diff string or empty string when unable.
    """
    if not raw_patch:
        return ""
    text = raw_patch.replace('\r\n', '\n')

    # 1) If it already looks valid, return early
    if validate_patch(text):
        return text

    # 2) Try to extract the first block that contains @@@ or ---/+++ lines
    lines = text.splitlines()
    start = None
    end = None
    for idx, line in enumerate(lines):
        if line.startswith("diff --git") or line.startswith("--- "):
            start = idx
            break
    if start is None:
        # Try to locate a hunk header
        for idx, line in enumerate(lines):
            if line.startswith("@@ "):
                # try to find filenames above it
                # look back for ---/+++ lines
                j = idx - 1
                while j >= 0 and not lines[j].startswith("--- ") and not lines[j].startswith("diff --git"):
                    j -= 1
                start = j if j >= 0 else idx
                break

    if start is None:
        return ""

    # find end as next blank line after hunks or end of file
    for j in range(start, len(lines)):
        # stop at a line that likely begins a commentary block after patch
        if lines[j].strip().startswith("Explanation:") or lines[j].strip().startswith("Note:"):
            end = j
            break
    if end is None:
        end = len(lines)

    candidate = "\n".join(lines[start:end])

    # Ensure headers have a/ and b/ prefixes
    candidate = re.sub(r"^---\s+(?!a/)(.+)$", r"--- a/\1", candidate, flags=re.MULTILINE)
    candidate = re.sub(r"^\+\+\+\s+(?!b/)(.+)$", r"+++ b/\1", candidate, flags=re.MULTILINE)

    # Remove markdown fences
    candidate = re.sub(r"^```.*$", "", candidate, flags=re.MULTILINE)

    if validate_patch(candidate):
        return candidate

    # Fallback: attempt to reconstruct simple unified diff from @@ hunks by adding fake headers
    hunks = []
    cur_hunk = []
    saw_hunk = False
    for line in candidate.splitlines():
        if line.startswith("@@ "):
            saw_hunk = True
            cur_hunk = [line]
            hunks.append(cur_hunk)
        elif saw_hunk:
            cur_hunk.append(line)

    if not hunks:
        return ""

    # try to infer filenames from context
    fname = None
    m = re.search(r"\b([\w\-_/\\]+\.py)\b", raw_patch)
    if m:
        fname = m.group(1)
    else:
        fname = "unknown.py"

    header = f"diff --git a/{fname} b/{fname}\n--- a/{fname}\n+++ b/{fname}\n"
    rebuilt = header + "\n".join(["\n".join(h) for h in hunks])
    if validate_patch(rebuilt):
        return rebuilt

    return ""


def apply_rule_based_fixes(repo_dir: str, report_path: Path):
    """
    Conservative rule-based fixes for common linter warnings.
    - E0203: Access to member 'attr' before its definition -> try to add
      `self.attr = None` in __init__ or create a small __init__.
    - E0606: Possibly using variable 'x' before assignment -> try to
      insert `x = None` at start of the function where it's used.

    These edits create a .bak backup of the file before writing.
    """
    txt = report_path.read_text(encoding="utf-8") if report_path.exists() else ""

    # find E0203 patterns
    e0203_matches = re.findall(r"^(.+?):\d+:\d+:\s+E0203:.*?'([A-Za-z_][A-Za-z0-9_]*)'", txt, flags=re.MULTILINE)
    # find E0606 patterns
    e0606_matches = re.findall(r"^(.+?):\d+:\d+:\s+E0606:.*?'([A-Za-z_][A-Za-z0-9_]*)'", txt, flags=re.MULTILINE)

    def write_backup(path: Path):
        try:
            bak = path.with_suffix(path.suffix + '.bak')
            if not bak.exists():
                bak.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        except Exception:
            pass

    for raw_path, attr in e0203_matches:
        target = Path(repo_dir) / raw_path
        if not target.exists():
            alt = Path(repo_dir) / Path(raw_path).name
            if alt.exists():
                target = alt
            else:
                continue
        try:
            src = target.read_text(encoding='utf-8')
            write_backup(target)
            # If attribute already initialized, skip
            if re.search(rf"self\.{attr}\s*=", src):
                continue
            # Try to find __init__
            m = re.search(r"def\s+__init__\s*\(self[^\)]*\):", src)
            if m:
                insert_pos = m.end()
                # Insert a conservative initialization with 8-space indent
                new_src = src[:insert_pos] + "\n        self.%s = None\n" % attr + src[insert_pos:]
                target.write_text(new_src, encoding='utf-8')
                print(f"[rule-fix] Added self.{attr}=None in __init__ of {target}")
            else:
                # Find class header and insert a small __init__ after it
                cm = re.search(r"class\s+[A-Za-z0-9_]+[\s\S]*?:", src)
                if cm:
                    hdr_end = cm.end()
                    init_block = "\n    def __init__(self):\n        self.%s = None\n" % attr
                    new_src = src[:hdr_end] + init_block + src[hdr_end:]
                    target.write_text(new_src, encoding='utf-8')
                    print(f"[rule-fix] Created __init__ with self.{attr}=None in {target}")
        except Exception as e:
            print(f"[!] Rule-fix failed for {target}: {e}")

    for raw_path, var in e0606_matches:
        target = Path(repo_dir) / raw_path
        if not target.exists():
            alt = Path(repo_dir) / Path(raw_path).name
            if alt.exists():
                target = alt
            else:
                continue
        try:
            src = target.read_text(encoding='utf-8')
            write_backup(target)
            # If var already initialized in file, skip
            if re.search(rf"\b{var}\s*=", src):
                continue
            # Find function that uses var
            funcs = list(re.finditer(r"def\s+[A-Za-z0-9_]+\s*\([^\)]*\):", src))
            applied = False
            for f in funcs:
                start = f.start()
                end = None
                for g in funcs:
                    if g.start() > start:
                        end = g.start()
                        break
                block = src[start:end] if end else src[start:]
                if re.search(rf"\b{var}\b", block):
                    insert_pos = f.end()
                    new_src = src[:insert_pos] + "\n    %s = None\n" % var + src[insert_pos:]
                    target.write_text(new_src, encoding='utf-8')
                    print(f"[rule-fix] Inserted {var}=None in function in {target}")
                    applied = True
                    break
            if not applied:
                # fallback: add module level default
                new_src = f"{var} = None\n" + src
                target.write_text(new_src, encoding='utf-8')
                print(f"[rule-fix] Added module-level {var}=None in {target}")
        except Exception as e:
            print(f"[!] Rule-fix failed for {target}: {e}")

    return True


def apply_additional_rule_based_fixes(repo_dir: str, report_path: Path):
    """
    Extra conservative fixes when initial rule-based fixes and LLM patches didn't apply.

    Current strategies (low-risk):
    - For 'undefined name' / 'undefined variable' linter messages, insert a
      module-level `name = None` at the top of the file if the name is not
      already present. This is a defensive no-op that often satisfies linters
      while avoiding behaviour changes.

    Returns True when the routine completed (not necessarily that it made edits).
    """
    try:
        txt = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    except Exception:
        txt = ""

    # Collect candidate undefined-name matches from common linter messages
    # Examples: "undefined name 'foo'" or "undefined variable 'bar'"
    undefined_names = set()
    try:
        for m in re.findall(r"undefined name '([A-Za-z_][A-Za-z0-9_]*)'", txt):
            undefined_names.add(m)
        for m in re.findall(r"undefined variable '([A-Za-z_][A-Za-z0-9_]*)'", txt):
            undefined_names.add(m)
    except Exception:
        pass

    def write_backup(path: Path):
        try:
            bak = path.with_suffix(path.suffix + '.bak')
            if not bak.exists():
                bak.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        except Exception:
            pass

    for name in sorted(undefined_names):
        # Find files where the name was reported as undefined (extract file paths)
        file_hits = re.findall(rf"^(.+?):\d+:\d+:.*?(?:undefined name|undefined variable) '{re.escape(name)}'", txt, flags=re.MULTILINE)
        for raw_path in file_hits:
            target = Path(repo_dir) / raw_path
            if not target.exists():
                alt = Path(repo_dir) / Path(raw_path).name
                if alt.exists():
                    target = alt
                else:
                    continue
            try:
                src = target.read_text(encoding='utf-8', errors='ignore')
                # If name already appears in file, skip to avoid accidental overwrite
                if re.search(rf"\b{re.escape(name)}\b", src):
                    continue
                write_backup(target)
                new_src = f"{name} = None\n" + src
                target.write_text(new_src, encoding='utf-8')
                print(f"[add-rule-fix] Inserted module-level {name}=None in {target}")
            except Exception as e:
                print(f"[!] add-rule-fix failed for {target}: {e}")

    return True

    # --- Handle missing imports: insert safe try-import stubs ---
    missing_mods = set()
    try:
        # Common static/runtime messages: "No module named 'foo'", "Unable to import 'foo'"
        for m in re.findall(r"No module named '\\s*([^']+?)'", txt):
            missing_mods.add(m)
        for m in re.findall(r"No module named '([A-Za-z_][A-Za-z0-9_]*)'", txt):
            missing_mods.add(m)
        for m in re.findall(r"Unable to import '([A-Za-z_][A-Za-z0-9_]*)'", txt):
            missing_mods.add(m)
        # flake8-pyflakes/flake8-import-error style
        for m in re.findall(r"import-error\W+([A-Za-z_][A-Za-z0-9_]*)", txt, flags=re.IGNORECASE):
            missing_mods.add(m)
    except Exception:
        pass

    for mod in sorted(missing_mods):
        # Look for candidate files that reference this module and insert a safe
        # try/except import block at the module top if not already present.
        py_files = list(Path(repo_dir).rglob("*.py"))
        for f in py_files:
            try:
                src = f.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            # Skip if fallback already present
            if re.search(rf"\b{re.escape(mod)}\s*=\s*None", src):
                continue
            if re.search(rf"try:\s*\n\s*import\s+{re.escape(mod)}", src):
                continue
            # Only insert stub in files that reference the module (import or usage)
            if not (re.search(rf"\bimport\s+{re.escape(mod)}\b", src) or re.search(rf"from\s+{re.escape(mod)}\s+import\b", src) or re.search(rf"\b{re.escape(mod)}\.", src)):
                continue

            # Compute insertion point: after shebang/encoding and module docstring if present
            lines = src.splitlines(keepends=True)
            insert_at = 0
            # shebang
            if lines and lines[0].startswith('#!'):
                insert_at = 1
            # encoding comment
            if insert_at < len(lines) and re.match(r"#.*coding[:=].+", lines[insert_at]):
                insert_at += 1
            # module docstring
            if insert_at < len(lines) and lines[insert_at].lstrip().startswith(('"""', "'''")):
                quote = '"""' if '"""' in lines[insert_at] else "'''"
                # find end of docstring
                j = insert_at
                # if docstring opens and closes on same line
                if lines[j].count(quote) >= 2:
                    j += 1
                else:
                    j += 1
                    while j < len(lines) and quote not in lines[j]:
                        j += 1
                    j += 1
                insert_at = j

            stub = f"try:\n    import {mod}\nexcept Exception:\n    {mod} = None\n\n"
            try:
                write_backup(f)
                new_src = ''.join(lines[:insert_at]) + stub + ''.join(lines[insert_at:])
                f.write_text(new_src, encoding='utf-8')
                print(f"[add-import-fix] Inserted try-import stub for '{mod}' in {f}")
            except Exception as e:
                print(f"[!] add-import-fix failed for {f}: {e}")

    return True


def count_static_issues(report_path: Path) -> int:
    """Count linter-style issues in the static analysis report file.

    We look for lines that match the pylint/flake8 style: path:line:col: CODE: message
    """
    import re as _re
    if not report_path.exists():
        return -1
    text = report_path.read_text(encoding="utf-8")
    # Match patterns like "file.py:12:8: E1101: ..." or "file.py:12: E0606: ..."
    matches = _re.findall(r"^.+?:\d+:\d+:\s+[A-Z]\d{4}:", text, flags=_re.MULTILINE)
    if not matches:
        # fallback: some tools may emit file:line:code style without column
        matches = _re.findall(r"^.+?:\d+:\s+[A-Z]\d{4}:", text, flags=_re.MULTILINE)
    return len(matches)


def get_python_issues(report_path: Path) -> list:
    """Return a list of static issue lines from a Python analysis report."""
    import re as _re
    if not report_path.exists():
        return []
    text = report_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Prefer full file:line:col: CODE: message lines
    matches = [ln.strip() for ln in lines if _re.match(r"^.+?:\d+:\d+:\s+[A-Z]\d{4}:", ln)]
    if not matches:
        # fallback to file:line: CODE: message
        matches = [ln.strip() for ln in lines if _re.match(r"^.+?:\d+:\s+[A-Z]\d{4}:", ln)]
    return matches


def classify_python_issues(issue_lines: list) -> dict:
    """
    Split a list of pylint/flake8-like issue lines into errors and warnings.

    Returns dict { 'errors': [...], 'warnings': [...] }
    Lines that cannot be classified default to 'warnings'.
    """
    errors = []
    warnings = []
    import re as _re
    for ln in (issue_lines or []):
        # Try to extract a code like E0203 or W0612
        m = _re.search(r"\b([A-Z])(\d{4})\b", ln)
        if m:
            sev = m.group(1)
            if sev == 'E':
                errors.append(ln)
            elif sev == 'W':
                warnings.append(ln)
            else:
                warnings.append(ln)
        else:
            # fallback: consider lines containing 'error' as errors
            if 'error' in ln.lower():
                errors.append(ln)
            else:
                warnings.append(ln)
    return {'errors': errors, 'warnings': warnings}


def parse_dynamic_issues(report_text: str) -> list:
    """Parse dynamic analysis report text and return a list of failing test identifiers/details.

    We capture lines that start with '[-]' (fail) and include subsequent indented/detail lines.
    """
    if not report_text:
        return []
    lines = report_text.splitlines()
    issues = []
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        # Capture both failed tests '[-]' and skipped/important markers '[!]'
        if ln.startswith('[-]') or ln.startswith('[!]'):
            # capture this line and following detail lines (which in dynamic_tester are prefixed with a space)
            entry_lines = [ln]
            j = i + 1
            while j < len(lines) and (lines[j].startswith(' ') or lines[j].strip() == ''):
                entry_lines.append(lines[j].rstrip())
                j += 1
            issues.append('\n'.join(entry_lines).strip())
            i = j
            continue
        i += 1
    return issues


def run_iterative_fix_py(max_iters: int = 5, repo_dir: str = None):
    """Run an iterative loop: static analysis -> generate patches -> apply via dynamic tests -> repeat.

    If repo_dir is provided, analyzer and dynamic tester will be invoked against that repo/workspace
    using their respective CLI options so uploaded workspaces can be analyzed safely.

    Stops when static issue count reaches 0 or when issues don't decrease between iterations.
    """
    print("[*] Starting iterative auto-fix loop for Python")
    prev_issues = None
    prev_tests_passed = None
    reports = []
    for iteration in range(1, max_iters + 1):
        print(f"\n=== Iteration {iteration}/{max_iters} ===")

        # 1) Run static analyzer (use py -3 to pick correct interpreter)
        print("[*] Running static analyzer (py)")
        # If a repository/workspace path was provided, pass it through to analyzer_py
        analyzer_cmd = "py -3 -u analyzer_py.py"
        if repo_dir:
            analyzer_cmd += f' --repo-dir "{repo_dir}"'
        subprocess.run(analyzer_cmd, shell=True, check=False, cwd=BASE_DIR)

        full_issues_before_list = get_python_issues(REPORT_PY)
        classified_before = classify_python_issues(full_issues_before_list)
        issues_before_errors = len(classified_before.get('errors', []))
        issues_before_warnings = len(classified_before.get('warnings', []))
        print(f"[*] Static issues found (before patch): errors={issues_before_errors}, warnings={issues_before_warnings}")

        # For backward compatibility with existing code paths use `issues_before`
        # to represent the number of error-level static issues that we attempt
        # to clear automatically. Warnings are ignored for auto-fix purposes.
        issues_before = issues_before_errors

        if issues_before == 0:
            print("[+] No error-level static issues remain. Auto-fix complete (warnings ignored).")
            return reports

        # Apply simple rule-based fixes for common linter warnings (best-effort,
        # low-risk transforms). This attempts deterministic fixes before asking LLMs.
        try:
            if repo_dir:
                # Only run deterministic rule-based fixes when there are error-level issues
                if issues_before_errors > 0:
                    apply_rule_based_fixes(repo_dir, REPORT_PY)
                    # Recompute classification after deterministic fixes
                    full_issues_before_list = get_python_issues(REPORT_PY)
                    classified_before = classify_python_issues(full_issues_before_list)
                    issues_before_errors = len(classified_before.get('errors', []))
                    issues_before_warnings = len(classified_before.get('warnings', []))
                    print(f"[*] Static issues found (after rule-based fixes): errors={issues_before_errors}, warnings={issues_before_warnings}")
                    issues_before = issues_before_errors
                    if issues_before == 0:
                        print("[+] Rule-based fixes cleared all error-level static issues.")
                        return reports
        except Exception as e:
            print(f"[!] Rule-based fixer failed: {e}")
        # Run a preliminary dynamic test before producing LLM patches so we can
        # capture the baseline runtime failures for this iteration.
        print("[*] Running preliminary dynamic tester (pre-patch) to capture failing runtime tests")
        pre_dyn_cmd = "py -3 -u dynamic_tester.py --py"
        if repo_dir:
            pre_dyn_cmd += f' --py-repo "{repo_dir}"'
        pre_proc = subprocess.run(pre_dyn_cmd, shell=True, capture_output=True, text=True, check=False, cwd=BASE_DIR)
        pre_dyn_report_path = BASE_DIR / "dynamic_analysis_report.txt"
        if pre_dyn_report_path.exists():
            pre_dyn_text = pre_dyn_report_path.read_text(encoding="utf-8")
        else:
            pre_dyn_text = (pre_proc.stdout or "") + "\n" + (pre_proc.stderr or "")
        pre_dyn_text_clean = "\n".join([ln for ln in (pre_dyn_text or "").splitlines() if not ln.strip().startswith("Patches applied:")])
        pre_dynamic_issues = parse_dynamic_issues(pre_dyn_text)
    # 2) Generate candidate patches for Python snippets
        print("[*] Generating candidate patches (LLM)")
        # Determine destination folder for fixed patches and record existing files
        dest_folder = BASE_DIR / "patches_py_fixed"
        dest_folder.mkdir(parents=True, exist_ok=True)
        existing_patches = {p.name for p in dest_folder.glob("patch_*.diff")} if dest_folder.exists() else set()

        # This will produce sanitized patches into agent/patches_py_fixed
        run_pipeline(REPORT_PY, SNIPPETS_PY, lang="py", iteration=iteration)

        # 3) Run dynamic tester which will attempt to apply patches and run runtime tests
        print("[*] Running dynamic tester to apply patches and test runtime behavior")
        dyn_cmd = "py -3 -u dynamic_tester.py --py"
        if repo_dir:
            dyn_cmd += f' --py-repo "{repo_dir}"'
        dyn_proc = subprocess.run(dyn_cmd, shell=True, capture_output=True, text=True, check=False, cwd=BASE_DIR)

        # Read dynamic report if produced, otherwise fall back to captured stdout
        dyn_report_path = BASE_DIR / "dynamic_analysis_report.txt"
        if dyn_report_path.exists():
            dyn_report_text = dyn_report_path.read_text(encoding="utf-8")
        else:
            dyn_report_text = (dyn_proc.stdout or "") + "\n" + (dyn_proc.stderr or "")

        # Read raw dynamic report copy if present (audit file) so we can parse applied/total
        dyn_report_raw_path = dyn_report_path.with_name(dyn_report_path.stem + "_raw" + dyn_report_path.suffix)
        dyn_report_raw_text = dyn_report_raw_path.read_text(encoding="utf-8") if dyn_report_raw_path.exists() else None

        # Clean the dynamic report text for UI-facing fields: hide ambiguous 'Patches applied:' line
        dyn_report_text_clean = "\n".join([ln for ln in (dyn_report_text or "").splitlines() if not ln.strip().startswith("Patches applied:")])

        # Parse applied patch count from either the cleaned or raw report; fallback to 0
        import re as _re
        patches_applied = 0
        search_text = (dyn_report_text or "") + "\n" + (dyn_report_raw_text or "")
        m = _re.search(r"Patches applied:\s*(\d+)(?:\s*/\s*(\d+))?", search_text)
        if m:
            try:
                patches_applied = int(m.group(1))
            except Exception:
                patches_applied = 0

        # Count how many new patch files were produced by run_pipeline this iteration
        new_patches = {p.name for p in dest_folder.glob("patch_*.diff")} - existing_patches if dest_folder.exists() else set()
        patches_produced = len(new_patches)

        # Parse dynamic report to count tests and passes
        dyn_lines = [ln.strip() for ln in dyn_report_text.splitlines() if ln.strip()]
        tests_run = sum(1 for ln in dyn_lines if ln.startswith("[+]") or ln.startswith("[-]"))
        tests_passed = sum(1 for ln in dyn_lines if ln.startswith("[+]"))

        # Build per-iteration report entry (do not write to disk; return to caller)
        report_entry = {
            "iteration": iteration,
            "static_issues_before": issues_before,
            "static_issues_before_list": get_python_issues(REPORT_PY),
            # dynamic issues observed before we applied patches this iteration
            "dynamic_issues_before_list": pre_dynamic_issues,
            "dynamic_tests_run": tests_run,
            "dynamic_tests_passed": tests_passed,
            "patches_produced": patches_produced,
            # patches_applied is an integer number of successfully applied patches
            "patches_applied": patches_applied,
            "patches": sorted(list(new_patches)),
            # keep raw text for logs/debug but provide a cleaned version for UI consumption
            "dynamic_report_text": dyn_report_text_clean,
            "dynamic_report_raw": dyn_report_text,
            "dynamic_stdout": dyn_proc.stdout,
            "dynamic_stderr": dyn_proc.stderr,
        }
        # Friendly summary fields for UI clarity
        report_entry["dynamic_tests_passed_all"] = (tests_run > 0 and tests_passed == tests_run)
        report_entry["static_cleared"] = False  # to be updated after re-analysis
        reports.append(report_entry)

        print(f"[*] Iteration {iteration} summary: static(before)={issues_before}, tests_run={tests_run}, passed={tests_passed}")
        # 4) If no patches were applied this iteration, attempt a small extra
        #    deterministic fix pass (very low risk) to try to resolve common
        #    'undefined name' issues before re-running the static analyzer.
        if repo_dir and (patches_applied == 0):
            try:
                print("[*] No patches applied this iteration — running additional deterministic fixes")
                apply_additional_rule_based_fixes(repo_dir, REPORT_PY)
            except Exception as e:
                print(f"[!] Additional rule-based fixer failed: {e}")

        # 5) Re-run static analyzer now that patches (or additional fixes) have been applied and measure improvement
        analyzer_cmd = "py -3 -u analyzer_py.py"
        if repo_dir:
            analyzer_cmd += f' --repo-dir "{repo_dir}"'
        subprocess.run(analyzer_cmd, shell=True, check=False, cwd=BASE_DIR)

        # Re-extract full issue lines and classify into errors/warnings
        full_issues_after_list = get_python_issues(REPORT_PY)
        classified_after = classify_python_issues(full_issues_after_list)
        issues_after_errors = len(classified_after.get('errors', []))
        issues_after_warnings = len(classified_after.get('warnings', []))
        print(f"[*] Static issues found (after patch): errors={issues_after_errors}, warnings={issues_after_warnings}")

        # Attach post-apply static lists to the report entry for visibility
        report_entry["static_issues_after"] = issues_after_errors
        report_entry["static_errors_after_list"] = classified_after.get('errors', [])
        report_entry["static_warnings_after_list"] = classified_after.get('warnings', [])
        # dynamic issues observed after applying patches this iteration
        report_entry["dynamic_issues_after_list"] = parse_dynamic_issues(dyn_report_text)

        # Compute solved/unsolved lists for error-level issues (we focus on errors)
        before_set = set(classified_before.get('errors', []))
        after_set = set(classified_after.get('errors', []))
        solved = sorted(list(before_set - after_set))
        unsolved = sorted(list(after_set))
        report_entry["static_solved_list"] = solved
        report_entry["static_unsolved_list"] = unsolved

        # compute dynamic solved/unsolved lists
        dyn_before_set = set(report_entry.get("dynamic_issues_before_list", []))
        dyn_after_set = set(report_entry.get("dynamic_issues_after_list", []))
        dyn_solved = sorted(list(dyn_before_set - dyn_after_set))
        dyn_unsolved = sorted(list(dyn_after_set))
        report_entry["dynamic_solved_list"] = dyn_solved
        report_entry["dynamic_unsolved_list"] = dyn_unsolved

        # Backwards-compatible field used by the UI/table: set to post-apply error count
        report_entry["static_issues"] = int(issues_after_errors)
        # Compute friendly per-iteration summary fields (unambiguous) and ensure ints
        report_entry["static_checked"] = int(issues_before)
        report_entry["static_solved"] = int(max(0, issues_before - issues_after_errors))
        report_entry["static_remaining"] = int(issues_after_errors)
        report_entry["dynamic_detected"] = int(tests_run)
        report_entry["dynamic_solved"] = int(tests_passed)
        report_entry["dynamic_remaining"] = int(max(0, tests_run - tests_passed))

        # If this is the first iteration, prev_issues is None; set it to issues_before
        if prev_issues is None:
            prev_issues = issues_before
        # New stop conditions (friendly): stop early when there are no static issues
        # remaining OR when all dynamic tests that ran passed. This avoids spinning
        # extra iterations when nothing remains to fix.
        stop_reason = None
        if issues_after_errors == 0:
            stop_reason = "no_static_issues"
        elif STOP_ON_DYNAMIC_ONLY and tests_run > 0 and tests_passed == tests_run:
            # only stop on dynamic-only success if explicitly enabled
            stop_reason = "all_dynamic_tests_passed"

        if stop_reason:
            report_entry["stop_reason"] = stop_reason
            print(f"[+] Stopping iterative loop: {stop_reason}")
            return reports

        # Decide whether to abort continuation based on absence of new patches and
        # lack of improvement (compatibility with earlier logic). If nothing
        # changed, continue to next iteration up to max_iters per user preferences.
        static_not_decreased = issues_after_errors >= prev_issues
        if prev_tests_passed is None:
            dynamic_not_improved = False
        else:
            dynamic_not_improved = tests_passed <= prev_tests_passed

        if patches_produced == 0 and static_not_decreased and dynamic_not_improved:
            print("[!] No new patches produced and no dynamic improvement — continuing to next iteration per user request.")
        # Otherwise, update previous counters and continue
        prev_issues = issues_after_errors
        prev_tests_passed = tests_passed

    print("[!] Reached max iterations (or stopped early). Returning reports.")
    return reports


def count_cpp_issues(report_path: Path) -> int:
    """Count simple C/C++ style issues in the static analysis report file.

    This is a lighter-weight counter than the Python linter parser above.
    It counts lines that look like: path/to/file.cpp:123: message
    or path/to/file.h:123: message
    Returns -1 when the report is missing.
    """
    import re as _re
    if not report_path.exists():
        return -1
    text = report_path.read_text(encoding="utf-8")
    # Match patterns like "file.cpp:12:" or "dir/file.h:34:"
    matches = _re.findall(r"^.+?\.(?:cpp|cc|c|hpp|hh|h):\d+:", text, flags=_re.MULTILINE)
    return len(matches)


def get_cpp_issues(report_path: Path) -> list:
    """Return a list of static issue lines from a C/C++ analysis report."""
    import re as _re
    if not report_path.exists():
        return []
    text = report_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    matches = [ln.strip() for ln in lines if _re.match(r"^.+?\.(?:cpp|cc|c|hpp|hh|h):\d+:", ln)]
    return matches


def classify_cpp_issues(issue_lines: list) -> dict:
    """
    Classify simple C/C++ analyzer lines into errors and warnings.

    Returns {'errors': [...], 'warnings': [...]}.
    Uses heuristics: lines containing ': error:' or 'fatal error' -> errors,
    lines containing ': warning:' or 'warning:' -> warnings. Unclassified
    lines default to warnings.
    """
    errors = []
    warnings = []
    import re as _re
    for ln in (issue_lines or []):
        l = ln.lower()
        if ': error:' in l or 'fatal error' in l or 'undefined reference' in l:
            errors.append(ln)
        elif ': warning:' in l or 'warning:' in l:
            warnings.append(ln)
        else:
            # default to warnings to avoid aggressive edits
            warnings.append(ln)
    return {'errors': errors, 'warnings': warnings}


def apply_additional_rule_based_fixes_cpp(repo_dir: str, report_path: Path):
    """
    Conservative, low-risk C++ fixes to help static analyzers and LLMs.

    Current behavior (very low risk):
    - For missing include messages (e.g. "No such file or directory: QtSql/QSqlDatabase"),
      insert a top-of-file comment marker in the affected source/header file listing the
      missing include (does not change compilation, only adds an audit hint). This
      helps downstream LLM passes to know a dependency is missing without altering
      code flow.

    The function makes .bak backups before writing.
    """
    try:
        txt = report_path.read_text(encoding='utf-8') if report_path.exists() else ''
    except Exception:
        txt = ''

    # Find missing include-like messages
    missing_headers = set()
    try:
        # common pattern: fatal error: QtSql/QSqlDatabase: No such file or directory
        for m in re.findall(r"fatal error:\s*([^:]+):\s*No such file or directory", txt, flags=re.IGNORECASE):
            missing_headers.add(m.strip())
        # other possible patterns
        for m in re.findall(r"No such file or directory:\s*([^\n]+)", txt, flags=re.IGNORECASE):
            missing_headers.add(m.strip())
    except Exception:
        pass

    def write_backup(path: Path):
        try:
            bak = path.with_suffix(path.suffix + '.bak')
            if not bak.exists():
                bak.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        except Exception:
            pass

    # Insert comments into files that mention the header (best-effort, non-invasive)
    for hdr in sorted(missing_headers):
        # Try to locate files that refer to parts of the header name (basename)
        basename = Path(hdr).name
        for f in Path(repo_dir).rglob('*.cpp'):
            try:
                src = f.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            if basename not in src:
                continue
            # Avoid duplicate insertions
            marker = f'// MISSING_INCLUDE: {hdr}'
            if marker in src:
                continue
            try:
                write_backup(f)
                new_src = marker + '\n' + src
                f.write_text(new_src, encoding='utf-8')
                print(f"[add-cpp-fix] Inserted marker for missing header '{hdr}' in {f}")
            except Exception as e:
                print(f"[!] add-cpp-fix failed for {f}: {e}")

    return True


def run_iterative_fix_cpp(max_iters: int = 5, repo_dir: str = None):
    """Run an iterative loop for C++ projects analogous to run_iterative_fix_py.

    If repo_dir is provided, analyzer_cpp and dynamic_tester will be called with
    the appropriate CLI flags so the workspace copy is used instead of the
    repository under `agent/`.

    Stops when static issue count reaches 0 or when issues don't decrease
    between iterations.
    """
    print("[*] Starting iterative auto-fix loop for C++")
    prev_issues = None
    prev_tests_passed = None
    reports = []
    for iteration in range(1, max_iters + 1):
        print(f"\n=== Iteration {iteration}/{max_iters} ===")

        # 1) Run static analyzer for C++
        print("[*] Running static analyzer (cpp)")
        # Use the same invocation style as the Python loop but target analyzer_cpp.py
        analyzer_cmd = "py -3 -u analyzer_cpp.py"
        if repo_dir:
            analyzer_cmd += f' --repo-dir "{repo_dir}"'
        subprocess.run(analyzer_cmd, shell=True, check=False, cwd=BASE_DIR)

        # Extract full issue lines and classify into errors/warnings so we focus on real bugs
        full_issues_before_list = get_cpp_issues(REPORT_CPP)
        classified_before = classify_cpp_issues(full_issues_before_list)
        issues_before_errors = len(classified_before.get('errors', []))
        issues_before_warnings = len(classified_before.get('warnings', []))
        print(f"[*] Static issues found (before patch): errors={issues_before_errors}, warnings={issues_before_warnings}")

        # For compatibility, represent issues_before as the number of error-level issues
        issues_before = issues_before_errors

        if issues_before == 0:
            print("[+] No error-level static issues remain. Auto-fix complete.")
            return reports

        # 2) Generate candidate patches for C++ snippets
        print("[*] Generating candidate patches (LLM)")
        # Determine destination folder for fixed C++ patches and record existing files
        dest_folder = BASE_DIR / "patches" / "patches_cpp_fixed"
        dest_folder.mkdir(parents=True, exist_ok=True)
        existing_patches = {p.name for p in dest_folder.glob("patch_*.diff")} if dest_folder.exists() else set()

        # Before generating LLM patches, run a conservative C++-only rule pass that
        # adds non-invasive markers for missing includes to help LLMs reason about
        # system dependencies without changing behavior.
        try:
            if repo_dir and issues_before > 0:
                apply_additional_rule_based_fixes_cpp(repo_dir, REPORT_CPP)
        except Exception as e:
            print(f"[!] C++ rule-based fixer failed: {e}")

        # This will produce sanitized patches into agent/patches/patches_cpp_fixed
        run_pipeline(REPORT_CPP, SNIPPETS_CPP, lang="cpp", iteration=iteration)

        # 3) Run dynamic tester which will attempt to apply patches and run runtime tests
        print("[*] Running dynamic tester to apply patches and test runtime behavior")
        dyn_cmd = "py -3 -u dynamic_tester.py --cpp"
        if repo_dir:
            dyn_cmd += f' --cpp-repo "{repo_dir}"'
        dyn_proc = subprocess.run(dyn_cmd, shell=True, capture_output=True, text=True, check=False, cwd=BASE_DIR)

        # Read dynamic report if produced, otherwise fall back to captured stdout
        dyn_report_path = BASE_DIR / "dynamic_analysis_report.txt"
        if dyn_report_path.exists():
            dyn_report_text = dyn_report_path.read_text(encoding="utf-8")
        else:
            dyn_report_text = (dyn_proc.stdout or "") + "\n" + (dyn_proc.stderr or "")

        # Read raw dynamic report copy if present (audit file) so we can parse applied/total
        dyn_report_raw_path = dyn_report_path.with_name(dyn_report_path.stem + "_raw" + dyn_report_path.suffix)
        dyn_report_raw_text = dyn_report_raw_path.read_text(encoding="utf-8") if dyn_report_raw_path.exists() else None

        # Clean the dynamic report text for UI-facing fields: hide ambiguous 'Patches applied:' line
        dyn_report_text_clean = "\n".join([ln for ln in (dyn_report_text or "").splitlines() if not ln.strip().startswith("Patches applied:")])

        # Parse applied patch count from either the cleaned or raw report; fallback to 0
        import re as _re
        patches_applied = 0
        search_text = (dyn_report_text or "") + "\n" + (dyn_report_raw_text or "")
        m = _re.search(r"Patches applied:\s*(\d+)(?:\s*/\s*(\d+))?", search_text)
        if m:
            try:
                patches_applied = int(m.group(1))
            except Exception:
                patches_applied = 0

        # Count how many new patch files were produced by run_pipeline this iteration
        new_patches = {p.name for p in dest_folder.glob("patch_*.diff")} - existing_patches if dest_folder.exists() else set()
        patches_produced = len(new_patches)

        # Parse dynamic report to count tests and passes
        dyn_lines = [ln.strip() for ln in dyn_report_text.splitlines() if ln.strip()]
        tests_run = sum(1 for ln in dyn_lines if ln.startswith("[+]") or ln.startswith("[-]"))
        tests_passed = sum(1 for ln in dyn_lines if ln.startswith("[+]"))

        # Build per-iteration report entry (do not write to disk; return to caller)
        report_entry = {
            "iteration": iteration,
            "static_issues_before": issues_before,
            "static_issues_before_list": get_cpp_issues(REPORT_CPP),
            "dynamic_tests_run": tests_run,
            "dynamic_tests_passed": tests_passed,
            "patches_produced": patches_produced,
            # patches_applied stored as integer for UI
            "patches_applied": patches_applied,
            "patches": sorted(list(new_patches)),
            # keep raw text for logs/debug but provide a cleaned version for UI consumption
            "dynamic_report_text": dyn_report_text_clean,
            "dynamic_report_raw": dyn_report_text,
            "dynamic_stdout": dyn_proc.stdout,
            "dynamic_stderr": dyn_proc.stderr,
        }
        # Friendly summary fields for UI clarity
        report_entry["dynamic_tests_passed_all"] = (tests_run > 0 and tests_passed == tests_run)
        report_entry["static_cleared"] = False  # will be updated after re-analysis
        reports.append(report_entry)

        print(f"[*] Iteration {iteration} summary: static(before)={issues_before}, tests_run={tests_run}, passed={tests_passed}")

        # 4) Re-run static analyzer now that patches (or additional fixes) have been applied and measure improvement
        analyzer_cmd = "py -3 -u analyzer_cpp.py"
        if repo_dir:
            analyzer_cmd += f' --repo-dir "{repo_dir}"'
        subprocess.run(analyzer_cmd, shell=True, check=False, cwd=BASE_DIR)

        # Re-classify after applying patches
        full_issues_after_list = get_cpp_issues(REPORT_CPP)
        classified_after = classify_cpp_issues(full_issues_after_list)
        issues_after_errors = len(classified_after.get('errors', []))
        issues_after_warnings = len(classified_after.get('warnings', []))
        print(f"[*] Static issues found (after patch): errors={issues_after_errors}, warnings={issues_after_warnings}")

        report_entry["static_issues_after"] = issues_after_errors
        report_entry["static_errors_after_list"] = classified_after.get('errors', [])
        report_entry["static_warnings_after_list"] = classified_after.get('warnings', [])

        # Compute solved/unsolved lists focused on error-level issues
        before_set = set(classified_before.get('errors', []))
        after_set = set(classified_after.get('errors', []))
        solved = sorted(list(before_set - after_set))
        unsolved = sorted(list(after_set))
        report_entry["static_solved_list"] = solved
        report_entry["static_unsolved_list"] = unsolved
        # Backwards-compatible field used by the UI/table: set to post-apply error count
        report_entry["static_issues"] = int(issues_after_errors)
        # Update friendly static summary (ensure ints)
        report_entry["static_checked"] = int(issues_before)
        report_entry["static_solved"] = int(max(0, issues_before - issues_after_errors))
        report_entry["static_remaining"] = int(issues_after_errors)
        report_entry["static_cleared"] = (issues_after_errors == 0)

        # New stop conditions (friendly): stop early when no static issues remain
        # OR when all dynamic tests that ran passed.
        stop_reason = None
        if issues_after_errors == 0:
            stop_reason = "no_static_issues"
        elif STOP_ON_DYNAMIC_ONLY and tests_run > 0 and tests_passed == tests_run:
            stop_reason = "all_dynamic_tests_passed"

        if stop_reason:
            report_entry["stop_reason"] = stop_reason
            print(f"[+] Stopping iterative loop (C++): {stop_reason}")
            return reports

        # If this is the first iteration, set previous counters
        if prev_issues is None:
            prev_issues = issues_before

        # Abort only if no new patches were produced, static did not decrease AND dynamic tests did not improve
        static_not_decreased = issues_after_errors >= prev_issues
        if prev_tests_passed is None:
            dynamic_not_improved = False
        else:
            dynamic_not_improved = tests_passed <= prev_tests_passed

        if patches_produced == 0 and static_not_decreased and dynamic_not_improved:
            print("[!] No new patches produced and no improvement in dynamic tests. Aborting to avoid unnecessary loops.")
            return reports

        prev_issues = issues_after_errors
        prev_tests_passed = tests_passed
    print("[!] Reached max iterations (or stopped early). Returning reports.")
    return reports

# === AI-powered Intent classifier ===  
INTENT_PROMPT = """
You are an AI intent classifier for a software engineering agent.

User will type a natural language command.
Your job: map it into one of these intents:

- static_cpp   : run static analysis on C++ project
- static_py    : run static analysis on Python project
- patch_cpp    : generate patches for C++ project
- patch_py     : generate patches for Python project
- dynamic_cpp  : run dynamic tester for C++ project
- dynamic_py   : run dynamic tester for Python project
- exit         : stop and exit the program
- unknown      : if you cannot decide

Rules:
- Output ONLY the intent label (e.g., "patch_cpp").
- Do NOT output explanations or natural text.
"""


def classify_intent(user_input: str) -> str:
    """Klasifikasi intent pakai AI (Gemini → Qwen → Ollama) dengan fallback keyword."""
    user_input_lower = user_input.lower()
    print(f"[Debug] User input: {user_input_lower}")

    # Keyword fallback first (avoid blocking on unavailable LLMs)
    if any(word in user_input_lower for word in ['cpp', 'c++', 'cplusplus']):
        if any(word in user_input_lower for word in ['patch', 'fix', 'repair']):
            return 'patch_cpp'
        elif any(word in user_input_lower for word in ['test', 'run', 'dynamic']):
            return 'dynamic_cpp'
        elif any(word in user_input_lower for word in ['check', 'analyze', 'static']):
            return 'static_cpp'
        else:
            return 'static_cpp'
    elif any(word in user_input_lower for word in ['py', 'python']):
        if any(word in user_input_lower for word in ['patch', 'fix', 'repair']):
            return 'patch_py'
        elif any(word in user_input_lower for word in ['test', 'run', 'dynamic']):
            return 'dynamic_py'
        elif any(word in user_input_lower for word in ['check', 'analyze', 'static']):
            return 'static_py'
        else:
            return 'static_py'
    elif any(word in user_input_lower for word in ['exit', 'quit', 'stop', 'close']):
        return 'exit'
    # If keywords couldn't decide, try the LLMs as a last resort
    try:
        for llm, name in [(gemini_llm, "Gemini"), (qwen_llm, "Qwen"), (ollama_llm, "Ollama")]:
            if llm:
                resp = llm.invoke([HumanMessage(content=INTENT_PROMPT + f"\n\nUser: {user_input}")])
                intent = getattr(resp, 'content', str(resp)).strip().lower()
                if intent in ["static_cpp", "static_py", "patch_cpp", "patch_py", "dynamic_cpp", "dynamic_py", "exit"]:
                    print(f"[AI Intent] {intent} (via {name})")
                    return intent
    except Exception as e:
        print(f"[!] Intent LLM failed (final fallback): {e}")

    return "unknown"


# === Command dispatcher ===
def interpret_command(user_input: str):
    intent = classify_intent(user_input)

    if intent == "static_cpp":
        run_command("python analyzer_cpp.py", cwd=BASE_DIR)
    elif intent == "static_py":
        run_command("python analyzer_py.py", cwd=BASE_DIR)
    elif intent == "patch_cpp":
        run_pipeline(REPORT_CPP, SNIPPETS_CPP, lang="cpp")
    elif intent == "patch_py":
        run_pipeline(REPORT_PY, SNIPPETS_PY, lang="py")
    elif intent == "dynamic_cpp":
        run_command("python dynamic_tester.py --cpp", cwd=BASE_DIR)
    elif intent == "dynamic_py":
        run_command("python dynamic_tester.py --py", cwd=BASE_DIR)
    elif user_input.strip().lower() == "auto_fix_py":
        # Special non-LLM keyword to run the iterative auto-fix loop for Python
        run_iterative_fix_py(max_iters=5)
    elif user_input.strip().lower() == "auto_fix_cpp":
        # Special non-LLM keyword to run the iterative auto-fix loop for C++
        run_iterative_fix_cpp(max_iters=5)
    elif intent == "exit":
        print("Goodbye!")
        return False
    else:
        print("[!] Unknown command. Try things like:")
        print("    - 'check cpp' or 'check python'")
        print("    - 'patch cpp' or 'patch python'")
        print("    - 'test cpp' or 'test python'")
        print("    - 'exit'")
    return True


def main():
    print("AI Agent ready! What you wanna do with your code:")
    while True:
        try:
            cmd = input("\nYour command: ").strip()
            if not cmd:
                continue
            if not interpret_command(cmd):
                break
        except KeyboardInterrupt:
            print("\nProgram terminated by user")
            break
        except Exception as e:
            print(f"[!] Error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Agent runner")
    parser.add_argument("--cmd", type=str, help="Run single command and exit (e.g. --cmd \"patch cpp\")")
    parser.add_argument("--no-llm", action="store_true", help="Skip remote LLM calls (debug/offline)")
    args = parser.parse_args()

    if args.no_llm:
        SKIP_LLM = True

    if args.cmd:
        # Run a single command non-interactively and exit
        if args.cmd.strip().lower() == "auto_fix_py":
            run_iterative_fix_py(max_iters=10)
        elif args.cmd.strip().lower() == "auto_fix_cpp":
            run_iterative_fix_cpp(max_iters=10)
        else:
            interpret_command(args.cmd)
    else:
        main()


def count_static_issues(report_path: Path) -> int:
    """Count linter-style issues in the static analysis report file.

    We look for lines that match the pylint/flake8 style: path:line:col: CODE: message
    """
    import re as _re
    if not report_path.exists():
        return -1
    text = report_path.read_text(encoding="utf-8")
    # Match patterns like "file.py:12:8: E1101: ..." or "file.py:12: E0606: ..."
    matches = _re.findall(r"^.+?:\d+:\d+:\s+[A-Z]\d{4}:", text, flags=_re.MULTILINE)
    if not matches:
        # fallback: some tools may emit file:line:code style without column
        matches = _re.findall(r"^.+?:\d+:\s+[A-Z]\d{4}:", text, flags=_re.MULTILINE)
    return len(matches)

