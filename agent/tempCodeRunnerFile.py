from flask import Flask, request, jsonify, render_template
import subprocess
import os
from pathlib import Path
import zipfile
import tempfile
from lc_pipeline import run_iterative_fix_py, run_pipeline, REPORT_PY, SNIPPETS_PY  # your pipeline imports

app = Flask(__name__)

# Global state
file_uploaded = False
uploaded_python_files = []


# === Helper Functions ===

def run_command(cmd, cwd=None):
    """Run a shell command and capture the output."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
        return result.stdout + result.stderr
    except subprocess.CalledProcessError as e:
        return f"[Error] {e.stdout}\n{e.stderr}"
    except Exception as e:
        return f"[Error] {str(e)}"


def run_static_analysis_py():
    """Run static analysis (example: pylint, bandit)."""
    # Make sure cwd points to agent folder if analyzer_py.py lives there
    return run_command("python analyzer_py.py", cwd="agent")


def run_dynamic_py():
    """Run dynamic tests for Python."""
    # Make sure cwd points to agent folder where dynamic_tester.py lives
    return run_command("python dynamic_tester.py --py", cwd="agent")


def run_patch_py():
    """Run patch pipeline for Python."""
    return run_pipeline(REPORT_PY, SNIPPETS_PY, lang="py")


def run_auto_fix_py():
    """Run iterative auto-fix loop for Python."""
    return run_iterative_fix_py(max_iters=5)


# === File Upload Handler ===

def handle_file_upload(file):
    """Handle ZIP file upload, extract, and run both static and dynamic analysis."""
    global file_uploaded, uploaded_python_files
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, file.filename)
            file.save(file_path)

            # Extract ZIP
            if zipfile.is_zipfile(file_path):
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    zip_ref.extractall(tmpdir)
            else:
                return "[Error] The uploaded file is not a valid ZIP file."

            # Find Python files
            python_files = [f for f in Path(tmpdir).rglob("*.py")]
            if not python_files:
                return "No Python files found in the uploaded zip."

            file_uploaded = True
            uploaded_python_files = python_files

            # --- Run Static Analysis ---
            static_results = ["=== STATIC ANALYSIS ==="]
            for py_file in python_files:
                static_results.append(f"\n--- Static Analysis for {py_file.name} ---\n")
                static_results.append(run_static_analysis_py())

            # --- Run Dynamic Analysis immediately ---
            dynamic_results = ["\n=== DYNAMIC ANALYSIS ===\n"]
            dynamic_results.append(run_dynamic_py())

            # Combine all results
            return "\n".join(static_results + dynamic_results)

    except Exception as e:
        return f"[Error] Upload failed: {str(e)}"


# === Command Interpreter ===

def interpret_command(user_input: str):
    """Interpret user command and execute corresponding function."""
    user_input_lower = user_input.strip().lower()

    try:
        # Conversation responses
        if "hello" in user_input_lower or "hi" in user_input_lower:
            return "Hello! 👋 Ready to analyze your Python files."
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
        elif "patch" in user_input_lower and "py" in user_input_lower:
            return run_patch_py()
        elif "auto_fix" in user_input_lower and "py" in user_input_lower:
            return run_auto_fix_py()
        else:
            return "❓ Unknown command. Try: static py | dynamic py | patch py | auto_fix py"
    except Exception as e:
        return f"[Error] {str(e)}"


# === Flask Routes ===

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle ZIP file upload."""
    if 'file' not in request.files:
        return jsonify({"status": "Error", "error": "No file part"})
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "Error", "error": "No selected file"})

    result = handle_file_upload(file)
    return jsonify({"status": "Success", "result": result})


@app.route('/process', methods=['POST'])
def process_command():
    """Handle text commands."""
    user_input = request.form.get('command')
    if not user_input:
        return jsonify({"status": "Error", "error": "No command entered."})

    result = interpret_command(user_input)
    return jsonify({"status": "Success", "result": result})


# === Config ===

app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['STATIC_FOLDER'] = 'static'
app.config['TEMPLATES_FOLDER'] = 'templates'


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
