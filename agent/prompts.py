BUG_FIX_PROMPT = """
You are an expert software engineer.

Your task: Generate a valid unified diff patch to fix bugs in the given code snippet, based on the static analysis report.

---------------------
# Static Analysis Report
{analysis}
---------------------
# Buggy Code Snippet
{code_snippet}
---------------------

# Output Rules (STRICT):
- Output ONLY a valid unified diff patch, compatible with `git apply`.
- DO NOT include any text before or after the patch. No explanations, no examples.
- The first line MUST start with:
  diff --git a/<file> b/<file>
- MUST include:
  index <hash>..<hash> <mode>
  --- a/<file>
  +++ b/<file>
- Include only the file shown in the snippet/report.
- The patch MUST modify at least one line (no empty diffs).
- The patch MUST be syntactically correct and compilable.
- Do NOT wrap the output in code blocks (no ``` or markdown).
- Do NOT include commentary or instructional text.
- If there are no necessary changes, return exactly: ""

# Final Output:
Return ONLY the unified diff patch or "".
"""
