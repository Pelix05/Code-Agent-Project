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

# Workspace/session state shared between routes. Guarded by a lock because uploads
# can run in background threads while users trigger commands concurrently.
workspace_lock = threading.Lock()
workspace_state = {
    "has_upload": False,
    "workspace": None,
    "language": None,
    "repo_path": None,
    "snapshot_path": None,
    "python_files": [],
    "cpp_files": [],
}


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


def run_static_analysis_py(repo_dir: str = None):
    cmd = "py -3 -u analyzer_py.py"
    if repo_dir:
        cmd += f' --repo-dir "{repo_dir}"'
    return run_command(cmd, cwd=AGENT_DIR)


def run_dynamic_py(repo_dir: str = None):
    cmd = "py -3 -u dynamic_tester.py --py"
    if repo_dir:
        cmd += f' --py-repo "{repo_dir}"'
    return run_command(cmd, cwd=AGENT_DIR)


def run_static_analysis_cpp(repo_dir: str = None):
    cmd = "py -3 -u analyzer_cpp.py"
    if repo_dir:
        cmd += f' --repo-dir "{repo_dir}"'
    return run_command(cmd, cwd=AGENT_DIR)


def run_dynamic_cpp(repo_dir: str = None):
    cmd = "py -3 -u dynamic_tester.py --cpp"
    if repo_dir:
        cmd += f' --cpp-repo "{repo_dir}"'
    return run_command(cmd, cwd=AGENT_DIR)


def run_patch_py(repo_dir: str = None):
    if repo_dir:
        return run_iterative_fix_py(max_iters=5, repo_dir=repo_dir)
    run_pipeline(REPORT_PY, SNIPPETS_PY, lang="py")
    return "Patch pipeline executed."


def run_patch_cpp(repo_dir: str = None):
    if repo_dir:
        return run_iterative_fix_cpp(max_iters=5, repo_dir=repo_dir)
    run_pipeline(REPORT_CPP, SNIPPETS_CPP, lang="cpp")
    return "Patch pipeline executed."


def run_auto_fix_py(repo_dir: str = None):
    return run_iterative_fix_py(max_iters=5, repo_dir=repo_dir)


def safe_extract_zip(zip_path: Path, extract_to: Path, max_members: int = 2000, max_bytes: int = 200 * 1024 * 1024):
    """Safely extract a ZIP archive, preventing path traversal and limiting resource use."""
    extract_to.mkdir(parents=True, exist_ok=True)
    base = extract_to.resolve()
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        members = zip_ref.infolist()
        if len(members) > max_members:
            raise ValueError(f"Archive has too many files ({len(members)} > {max_members}).")

        total_size = 0
        for member in members:
            member_name = member.filename
            member_path = Path(member_name)
            if member_path.is_absolute():
                raise ValueError(f"Archive member uses an absolute path: {member_name}")
            if ".." in member_path.parts:
                raise ValueError(f"Archive member attempts path traversal: {member_name}")

            target_path = (base / member_path).resolve()
            if os.path.commonpath([str(base), str(target_path)]) != str(base):
                raise ValueError(f"Archive member escapes extraction directory: {member_name}")

            if not member.is_dir():
                total_size += member.file_size
                if total_size > max_bytes:
                    raise ValueError("Archive exceeds maximum allowed size for extraction.")

            zip_ref.extract(member, extract_to)


def record_workspace_state(info: dict):
    """Persist metadata for the most recent upload so command routes can reuse it."""
    lang = info.get("language")
    repo_path = info.get("target")
    with workspace_lock:
        workspace_state.update({
            "has_upload": True,
            "workspace": info.get("workspace"),
            "language": lang,
            "repo_path": repo_path,
            "snapshot_path": info.get("snapshot"),
            "python_files": info.get("python_files", []),
            "cpp_files": info.get("cpp_files", []),
        })


def get_active_workspace():
    """Return a shallow copy of current workspace metadata."""
    with workspace_lock:
        return dict(workspace_state)

