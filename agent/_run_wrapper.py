import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agent.lc_pipeline import run_pipeline, REPORT_PY, SNIPPETS_PY

if __name__ == '__main__':
    run_pipeline(REPORT_PY, SNIPPETS_PY, lang='py')
