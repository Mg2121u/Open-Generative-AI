"""Read-only proof for owner approval of a *completed* QA-rejected section.

The archived provider video/QA artifact remain immutable. Authorization to
continue remaining sections is an explicit /approve_qa command, never a
background retry or implied permission to resubmit an ambiguous request.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import db,full_video,provider,quality,storage


def reviewable(job):
    """Verify contiguous completed Wan steps and the saved negative face QA."""
    if (job.get('state')!='NEEDS_REVIEW' or job.get('stage')!='AUTO_GENERATE'
            or job.get('character')!='NICOLE' or int(job.get('segment_total') or 0)<2):
        raise ValueError('Only multi-section Nicole face-QA review jobs may continue')
    failure=str(job.get('last_error') or '')
    if not (failure.startswith('Nicole facial identity QA mismatch:')
            or failure.startswith('Nicole facial identity QA unverified:')):
        raise ValueError('Only a facial identity mismatch/unverified clip can be approved. Provider uncertainty must be reconciled separately.')
    cp=job.get('checkpoint') or {}
    preflight=cp.get('local_preflight') or {}
    identity=cp.get('identity') or {}
    if (not preflight.get('complete') or
            preflight.get('source_key')!=job.get('original_key') or
            identity.get('status')!='ready' or
            not identity.get('nicole_image_key')):
        raise ValueError('The archived original and Nicole identity must remain verified')
    duration=(preflight.get('source_probe') or {}).get('duration')
    plan=quality.quality_plan(duration)
    if len(plan)!=int(job['segment_total']):
        raise ValueError('Stored section plan changed; refusing to continue with new billing')
    steps=db.auto_provider_steps(job['job_id'])
    step_by_key={x['step_key']:x for x in steps}
    if len(step_by_key)!=len(steps) or not steps or len(steps)>len(plan):
        raise ValueError('Paid provider steps are ambiguous; manual reconciliation required')
    # Every paid step must be completed, contiguous, and have a provider ID.
    for i,step in enumerate(sorted(steps,key=lambda x:x['step_key'])):
        if (step['step_key']!=full_video.step_key(i)
                or step.get('state')!='COMPLETED'
                or not step.get('external_request_id')):
            raise ValueError('Provider request is pending/ambiguous; owner approval cannot retry it')
        if not full_video._stored(full_video.segment_key(str(job['job_id']),i)):
            raise ValueError('Paid segment archive missing; approval cannot proceed')
    idx=len(steps)-1
    key=full_video.segment_key(str(job['job_id']),idx)
    qa_key=key+'.face_qa_v1.json'
    with tempfile.TemporaryDirectory() as tmp:
        artifact=Path(tmp)/'original_face_review.json'
        try:
            storage.download_key(qa_key,artifact)
            qa=json.loads(artifact.read_text())
        except (OSError,ValueError,TypeError) as exc:
            raise ValueError('Original failed face-QA evidence is missing; cannot override') from exc
    expected_status='mismatch' if ' QA mismatch:' in failure else 'unverified'
    if not isinstance(qa,dict) or qa.get('status')!=expected_status:
        raise ValueError('Archived QA result does not match the pause reason')
    # Approvals allow use of the paid section AS-IS, not claiming it passed.
    return {'index':idx,'number':idx+1,'total':len(plan),
            'key':key,'source_step':full_video.step_key(idx),
            'status':expected_status,
            'matched':qa.get('above_threshold'),
            'detected':qa.get('detected_faces'),
            'remaining_count':len(plan)-idx-1,
            'remaining_estimated_usd':round(sum(
                part['estimated_usd'] for part in plan[idx+1:]),2)}


def owner_acceptance(job,video_key):
    """No fresh face-QA verdict: expose the owner's exceptional authorization."""
    cp=job.get('checkpoint') or {}
    approvals=cp.get('face_qa_approvals') or {}
    if not isinstance(approvals,dict):return None
    for idx,record in approvals.items():
        if not isinstance(record,dict):continue
        if (record.get('type')=='owner_accepted_facial_mismatch_for_continuation'
                and record.get('owner_id')==job.get('owner_id')
                and record.get('segment_key')==video_key
                and record.get('accepted_status') in ('mismatch','unverified')
                and str(idx).isdigit()
                and record.get('source_step')==full_video.step_key(int(idx))
                and video_key==full_video.segment_key(str(job['job_id']),int(idx))
                and str(record.get('original_failure') or '').startswith(
                    'Nicole facial identity QA '+record['accepted_status']+':')):
            return {'status':'owner_accepted', 'original_status':record['accepted_status'],
                    'approved_by_owner':True}
    return None
