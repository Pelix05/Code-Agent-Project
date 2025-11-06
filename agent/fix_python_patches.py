import re
from pathlib import Path

PATCHES_DIR = Path("patches") / "patches_py"
FIXED_DIR = Path("patches_py_fixed")  # Fixed patches
FIXED_DIR.mkdir(exist_ok=True)

def fix_patch(patch_text):
    lines = patch_text.splitlines()
    fixed_lines = []
    hunk_start = None
    hunk_lines = []

    for line in lines:
        if line.startswith("diff --git"):
            # Save previous hunk if exists
            if hunk_start is not None:
                fixed_lines.extend(fix_hunk(hunk_start, hunk_lines))
                hunk_lines = []
            fixed_lines.append(line)
        elif line.startswith("@@ "):
            # Start of a new hunk
            if hunk_start is not None:
                fixed_lines.extend(fix_hunk(hunk_start, hunk_lines))
            hunk_start = line
            hunk_lines = []
        elif hunk_start is not None:
            hunk_lines.append(line)
        else:
            # Lines before first hunk (diff headers)
            fixed_lines.append(line)

    # Fix last hunk
    if hunk_start is not None:
        fixed_lines.extend(fix_hunk(hunk_start, hunk_lines))

    return "\n".join(fixed_lines) + "\n"

def fix_hunk(hunk_header, hunk_lines):
    """
    Adjust hunk line counts to match actual lines.
    """
    # Remove any trailing Explanation / notes
    clean_lines = []
    for l in hunk_lines:
        if re.match(r"^\s*Explanation:.*", l):
            break
        clean_lines.append(l)

    # Count lines
    orig_count = sum(1 for l in clean_lines if not l.startswith("+"))
    new_count = sum(1 for l in clean_lines if not l.startswith("-"))

    # Fix hunk header line counts
    fixed_header = re.sub(r"@@ -\d+,\d+ \+\d+,\d+ @@", f"@@ {adjust_hunk_header(hunk_header, clean_lines)} @@", hunk_header)
    return [fixed_header] + clean_lines

def adjust_hunk_header(header, clean_lines):
    # Parse original header
    m = re.match(r"@@ -(\d+),\d+ \+(\d+),\d+ @@", header)
    if not m:
        return header
    start_orig = int(m.group(1))
    start_new = int(m.group(2))
    # Recalculate counts
    orig_count = sum(1 for l in clean_lines if not l.startswith("+"))
    new_count = sum(1 for l in clean_lines if not l.startswith("-"))
    return f"-{start_orig},{orig_count} +{start_new},{new_count}"

def main():
    patch_files = sorted(PATCHES_DIR.glob("*.diff"))
    if not patch_files:
        print("[!] No patch files found.")
        return

    for patch_file in patch_files:
        print(f"[*] Fixing {patch_file.name}")
        text = patch_file.read_text(encoding="utf-8")
        fixed_text = fix_patch(text)
        (FIXED_DIR / patch_file.name).write_text(fixed_text, encoding="utf-8")

    print(f"[+] Fixed patches written to {FIXED_DIR}")

if __name__ == "__main__":
    main()
