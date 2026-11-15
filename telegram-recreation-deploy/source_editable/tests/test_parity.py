"""Test the exact original workflow transformations, without paid API calls."""
import json
import hashlib
from pathlib import Path
import pytest
from recreation import parity

ROOT=Path(__file__).resolve().parents[1]
ASSETS=json.loads((ROOT/'recreation/parity_assets.json').read_text())
MANIFEST=json.loads((ROOT/'workflow_manifest.json').read_text())


def fixtures():
    context={'job_id':'test','character':'NICOLE','character_name':'Nicole',
             'character_mode':'SINGLE','platform':'INSTAGRAM',
             'user_instructions':'Change clothes to a black dress; scenery to Beirut rooftop',
             'identity_images':['https://example.org/nicole.jpg'],
             'identity_groups':{'aria':[],'nicole':['https://example.org/nicole.jpg']},
             'identity_lock':{'permanent_identity_locked':True}}
    blueprint={'creative_duration_seconds':9,'reference_video_url':'https://example.org/original.mp4',
               'original_reference_video_url':'https://example.org/original.mp4',
               'identity_images':context['identity_images'],'identity_groups':context['identity_groups'],
               'identity_lock':context['identity_lock']}
    return context,blueprint


def test_original_script_hashes_equal_saved_manifest():
    by_name={n['name']:n for n in MANIFEST['nodes']}
    for asset in ASSETS.values():
        original=by_name[asset['source_node']]
        digest=hashlib.sha256(asset['source'].encode()).hexdigest()
        if asset.get('sanitization'):
            assert asset['original_sha256']==original['script_sha256']
            assert 'token=' not in asset['source']
        else:
            assert digest in (original.get('script_sha256'),original.get('request_sha256'))


def test_forensics_prompt_is_source_exact():
    context,blueprint=fixtures()
    request=parity.run('forensics_request',named={
        'Build Recreation Context':context,
        'Prepare Reference Media':{'reference_video_url':'https://example.org/original.mp4'}})
    assert request['model']=='google/gemini-2.5-pro'
    assert 'Change clothes to a black dress' in str(request)
    assert 'https://example.org/original.mp4' in str(request)


def test_grok_plan_parsing_and_wan_payload_reference_roles():
    context,blueprint=fixtures()
    raw=json.dumps({'total_duration':9,'segments':[{
        'source_start':0,'source_end':9,'output_duration':9,
        'wan_prompt':'Reproduce the exact source choreography and camera framing',
        'wardrobe_state':{'nicole':'black dress'},'camera':'Fixed smartphone'}]})
    plan=parity.run('grok_parse',input={'choices':[{'message':{'content':raw}}]},
        named={'Parse Gemini Forensics':blueprint,'Build Recreation Context':context})
    assert plan['segment_count']==1
    prepared=parity.run('wan_initial_prepare',input={'video':{'url':'https://example.org/trim.mp4'}},
        named={'Parse Grok Segment Plan':plan,'Build Recreation Context':context})
    payload=parity.wan_payload(first=True,prepared=prepared)
    assert payload['prompt']==prepared['wan_recreation_prompt']
    assert payload['resolution']=='480p'
    assert payload['duration']==9
    assert payload['enable_audio'] is False
    assert payload['seed']==223456789
    assert payload['reference_videos']==['https://example.org/trim.mp4']
    assert 'Video1=ORIGINAL SOURCE SEGMENT' in payload['prompt']


def test_next_wan_uses_original_previous_tail_continuity_and_seed():
    context,blueprint=fixtures()
    segment={'output_duration':7,'wan_prompt':'Source performer raises one hand then turns left',
             'continuity_from_previous':'Continue right hand at shoulder level'}
    state={'segments':[{'output_duration':8},segment],'next_segment_index':1,
           'character_name':'Nicole','character_mode':'SINGLE',
           'identity_images':context['identity_images'],'identity_groups':context['identity_groups'],
           'source_segment_video_url':'https://example.org/source-2.mp4'}
    prepared=parity.run('wan_next_prepare',input={'video':{'url':'https://example.org/tail-1.mp4'}},
        named={'Prepare Previous Tail Trim':[state],'Build Recreation Context':context})
    payload=parity.wan_payload(first=False,prepared=prepared)
    assert payload['reference_videos']==['https://example.org/source-2.mp4','https://example.org/tail-1.mp4']
    assert payload['resolution']=='480p'
    assert payload['seed']==223456790
    assert 'Continuity:' in payload['prompt']
    assert 'Video2=PREVIOUS GENERATED TAIL' in payload['prompt']


def test_invalid_plan_fails_in_same_place_as_n8n():
    context,blueprint=fixtures()
    raw=json.dumps({'segments':[{'source_start':0,'source_end':11,
                                 'wan_prompt':'Bad overlong segment'}]})
    with pytest.raises(parity.ParityError,match='too long'):
        parity.run('grok_parse',input={'choices':[{'message':{'content':raw}}]},
                   named={'Parse Gemini Forensics':blueprint,'Build Recreation Context':context})


def test_arbitrary_script_rejected():
    with pytest.raises(parity.ParityError,match='Unknown frozen workflow script'):
        parity.run('user_supplied_code',input={'code':'console.log(123)'})


def test_original_identity_profile_preserved_with_runtime_refs():
    ctx=parity.run('context_build',input={
        'character':'NICOLE','instructions':'Beirut rooftop black dress',
        'aria_identity_urls':[], 'nicole_identity_urls':['https://example.org/nicole.jpg']})
    assert ctx['character_mode']=='SINGLE'
    assert ctx['identity_images']==['https://example.org/nicole.jpg']
    assert ctx['identity_lock']['permanent_identity_locked'] is True
    assert ctx['user_instructions']=='Beirut rooftop black dress'
