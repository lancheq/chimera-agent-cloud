"""Freeze existing guideline resources for shared authorization, without generation."""
import argparse
from pathlib import Path
from chimera_agent_baseline.knowledge_access import build_snapshot, sha

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--resources', type=Path, default=Path('resources'))
    p.add_argument('--encoder', type=Path, default=Path('resources/embedding_model'))
    p.add_argument('--reranker', type=Path, default=Path('model/reranker_model'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    manifest = build_snapshot(args.resources, args.encoder, args.reranker, args.output)
    print(str(manifest) + '\nsha256=' + sha(manifest))
