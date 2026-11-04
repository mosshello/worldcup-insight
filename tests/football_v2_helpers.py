from contextlib import ExitStack, nullcontext
from unittest.mock import patch
from worldcup_mvp.console.catalog import classify
from worldcup_mvp.console.model_registry import competition_spec
from worldcup_mvp.console.store import fingerprint
from worldcup_mvp.console.football_markets import assessment


def football(match):
    cid = classify(match)['id']
    return {'status':'shadow','model_id':'test-model','competition_id':cid,'run_id':'test-run',
        'dataset_digest':'test-data','trained_before':match['captured_at'],'training_scope':'competition_only',
        'selection_revision':0,'rule_revision':fingerprint(competition_spec(cid)),
        'team_samples':[10,10],'sample_count':400,'team_identity':{'home_id':'h','away_id':'a'},
        'markets':[assessment('had',{'h':0.5,'d':0.3,'a':0.2},origin='model')],
        'expected_goals':None,'reference_check':{},'kind':'test'}


def selected_model(match):
    stack = ExitStack()
    value = football(match)
    stack.enter_context(patch('worldcup_mvp.console.model_registry.forecast',return_value=value))
    stack.enter_context(patch('worldcup_mvp.console.model_registry.selection',return_value={'run_id':'test-run','revision':0}))
    stack.enter_context(patch('worldcup_mvp.console.model_registry.commit_lock',return_value=nullcontext()))
    return stack
