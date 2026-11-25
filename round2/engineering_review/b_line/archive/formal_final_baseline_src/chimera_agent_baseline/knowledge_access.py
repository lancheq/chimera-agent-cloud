"""Pinned existing hybrid retrieval; no new retrieval ranking or patient data.

A private copy shields the frozen Chroma bytes from library migration writes.
The encoder is loaded from the pinned local model, never an unidentified socket.
"""
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import tempfile

from chimera_agent_baseline.information_access import canonical, digest

PACKAGES = ('chromadb', 'sentence-transformers', 'transformers', 'torch', 'numpy', 'rank-bm25')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inventory(root):
    root = Path(root)
    return {str(p.relative_to(root)): {'sha256': sha(p), 'bytes': p.stat().st_size}
            for p in sorted(root.rglob('*')) if p.is_file() and not p.name.startswith('._')
            and '__pycache__' not in p.parts and not p.name.startswith('.')}


def alignment_binding(manifest):
    return digest({'resources': {name: manifest[name]['files'] for name in ('corpus', 'encoder', 'reranker')},
                   'packages': manifest['packages'], 'document_api': 'SentenceTransformer.encode_document'})


def load_manifest(path, expected=None, *, require_alignment=False):
    path = Path(path)
    raw = path.read_bytes()
    if expected and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Knowledge manifest identity differs')
    value = json.loads(raw)
    if value.get('schema_version') != 1 or value.get('pipeline') != 'existing_hybrid_strict_v1':
        raise ValueError('Unknown authorized retrieval pipeline')
    for name in ('corpus', 'encoder', 'reranker'):
        item = value[name]
        if not Path(item['path']).is_absolute() or not item['files']:
            raise ValueError('Absolute populated knowledge resource binding required')
        if inventory(item['path']) != item['files']:
            raise ValueError('Knowledge resource differs: ' + name)
    if value['packages'] != {p: version(p) for p in PACKAGES}:
        raise ValueError('Knowledge dependency versions differ')
    if require_alignment:
        evidence=value.get('alignment_evidence') or {}
        if evidence.get('resource_binding') != alignment_binding(value):
            raise ValueError('Knowledge vector alignment not authorized for these resources')
        report=Path(evidence.get('report_path', ''))
        if not report.is_file() or sha(report) != evidence.get('report_sha256'):
            raise ValueError('Knowledge vector alignment evidence differs')
        result=json.loads(report.read_text())
        if not result.get('passed') or result.get('resource_binding') != alignment_binding(value):
            raise ValueError('Knowledge vector compatibility screen failed')
    return value


