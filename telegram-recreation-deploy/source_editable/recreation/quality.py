"""480p motion-reference quality profile, no external provider calls.

Image 1 owns Nicole's identity. Image 2 is optional temporal continuity.
Video 1 owns choreography and camera movement, not automatically the scenery,
wardrobe or the identity of the original performer.
"""
from __future__ import annotations

import hashlib
import math
import os
import re


QUALITY_MODE = 'identity_lock'
SOURCE_SCENE = {'keep source scenery', 'same as source', 'original scenery',
                'keep original background', 'keep source background', ''}
SOURCE_OUTFIT = {'keep source clothing', 'same as source', 'original clothing',
                 'keep original outfit', ''}
PHOTO_OUTFIT = {'same as reference image', 'the same as her identity image',
                'as per reference photo', 'as per refenrence photo',
                'same as identity photo', 'same as reference photo'}


def enabled(job):
    return (os.getenv('RECREATION_QUALITY_MODE', '').strip().lower() == QUALITY_MODE
            and job.get('stage') in ('UPLOAD_REFERENCE', 'AUTO_PREFLIGHT', 'AUTO_GENERATE'))


def _clean(text, limit=260):
    # Treat owner-supplied instructions as data inside a model prompt; never
    # allow huge text to crowd out explicit reference priorities.
    return re.sub(r'\s+', ' ', str(text or '')).strip()[:limit]


def quality_plan(duration, *, max_clips=2400):
    """Short motion windows distributed evenly, no tiny final remainder.

    Keep 2.0s <= window <= 3.99s; do not round one frame into a jump cut.
    Billing still uses the provider-required integer output seconds.
    """
    try:
        duration = round(float(duration), 3)
    except (TypeError, ValueError):
        raise ValueError('Invalid source duration') from None
    if not math.isfinite(duration) or not 2 <= duration <= 7200:
        raise ValueError('Unsupported source duration')
    clips = min(max_clips, max(1, math.ceil(duration / 3.99)))
    if not clips:
        raise ValueError('Invalid quality plan')
    sizes = [round(duration / clips, 3)] * clips
    sizes[-1] = round(duration - sum(sizes[:-1]), 3)
    if any(not 2 <= size <= 3.99 for size in sizes):
        raise ValueError('Cannot split motion into safe short windows')
    result = []
    at = 0.0
    for i, size in enumerate(sizes):
        output_seconds = max(2, math.ceil(size - 1e-7))
        result.append({'index': i, 'start': round(at, 3), 'duration': size,
                       'output_seconds': output_seconds, 'reused': False,
                       'estimated_usd': round((math.ceil(size) + output_seconds) * .05, 2)})
        at += size
    if abs(at - duration) > .006:
        raise ValueError('Duration was not conserved')
    return result


def fixed_seed(job):
    digest = hashlib.sha256(str(job['job_id']).encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big') % 2147483648


def scene_instruction(job):
    requested = _clean(job.get('scenery'))
    if requested.lower() in SOURCE_SCENE:
        return (
            'SCENERY RULE: Keep the original background and lighting of Video 1. '
            'The setting must remain stable across every frame.'
        )
    return (
        'SCENERY OVERRIDE (higher priority than Video 1 background or Image 2 background): '
        f'The entire scene must be "{requested}". Replace the source location completely. '
        'Keep one physically coherent, photorealistic location with consistent architecture, '
        'ground plane, scale, lighting and perspective. Never retain the original source '
        'landmarks or scenery. Do not switch backgrounds as the performer moves. '
    )


def outfit_instruction(job):
    requested = _clean(job.get('outfit'))
    if requested.lower() in PHOTO_OUTFIT or 'reference photo' in requested.lower():
        return (
            'CLOTHING RULE: Image 1 is the wardrobe authority. Precisely retain '
            'the reference photo outfit, garment shape, seams, colors and accessories '
            'on every frame. Do not borrow clothing from Video 1. '
        )
    if requested.lower() in SOURCE_OUTFIT:
        return (
            'CLOTHING RULE: Copy the complete wardrobe from Video 1, '
            'but keep Nicole\'s face, hair and body from Image 1. '
        )
    return (
        f'CLOTHING OVERRIDE: Nicole must wear "{requested}". '
        'Keep the clothing unchanged throughout; no changing hemlines, merging '
        'garments, vanishing footwear, fabric melting or shape-shifting. '
    ) if requested else (
        'CLOTHING RULE: Preserve exactly the wardrobe in Image 1. '
    )


def build_prompt(job, *, chained=False):
    motion = (
        'VIDEO 1 IS FOR MOTION AND CAMERA TIMING ONLY. Reproduce the source '
        'choreography pose by pose, including hand movements, steps, limb trajectories, '
        'head direction, rhythm, framing and camera path. Do not borrow the Video 1 '
        'performer\'s identity, clothes, or scenery except when a rule below explicitly says so. '
    )
    identity = (
        'IMAGE 1 IS THE ONLY IDENTITY AUTHORITY: Nicole is the same single adult woman '
        'throughout, with identical facial geometry, eyes, nose, jaw, hairline, hair color, '
        'skin tone, body proportions and stature. Do not blend, average, morph, or swap '
        'Nicole with the Video 1 performer or with Image 2. '
        'Every frame must look like one continuous real video of the same person. '
    )
    continuity = (
        'IMAGE 2 is the final generated pose from the preceding segment. Use it ONLY '
        'to align starting pose, screen position, camera framing and physical continuity; '
        'Image 1 still controls identity and wardrobe, and the scenery rule below '
        'controls the location. Continue the physical dance without resetting, '
        'teleporting or morphing between poses. '
    ) if chained else (
        'Begin this clip using Nicole from Image 1, already in the action and '
        'camera position shown by the beginning of Video 1. '
    )
    stability = (
        'STRICT TEMPORAL ANATOMY: Keep exactly two arms, two legs, two hands and '
        'one head, each attached to the same skeleton across all frames. '
        'No flickering facial features, changing limb thickness, duplicate fingers, '
        'rubbery joints, stretched torsos, melting fabric, fluctuating body shapes, '
        'skin texture swimming, motion smear, ghosting, overlapping bodies or scene cuts. '
        'If a pose is difficult, simplify small details rather than deform anatomy. '
        'Natural realistic movement, one continuous shot, steady camera and stable scale. '
        'Preserve exact scene and wardrobe throughout. '
        'ABSOLUTELY NO visible writing or graphics: remove all account names, '
        'usernames, social-media handles, Instagram/TikTok icons, logos, '
        'watermarks, captions, subtitles, labels, stickers, signage copied '
        'from the video, interface controls, badges or graphic overlays. '
        'Any such marks in Video 1 are editing artifacts, not scenery or '
        'choreography; do not reproduce them. No words or symbols anywhere. '
        'Generate NO new audio.'
    )
    extra = _clean(job.get('instructions'), 380)
    return (identity + motion + scene_instruction(job) + outfit_instruction(job)
            + continuity + stability
            + (f' Additional owner directions: {extra}.' if extra else ''))
