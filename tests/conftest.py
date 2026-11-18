import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Tests never call a real LLM; test_llm.py replaces llm.generate with a fake.
os.environ["RMA_LLM"] = "off"
