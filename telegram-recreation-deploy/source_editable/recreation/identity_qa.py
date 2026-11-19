"""Local, private, CPU-only Nicole facial reference strengthening and QA.

OpenCV YuNet/SFace use pinned build-time models; no provider/model endpoint or
user media is sent to any extra external service. Every comparison is between
the owner's canonical image and sampled generated frames, not identification
of a stranger. Findings are supporting evidence, not a biometric guarantee.
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import storage

LOG=logging.getLogger('identity-qa')
MODEL_ROOT=Path(os.environ.get('RECREATION_FACE_MODEL_DIR','/app/face_models'))
DETECTOR=MODEL_ROOT/'face_detection_yunet_2023mar.onnx'
RECOGNIZER=MODEL_ROOT/'face_recognition_sface_2021dec.onnx'
COSINE_THRESHOLD=0.363  # OpenCV Zoo's published default for SFace


class IdentityQualityReview(RuntimeError):
    """Preserve completed provider output, require human review before next bill."""


def load_models():
    if not DETECTOR.is_file() or DETECTOR.stat().st_size<100_000:
        raise IdentityQualityReview('Nicole face detector is not installed; paid generation blocked')
    if not RECOGNIZER.is_file() or RECOGNIZER.stat().st_size<5_000_000:
        raise IdentityQualityReview('Nicole face recognition model is not installed; paid generation blocked')
    try:
        detector=cv2.FaceDetectorYN.create(str(DETECTOR),'',(320,320),0.74,0.3,150)
        recognizer=cv2.FaceRecognizerSF.create(str(RECOGNIZER),'')
        return detector,recognizer
    except (cv2.error,ValueError) as exc:
        raise IdentityQualityReview('Nicole face verification model could not load') from exc


def detect_one(frame,detector,*,min_face_width=26):
    h,w=frame.shape[:2]
    detector.setInputSize((w,h))
    _,faces=detector.detect(frame)
    if faces is None or len(faces)==0:
        return None
    eligible=[face for face in faces if face[2]>=min_face_width and face[3]>=min_face_width]
    if not eligible:
        return None
    eligible.sort(key=lambda f:float(f[2]*f[3]*f[14]),reverse=True)
    # Do not anchor an identity to an ambiguous multi-face collage.
    if len(eligible)>1:
        a,b=eligible[:2]
        if b[2]*b[3]>a[2]*a[3]*0.55 and b[14]>.8:
            raise IdentityQualityReview('Nicole reference contains multiple prominent faces; use a single clear photo')
    return eligible[0]


def image_feature(frame,detector,recognizer,*,min_face_width=26):
    face=detect_one(frame,detector,min_face_width=min_face_width)
    if face is None:return None
    try:
        aligned=recognizer.alignCrop(frame,face)
        feature=recognizer.feature(aligned)
    except cv2.error:
        return None
    if not np.isfinite(feature).all():
        return None
    return feature


def crop_face(frame,face):
    height,width=frame.shape[:2]
    cx=float(face[0]+face[2]/2)
    cy=float(face[1]+face[3]/2)
    size=int(max(face[2],face[3])*2.45)
    size=max(size,128)
    x1=max(0,int(cx-size/2));x2=min(width,int(cx+size/2))
    y1=max(0,int(cy-size*.49));y2=min(height,int(cy+size*.51))
    if x2-x1<48 or y2-y1<48:
        raise IdentityQualityReview('Nicole face reference crop is too small')
    region=frame[y1:y2,x1:x2]
    return cv2.resize(region,(512,512),interpolation=cv2.INTER_CUBIC)


def prepare_anchor(job):
    """Create same-person close-up Image 2, never from a generated frame."""
    identity=((job.get('checkpoint') or {}).get('identity') or {}).get('nicole_image_key')
    if not identity:
        raise IdentityQualityReview('Nicole identity image is missing')
    key=f'recreation/{job["job_id"]}/identity/nicole_face_closeup_v1.jpg'
    s3=storage.client()
    try:
        s3.head_object(Bucket=storage.bucket(),Key=key)
        return key
    except Exception as exc:
        # Only a missing object should trigger generation; other storage
        # failures must not be silently ignored.
        code=str(getattr(exc,'response',{}).get('Error',{}).get('Code',''))
        if code not in ('404','NoSuchKey','NotFound'):
            raise
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'reference.jpg'
        close=Path(td)/'closeup.jpg'
        storage.download_key(identity,path)
        reference=cv2.imread(str(path))
        if reference is None:
            raise IdentityQualityReview('Nicole identity reference could not be decoded')
        detector,_=load_models()
        face=detect_one(reference,detector,min_face_width=38)
        if face is None:
            raise IdentityQualityReview('Nicole reference face is not clearly detectable; supply a sharp frontal identity photo')
        crop=crop_face(reference,face)
        if not cv2.imwrite(str(close),crop,[cv2.IMWRITE_JPEG_QUALITY,94]):
            raise IdentityQualityReview('Could not create Nicole face reference')
        storage.upload_key(key,close,mime='image/jpeg')
    LOG.info('NICOLE_IDENTITY anchor_ready=true job=%s original_photo_only=true',str(job['job_id'])[:8])
    return key


def sample_video_matches(reference_path,video_path,*,max_samples=12):
    detector,recognizer=load_models()
    reference=cv2.imread(str(reference_path))
    if reference is None:
        raise IdentityQualityReview('Nicole canonical reference image is unreadable')
    face_feature=image_feature(reference,detector,recognizer,min_face_width=38)
    if face_feature is None:
        raise IdentityQualityReview('Nicole canonical reference has no measurable frontal face')
    cap=cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise IdentityQualityReview('Generated segment cannot be read for identity QA')
    try:
        duration=float(cap.get(cv2.CAP_PROP_FRAME_COUNT))/max(float(cap.get(cv2.CAP_PROP_FPS)),1)
        if not 0<duration<32:
            raise IdentityQualityReview('Generated segment duration is invalid for face QA')
        count=max(5,min(max_samples,round(duration*1.3)))
        scores=[]
        for seconds in np.linspace(.28,max(.35,duration-.18),count):
            cap.set(cv2.CAP_PROP_POS_MSEC,float(seconds*1000))
            ok,frame=cap.read()
            if not ok:continue
            try:generated=image_feature(frame,detector,recognizer,min_face_width=25)
            except IdentityQualityReview:
                # Multi-person video is not acceptable; preserve paid output
                # for review, never replace automatically.
                raise
            if generated is not None:
                scores.append(float(recognizer.match(
                    face_feature,generated,cv2.FaceRecognizerSF_FR_COSINE)))
    finally:
        cap.release()
    return {'detected_faces':len(scores),
            'samples':count,
            'median_similarity':round(float(np.median(scores)),4) if scores else None,
            'min_similarity':round(float(min(scores)),4) if scores else None,
            'above_threshold':sum(s>=COSINE_THRESHOLD for s in scores),
            'threshold':COSINE_THRESHOLD}


def check_archived(job,video_key,*,qa_key=None):
    """Persist private evidence. Block later paid clips when confidence is bad."""
    if os.getenv('RECREATION_FACE_QA_MODE','on').strip().lower()=='off':
        return {'status':'disabled'}
    anchor=((job.get('checkpoint') or {}).get('identity') or {}).get('nicole_image_key')
    if not anchor:
        raise IdentityQualityReview('Nicole reference is not ready')
    qa_key=qa_key or f'{video_key}.face_qa_v1.json'
    # PASSED metrics are durable. Failed/unverified clips remain archived;
    # subsequent provider submissions stay blocked until owner review.
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        saved=root/'face_qa.json'
        try:
            storage.download_key(qa_key,saved)
            old=json.loads(saved.read_text())
            if old.get('status')=='passed':
                return old
            raise IdentityQualityReview('Saved Nicole identity QA did not pass; review this clip')
        except IdentityQualityReview:
            raise
        except Exception:
            pass
        canonical=root/'nicole.jpg';segment=root/'wan.mp4'
        storage.download_key(anchor,canonical)
        storage.download_key(video_key,segment)
        result=sample_video_matches(canonical,segment)
        # The model cannot certify a match from profile-only/very small faces.
        # Do not pay for the next segment when little identity evidence exists.
        detected=result['detected_faces']
        matches=result['above_threshold']
        if detected<2:
            status='unverified'
        elif matches<2 or (matches/detected)<.6:
            status='mismatch'
        else:
            status='passed'
        result['status']=status
        saved.write_text(json.dumps(result,sort_keys=True))
        storage.upload_key(qa_key,saved,mime='application/json')
        LOG.info('NICOLE_IDENTITY job=%s status=%s faces=%d passed=%d',
                 str(job['job_id'])[:8],status,detected,matches)
        if status!='passed':
            raise IdentityQualityReview(
                f'Nicole facial identity QA {status}: {matches}/{detected} visible '
                'faces matched the saved reference. Paid generated clip is '
                'archived. Review it; no further paid sections were submitted.')
        return result
