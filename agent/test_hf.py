import os
import sys
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
# adjust path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agent import lc_pipeline
import multiprocessing
import time

q = multiprocessing.Queue()
prompt = 'Write a one-line summary: Hello from HuggingFace model.'
try:
    lc_pipeline._invoke_child_process('HuggingFace', prompt, q)
    # wait up to 60s for a result
    start = time.time()
    while time.time() - start < 60:
        try:
            res = q.get(timeout=5)
            print('RESULT:', res)
            break
        except Exception:
            continue
    else:
        print('No result from HuggingFace handler within timeout')
except Exception as e:
    print('Exception when invoking HuggingFace handler:', repr(e))
