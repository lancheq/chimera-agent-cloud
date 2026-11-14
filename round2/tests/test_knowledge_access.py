"""Pinned resource provenance is an execution condition, not a prompt claim."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import pytest
from chimera_agent_baseline.knowledge_access import inventory, load_manifest, PACKAGES
from chimera_agent_baseline.information_access import AccessDenied


def fake_manifest(tmp_path):
    value = dict(schema_version=1, pipeline='existing_hybrid_strict_v1', packages={p: version(p) for p in PACKAGES})
    for name in ('corpus', 'encoder', 'reranker'):
        root = tmp_path / name; root.mkdir()
        (root / 'synthetic.txt').write_text('resource fixture, no clinical content')
        value[name] = {'path': str(root), 'files': inventory(root)}
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps(value))
    return path


def test_pin_refuses_changed_or_added_resource_and_wrong_identity(tmp_path):
    manifest = fake_manifest(tmp_path)
    original = manifest.read_bytes()
    pin = hashlib.sha256(original).hexdigest()
    load_manifest(manifest, pin)
    with pytest.raises(ValueError, match='identity differs'):
        load_manifest(manifest, '0' * 64)
    resource = tmp_path / 'corpus/synthetic.txt'
    resource.write_text('changed')
    with pytest.raises(ValueError, match='resource differs'):
        load_manifest(manifest, pin)
    resource.write_text('resource fixture, no clinical content')
    (tmp_path / 'encoder/unlisted.json').write_text('{}')
    with pytest.raises(ValueError, match='resource differs'):
        load_manifest(manifest, pin)


def test_dependency_version_not_just_resource_files_is_bound(tmp_path):
    path = fake_manifest(tmp_path)
    value = json.loads(path.read_text()); value['packages']['torch'] = 'unverified'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='dependency versions'):
        load_manifest(path)


def test_knowledge_observation_must_equal_granted_cache(tmp_path):
    from chimera_agent_baseline.information_access import InformationAccess
    from chimera_agent_baseline.guarded_runtime import verify_messages
    from langchain_core.messages import ToolMessage
    access = InformationAccess(ledger_path=tmp_path / 'ledger.sqlite', session_id='fixture', case_id='A', task=1,
        snapshot_id='fixture', budget_units=0, max_attempts=2, knowledge_snapshot_id='fixture-knowledge')
    value = {'query': 'synthetic', 'results': [{'text': 'synthetic source'}]}
    access.execute('search_guidelines', 'A', lambda: value, query='synthetic')
    message = ToolMessage(content=json.dumps(value), name='search_guidelines', tool_call_id='q')
    verify_messages(access, [message])
    assert message.additional_kwargs['information_access_knowledge_success']
    assert not message.additional_kwargs['information_access_clinical_success']
    value['results'][0]['text'] = 'ungranted change'
    with pytest.raises(AccessDenied, match='granted source'):
        verify_messages(access, [ToolMessage(content=json.dumps(value), name='search_guidelines', tool_call_id='q')])
    assert access.snapshot()['spent_units'] == 0 and not access.snapshot()['visible_sections']


def test_resource_hashes_without_vector_alignment_cannot_authorize_search(tmp_path):
    path = fake_manifest(tmp_path)
    with pytest.raises(ValueError, match='vector alignment not authorized'):
        load_manifest(path, require_alignment=True)
