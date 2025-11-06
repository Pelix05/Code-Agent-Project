import re
from pathlib import Path

# === Paths ===
agent_dir = Path(__file__).resolve().parent
patches_cpp = agent_dir / "patches" / "patches_cpp"
fixed_dir = agent_dir / "patches" / "patches_cpp_fixed"
fixed_dir.mkdir(exist_ok=True)

# === Patch sanitizer ===
def fix_patch(patch_text):
    """
    Basic fixes for common issues in diff files:
    - Remove extra leading/trailing spaces in diff headers
    - Ensure line endings are consistent
    - Remove unexpected non-ASCII characters
    """
    # Normalize line endings
    patch_text = patch_text.replace("\r\n", "\n")

    # Remove non-printable characters
    patch_text = "".join(c if c.isprintable() or c in "\n\t" else "" for c in patch_text)

    # Ensure diff headers start with "diff --git a/... b/..."
    patch_text = re.sub(r"^diff\s+--git\s+", "diff --git ", patch_text, flags=re.MULTILINE)

    return patch_text

# === Process all patches ===
patch_files = sorted(patches_cpp.glob("patch_*.diff"))
if not patch_files:
    print("[!] No C++ patch files found.")
else:
    for patch_file in patch_files:
        print(f"[*] Fixing {patch_file.name}")
        text = patch_file.read_text(encoding="utf-8")
        fixed_text = fix_patch(text)
        (fixed_dir / patch_file.name).write_text(fixed_text, encoding="utf-8")

    print(f"[+] Fixed patches written to {fixed_dir}")