def handle_file_upload(file, file_type="py"):
    """Extract uploaded archive, validate contents, and prepare an isolated workspace."""
    tmpdir_root = None
    try:
        tmpdir_root = Path(tempfile.mkdtemp())
        upload_name = Path(file.filename).name if file and getattr(file, 'filename', None) else "upload.zip"
        upload_path = tmpdir_root / upload_name
        file.save(str(upload_path))

        if not zipfile.is_zipfile(upload_path):
            shutil.rmtree(tmpdir_root, ignore_errors=True)
            return None, "[Error] The uploaded file is not a valid ZIP file."

        extracted_dir = tmpdir_root / "contents"
        try:
            safe_extract_zip(upload_path, extracted_dir)
        except ValueError as ve:
            shutil.rmtree(tmpdir_root, ignore_errors=True)
            return None, f"[Error] Unsafe archive: {ve}"

        python_files = [f for f in extracted_dir.rglob("*.py")]
        cpp_files = [f for f in extracted_dir.rglob("*.cpp")]

        if not python_files and not cpp_files:
            shutil.rmtree(tmpdir_root, ignore_errors=True)
            return None, "No Python or C++ files found in the uploaded zip."

        detected_type = None
        if python_files and not cpp_files:
            detected_type = "py"
        elif cpp_files and not python_files:
            detected_type = "cpp"
        else:
            if file_type in ("py", "cpp"):
                detected_type = file_type
            else:
                shutil.rmtree(tmpdir_root, ignore_errors=True)
                return None, "Archive contains both Python and C++ files - please specify file_type ('py' or 'cpp')."

        file_base = Path(file.filename).stem if file and getattr(file, 'filename', None) else uuid.uuid4().hex
        safe_name = re.sub(r'[^A-Za-z0-9_-]', '_', str(file_base))
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        ws_id = f"{safe_name}_{ts}"
        workspaces_root = AGENT_DIR / "workspaces"
        workspaces_root.mkdir(parents=True, exist_ok=True)
        ws_dir = workspaces_root / ws_id
        counter = 1
        while ws_dir.exists():
            ws_id = f"{safe_name}_{ts}_{counter}"
            ws_dir = workspaces_root / ws_id
            counter += 1
        ws_dir.mkdir()

        snapshot_dir = ws_dir / "uploaded_source"
        shutil.copytree(extracted_dir, snapshot_dir)
        shutil.rmtree(tmpdir_root, ignore_errors=True)

        if detected_type == "py":
            target = ws_dir / "python_repo"
            shutil.copytree(snapshot_dir, target)
            rel_python_files = [str(p.relative_to(snapshot_dir)) for p in snapshot_dir.rglob("*.py")]
            return {
                "workspace": ws_id,
                "language": "py",
                "target": str(target),
                "snapshot": str(snapshot_dir),
                "python_files": rel_python_files,
                "cpp_files": [],
            }, None

        target_root = ws_dir / "cpp_project"
        target = target_root / "puzzle-2"
        target_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(snapshot_dir, target)
        rel_cpp_files = [str(p.relative_to(snapshot_dir)) for p in snapshot_dir.rglob("*.cpp")]
        return {
            "workspace": ws_id,
            "language": "cpp",
            "target": str(target_root),
            "snapshot": str(snapshot_dir),
            "python_files": [],
            "cpp_files": rel_cpp_files,
        }, None

    except Exception as e:
        try:
            if tmpdir_root:
                shutil.rmtree(tmpdir_root, ignore_errors=True)
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

    record_workspace_state(workspace_info)
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
    state = get_active_workspace()

    try:
        # Conversation responses
        if "hello" in user_input_lower or "hi" in user_input_lower:
            return "Hello! 👋 Ready to analyze your code."
        elif "how are you" in user_input_lower:
            return "I'm great! Let's fix some code today 😄"
        elif "bye" in user_input_lower:
            return "Goodbye! 👋"

        # Require upload first
        if not state.get("has_upload"):
            return "⚠️ Please upload a file before running commands."

        repo_path = state.get("repo_path")
        lang = state.get("language")

        # Command matching
        if "static" in user_input_lower and "py" in user_input_lower:
            return run_static_analysis_py(repo_path if lang == "py" else None)
        elif "dynamic" in user_input_lower and "py" in user_input_lower:
            return run_dynamic_py(repo_path if lang == "py" else None)
        elif "static" in user_input_lower and "cpp" in user_input_lower:
            return run_static_analysis_cpp(repo_path if lang == "cpp" else None)
        elif "dynamic" in user_input_lower and "cpp" in user_input_lower:
            return run_dynamic_cpp(repo_path if lang == "cpp" else None)
        elif "patch" in user_input_lower and "py" in user_input_lower:
            return run_patch_py(repo_path if lang == "py" else None)
        elif "auto_fix" in user_input_lower and "py" in user_input_lower:
            return run_auto_fix_py(repo_path if lang == "py" else None)
        elif "compare" in user_input_lower and "patch" in user_input_lower:
            return compare_patch()
        else:
            return "�?Unknown command. Try: static py | dynamic py | patch py | auto_fix py | compare patch | static cpp | dynamic cpp"
    except Exception as e:
        return f"[Error] {str(e)}"


