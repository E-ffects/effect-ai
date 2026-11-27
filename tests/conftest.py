import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Tests never call a real LLM; test_llm.py replaces llm.generate with a fake.
os.environ["RMA_LLM"] = "off"
# Tests never call the embedding API either: the chat retrieval index uses character search only.
os.environ["RMA_RAG_EMBEDDING"] = "off"
