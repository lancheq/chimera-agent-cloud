import json
import pytest
from langchain_core.messages import AIMessage
from chimera_agent_baseline.agent import form_fill
from tools.run_t1_hint_validation import LoggedModel, run_arm


class Model:
    def __init__(self, judgment):
        self.judgment = judgment

    def invoke(self, messages):
        raw = json.dumps(self.judgment)
        return AIMessage(content=raw[1:] if messages[-1].type == 'ai' else raw)


def test_hint_pair_changes_only_reference_and_really_executes_same_gate(monkeypatch, tmp_path):
    monkeypatch.setenv('CHIMERA_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setenv('CHIMERA_STRICT_RUNTIME', '1')
    for name in ('_T1_DL_MODE', '_T1_DECISION_LAYER', '_PREDICTOR_NOTE_ENABLED'):
        monkeypatch.setattr(form_fill, name, getattr(form_fill, name))
    judgment = form_fill._default_judgment(1)
    judgment.update(biopsy_decision='yes', free_text='Synthetic context for testing prompt reference routing.')
    state = {'task': 1, 'case_id': 'synthetic', 'patient': {'age': 65},
             'messages': [AIMessage(content='Synthetic context for testing prompt reference routing.')],
             'predictor_decision': {'decision': 'no', 'probabilities': {'yes': .1, 'no': .9}}}
    off = LoggedModel(Model(judgment), 123)
    on = LoggedModel(Model(judgment), 123)
    a = run_arm(state, off, False, atomic_check=False)
    b = run_arm(state, on, True, atomic_check=False)
    assert a['structured_response']['biopsy_decision'] == b['structured_response']['biopsy_decision'] == 'no'
    assert 'Clinical prediction reference' not in off.calls[0]['messages'][1]['content']
    assert 'Clinical prediction reference' in on.calls[0]['messages'][1]['content']
    assert off.calls[0]['messages'][0] == on.calls[0]['messages'][0]
    assert state['predictor_decision']['probabilities']['yes'] == .1
    assert off.calls[0]['seed'] == on.calls[0]['seed'] == 123
