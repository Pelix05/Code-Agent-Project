import difflib
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PY = BASE / 'python_repo'
PATCH_DIR = BASE / 'agent' / 'patches_py_fixed' if (BASE / 'agent' / 'patches_py_fixed').exists() else BASE / 'agent' / 'patches_py_fixed'
PATCH_DIR.mkdir(parents=True, exist_ok=True)

# 1) labels.py: insert `self.font = None` after `self.path, self.size = path, size` in __init__
labels_path = PY / 'puzzle-challenge' / 'labels.py'
labels_src = labels_path.read_text(encoding='utf-8').splitlines(keepends=False)
labels_new = []
for i, line in enumerate(labels_src):
    labels_new.append(line)
    if line.strip().startswith('self.path, self.size = path, size'):
        labels_new.append('        # Ensure font attribute exists to satisfy static analyzers')
        labels_new.append('        self.font = None')

# produce unified diff
("""Create unified diffs with paths relative to the python repo root: a/puzzle-challenge/...
so they can be applied when running `git apply` from the python_repo directory.""")
ldiff = list(difflib.unified_diff(labels_src, labels_new, fromfile='a/puzzle-challenge/labels.py', tofile='b/puzzle-challenge/labels.py', lineterm=''))
(Path(PATCH_DIR) / 'patch_16.diff').write_text('\n'.join(ldiff), encoding='utf-8')
print('wrote', Path(PATCH_DIR) / 'patch_16.diff')

# 2) puzzle_piece.py: insert x_diff/y_diff initialization before the if side == ... block
pp_path = PY / 'puzzle-challenge' / 'puzzle_piece.py'
pp_src = pp_path.read_text(encoding='utf-8').splitlines(keepends=False)
pp_new = []
for i, line in enumerate(pp_src):
    pp_new.append(line)
    if line.strip().startswith('p2.center = other_piece.rect.center'):
        # next non-empty line will likely be the if side line; insert initialization after this
        pp_new.append('                        # Ensure x_diff and y_diff are always defined')
        pp_new.append('                        x_diff = 0')
        pp_new.append('                        y_diff = 0')

pdiff = list(difflib.unified_diff(pp_src, pp_new, fromfile='a/puzzle-challenge/puzzle_piece.py', tofile='b/puzzle-challenge/puzzle_piece.py', lineterm=''))
(Path(PATCH_DIR) / 'patch_17.diff').write_text('\n'.join(pdiff), encoding='utf-8')
print('wrote', Path(PATCH_DIR) / 'patch_17.diff')
