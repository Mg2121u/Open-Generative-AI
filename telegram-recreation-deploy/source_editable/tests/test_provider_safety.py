"""No paid provider resubmission after ambiguous or recorded requests."""
import importlib
import sys
import types
import pytest

@pytest.fixture
def provider(monkeypatch):
    fake=types.ModuleType('recreation.db')
    fake.steps={}
    fake.get_step=lambda job,key:fake.steps.get((job,key))
    def prepare(job,key,provider,payload):
        row={'state':'SUBMITTING','external_request_id':None,'job_id':job,'step_key':key}
        fake.steps[(job,key)]=row
        return row
    fake.prepare_external=prepare
    def submitted(job,key,id):
        row=fake.steps[(job,key)]
        row.update(state='SUBMITTED',external_request_id=id)
        return row
    fake.external_submitted=submitted
    monkeypatch.setitem(sys.modules,'recreation.db',fake)
    sys.modules.pop('recreation.provider',None)
    module=importlib.import_module('recreation.provider')
    module.db=fake
    return module


def test_submitting_is_never_retried(provider,monkeypatch):
    class Requests:
        def post(self,*a,**k):
            pytest.fail('A second paid request was attempted')
    monkeypatch.setattr(provider,'requests',Requests())
    provider.db.steps[('abc','wan-07')]={'state':'SUBMITTING'}
    with pytest.raises(provider.NeedsReview):
        provider.submit_once('abc','wan-07','alibaba/wan-3.0/reference-to-video',{'resolution':'480p'})


def test_saved_prediction_skips_new_post(provider,monkeypatch):
    class Requests:
        def post(self,*a,**k):
            pytest.fail('Paid request repeated despite saved provider ID')
    monkeypatch.setattr(provider,'requests',Requests())
    original={'state':'SUBMITTED','external_request_id':'provider-123'}
    provider.db.steps[('abc','wan-07')]=original
    output=provider.submit_once('abc','wan-07','model',{'resolution':'480p'})
    assert output is original
