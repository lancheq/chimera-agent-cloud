import json
from dataclasses import asdict
import numpy as np
import pytest
from joblib.externals import cloudpickle
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

from chimera_agent_baseline.agent.decision_gate import propose_t1_revision,stage_t1_proposal
from tools.nested_t1_gate import fit_nested_gate


def test_symmetric_proposals_require_a_wholly_opposite_interval():
    assert propose_t1_revision('yes',.2,.1,.3).proposed=='no'
    assert propose_t1_revision('no',.8,.7,.9).proposed=='yes'
    assert propose_t1_revision('yes',.49,.3,.7).action=='keep'
    assert propose_t1_revision('no',.51,.5,.9).action=='keep'
    assert propose_t1_revision('yes',.49,.1,.5).action=='keep'


@pytest.mark.parametrize('values',[(float('nan'),.1,.3),(.2,.4,.3),(.2,-.1,.3),(.2,.1,1.1)])
def test_invalid_gate_estimates_fail_instead_of_becoming_silent_keep(values):
    with pytest.raises(ValueError):
        propose_t1_revision('yes',*values)


def test_proposal_cannot_change_decision_while_reusing_old_rationale():
    judgment={'biopsy_decision':'yes','free_text':'Synthetic supporting explanation.','repeat_test':'Synthetic plan'}
    p=propose_t1_revision('yes',.2,.1,.3)
    staged=stage_t1_proposal(judgment,p)
    assert staged['judgment']==judgment
    assert staged['decision_review']['action']=='review_required'
    assert not staged['revision_applied']
    staged['judgment']['free_text']='changed'
    assert judgment['free_text']=='Synthetic supporting explanation.'
    with pytest.raises(ValueError,match='different original'):
        stage_t1_proposal({'biopsy_decision':'no'},p)


def test_every_inner_fit_excludes_outer_patients_and_models_are_frozen():
    rows=[{'psa':float(i+1),'age':float(60+i%10),'psad':.1,'vol':30.,'pirads_int':3.} for i in range(24)]
    ids=[str(i) for i in range(24)]
    labels=['yes' if i%2 else 'no' for i in range(24)]
    bundle={'num_names':['psa','age','knn_avg_outcome'],'cat_names':[],
            'imp':SimpleImputer(strategy='median'),'scaler':StandardScaler(),
            'clf':LogisticRegression(C=.5,max_iter=1000),'reducer':None}
    settings={'inner_folds':3,'inner_seed':17,'meta_model':{'C':.5,'max_iter':1000},
              'bootstrap':{'seed':19,'n':20}}
    ledger=[]
    gate=fit_nested_gate(bundle,ids[:18],rows[:18],[None]*18,labels[:18],labels[:18],set(ids[18:]),settings,ledger)
    assert len(ledger)==5
    assert all(not set(x['train_ids'])&set(ids[18:]) for x in ledger)
    assert all(not set(x['train_ids'])&set(x['forbidden_ids']) for x in ledger)
    assert next(x for x in ledger if x['tag']=='meta_and_bootstrap')['meta_input']=='inner_oof_only'
    frozen=cloudpickle.loads(cloudpickle.dumps(gate))
    old=gate.meta.coef_.copy()
    a=gate.predict(ids[18:],rows[18:],[None]*6,['yes']*6)
    b=frozen.predict(ids[18:],rows[18:],[None]*6,['yes']*6)
    assert all(np.array_equal(x,y) for x,y in zip(a,b))
    assert np.array_equal(old,gate.meta.coef_)
    assert (a[2]<=a[3]).all()
    with pytest.raises(ValueError,match='held-out'):
        gate.base.predict(ids[:1],rows[:1],[None])
    with pytest.raises(ValueError,match='Outer evaluation'):
        fit_nested_gate(bundle,ids[:18],rows[:18],[None]*18,labels[:18],labels[:18],{'0'},settings,[])