@app.route('/')
def index():
    return render_template('index.html')





@app.route('/process', methods=['POST'])
def process_command():
    """Handle text commands."""
    user_input = request.form.get('command')
    if not user_input:
        return jsonify({"status": "Error", "error": "No command entered."})

    state = get_active_workspace()
    user_input_lower = user_input.lower()
    result = interpret_command(user_input)

    # Check for patch-related commands
    if "patch py" in user_input_lower:
        # Ensure the file has been uploaded first
        if not state.get("has_upload"):
            return jsonify({"status": "Error", "error": "No file uploaded."})

        repo_dir = state.get("repo_path") if state.get("language") == "py" else None
        patch_result = run_patch_py(repo_dir=repo_dir)
        return jsonify({"status": "Success", "result": patch_result})

    # If it's an auto-fix command
    if "auto_fix py" in user_input_lower:
        # Run the auto-fix process and show progress
        repo_dir = state.get("repo_path") if state.get("language") == "py" else None
        auto_fix_result = run_auto_fix_py(repo_dir=repo_dir)
        return jsonify({"status": "Success", "result": auto_fix_result})

    return jsonify({"status": "Success", "result": result})


@app.route('/compare_patch', methods=['POST'])
def compare_patch():
    """Compare a file in the current workspace against its original upload."""
    state = get_active_workspace()
    if not state.get("has_upload"):
        return jsonify({"status": "Error", "error": "No file uploaded for patch comparison."})

    snapshot = state.get("snapshot_path")
    repo_path = state.get("repo_path")
    if not snapshot or not repo_path:
        return jsonify({"status": "Error", "error": "Workspace metadata is incomplete."})

    rel_path = request.form.get('file_path')
    if rel_path:
        rel_path = rel_path.strip().lstrip('/\\')
    else:
        candidates = state.get("python_files") if state.get("language") == "py" else state.get("cpp_files")
        if not candidates:
            return jsonify({"status": "Error", "error": "No source files recorded for diff."})
        rel_path = candidates[0]

    original_file = Path(snapshot) / rel_path
    repo_root = Path(repo_path)
    if state.get("language") == "cpp":
        repo_root = repo_root / "puzzle-2"
    patched_file = repo_root / rel_path

    if not original_file.exists():
        return jsonify({"status": "Error", "error": f"Original file not found: {rel_path}"})
    if not patched_file.exists():
        return jsonify({"status": "Error", "error": f"Patched file not found: {rel_path}"})

    diff = compare_files(str(original_file), str(patched_file))
    return jsonify({"status": "Success", "file": rel_path, "diff": diff})


# === Config ===
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['STATIC_FOLDER'] = 'static'
app.config['TEMPLATES_FOLDER'] = 'templates'


if __name__ == '__main__':
    # Disable the reloader to avoid the Flask dev server restarting when
    # uploaded files are copied into the project folder (which triggers
    # watchdog and can interrupt requests).
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False, threaded=True)
