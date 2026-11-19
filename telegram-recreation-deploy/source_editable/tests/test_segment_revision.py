"""One-section remake: protect old assets, require confirmation, one provider request.

All provider calls are mocked; test suite must never spend credits.
"""
from types import SimpleNamespace
from pathlib import Path
import pytest

from recreation import db, full_video, quality, segment_revision, app_queue, telegram


def parent():
    return {
        'job_id':'f6634b83-5f21-4144-9561-a5665a79a129',
        'owner_id':123,'chat_id':123,'state':'COMPLETED','stage':'DONE',
        'character':'NICOLE','segment_total':7,'model':'wan-3.0',
        'source_file_id':'file-id',
        'original_key':'archive/source-original.mp4',
        'scenery':'keep source scenery','outfit':'same as the identity image',
        'instructions':'follow original dance','voiceover_voice':'AUTO',
        'checkpoint':{
            'identity':{'status':'ready','nicole_image_key':'photos/nicole.jpg'},
            'local_preflight':{'source_key':'archive/source-original.mp4',
                               'complete':True,'source_probe':{
                                   'duration':57.533,'width':480,'height':854,'has_audio':True}}
        }
    }


def revision():
    p=parent()
    cp={**p['checkpoint'],'segment_revision':{
        'parent_job_id':p['job_id'],'parent_run_key':full_video.RUN_KEY,
        'parent_sections':7,'section_index':2,'revision_version':1}}
    return {
        'job_id':'revision-uuid','owner_id':123,'chat_id':123,
        'character':'NICOLE','model':'wan-3.0','state':'RUNNING',
        'stage':'SEGMENT_REDO','segment_total':1,'original_key':p['original_key'],
        'checkpoint':cp,
        'scenery':p['scenery'],'outfit':p['outfit'],
        'instructions':p['instructions'],'lease_owner':'worker-1'
    }


def test_only_one_segment_eligible_for_revision():
    plan=quality.quality_plan(57.533)
    assert len(plan)==7
    assert all(8<seg['duration']<8.5 for seg in plan)
    assert plan[2]['start']>0
    assert plan[2]['estimated_usd']>0


def test_original_job_lookup_never_uses_other_owner(monkeypatch):
    statements=[]
    class C:
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def execute(self,sql,params):
            statements.append((sql,params))
            return SimpleNamespace(fetchall=lambda:[parent()])
    monkeypatch.setattr(db,'connect',lambda:C())
    p=db.completed_segment_job(123,'f6634b83')
    assert p['state']=='COMPLETED'
    assert statements[0][1][0]==123
    assert statements[0][1][1]=='f6634b83'
    with pytest.raises(ValueError):
        db.completed_segment_job(123,'bad;DROP TABLE')


def test_revision_draft_reuses_parent_and_no_paid_requests(monkeypatch):
    monkeypatch.setattr(db,'completed_segment_job',lambda owner,prefix:parent())
    monkeypatch.setattr(full_video,'RUN_KEY','one_section_test_run')
    statements=[]
    class C:
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def execute(self,sql,params):
            statements.append((sql,params))
            if sql.lstrip().startswith('SELECT'):return SimpleNamespace(fetchone=lambda:{'n':7})
            return SimpleNamespace()
    monkeypatch.setattr(db,'connect',lambda:C())
    monkeypatch.setattr(segment_revision.provider,'submit_once',
                        lambda *a:pytest.fail('Draft must never spend'))
    item=db.create_segment_revision(123,123,'f6634b83',3)
    assert item['number']==3
    assert item['total']==7
    assert item['estimate']>0
    assert item['parent_id']==parent()['job_id']
    assert len(statements)==2
    assert "'SEGMENT_REDO'" in statements[-1][0]
    assert statements[-1][1][0]!=parent()['job_id']
    assert statements[-1][1][6]==parent()['outfit']
    assert 'original_key' in statements[-1][0]


