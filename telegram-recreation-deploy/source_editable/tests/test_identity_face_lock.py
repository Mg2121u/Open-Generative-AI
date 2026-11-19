"""Pure offline face-anchor and identity-match guard regression tests."""
import json
from pathlib import Path
import pytest
import numpy as np
from recreation import identity_qa,full_video,quality


def job():
    return {'job_id':'faux-uuid','stage':'AUTO_GENERATE','state':'RUNNING',
            'character':'NICOLE','scenery':'keep source scenery',
            'outfit':'same as the identity image','instructions':'',
            'checkpoint':{'identity':{'status':'ready','nicole_image_key':'private/nicole.jpg'}}}


def test_closeup_is_cropped_from_exact_canonical_image_not_wan_output():
    original=np.zeros((850,480,3),np.uint8)
    original[140:320,170:330]=(120,90,175)
    face=np.array([185.,155.,130.,150.,0.,0.,0.,0.,0.,0.,0.,0.,0.,0.,.98])
    result=identity_qa.crop_face(original,face)
    assert result.shape==(512,512,3)
    assert np.mean(result[:,:,2])>40


def test_local_models_missing_block_before_any_paid_submission(monkeypatch,tmp_path):
    monkeypatch.setattr(identity_qa,'DETECTOR',tmp_path/'missing_yunet.onnx')
    monkeypatch.setattr(identity_qa,'RECOGNIZER',tmp_path/'missing_sface.onnx')
    with pytest.raises(identity_qa.IdentityQualityReview,match='not installed'):
        identity_qa.load_models()


def test_face_matched_output_saved_and_cached_without_resampling(monkeypatch,tmp_path):
    monkeypatch.setenv('RECREATION_FACE_QA_MODE','on')
    saved={}
    def download(key,dest):
        if key.endswith('.face_qa_v1.json'):
            if key not in saved:raise FileNotFoundError(key)
            Path(dest).write_bytes(saved[key])
        else:
            Path(dest).write_bytes(b'fake private image or video')
    def upload(key,path,**kwargs):
        saved[key]=Path(path).read_bytes()
    monkeypatch.setattr(identity_qa.storage,'download_key',download)
    monkeypatch.setattr(identity_qa.storage,'upload_key',upload)
    calls=[]
    def samples(*a,**kw):
        calls.append('compare')
        return {'detected_faces':5,'samples':10,
                'median_similarity':0.58,'min_similarity':0.41,
                'above_threshold':5,'threshold':0.363}
    monkeypatch.setattr(identity_qa,'sample_video_matches',samples)
    j=job()
    assert identity_qa.check_archived(j,'output/clip.mp4')['status']=='passed'
    assert identity_qa.check_archived(j,'output/clip.mp4')['status']=='passed'
    assert calls==['compare']
    assert 'reference_images' not in json.dumps(saved)


def test_face_mismatch_preserves_archive_and_blocks_next_paid_segment(monkeypatch,tmp_path):
    monkeypatch.setenv('RECREATION_FACE_QA_MODE','on')
    stored={}
    def download(key,dest):
        if key.endswith('.face_qa_v1.json') and key not in stored:
            raise FileNotFoundError(key)
        Path(dest).write_bytes(stored.get(key,b'original bytes'))
    def upload(key,path,**kwargs):
        stored[key]=Path(path).read_bytes()
    monkeypatch.setattr(identity_qa.storage,'download_key',download)
    monkeypatch.setattr(identity_qa.storage,'upload_key',upload)
    monkeypatch.setattr(identity_qa,'sample_video_matches',
        lambda *a:{'detected_faces':7,'samples':10,
                   'median_similarity':0.19,'min_similarity':0.11,
                   'above_threshold':0,'threshold':0.363})
    with pytest.raises(identity_qa.IdentityQualityReview,match='mismatch'):
        identity_qa.check_archived(job(),'output/paid-clip.mp4')
    result=json.loads(stored['output/paid-clip.mp4.face_qa_v1.json'])
    assert result['status']=='mismatch'
    with pytest.raises(identity_qa.IdentityQualityReview,match='Saved'):
        identity_qa.check_archived(job(),'output/paid-clip.mp4')


def test_face_unobservable_fails_closed_and_does_not_claim_match(monkeypatch):
    monkeypatch.setenv('RECREATION_FACE_QA_MODE','on')
    cache={}
    def download(key,dest):
        if key.endswith('.json') and key not in cache:raise FileNotFoundError()
        Path(dest).write_bytes(cache.get(key,b'x'))
    monkeypatch.setattr(identity_qa.storage,'download_key',download)
    monkeypatch.setattr(identity_qa.storage,'upload_key',
                        lambda key,path,**kw:cache.update({key:Path(path).read_bytes()}))
    monkeypatch.setattr(identity_qa,'sample_video_matches',lambda *a:{
        'detected_faces':0,'samples':12,'median_similarity':None,
        'min_similarity':None,'above_threshold':0,'threshold':0.363})
    with pytest.raises(identity_qa.IdentityQualityReview,match='unverified'):
        identity_qa.check_archived(job(),'private/new.mp4')


def test_paid_payload_contains_both_original_nicole_and_her_face_closeup(monkeypatch):
    monkeypatch.setenv('RECREATION_FACE_QA_MODE','on')
    monkeypatch.setenv('RECREATION_QUALITY_MODE','identity_lock')
    monkeypatch.setenv('RECREATION_IDENTITY_ANCHOR','canonical')
    j=job()
    segments=[{'index':0,'start':0,'duration':8,'output_seconds':8,
               'reused':False,'estimated_usd':.8}]
    monkeypatch.setattr(full_video,'_guard_auto',lambda _: {'segments':segments})
    monkeypatch.setattr(full_video.db,'get_job',lambda *_:j)
    monkeypatch.setattr(full_video.db,'get_step',lambda *_:None)
    monkeypatch.setattr(full_video,'_stored',lambda *_:False)
    monkeypatch.setattr(full_video,'_prepare_reference',lambda *a:'motion/000.mp4')
    monkeypatch.setattr(full_video.identity_qa,'prepare_anchor',
                        lambda *a:'private/nicole_face_closeup_v1.jpg')
    monkeypatch.setattr(full_video.storage,'presign',
                        lambda key,ttl:'https://example.invalid/'+key)
    submitted=[]
    def submit(jid,key,endpoint,payload):
        submitted.append(payload)
        return {'state':'SUBMITTED','input':{'payload':payload},
                'external_request_id':'fake-provider-id'}
    monkeypatch.setattr(full_video.provider,'submit_once',submit)
    monkeypatch.setattr(full_video.provider,'poll',lambda *_:{'status':'processing'})
    assert full_video.run_auto(j)=='processing'
    assert len(submitted)==1
    pics=submitted[0]['reference_images']
    assert pics[0].endswith('private/nicole.jpg')
    assert pics[1].endswith('private/nicole_face_closeup_v1.jpg')
    assert 'close-up crop' in submitted[0]['prompt']
    assert len(submitted[0]['reference_videos'])==1
