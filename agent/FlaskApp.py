from flask import Flask, request, jsonify, render_template
import subprocess
import os
import tempfile
import re
from datetime import datetime
from pathlib import Path
import zipfile
import difflib
from lc_pipeline import (
    run_iterative_fix_py,
    run_pipeline,
    REPORT_PY,
    SNIPPETS_PY,
    REPORT_CPP,
    SNIPPETS_CPP,
    run_iterative_fix_cpp,
)
import shutil
import logging
import threading
import json

# Configure simple logging to the console for debugging upload requests
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger(__name__)
import uuid

AGENT_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

# Global state
file_uploaded = False
uploaded_python_files = []
uploaded_cpp_files = []


# --- Helper runner used by background worker and UI commands
def run_command(cmd, cwd=None):
    """Run a shell command and return combined stdout+stderr as string."""
    try:
        if isinstance(cwd, Path):
            cwd = str(cwd)
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
        return (result.stdout or "") + (result.stderr or "")
    except Exception as e:
        return f"[Error] {e}"


def run_static_analysis_py():
    return run_command("py -3 -u analyzer_py.py", cwd=AGENT_DIR)


def run_dynamic_py():
    return run_command("py -3 -u dynamic_tester.py --py", cwd=AGENT_DIR)


def run_static_analysis_cpp():
    return run_command("py -3 -u analyzer_cpp.py", cwd=AGENT_DIR)


def run_dynamic_cpp():
    return run_command("py -3 -u dynamic_tester.py --cpp", cwd=AGENT_DIR)


def run_patch_py():
    run_pipeline(REPORT_PY, SNIPPETS_PY, lang="py")
    return "Patch pipeline executed."


def run_patch_cpp():
    run_pipeline(REPORT_CPP, SNIPPETS_CPP, lang="cpp")
    return "Patch pipeline executed."


def run_auto_fix_py():
    return run_iterative_fix_py(max_iters=5)

def handle_file_upload(file, file_type="py"):
    """Old synchronous upload helper refactored to only extract and create a workspace.

    Returns (workspace_info, error_message). workspace_info is a dict:
      { workspace: <id>, language: 'py'|'cpp', target: <path str> }
    """
    try:
        tmpdir_root = tempfile.mkdtemp()
        file_path = os.path.join(tmpdir_root, file.filename)
        file.save(file_path)

        # Extract ZIP
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdir_root)
        else:
            shutil.rmtree(tmpdir_root)
            return None, "[Error] The uploaded file is not a valid ZIP file."

        # Find Python or C++ files in the extracted archive
        python_files = [f for f in Path(tmpdir_root).rglob("*.py")]
        cpp_files = [f for f in Path(tmpdir_root).rglob("*.cpp")]

        if not python_files and not cpp_files:
            shutil.rmtree(tmpdir_root)
            return None, "No Python or C++ files found in the uploaded zip."

        # Auto-detect when file_type is not provided.
        detected_type = None
        if python_files and not cpp_files:
            detected_type = "py"
        elif cpp_files and not python_files:
            detected_type = "cpp"
        else:
            # both present
            if file_type in ("py", "cpp"):
                detected_type = file_type
            else:
                shutil.rmtree(tmpdir_root)
                return None, "Archive contains both Python and C++ files — please specify file_type ('py' or 'cpp')."

        # Create isolated workspace for this upload to avoid mutating repo folders
        # Use sanitized original filename + timestamp for a cleaner workspace name
        file_base = Path(file.filename).stem if file and getattr(file, 'filename', None) else uuid.uuid4().hex
        # sanitize to safe chars
        safe_name = re.sub(r'[^A-Za-z0-9_-]', '_', str(file_base))
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        ws_id = f"{safe_name}_{ts}"
        workspaces_root = AGENT_DIR / "workspaces"
        workspaces_root.mkdir(parents=True, exist_ok=True)
        ws_dir = workspaces_root / ws_id
        # ensure uniqueness if a collision occurs (very unlikely)
        counter = 1
        while ws_dir.exists():
            ws_id = f"{safe_name}_{ts}_{counter}"
            ws_dir = workspaces_root / ws_id
            counter += 1
        ws_dir.mkdir()

        if detected_type == "py":
            # Copy extracted files into workspace/python_repo
            target = ws_dir / "python_repo"
            shutil.copytree(tmpdir_root, target)
            shutil.rmtree(tmpdir_root)
            return {"workspace": ws_id, "language": "py", "target": str(target)}, None

        # detected_type == 'cpp'
        target_root = ws_dir / "cpp_project"
        target = target_root / "puzzle-2"
        target_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(tmpdir_root, target)
        shutil.rmtree(tmpdir_root)
        return {"workspace": ws_id, "language": "cpp", "target": str(target_root)}, None

    except Exception as e:
        try:
            shutil.rmtree(tmpdir_root)
        except Exception:
            pass
        return None, f"[Error] Upload failed: {str(e)}"