def build_snapshot(resource_dir, encoder_dir, reranker_dir, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Knowledge snapshot output must be new')
    resource_dir = Path(resource_dir).resolve()
    corpus = output / 'corpus'
    corpus.mkdir(parents=True)
    selected = ('guidelines_db', 'guideline_qa.jsonl', 'guideline_qa.embeddings.npy')
    sources = {}
    for name in selected:
        source = resource_dir / name
        before = inventory(source) if source.is_dir() else {'file': {'sha256': sha(source), 'bytes': source.stat().st_size}}
        if source.is_dir():
            shutil.copytree(source, corpus / name, ignore=shutil.ignore_patterns('._*', '*.lock'))
        else:
            shutil.copy2(source, corpus / name)
        after = inventory(source) if source.is_dir() else {'file': {'sha256': sha(source), 'bytes': source.stat().st_size}}
        copied = inventory(corpus / name) if source.is_dir() else {'file': {'sha256': sha(corpus / name), 'bytes': (corpus / name).stat().st_size}}
        if before != after or copied != before:
            raise ValueError('Knowledge input changed during snapshot; do not publish manifest')
        sources[name] = {'path': str(source), 'files': before}
    manifest = {'schema_version': 1, 'pipeline': 'existing_hybrid_strict_v1',
        'scope': 'Knowledge resources only; authorization is not independent clinical guideline adjudication',
        'sources': sources, 'corpus': {'path': str(corpus), 'files': inventory(corpus)},
        'encoder': {'path': str(Path(encoder_dir).resolve()), 'files': inventory(encoder_dir)},
        'reranker': {'path': str(Path(reranker_dir).resolve()), 'files': inventory(reranker_dir)},
        'packages': {p: version(p) for p in PACKAGES},
        'channels': ['dense', 'bm25', 'rrf', 'cross_encoder', 'qa_dense'],
        'fallbacks_allowed': False}
    path = output / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    load_manifest(path)
    return path


def create_search(manifest_path, snapshot_id, *, _engineering_audit=False):
    """Import heavy dependencies only inside a budget-admitted search callback."""
    manifest = load_manifest(manifest_path, snapshot_id, require_alignment=not _engineering_audit)
    from packaging.version import Version
    if Version(version('transformers')) < Version('4.56.2'):
        raise ValueError('EmbeddingGemma bidirectional attention requires transformers >= 4.56.2; do not silently use causal embeddings')
    if Version(version('torch')) < Version('2.6'):
        raise ValueError('Pinned bidirectional masks require torch >= 2.6')
    from chimera_agent_baseline.rag import GuidelinesSearch
    from sentence_transformers import SentenceTransformer

    class VerifiedSearch(GuidelinesSearch):
        def __init__(self):
            if os.environ.get('CHIMERA_RERANKER', 'on').lower() != 'on':
                raise ValueError('Pinned hybrid retrieval requires its configured reranker')
            # Guarded subprocesses fail on any required component fallback.
            os.environ['CHIMERA_STRICT_RUNTIME'] = '1'
            os.environ['CHIMERA_QA_STRICT'] = '1'
            os.environ['HF_HUB_OFFLINE'] = '1'
            os.environ['TRANSFORMERS_OFFLINE'] = '1'
            os.environ['ANONYMIZED_TELEMETRY'] = 'False'
            self._private = tempfile.TemporaryDirectory(prefix='chimera-knowledge-')
            private = Path(self._private.name) / 'corpus'
            shutil.copytree(manifest['corpus']['path'], private)
            if inventory(private) != manifest['corpus']['files']:
                raise ValueError('Knowledge copy differs')
            self._encoder = SentenceTransformer(manifest['encoder']['path'], device='cpu', local_files_only=True)
            super().__init__(private, reranker_dir=manifest['reranker']['path'])
            if self._qa_vecs is None or self._reranker is None or not self._chunks:
                raise ValueError('Pinned hybrid channel not loaded')
            raw = self._collection.get(include=['documents', 'metadatas'])
            self._provenance = {}
            for cid, text, meta in zip(raw['ids'], raw['documents'], raw['metadatas']):
                self._provenance.setdefault(text, []).append({'record_id': cid, 'metadata': meta})
            for index, q in enumerate(self._qa):
                head = f"[{q['source']} {q['section_id']}]".strip()
                if q.get('section_title'):
                    head += ' ' + q['section_title']
                text = head + '\n' + q['support_quote'] if q.get('support_quote') else f"Q: {q['question']}\nA: {q['answer']}"
                self._provenance.setdefault(text, []).append({'record_id': 'qa:' + str(index),
                    'source': q['source'], 'section': q['section_id'], 'support_quote_present': bool(q.get('support_quote'))})

        def _requires_embedding_socket(self):
            return False

        def _encoder_resource(self):
            return manifest['encoder']['path']

        def _encode_query(self, text):
            return self._encoder.encode_query(text).tolist()

        def _format(self, ranked_chunks, qa_hits):
            hits = super()._format(ranked_chunks, qa_hits)
            for hit in hits:
                if hit['text'] not in self._provenance:
                    raise ValueError('Retrieved text lacks pinned source')
                hit['provenance'] = self._provenance[hit['text']]
                hit['text_sha256'] = hashlib.sha256(hit['text'].encode()).hexdigest()
                hit['knowledge_snapshot_id'] = snapshot_id
            return hits

    return VerifiedSearch()
