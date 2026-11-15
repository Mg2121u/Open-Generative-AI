"""Direct port of the 67-node Recreation Agent (Wan 3.0 path).

Important: this is staged, not activated in production. Frozen n8n source scripts
execute in a standalone Node process; NO n8n Cloud, no modified source prompts.
State survives web/worker restarts through PostgreSQL and object storage.
"""
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from . import db, ffmpeg, parity, provider, storage, telegram, voiceover

ALLOWED_STAGES={
    'UPLOAD_REFERENCE','FORENSICS','SEGMENT_PLAN','SEGMENT_TRIM','WAN_SUBMIT','WAN_POLL',
    'SAVE_SEGMENT','MERGE','QA','RESTORE_AUDIO','VOICEOVER','DELIVER'
}

class ConfigurationError(RuntimeError):
    pass


def identity_context(job):
    """Build original identity profiles with secure runtime references."""
    aria=[v.strip() for v in os.getenv('ARIA_IDENTITY_URLS','').split(',') if v.strip()]
    nicole=[v.strip() for v in os.getenv('NICOLE_IDENTITY_URLS','').split(',') if v.strip()]
    identity=(job.get('checkpoint') or {}).get('identity') or {}
    if identity.get('status')=='ready' and identity.get('nicole_image_key'):
        nicole.insert(0,storage.presign(identity['nicole_image_key'],3600))
    for ref in aria+nicole:
        if urlsplit(ref).scheme!='https':
            raise ConfigurationError('Identity reference URLs must use HTTPS')
    scene=(job['scenery'] or '').strip()
    outfit=(job['outfit'] or '').strip()
    user=(job['instructions'] or '').strip()
    instructions='\n'.join([f'SCENERY REQUEST: {scene}',
                            f'WARDROBE REQUEST: {outfit}',user]).strip()
    context=parity.run('context_build',input={
        'character':job['character'], 'instructions':instructions,
        'aria_identity_urls':aria, 'nicole_identity_urls':nicole})
    context['job_id']=str(job['job_id'])
    return context


def progress(job, *, stage, state='QUEUED', payload=None, outputs=None, current_segment=None, delay=0, event=None):
    fields={'checkpoint': payload if payload is not None else (job['checkpoint'] or {}),
            'attempt':0,'last_error':None}
    if outputs is not None: fields['outputs']=outputs
    if current_segment is not None: fields['current_segment']=current_segment
    updated=db.checkpoint(job['job_id'],job['lease_owner'],stage=stage,state=state,
                          fields=fields,delay_seconds=delay,
                          event=(stage.lower(),event or f'Started {stage}'))
    if not updated: raise RuntimeError('Job cancelled or lease lost')
    return updated


def _llm_or_stage(job, step_key, body):
    if os.getenv('ENABLE_PAID_GENERATION','false').lower()!='true':
        raise ConfigurationError('Paid generation is disabled until parity and restart tests complete')
    return provider.chat_once(job['job_id'],step_key,body)