def compare_files(original_file, patched_file):
    """Compare the original and patched files to prove the patch was applied."""
    with open(original_file, 'r') as f1, open(patched_file, 'r') as f2:
        original_code = f1.readlines()
        patched_code = f2.readlines()

    diff = difflib.unified_diff(original_code, patched_code, fromfile='original_code.py', tofile='patched_code.py')

    return '\n'.join(diff)  # Return the diff as a string


# === File Upload / Background Processing ===


@app.route('/upload', methods=['POST'])
def upload_file_route():
    """Handle ZIP file upload by creating a workspace and starting background work.

    Returns immediately with {status: 'Accepted', workspace: <id>} so the client
    can poll /status for results.
    """
    if 'file' not in request.files:
        return jsonify({"status": "Error", "error": "No file part"})
    file = request.files['file']
    file_type = request.form.get('file_type')
    logger.info("/upload request received: filename=%s, file_type=%s, remote=%s", file.filename if file else None, file_type, request.remote_addr)

    if file.filename == '':
        return jsonify({"status": "Error", "error": "No selected file"})

    workspace_info, err = handle_file_upload(file, file_type)
    if err:
        logger.info("/upload error: %s", err)
        return jsonify({"status": "Error", "error": err}), 400

    ws_id = workspace_info['workspace']
    lang = workspace_info['language']
    target = workspace_info['target']

    def bg():
        logger.info("[BG] Start processing workspace %s (%s)", ws_id, lang)
        try:
            # Default to skipping C++ compile for Qt projects in the background worker
            # because many runner environments don't have Qt dev headers installed.
            os.environ.setdefault('CPP_QT_BEHAVIOR', 'skip')
            if lang == 'py':
                static_out = run_command(f"py -3 -u analyzer_py.py --repo-dir \"{target}\"", cwd=AGENT_DIR)
                dyn_out = run_command(f"py -3 -u dynamic_tester.py --py --py-repo \"{target}\"", cwd=AGENT_DIR)
                try:
                    auto_fix_reports = run_iterative_fix_py(max_iters=5, repo_dir=target)
                except Exception as e:
                    auto_fix_reports = {"error": str(e)}
            else:
                static_out = run_command(f"py -3 -u analyzer_cpp.py --repo-dir \"{target}\"", cwd=AGENT_DIR)
                dyn_out = run_command(f"py -3 -u dynamic_tester.py --cpp --cpp-repo \"{target}\"", cwd=AGENT_DIR)
                try:
                    auto_fix_reports = run_iterative_fix_cpp(max_iters=5, repo_dir=target)
                except Exception as e:
                    auto_fix_reports = {"error": str(e)}

            # Clean dynamic output for UI: hide ambiguous 'Patches applied: X/Y' line
            dyn_clean_lines = [ln for ln in (dyn_out or "").splitlines() if not ln.strip().startswith("Patches applied:")]
            dyn_clean = "\n".join(dyn_clean_lines)

            result = {
                "workspace": ws_id,
                "language": lang,
                "static": static_out,
                # keep raw dynamic output for debugging, but present a cleaned version to UI
                "dynamic_raw": dyn_out,
                "dynamic": dyn_clean,
                "auto_fix_reports": auto_fix_reports,
            }
            ws_path = AGENT_DIR / 'workspaces' / ws_id
            with open(ws_path / 'result.json', 'w', encoding='utf-8') as fh:
                json.dump(result, fh, ensure_ascii=False, indent=2)
            with open(ws_path / 'status.txt', 'w', encoding='utf-8') as fh:
                fh.write('done')
            logger.info("[BG] Finished processing workspace %s", ws_id)
        except Exception as e:
            logger.exception("[BG] Error processing workspace %s: %s", ws_id, e)
            try:
                ws_path = AGENT_DIR / 'workspaces' / ws_id
                with open(ws_path / 'result.json', 'w', encoding='utf-8') as fh:
                    json.dump({"error": str(e)}, fh)
                with open(ws_path / 'status.txt', 'w', encoding='utf-8') as fh:
                    fh.write('error')
            except Exception:
                pass

    t = threading.Thread(target=bg, daemon=True)
    t.start()

    logger.info("/upload accepted: workspace=%s", ws_id)
    return jsonify({"status": "Accepted", "workspace": ws_id})