def test_running_revision_reuses_parent_six_clips_and_one_new_clip(monkeypatch):
    p=parent()
    j=revision()
    monkeypatch.setattr(db,'get_job',lambda job_id,owner_id:p)
    monkeypatch.setattr(full_video,'_stored',
                        lambda key: not key.endswith('section_revision/v1/segment_002.mp4'))
    monkeypatch.setattr(db,'get_step',lambda *a:None)
    monkeypatch.setenv('WORKER_MODE','app_always_on')
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setenv('WAVESPEED_API_KEY','fake')
    monkeypatch.setenv('ALLOWED_TELEGRAM_USER_IDS','123')
    monkeypatch.setenv('APP_MAX_ESTIMATED_USD','10')
    monkeypatch.setattr(segment_revision.storage,'presign',
                        lambda key,ttl:'https://store.example/'+key)
    events=[]
    def fake_submit(jid,key,model,payload):
        events.append(('paid_prediction',payload))
        return {'state':'SUBMITTED','input':{'payload':payload},
                'external_request_id':'provider-1'}
    monkeypatch.setattr(segment_revision.provider,'submit_once',fake_submit)
    monkeypatch.setattr(segment_revision.provider,'poll',
                        lambda *_:{'status':'processing'})
    assert segment_revision.run(j)=='processing'
    assert len(events)==1
    payload=events[0][1]
    assert len(payload['reference_images'])==1
    assert payload['reference_images'][0].endswith('/photos/nicole.jpg')
    assert payload['reference_videos'][0].endswith('/inputs/002.mp4')
    assert payload['generate_audio'] is False
    assert payload['duration']==9
    assert 'Image 1' in payload['prompt']
    assert 'NO previous generated frame' in payload['prompt']

    # On recovery the new section was archived: the other six are unchanged.
    rev_key=segment_revision.revision_key(j,2)
    orig=[full_video.segment_key(p['job_id'],i) for i in range(7)]
    monkeypatch.setattr(full_video,'_stored',lambda key:True)
    monkeypatch.setattr(segment_revision.provider,'submit_once',
                        lambda *a:pytest.fail('NEVER charge twice'))
    monkeypatch.setattr(segment_revision.provider,'poll',
                        lambda *a:pytest.fail('do not poll after archive'))
    merge_calls=[]
    def merge(job,segments,keys,**kwargs):
        merge_calls.append((keys,kwargs))
        return kwargs['destination_override']
    monkeypatch.setattr(full_video,'_merge_original_sound',merge)
    delivery=[]
    monkeypatch.setattr(full_video,'_deliver_once',
                        lambda *a,**kw:delivery.append((a,kw)))
    assert segment_revision.run(j)=='completed'
    assert len(merge_calls)==1
    saved,opts=merge_calls[0]
    assert opts['force_no_warp'] is True
    assert saved[2]==rev_key
    assert all(saved[k]==orig[k] for k in range(7) if k!=2)
    assert len(delivery)==1
    assert 'section 3/7 replaced' in delivery[0][1]['caption']


def test_worker_routes_revision_without_full_paid_video(monkeypatch):
    j=revision()
    changes=[]
    monkeypatch.setattr(segment_revision,'run',lambda _: 'completed')
    monkeypatch.setattr(full_video,'run_auto',
                        lambda *_:pytest.fail('Wrong paid full-video path'))
    monkeypatch.setattr(db,'checkpoint',lambda *a,**kw:changes.append(kw) or True)
    app_queue.process(j)
    assert changes[-1]['state']=='COMPLETED'
    assert changes[-1]['fields']['current_segment']==1


def test_web_command_creates_free_draft_and_requires_explicit_confirm(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    messages=[]
    monkeypatch.setattr(telegram,'say',lambda chat,msg:messages.append(msg))
    monkeypatch.setattr(telegram.db,'create_segment_revision',
                        lambda chat,owner,prefix,num:{
                            'job_id':'revision-uuid','parent_id':parent()['job_id'],
                            'number':3,'total':7,'start':16.4,'end':24.7,'estimate':.90})
    monkeypatch.setattr(telegram.db,'latest_job',lambda owner:revision())
    monkeypatch.setattr(telegram.db,'queue_segment_revision',
                        lambda *a:True)
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    telegram.process_message({'chat':{'id':123},'from':{'id':123},
                              'text':'/redo_segment f6634b83 3'})
    assert 'No' in messages[-1] or 'Nothing has been charged' in messages[-1]
    assert '/confirm_segment' in messages[-1]
    telegram.process_message({'chat':{'id':123},'from':{'id':123},
                              'text':'/confirm_segment'})
    assert 'ONE new Wan section' in messages[-1]