def process(job):
    stage=job['stage']; job_id=str(job['job_id'])
    cp=dict(job['checkpoint'] or {}); outputs=list(job['outputs'] or [])
    if stage not in ALLOWED_STAGES: raise ConfigurationError(f'Unrecognized stage: {stage}')
    context=cp.get('context') or identity_context(job)
    # Refresh private image URLs for each stage. Signed URLs can expire between
    # segments while the job is waiting on a provider prediction.
    identity=(job.get('checkpoint') or {}).get('identity') or {}
    if identity.get('status')=='ready' and identity.get('nicole_image_key'):
        fresh=storage.presign(identity['nicole_image_key'],3600)
        context={**context,'identity_images':[fresh]+[x for x in context.get('identity_images',[]) if 'X-Amz-Signature=' not in x]}
        groups=dict(context.get('identity_groups') or {})
        groups['nicole']=context['identity_images']
        context['identity_groups']=groups
    if stage=='UPLOAD_REFERENCE':
        with tempfile.TemporaryDirectory() as td:
            original=Path(td)/'original.mp4'
            storage.download_key(job['original_key'],original)
            url=provider.upload_media_once(job_id, original)
        cp['reference_video_url']=url
        cp['context']=context
        return progress(job,stage='FORENSICS',payload=cp,
                        event='Original source uploaded to WaveSpeed as in n8n')

    if stage=='FORENSICS':
        source_url=cp['reference_video_url']
        ref={'reference_video_url':source_url,'original_reference_video_url':source_url}
        req=parity.run('forensics_request',named={'Build Recreation Context':context,'Prepare Reference Media':ref})
        response=_llm_or_stage(job,'gemini_forensics',req)
        blueprint=parity.run('forensics_parse',input=response,
                             named={'Build Recreation Context':context,'Prepare Reference Media':ref})
        cp.update(context=context,blueprint=blueprint)
        return progress(job,stage='SEGMENT_PLAN',payload=cp,event='Gemini source forensics checkpointed')

    if stage=='SEGMENT_PLAN':
        blueprint=cp['blueprint']
        req=parity.run('grok_request',named={'Build Recreation Context':context,'Parse Gemini Forensics':blueprint})
        response=_llm_or_stage(job,'grok_segment_plan',req)
        plan=parity.run('grok_parse',input=response,
                        named={'Build Recreation Context':context,'Parse Gemini Forensics':blueprint})
        cp['plan']=plan
        db.set_segment_total(job_id,len(plan['segments']))
        row=progress(job,stage='SEGMENT_TRIM',payload=cp,current_segment=0,event='Exact Grok segment plan checkpointed')
        telegram.say(job['chat_id'],f'Video analysis ready: {len(plan["segments"])} source segments. Processing at 480p.')
        return row

    if stage=='SEGMENT_TRIM':
        i=int(job['current_segment']);segment=cp['plan']['segments'][i]
        keys=cp.get('segment_inputs',{})
        if str(i) not in keys:
            with tempfile.TemporaryDirectory() as td:
                td=Path(td); original=td/'source.mp4'; clipped=td/'clip.mp4'
                storage.download_key(job['original_key'],original)
                ffmpeg.trim(original,clipped,segment['source_start'],segment['source_duration'])
                clip_key=f'recreation/{job_id}/segments/{i:03}_source.mp4'
                storage.upload_key(clip_key,clipped)
                info={'source_key':clip_key}
                if i>0:
                    last_key=outputs[i-1]['video_key']
                    prior=td/'previous.mp4';tail=td/'tail.mp4'
                    storage.download_key(last_key,prior)
                    previous_segment=cp['plan']['segments'][i-1]
                    tail_dur=min(2,float(previous_segment['output_duration']))
                    ffmpeg.trim(prior,tail,max(0,float(previous_segment['output_duration'])-tail_dur),tail_dur)
                    tail_key=f'recreation/{job_id}/segments/{i:03}_tail.mp4'
                    storage.upload_key(tail_key,tail)
                    info['tail_key']=tail_key
                keys[str(i)]=info;cp['segment_inputs']=keys
        return progress(job,stage='WAN_SUBMIT',payload=cp,event=f'Source segment {i+1} trimmed and saved')

    if stage=='WAN_SUBMIT':
        if os.getenv('ENABLE_PAID_GENERATION','false').lower()!='true':
            raise ConfigurationError('Paid generation remains disabled')
        if job['state']=='CANCELLED': return
        i=int(job['current_segment']); plan=cp['plan'];inputs=cp['segment_inputs'][str(i)]
        source_segment_url=storage.presign(inputs['source_key'],7200)
        if i==0:
            prepared=parity.run('wan_initial_prepare',input={'video':{'url':source_segment_url}},
                                named={'Parse Grok Segment Plan':plan,'Build Recreation Context':context})
        else:
            state={**plan,
                   'character_mode':context['character_mode'], 'character_name':context['character_name'],
                   'next_segment_index':i,'current_segment_index':i-1,'generated_segments':outputs,
                   'source_segment_video_url':source_segment_url}
            tail_url=storage.presign(inputs['tail_key'],7200)
            prepared=parity.run('wan_next_prepare',input={'video':{'url':tail_url}},
                    named={'Build Recreation Context':context,'Prepare Previous Tail Trim':[state]})
        payload=parity.wan_payload(first=i==0,prepared=prepared,resolution='480p')
        provider.submit_once(job_id,f'wan_segment_{i:03}',
            'alibaba/wan-3.0/reference-to-video',payload)
        # Request ID saved durably before stage transition; restarting won't resubmit.
        return progress(job,stage='WAN_POLL',state='WAITING_PROVIDER',payload=cp,delay=20,
                        event=f'Wan 3.0 segment {i+1} request ID saved')

    if stage=='WAN_POLL':
        i=int(job['current_segment']); step=db.get_step(job_id,f'wan_segment_{i:03}')
        if not step: raise ConfigurationError('Missing saved provider request')
        if step['state']=='COMPLETED': result=step['result']
        else: result=provider.poll(step)
        if str(result.get('status','')).lower() != 'completed':
            return progress(job,stage='WAN_POLL',state='WAITING_PROVIDER',payload=cp,delay=30,
                            event=f'Waiting for existing segment {i+1} request')
        cp['last_provider_result']=result
        return progress(job,stage='SAVE_SEGMENT',payload=cp)

    if stage=='SAVE_SEGMENT':
        i=int(job['current_segment']);data=cp['last_provider_result']
        result_url=(data.get('outputs') or [None])[0]
        if not result_url: raise ValueError('Wan returned no video URL')
        key=f'recreation/{job_id}/generated/{i:03}.mp4'
        storage.store_provider_url(result_url,key)
        segment=cp['plan']['segments'][i]
        saved={'segment_index':i,'mode':'REFERENCE','video_key':key,
               'source_start':segment['source_start'],'source_end':segment['source_end']}
        outputs=[o for o in outputs if o['segment_index'] != i]+[saved]
        outputs.sort(key=lambda o:o['segment_index'])
        cp.pop('last_provider_result',None)
        next_i=i+1
        if next_i<len(cp['plan']['segments']):
            return progress(job,stage='SEGMENT_TRIM',payload=cp,outputs=outputs,
                            current_segment=next_i,event=f'Generated segment {i+1} saved; resuming from {next_i+1}')
        return progress(job,stage='MERGE',payload=cp,outputs=outputs,
                        event='All segments generated and preserved')

    if stage=='MERGE':
        if len(outputs)!=len(cp['plan']['segments']):
            raise ConfigurationError('Cannot merge: missing completed segments')
        merged_key=cp.get('merged_key') or f'recreation/{job_id}/merged.mp4'
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); paths=[]
            for i,s in enumerate(outputs):
                path=td/f'{i:03}.mp4'
                storage.download_key(s['video_key'],path); paths.append(path)
            merged=td/'merged.mp4'
            if len(paths)==1: merged.write_bytes(paths[0].read_bytes())
            else: ffmpeg.concatenate(paths,merged)
            storage.upload_key(merged_key,merged)
        cp['merged_key']=merged_key
        return progress(job,stage='QA',payload=cp,event='Merged video saved; original QA next')

    if stage=='QA':
        blueprint=cp['blueprint'];plan=cp['plan']
        video_url=storage.presign(cp['merged_key'],7200)
        source_url=cp['reference_video_url']
        qa_source={
          'job_id':job_id,'character':context['character'],
          'character_name':context['character_name'],'character_mode':context['character_mode'],
          'platform':context['platform'],'generated_video_url':video_url,
          'original_reference_video_url':source_url,
          'creative_duration_seconds':blueprint['creative_duration_seconds'],
          'segment_count':len(plan['segments']),
          'generation_type':'WAN3_REFERENCE_SEGMENTS_IDENTITY_LOCKED' if len(outputs)>1 else 'WAN3_REFERENCE_GUIDED_IDENTITY_LOCKED'
        }
        prepared=parity.run('qa_prepare',input=qa_source)
        response=_llm_or_stage(job,'gemini_final_qa',prepared['qa_request_body'])
        qa=parity.run('qa_parse',input=response,
                      named={'Prepare Generated Video1':qa_source,'Build Recreation Context':context})
        cp['qa_result']=qa
        if not qa['approved']:
            # Preserve all generated clips and QA feedback; no automatic paid retry.
            db.checkpoint(job_id,job['lease_owner'],state='NEEDS_REVIEW',
                fields={'checkpoint':cp,'last_error':'Final source-aware QA rejected: '+str(qa.get('major_issues',[]))[:500]},
                event=('qa_rejected','Review the completed video without rerunning paid clips'))
            telegram.say(job['chat_id'],'Video rendered, but source-aware QA requires review. No paid segments will be regenerated automatically.')
            return
        return progress(job,stage='RESTORE_AUDIO',payload=cp,event='Exact Gemini QA passed')

    if stage=='RESTORE_AUDIO':
        key=f'recreation/{job_id}/final_with_audio.mp4'
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);original=td/'original.mp4';merged=td/'generated.mp4';final=td/'final.mp4'
            storage.download_key(job['original_key'],original)
            storage.download_key(cp['merged_key'],merged)
            ffmpeg.restore_source_audio(merged,original,final)
            storage.upload_key(key,final)
        cp['final_key']=key
        return progress(job,stage='VOICEOVER' if (job.get('voiceover_text') or '').strip() else 'DELIVER',
                        payload=cp,event='Original soundtrack restored; optional voiceover next' if job.get('voiceover_text') else 'Original soundtrack restored')

    if stage=='VOICEOVER':
        # This optional stage never alters the original n8n recreation stages.
        # Persistent TTS step prevents charging again after a restart.
        script=(job.get('voiceover_text') or '').strip()
        if not script:
            return progress(job,stage='DELIVER',payload=cp,event='Voiceover disabled')
        audio_key=voiceover.synthesize_once(job_id,job['character'],
                  job.get('voiceover_voice','AUTO'),script)
        final_key=f'recreation/{job_id}/final_with_voiceover.mp4'
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            final=td/'final.mp4'; voice=td/'speech.mp3'; mixed=td/'mixed.mp4'
            storage.download_key(cp['final_key'],final)
            storage.download_key(audio_key,voice)
            ffmpeg.overlay_voiceover(final,voice,mixed)
            storage.upload_key(final_key,mixed)
        cp['final_key']=final_key
        cp['voiceover_used']=True
        return progress(job,stage='DELIVER',payload=cp,event='ElevenLabs v4 optional voiceover mixed with original audio')

    if stage=='DELIVER':
        key=cp['final_key']; step=db.get_step(job_id,'telegram_delivery')
        if step and step['state']=='COMPLETED':
            message=step['result']
        elif step:
            raise provider.NeedsReview('Telegram delivery uncertain; avoid sending duplicate final video')
        else:
            if not db.prepare_external(job_id,'telegram_delivery','telegram',{'key':key}):
                raise provider.NeedsReview('Final delivery already reserved by another worker')
            try:
                message=telegram.telegram('sendVideo',{
                    'chat_id':job['chat_id'],'video':storage.presign(key,3600),
                    'caption':f'Video recreation ready · 480p · {len(outputs)} segments · Original audio' + (' + ElevenLabs v4 voiceover' if cp.get('voiceover_used') else '')
                })
            except Exception as exc:
                raise provider.NeedsReview('Telegram send outcome uncertain; manual check required') from exc
            db.external_finished(job_id,'telegram_delivery',{'message_id':message.get('message_id')})
        db.checkpoint(job_id,job['lease_owner'],state='COMPLETED',
                      fields={'checkpoint':cp,'last_error':None},
                      event=('delivered','Final video delivered on Telegram'))
        return