@app.route('/status', methods=['GET'])
def status_route():
    ws = request.args.get('ws')
    if not ws:
        return jsonify({"status": "Error", "error": "Missing workspace id (ws)"}), 400
    ws_path = AGENT_DIR / 'workspaces' / ws
    if not ws_path.exists():
        return jsonify({"status": "Error", "error": "Workspace not found"}), 404
    status_file = ws_path / 'status.txt'
    result_file = ws_path / 'result.json'
    if status_file.exists():
        st = status_file.read_text(encoding='utf-8')
        if st.strip() == 'done' and result_file.exists():
            data = json.loads(result_file.read_text(encoding='utf-8'))
            return jsonify({"status": "Done", "result": data})
        else:
            return jsonify({"status": st.strip()})
    else:
        return jsonify({"status": "Processing"})


# === Command Interpreter ===

def interpret_command(user_input: str):
    """Interpret user command and execute corresponding function."""
    user_input_lower = user_input.strip().lower()

    try:
        # Conversation responses
        if "hello" in user_input_lower or "hi" in user_input_lower:
            return "Hello! 👋 Ready to analyze your code."
        elif "how are you" in user_input_lower:
            return "I'm great! Let's fix some code today 😄"
        elif "bye" in user_input_lower:
            return "Goodbye! 👋"

        # Require upload first
        if not file_uploaded:
            return "⚠️ Please upload a file before running commands."

        # Command matching
        if "static" in user_input_lower and "py" in user_input_lower:
            return run_static_analysis_py()
        elif "dynamic" in user_input_lower and "py" in user_input_lower:
            return run_dynamic_py()
        elif "static" in user_input_lower and "cpp" in user_input_lower:
            return run_static_analysis_cpp()
        elif "dynamic" in user_input_lower and "cpp" in user_input_lower:
            return run_dynamic_cpp()
        elif "patch" in user_input_lower and "py" in user_input_lower:
            return run_patch_py()
        elif "auto_fix" in user_input_lower and "py" in user_input_lower:
            return run_auto_fix_py()
        elif "compare" in user_input_lower and "patch" in user_input_lower:
            return compare_patch()
        else:
            return "❓ Unknown command. Try: static py | dynamic py | patch py | auto_fix py | compare patch | static cpp | dynamic cpp"
    except Exception as e:
        return f"[Error] {str(e)}"


# === Flask Routes ===

@app.route('/')
def index():
    return render_template('index.html')





@app.route('/process', methods=['POST'])
def process_command():
    """Handle text commands."""
    user_input = request.form.get('command')
    if not user_input:
        return jsonify({"status": "Error", "error": "No command entered."})

    result = interpret_command(user_input)

    # Check for patch-related commands
    if "patch py" in user_input.lower():
        # Ensure the file has been uploaded first
        if not file_uploaded:
            return jsonify({"status": "Error", "error": "No file uploaded."})

        patch_result = run_patch_py()
        return jsonify({"status": "Success", "result": patch_result})

    # If it's an auto-fix command
    if "auto_fix py" in user_input.lower():
        # Run the auto-fix process and show progress
        auto_fix_result = run_auto_fix_py()
        return jsonify({"status": "Success", "result": auto_fix_result})

    return jsonify({"status": "Success", "result": result})


@app.route('/compare_patch', methods=['POST'])
def compare_patch():
    """Compare original file and patched file."""
    if not file_uploaded:
        return jsonify({"status": "Error", "error": "No file uploaded for patch comparison."})

    original_file = uploaded_python_files[0] if uploaded_python_files else uploaded_cpp_files[0]
    patched_file = f"{original_file.stem}_patched.py" if uploaded_python_files else f"{original_file.stem}_patched.cpp"

    if not os.path.exists(patched_file):
        return jsonify({"status": "Error", "error": "Patched file not found."})

    # Get the diff between the original and patched files
    diff = compare_files(original_file, patched_file)
    
    return jsonify({"status": "Success", "diff": diff})


# === Config ===

app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['STATIC_FOLDER'] = 'static'
app.config['TEMPLATES_FOLDER'] = 'templates'


if __name__ == '__main__':
    # Disable the reloader to avoid the Flask dev server restarting when
    # uploaded files are copied into the project folder (which triggers
    # the watchdog and causes the request to be interrupted).
    # Keeping debug=True preserves helpful tracebacks; use_reloader=False
    # prevents automatic process restarts when files change.
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False, threaded=True)
