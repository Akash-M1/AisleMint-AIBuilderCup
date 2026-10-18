"""Historical training survives the API moving onto an ephemeral Cloud Run instance."""
import pytest
from promo import pg,legacy_worker,legacy_data_api,demand_api,pos_learning


def dataset():
    pg.database().datasets.insert_one(dict(id='snapshot',name='Test',quality={},status='validated',error=None,created='2026-10-17'))


def test_external_dataset_training_is_durable_and_worker_claims_it(monkeypatch):
    dataset();monkeypatch.setenv('LEGACY_TRAINING_WORKER','external')
    monkeypatch.setattr(legacy_data_api.service(),'workspace_path',lambda id:None)
    assert legacy_data_api.train_dataset('snapshot')['status']=='queued'
    assert pg.database().datasets.find_one({'id':'snapshot'})['status']=='queued'
    called=[]
    def train(id,release):
        assert legacy_worker.training_lease.owner and release is False
        assert pg.database().datasets.find_one({'id':id})['status']=='training'
        called.append(id);pg.database().datasets.update_one({'id':id},{'$set':{'status':'ready'}})
    monkeypatch.setattr(legacy_data_api,'train_job',train)
    assert legacy_worker.process_one() and called==['snapshot']
    assert not legacy_worker.process_one() and legacy_worker.training_lease.owner is None


def test_external_demand_request_is_queued_once_and_drained(monkeypatch,tmp_path):
    monkeypatch.setenv('LEGACY_TRAINING_WORKER','external')
    monkeypatch.setattr(demand_api,'paths',lambda id:(tmp_path/'panel',tmp_path))
    assert demand_api.start_training(demand_api.SourceRequest(source_id='snapshot'))['status']=='queued'
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:demand_api.start_training(demand_api.SourceRequest(source_id='snapshot'))
    assert error.value.status_code==409
    calls=[]
    def train(id,release):
        assert legacy_worker.training_lease.owner and release is False
        calls.append(id);pg.database().demand_jobs.update_one({'source_id':id},{'$set':{'status':'ready'}})
    monkeypatch.setattr(demand_api,'training_job',train)
    assert legacy_worker.process_one() and calls==['snapshot']
    assert not legacy_worker.process_one()


def test_external_worker_recovers_only_after_global_lease_expires(monkeypatch):
    from promo.jobs import TrainingLease
    dataset();pg.database().datasets.update_one({'id':'snapshot'},{'$set':{'status':'training'}})
    other=TrainingLease();assert other.acquire(blocking=False)
    try:
        assert not legacy_worker.process_one()
        assert pg.database().datasets.find_one({'id':'snapshot'})['status']=='training'
    finally:other.release()
    assert not legacy_worker.process_one()
    assert pg.database().datasets.find_one({'id':'snapshot'})['status']=='failed'


def test_external_pos_launcher_leaves_job_for_worker(monkeypatch):
    monkeypatch.setenv('LEGACY_TRAINING_WORKER','external')
    monkeypatch.setattr(pos_learning.threading,'Thread',lambda *a,**k:pytest.fail('API spawned a fit'))
    pos_learning.launch('queued-job')
