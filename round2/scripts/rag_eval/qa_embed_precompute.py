#!/usr/bin/env python
"""Precompute dense embeddings for the QA library questions (run once per lib version).

Usage:
  ~/miniconda3/envs/chimera/bin/python qa_embed_precompute.py \
      ~/CHIMERA-agent/resources/guideline_qa.jsonl \
      ~/CHIMERA-agent/resources/guideline_qa.embeddings.npy \
      ~/CHIMERA-agent/resources/embedding_model
"""

import json
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

lib_path, out_path, model_path = sys.argv[1], sys.argv[2], sys.argv[3]
qa = [json.loads(l) for l in open(lib_path, encoding="utf-8") if l.strip()]
model = SentenceTransformer(model_path, device="cpu")
vecs = model.encode_document([q["question"] for q in qa], show_progress_bar=True)
np.save(out_path, vecs.astype("float32"))
print("saved", vecs.shape, "->", out_path)
