"""Synthetic reflection protocol and the real inference/graph/form-fill route."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from shutil import copytree

import pytest
from omegaconf import OmegaConf

import inference
from chimera_agent_baseline import run as runner
from chimera_agent_baseline.agent import form_fill, self_refine
from chimera_agent_baseline.agent.reflection_contract import (
    AuditVerdict, Citation, Context, CorrectionProposal, DraftArtifact, OverwriteRule, Review,
    ReviewBatch, Subject, SyntheticReview, finalize_reflection, resolve, upfront_digest,
)
from chimera_agent_baseline.output.schema import build_dynamic_model, eligible_variables
from chimera_agent_baseline.runtime_audit import PrivateRuntime, RuntimeFatal, canonical, content_digest
from reasoning_align import WEIGHT_TABLE_BY_TASK
from test_p01_g1a_runtime import completion, counter, model_with_transport, profile_for

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_REASON = 'The original value is part of a fictional software-only acceptance fixture.'
CANDIDATE_REASON = 'The reviewed value is an invented software fixture accepted by the synthetic oracle.'
T3_FIELD_NAMES = ' Gleason, margin and postoperative PSA are field names in this fixture.'


def base(task):
    common = {'free_text': SYNTHETIC_REASON, 'repeat_test': None}
    if task == 3:
        return {**common, 'free_text': SYNTHETIC_REASON + T3_FIELD_NAMES,
                'event': 0, 'months_to_recurrence': 12.0}
    common.update(confidence='clear', variable_weights={
        key: WEIGHT_TABLE_BY_TASK[task].get(key, 'not_used') for key in eligible_variables(task, set())})
    choice = {'biopsy_decision': 'no'} if task == 1 else {
        'treatment_recommendation': {'primary': 'active_surveillance', 'modalities': [],
                                     'detail': None, 'as_protocol': None, 'as_trigger': None}}
    return common | choice


def new_candidate(task, original):
    candidate = deepcopy(original)
    candidate['free_text'] = CANDIDATE_REASON
    if task == 1:
        candidate['biopsy_decision'] = 'yes'
    elif task == 2:
        candidate['treatment_recommendation']['primary'] = 'active_treatment'
    else:
        candidate.update(event=1, months_to_recurrence=18.0)
        candidate['free_text'] += T3_FIELD_NAMES
    return candidate


def approved(context, candidate, *, status='accepted', mechanism='external_graph'):
    original = json.loads(context.initial_json)
    quote = next(line.strip() for line in context.upfront.splitlines() if line.strip())
    proposal = CorrectionProposal(
        context.subject, mechanism, canonical(candidate), content_digest(candidate),
        tuple(sorted(key for key in original if candidate[key] != original[key])),
        'Synthetic correctness is defined only by the software fixture.',
        (Citation('rendered_upfront', quote, 'Synthetic source binding assertion'),))
    verdict = AuditVerdict(
        context.subject, mechanism, content_digest(asdict(proposal)), proposal.candidate_digest,
        status, tuple(sorted(candidate)), 'synthetic_contract_stub')
    return Review(proposal, verdict)


@pytest.fixture
def bound():
    original = base(1)
    draft = DraftArtifact('draft-text', 'draft-messages')
    text = 'Synthetic authorized upfront software fixture.'
    subject = Subject('run', 1, 'synthetic', 'profile', draft, content_digest(original),
                      upfront_digest(text), 'process')
    context = Context(subject, canonical(original), text, ())
    authority = SyntheticReview(lambda _: ReviewBatch('not_triggered'))
    return context, authority, build_dynamic_model(1, set())


@pytest.mark.parametrize('scenario,status', [
    ('S01', 'accepted_candidate'), ('S02', 'audit_rejected'),
    ('S03', 'detected_no_executable_proposal'), ('S04', 'no_issue'),
    ('S05', 'executed_no_delta'), ('S06', 'accepted_candidate'), ('S07', 'audit_rejected'),
    ('S08-json', 'invalid_proposal'), ('S08-missing', 'invalid_proposal'),
    ('S08-null', 'invalid_proposal'), ('S08-type', 'invalid_proposal'),
    ('S09', 'error_fallback_initial'),
    ('S10-unknown', 'blocked_missing_authorization'),
    ('S10-not_requested', 'blocked_missing_authorization'),
    ('S10-missing', 'blocked_missing_authorization'),
    ('S10-no-authority', 'blocked_missing_authorization'),
    ('S17-weights', 'binding_or_authority_rejected'),
    ('S17-confidence', 'binding_or_authority_rejected'),
    ('S17-generated-opinion', 'binding_or_authority_rejected'),
    ('S17-unavailable-source', 'binding_or_authority_rejected'),
    ('S17-bad-quote', 'binding_or_authority_rejected'),
    ('S17-malformed-citation', 'binding_or_authority_rejected'),
    ('S18-disabled', 'disabled'), ('S18-not_triggered', 'not_triggered'),
    ('S18-ambiguous', 'ambiguous_candidates'),
])
def test_selector_contract(bound, scenario, status):
    context, authority, schema = bound
    original = json.loads(context.initial_json)
    candidate = new_candidate(1, original)
    if scenario == 'S05':
        candidate = original
    if scenario == 'S06':
        candidate.update(biopsy_decision=original['biopsy_decision'])
        candidate['variable_weights']['age'] = 'noted'
    row = approved(context, candidate)
    proposal, verdict = row.proposal, row.verdict
    if scenario == 'S02':
        verdict = replace(verdict, status='rejected', blocking_codes=('fixture_oracle_rejects',))
    if scenario == 'S07':
        verdict = replace(verdict, blocking_codes=('fixture_semantic_rejection',))
    if scenario.startswith('S08'):
        broken = '{'
        if scenario != 'S08-json':
            wrong = deepcopy(candidate)
            if scenario == 'S08-missing':
                del wrong['biopsy_decision']
            elif scenario == 'S08-null':
                wrong['biopsy_decision'] = None
            else:
                wrong['biopsy_decision'] = 1
            broken = canonical(wrong)
        proposal = replace(proposal, candidate_json=broken)
    if scenario == 'S09':
        verdict = replace(verdict, status='error')
    if scenario.startswith('S10'):
        if scenario == 'S10-no-authority':
            authority = None
        elif scenario == 'S10-missing':
            verdict = None
        else:
            verdict = replace(verdict, status=scenario.split('-', 1)[1])
    if scenario in ('S17-weights', 'S17-confidence'):
        omitted = 'variable_weights' if scenario.endswith('weights') else 'confidence'
        verdict = replace(verdict, reviewed_fields=tuple(k for k in candidate if k != omitted))
    if scenario == 'S17-malformed-citation':
        proposal = replace(proposal, citations=(Citation('rendered_upfront', None, 'Fixture'),))
    elif scenario.startswith('S17-') and scenario not in ('S17-weights', 'S17-confidence'):
        cid = 'generated_opinion' if scenario.endswith('opinion') else 'hidden_test'
        quote = 'missing quote' if scenario.endswith('quote') else proposal.citations[0].quote
        proposal = replace(proposal, citations=(Citation(
            'rendered_upfront' if scenario.endswith('quote') else cid, quote, 'Fixture'),))
    batch = ReviewBatch('detected', (Review(proposal, verdict),))
    if scenario in ('S03', 'S04', 'S18-disabled', 'S18-not_triggered'):
        batch = ReviewBatch({'S03': 'detected', 'S04': 'no_issue', 'S18-disabled': 'disabled',
                             'S18-not_triggered': 'not_triggered'}[scenario])
    if scenario == 'S18-ambiguous':
        inner = approved(context, candidate, mechanism='internal_formfill')
        inner = replace(inner, proposal=replace(inner.proposal, subject=replace(context.subject, base_digest='old')))
        batch = ReviewBatch('detected', (row, inner))
    before = asdict(batch)
    selection = resolve(context, batch, authority, schema)
    assert selection.status == status
    assert asdict(batch) == before and json.loads(context.initial_json) == original
    assert selection.judgment == (candidate if status in ('accepted_candidate', 'executed_no_delta') else original)
    assert selection.judgment['repeat_test'] is None


@pytest.mark.parametrize('side,field', [
    (side, field) for side in ('proposal', 'verdict')
    for field in ('run_id', 'task', 'case_id', 'profile_digest', 'draft', 'base_digest',
                  'source_digest', 'process_digest', 'candidate_digest')
] + [('verdict', field) for field in ('proposal_digest', 'mechanism', 'method')])
def test_S11_bindings_reject_stale_or_other_review(bound, field, side):
    context, authority, schema = bound
    original = json.loads(context.initial_json)
    review = approved(context, new_candidate(1, original))
    component = getattr(review, side)
    if hasattr(component.subject, field):
        component = replace(component, subject=replace(component.subject, **{field: 'wrong'}))
    else:
        component = replace(component, **{field: 'wrong'})
    selection = resolve(context, ReviewBatch('detected', (replace(review, **{side: component}),)), authority, schema)
    assert selection.status == 'binding_or_authority_rejected' and selection.judgment == original


def test_S15_invalid_initial_cannot_be_rescued(bound):
    context, authority, schema = bound
    edited = json.loads(context.initial_json)
    edited['biopsy_decision'] = None
    with pytest.raises(RuntimeFatal, match='failed_no_valid_initial'):
        resolve(replace(context, initial_json=canonical(edited)), ReviewBatch('error'), authority, schema)


def entry_fixture(tmp_path, monkeypatch, task, *, external=False, internal=False, invalid=False):
    """Real adapter/runner/graph. Network generation is an in-memory HTTP stub."""
    monkeypatch.chdir(tmp_path)
    copytree(ROOT / 'templates', tmp_path / 'templates')
    inputs = tmp_path / 'input'
    inputs.mkdir()
    slug = next(slug for slug, number in inference.CLINICAL_SLUG_TO_TASK.items() if number == task)
    (inputs / 'inputs.json').write_text(json.dumps([
        {'socket': {'slug': slug, 'relative_path': 'clinical.json'}},
        {'socket': {'slug': inference.STRUCTURED_PROMPT_SLUG, 'relative_path': 'prompt.json'}}]))
    (inputs / 'clinical.json').write_text('{}')
    (inputs / 'prompt.json').write_text(json.dumps({'age': 65, 'enc_type': 'Fictional software fixture'}))
    config_path = tmp_path / 'config.yaml'
    config = OmegaConf.load(ROOT / 'configs/config.yaml')
    config.agent.predictor.enabled = False
    config.agent.predictor.decision_override = False
    config.agent.predictor.decision_override_tasks = []
    config.agent.form_fill.atomic_check = False
    config.agent.form_fill.self_refine = external
    config.agent.form_fill.self_refine_max_iter = 1
    config.agent.form_fill.max_retries = 1
    config.agent.workflow = 'react'
    OmegaConf.save(config, config_path)
    for name, value in {'INPUT_PATH': inputs, 'OUTPUT_PATH': tmp_path / 'sockets',
                        'CONFIG_PATH': config_path, 'RESOURCE_PATH': tmp_path / 'empty_resources',
                        'MODEL_PATH': tmp_path / 'no_model'}.items():
        monkeypatch.setattr(inference, name, value)
    monkeypatch.setenv('CHIMERA_ENTRY_PROFILE', 'synthetic')
    monkeypatch.setenv('CHIMERA_FORMFILL_SELF_REFINE', '1' if internal else '0')
    monkeypatch.setenv('CHIMERA_STRICT_RUNTIME', '0')
    monkeypatch.setenv('PYTHONPATH', str(ROOT / 'src'))
    monkeypatch.setattr(inference, 'start_embedding_service', lambda *a: pytest.fail('service startup'))
    profile = profile_for(monkeypatch, tmp_path, inference._load_config(inputs, tmp_path / 'unused', task), config_path)
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        system = payload['messages'][0]['content']
        if system == form_fill._SYSTEM_PROMPT:
            judgment = base(task)
            if invalid:
                judgment['biopsy_decision'] = None
            return completion(canonical(judgment)[1:])
        if system in (form_fill._CRITIQUE_SYSTEM, self_refine._CRITIQUE_SYSTEM):
            return completion('{"issues":["Synthetic detection only"],"severity":"low"}')
        if system == form_fill._REFINE_SYSTEM:
            return completion(canonical(new_candidate(task, base(task))))
        if system == self_refine._REFINE_SYSTEM:
            return completion('Synthetic external revised prose with no authority to enter the form fill.')
        return completion('Synthetic software draft with no clinical authority.')

    def load_fake(_cfg):
        return model_with_transport(handler)

    monkeypatch.setattr(runner, 'load_model', load_fake)
    return profile, requests


def read_entry(tmp_path):
    root = next((tmp_path / 'private').iterdir())
    rows = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
    previous = None
    for seq, row in enumerate(rows):
        assert row['seq'] == seq and row['previous'] == previous
        assert content_digest({k: v for k, v in row.items() if k != 'digest'}) == row['digest']
        previous = row['digest']
    return root, rows


@pytest.mark.parametrize('task,scenario', [(1, 'S06'), (2, 'projection'), (3, 'S01'),
    (3, 'S01-internal-authority'),
    (3, 'S02'), (3, 'S03'), (3, 'S04'), (3, 'S05'), (3, 'S08'), (3, 'S09'), (3, 'S12'),
    (3, 'S12-rationale'), (3, 'S14')])
def test_real_entry_assigns_and_traces(task, scenario, tmp_path, monkeypatch):
    profile, requests = entry_fixture(tmp_path, monkeypatch, task)
    rules = ()
    if scenario == 'S14':
        reviewed_reason = CANDIDATE_REASON + T3_FIELD_NAMES
        expected_note = (
            '\n\n[校准说明] 虽然上文推理识别出复发风险因素，但预测复发时间 (24.0 个月) 不在早期复发'
            '窗口内（训练集基率校准阈值 τ=24.0 个月），按生化复发基率校准规则判定 event=0（观察期内无复发）。'
        )
        rules = (
            OverwriteRule('t3_time_calibration', '/event', '1', '0', 'synthetic-tau-event'),
            OverwriteRule('t3_time_calibration', '/free_text', canonical(reviewed_reason),
                          canonical(reviewed_reason + expected_note), 'synthetic-tau-note'),
        )

    def reviewer(context):
        original = json.loads(context.initial_json)
        candidate = new_candidate(task, original)
        if scenario == 'S03':
            return ReviewBatch('detected')
        if scenario == 'S04':
            return ReviewBatch('no_issue')
        if scenario == 'S05':
            candidate = original
        if scenario == 'S06':
            candidate['biopsy_decision'] = original['biopsy_decision']
            candidate['variable_weights']['age'] = 'noted'
        if scenario == 'S09':
            raise TimeoutError('synthetic-only review timeout')
        if scenario in ('S12', 'S14'):
            candidate['months_to_recurrence'] = 24.0
        if scenario == 'S12-rationale':
            candidate['free_text'] = CANDIDATE_REASON
        review = approved(context, candidate, status='rejected' if scenario == 'S02' else 'accepted',
                          mechanism='internal_formfill' if scenario == 'S01-internal-authority'
                          else 'external_graph')
        if scenario == 'S08':
            review = replace(review, proposal=replace(review.proposal, candidate_json='{'))
        return ReviewBatch('detected', (review,))

    assert inference.run(_profile_registry={'synthetic': profile}, _token_counter=counter,
                         _reflection_test=SyntheticReview(reviewer, rules)) == 0
    root, rows = read_entry(tmp_path)
    assert len(requests) == len([e for e in rows if e['kind'] == 'wire_attempt']) == 2
    by_stage = {r['stage']: r for r in rows if r['kind'] == 'consumption'}
    selection = by_stage['reflection_selection']
    consumed = by_stage['reflection_formfill_consumed']
    assert selection['after_digest'] == consumed['after_digest']
    assert by_stage['atomic_verification']['before_digest'] == consumed['after_digest']
    assert by_stage['formfill_input']['after']['transcript'] == 'Synthetic software draft with no clinical authority.'
    final = by_stage['final_sockets']
    assert final['status'] == 'finalized'
    for socket in final['files'].values():
        assert hashlib.sha256((tmp_path / 'sockets' / socket['file']).read_bytes()).hexdigest() == socket['sha256']
    assert len(list((tmp_path / 'sockets').iterdir())) == 2
    assert (root / 'temporary_evidence/output' / f'task{task}/gc-case/prediction.json').is_file()
    assert rows[-1]['kind'] == 'run_completed'
    fields = {x['field']: x for x in final['reflection_receipt']['fields']}
    assert fields['/repeat_test']['status'] == 'internal_only' and fields['/repeat_test']['final_present']
    expected = {'S02': 'audit_rejected', 'S03': 'detected_no_executable_proposal', 'S04': 'no_issue',
                'S05': 'executed_no_delta', 'S08': 'invalid_proposal', 'S09': 'error_fallback_initial'}
    assert selection['status'] == expected.get(scenario, 'accepted_candidate')
    if scenario in ('S02', 'S03', 'S04', 'S08', 'S09'):
        assert consumed['after'] == base(task)
        assert final['reflection_consumption'] == ('finalized_initial_with_error'
                                                   if scenario in ('S08', 'S09') else 'finalized_initial')
    if scenario in ('S12', 'S14'):
        assert final['after']['decision']['event'] == 0
        assert final['reflection_consumption'] == ('accepted_then_overridden' if scenario == 'S14' else 'consumption_contract_failed')
        assert fields['/event']['history'][0]['stage'] == 't3_time_calibration'
        assert fields['/event']['history'][0]['rule_id'] == (rules[0].rule_id if rules else None)
        assert fields['/free_text']['history'][0]['rule_id'] == (rules[1].rule_id if rules else None)
    if scenario == 'S12-rationale':
        assert final['reflection_consumption'] == 'consumption_contract_failed'
        assert fields['/free_text']['history'][0]['stage'] == 't3_rationale_elements'
        assert fields['/event']['status'] == fields['/months_to_recurrence']['status'] == 'retained'
    if scenario.startswith('S01'):
        assert final['reflection_consumption'] == 'finalized_retained'
        assert final['after']['reasoning'] == CANDIDATE_REASON + T3_FIELD_NAMES
        assert final['after']['decision'] == {'event': 1, 'months_to_recurrence': 18.0}
    if scenario == 'S06':
        assert selection['label_changed'] is False
        assert {change['field'] for change in selection['changed_fields']} == {'/free_text', '/variable_weights/age'}
        assert selection['after']['biopsy_decision'] == selection['before']['biopsy_decision'] == 'no'
        assert consumed['after']['variable_weights']['age'] == 'noted'
        assert fields['/free_text']['status'] == 'retained'
        assert final['reflection_consumption'] == 'consumption_contract_failed'
        assert fields['/variable_weights/age']['history'][0]['stage'] == 'weight_and_confidence_calibration'
    if task == 2:
        assert final['after']['decision'] == 'active_treatment'
        assert final['after']['reasoning']['free_text'] == CANDIDATE_REASON
        assert fields['/treatment_recommendation/detail']['status'] == 'internal_only'


@pytest.mark.parametrize('phase', ['assignment', 'assembly', 'socket'])
def test_S13_detects_first_missing_or_old_value(phase):
    original, chosen = base(3), new_candidate(3, base(3))
    selected = {'stage': 'reflection_selection', 'after': chosen, 'seq': 0,
                'selected_source': 'authorized_candidate', 'digest': 'selected',
                'after_digest': content_digest(chosen)}
    consumed = {'stage': 'reflection_formfill_consumed', 'seq': 1, 'selection_digest': 'selected',
                'digest': 'consumed', 'after_digest': content_digest(original if phase == 'assignment' else chosen)}
    assembly = deepcopy(chosen)
    if phase == 'assembly':
        del assembly['free_text']
    stages = [selected, consumed, dict(stage='assembled_output', seq=2, after=assembly),
              dict(stage='graph_output', seq=3, after={'structured_response': chosen}),
              dict(stage='prediction_file', seq=4, after=chosen)]
    sockets = {'decision': {'event': 1, 'months_to_recurrence': 18.0}}
    if phase != 'socket':
        sockets['reasoning'] = chosen['free_text']
    receipt = finalize_reflection(3, stages, sockets)
    assert receipt.status == 'consumption_contract_failed'
    if phase == 'assignment':
        assert receipt.consumption_digest is None
    else:
        row = next(r for r in receipt.fields if r['field'] == '/free_text')
        assert row['history'][0]['stage'] == ('assembled_output' if phase == 'assembly' else 'final_sockets')


def test_S06_same_label_keeps_reviewed_reason_and_weight_when_unmodified():
    chosen = base(1)
    original = deepcopy(chosen)
    chosen['free_text'] = CANDIDATE_REASON
    chosen['variable_weights']['age'] = 'noted'
    assert original['biopsy_decision'] == chosen['biopsy_decision']
    assert original['variable_weights']['age'] != chosen['variable_weights']['age']
    digest = content_digest(chosen)
    reasoning = {key: chosen[key] for key in ('free_text', 'confidence', 'variable_weights')}
    stages = [dict(stage='reflection_selection', seq=0, digest='selected', after=chosen,
                   after_digest=digest, selected_source='authorized_candidate'),
              dict(stage='reflection_formfill_consumed', seq=1, digest='consumed', after_digest=digest,
                   selection_digest='selected'),
              dict(stage='assembled_output', seq=2, after=chosen),
              dict(stage='graph_output', seq=3, after={'structured_response': chosen}),
              dict(stage='prediction_file', seq=4, after=chosen),
              dict(stage='reasoning_align', seq=5, after=reasoning)]
    receipt = finalize_reflection(1, stages, {'decision': chosen['biopsy_decision'], 'reasoning': reasoning})
    fields = {row['field']: row for row in receipt.fields}
    assert receipt.status == 'finalized_retained'
    assert fields['/free_text']['final'] == CANDIDATE_REASON
    assert fields['/variable_weights/age']['final'] == 'noted'


@pytest.mark.parametrize('failure', ['invalid_initial', 'review_fatal', 'audit_persistence'])
def test_entry_fatal_has_no_finalized_receipt(failure, tmp_path, monkeypatch):
    task = 1 if failure == 'invalid_initial' else 3
    profile, requests = entry_fixture(tmp_path, monkeypatch, task, invalid=failure == 'invalid_initial')
    called = []

    def reviewer(context):
        called.append(True)
        if failure == 'review_fatal':
            raise RuntimeFatal('fixture_fatal_budget')
        return ReviewBatch('detected', (approved(context, new_candidate(task, json.loads(context.initial_json))),))

    if failure == 'audit_persistence':
        def fail_export(self, root):
            raise OSError('Synthetic audit I/O failure')
        monkeypatch.setattr(PrivateRuntime, 'export_tree', fail_export)
    with pytest.raises(RuntimeFatal):
        inference.run(_profile_registry={'synthetic': profile}, _token_counter=counter,
                      _reflection_test=SyntheticReview(reviewer))
    _, rows = read_entry(tmp_path)
    assert len(called) == (0 if failure == 'invalid_initial' else 1)
    assert len(requests) == 2
    assert not any(e['kind'] == 'run_completed' or e['stage'] == 'final_sockets' for e in rows)
    assert any(e['kind'] == 'run_failed' for e in rows)


def test_external_graph_artifact_remains_blocked_without_bound_review(tmp_path, monkeypatch):
    profile, requests = entry_fixture(tmp_path, monkeypatch, 3, external=True)
    assert inference.run(_profile_registry={'synthetic': profile}, _token_counter=counter) == 0
    _, rows = read_entry(tmp_path)
    observation = next(x for x in rows if x['stage'] == 'external_reflection')
    assert observation['artifact']['mechanism'] == 'external_graph'
    assert observation['status'] == 'detected_no_executable_proposal'
    assert next(x for x in rows if x['stage'] == 'final_sockets')['external_reflection_status'] == observation['status']
    assert observation['consumed'] is False and observation['before'] == observation['after']
    assert not any(x['stage'] == 'reflection_formfill_consumed' for x in rows)
    assert len(requests) == 4


def test_internal_graph_keeps_existing_unreviewed_form_fill_behavior(tmp_path, monkeypatch):
    profile, requests = entry_fixture(tmp_path, monkeypatch, 3, internal=True)
    assert inference.run(_profile_registry={'synthetic': profile}, _token_counter=counter) == 0
    _, rows = read_entry(tmp_path)
    observation = next(x for x in rows if x['kind'] == 'consumption' and x['stage'] == 'internal_reflection')
    assert observation['artifact']['mechanism'] == 'internal_formfill'
    assert observation['acceptance'] == 'legacy_behavior_unreviewed'
    assert next(x for x in rows if x['stage'] == 'atomic_verification')['before'] == new_candidate(3, base(3))
    assert not any(x['stage'] == 'reflection_formfill_consumed' for x in rows)
    assert len(requests) == 4


def test_code_fixture_cannot_opt_in_legacy_entry(tmp_path, monkeypatch):
    profile, requests = entry_fixture(tmp_path, monkeypatch, 3)
    monkeypatch.delenv('CHIMERA_ENTRY_PROFILE')
    with pytest.raises(RuntimeFatal, match='code-injected synthetic profile'):
        inference.run(_reflection_test=SyntheticReview(lambda _: ReviewBatch('disabled')))
    assert requests == []
