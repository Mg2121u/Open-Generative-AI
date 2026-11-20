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
                'same as identity photo', 'same as reference photo',
                'same as the identity image', 'same as her identity image'}


def enabled(job):
    return (os.getenv('RECREATION_QUALITY_MODE', '').strip().lower() == QUALITY_MODE
            and job.get('stage') in ('UPLOAD_REFERENCE', 'AUTO_PREFLIGHT', 'AUTO_GENERATE'))


def _clean(text, limit=260):
    # Treat owner-supplied instructions as data inside a model prompt; never
    # allow huge text to crowd out explicit reference priorities.
    return re.sub(r'\s+', ' ', str(text or '')).strip()[:limit]


def quality_plan(duration, *, max_clips=2400):
    """Use bounded 8.5-second windows, rather than 13 chained 4s windows.

    This model supports up to 15s of video reference and requires total
    input+output duration <=30s. At 8.5s both sides stay well within limits.
    Each independent window resets to the canonical character reference.
    """
    try:
        duration = round(float(duration), 3)
    except (TypeError, ValueError):
        raise ValueError('Invalid source duration') from None
    if not math.isfinite(duration) or not 2 <= duration <= 7200:
        raise ValueError('Unsupported source duration')
    clips = min(max_clips, max(1, math.ceil(duration / 8.5)))
    if not clips:
        raise ValueError('Invalid quality plan')
    sizes = [round(duration / clips, 3)] * clips
    sizes[-1] = round(duration - sum(sizes[:-1]), 3)
    if any(not 2 <= size <= 8.5 for size in sizes):
        raise ValueError('Cannot split motion into safe short windows')
    result = []
    at = 0.0
    for i, size in enumerate(sizes):
        output_seconds = max(2, math.ceil(size - 1e-7))
        if size + output_seconds > 30 or size > 15:
            raise ValueError('Reference duration exceeds Wan input/output limit')
        result.append({'index': i, 'start': round(at, 3), 'duration': size,
                       'output_seconds': output_seconds, 'reused': False,
                       'estimated_usd': round((math.ceil(size) + output_seconds) * .05, 2)})
        at += size
    if abs(at - duration) > .006:
        raise ValueError('Duration was not conserved')
    return result


def reset_identity_each_segment(job):
    """Do not recursively treat imperfect Wan output as the next reference.

    The first-priority image is the same saved Nicole identity on every paid
    segment. Previous outputs may be used for local continuity metrics only.
    """
    return enabled(job) and os.getenv('RECREATION_IDENTITY_ANCHOR', 'canonical').lower() == 'canonical'


def fixed_seed(job):
    digest = hashlib.sha256(str(job['job_id']).encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big') % 2147483648


def scene_reference(job):
    """Scenery pictures are NEVER person/identity references."""
    return ((job.get('checkpoint') or {}).get('scenery_reference') or {})


def scene_instruction(job, *, scene_image_number=None):
    """Image 3 owns LOCATION, not character, wardrobe, or dance."""
    reference=scene_reference(job)
    status=reference.get('status')
    if status in ('awaiting','pending','failed'):
        raise ValueError('Scenery reference photo is not ready; finish image upload before generation')
    if status=='ready':
        if not reference.get('scenery_image_key') or scene_image_number!=3:
            raise ValueError('An uploaded scenery photo MUST be Image 3 after two Nicole references')
        details=_clean(reference.get('description'))
        return (
            'IMAGE 3 IS THE EXCLUSIVE ENVIRONMENT AND SCENERY AUTHORITY. '
            'Generate a new, single realistic moving video SET INSIDE the exact '
            'physical location depicted in Image 3. Reconstruct the same architecture, '
            'surfaces, furniture and object placement, spatial layout, colors, '
            'depth, horizon, ground plane, scale, weather and lighting direction. '
            'Nicole must be physically present in that environment and dance there '
            'naturally, grounded by plausible contact shadows and scene-relative '
            'reflections, realistic occlusion, perspective, motion parallax and '
            'matching illumination. As the camera moves with Video 1 timing, '
            'reveal spatially coherent views of this same location, never a flat '
            'stretched photo. Do not paste a foreground cutout onto a still picture. '
            'Ignore and completely DISCARD the room, walls, floor, background, '
            'furniture and lighting from Image 1, Image 2 and Video 1. '
            'They are NOT scenery references. No frames may flash back to any '
            'other room; no background dissolve, crossfade, double exposure, '
            'green-screen halo, compositing outline or mixed environments. '
            'Ignore people, faces and clothes present in Image 3: '
            'it is BACKGROUND ONLY, never a character or outfit reference. '
            + (f'Additional location details: {details}. ' if details else '')
        )
    requested=_clean(job.get('scenery'))
    if requested.lower() in SOURCE_SCENE:
        return (
            'SCENERY RULE: Keep the original background and lighting of Video 1. '
            'The setting must remain stable across every frame. '
        )
    return (
        'SCENERY OVERRIDE (higher priority than Video 1 background): '
        f'The entire scene must be "{requested}". Replace the source location completely. '
        'Keep one physically coherent, photorealistic location with consistent '
        'architecture, ground plane, scale, lighting and perspective. Never retain '
        'the original source landmarks or scenery. Do not switch backgrounds. '
    )


def outfit_instruction(job):
    requested=_clean(job.get('outfit'))
    normalized=requested.lower()
    photo=scene_reference(job).get('status')=='ready'
    # Source-wardrobe is the old default on draft creation. For an uploaded
    # scenery PHOTO, the requested three-reference contract explicitly makes
    # Nicole's Image 1 the outfit authority instead of the source dancer.
    if photo and (normalized in SOURCE_OUTFIT or normalized in PHOTO_OUTFIT
                  or 'reference photo' in normalized):
        return (
            'WARDROBE AUTHORITY = IMAGE 1 ONLY. Nicole wears exactly the outfit '
            'shown on her canonical full-body reference photograph: identical '
            'colors, fabric, fit, neckline, sleeves, skirt/pants, footwear, '
            'accessories and hairstyle, continuously across every frame. '
            'Neither Video 1 nor Image 3 may change her clothing. '
        )
    if normalized in PHOTO_OUTFIT or 'reference photo' in normalized:
        return (
            'CLOTHING RULE: Image 1 is the wardrobe authority. Precisely retain '
            'the reference photo outfit, garment shape, seams, colors and accessories '
            'on every frame. Do not borrow clothing from Video 1. '
        )
    if normalized in SOURCE_OUTFIT:
        return (
            'CLOTHING RULE: Copy the complete wardrobe from Video 1, '
            "but keep Nicole's face, hair and body from Image 1. "
        )
    return (
        f'CLOTHING OVERRIDE: Nicole must wear "{requested}". '
        'Keep the clothing unchanged throughout; no changing hemlines, merging '
        'garments, vanishing footwear, fabric melting or shape-shifting. '
    ) if requested else (
        'CLOTHING RULE: Preserve exactly the wardrobe in Image 1. '
    )


def build_prompt(job, *, chained=False, scene_image_number=None, face_anchor_present=True):
    scene=scene_reference(job)
    if scene.get('status')=='ready':
        if chained or not face_anchor_present or scene_image_number!=3:
            raise ValueError('Photo scenery requires canonical Nicole Image 1, face Image 2, scenery Image 3, and NO generated-frame chain')
        clothing=outfit_instruction(job)
        setting=scene_instruction(job,scene_image_number=3)
        extra=_clean(job.get('instructions'),380)
        return (
            'WAN 3.0 REFERENCE-TO-VIDEO — STRICT INPUT ROLE CONTRACT. '
            'IMAGE 1: Original canonical NICOLE FULL-BODY IDENTITY AND WARDROBE '
            'REFERENCE. Recreate THIS SAME adult woman: face geometry, eyes, '
            'nose, lips, jawline, skin, hair texture and color, body shape and '
            'proportions. Her appearance and clothes remain exactly consistent. '
            'Do NOT use Image 1 for the background or indoor room. '
            'IMAGE 2: CLOSE-UP OF THE EXACT SAME NICOLE FACE CROPPED FROM IMAGE 1. '
            'It reinforces facial geometry and identity ONLY. Ignore its '
            'background and do not treat it as a second woman. '
            'IMAGE 3: TARGET SCENERY PHOTO. It determines LOCATION/ENVIRONMENT '
            'ONLY; not her face, hair, proportions or outfit. '
            'VIDEO 1: ORIGINAL REFERENCE CLIP. Follow its choreography and '
            'movement exactly: each dance move, direction, footwork, gestures, '
            'timing, rhythm, pose trajectories, head turns, body orientation, '
            'relative camera framing and camera-motion sequence. Video 1 is '
            'NOT an identity, wardrobe, lighting, room, floor, furniture or '
            'background reference. Completely replace its original performer '
            'with Nicole from Image 1/2 and its environment with Image 3. '
            + clothing + setting +
            'OUTPUT: Generate a NEW unified photorealistic video OF NICOLE '
            'performing the choreography IN the location depicted by Image 3. '
            'No picture-in-picture, matte, static-photo backdrop, copy-paste, '
            'silhouette, paper cutout, compositing, fake green screen or visible '
            'subject/background edge. Align Nicole to the physical environment '
            'with accurate scale, plausible scene-consistent shadows, reflected '
            'light and natural feet-ground contact throughout. Every source '
            'interval begins at its correct source motion pose; transitions '
            'must not restart the dance or change the scene. Preserve the '
            'same face, hairstyle, body and clothes in every frame. Keep '
            'camera motion smooth, perspective and layout coherent, no room '
            'flashing or source-background bleed. Maintain realistic anatomy, '
            'no warping, ghosting, melting, duplicate limbs, stretched torso, '
            'scene transitions, flicker, faces from other inputs or extra people. '
            'No text, usernames, logos, stickers, subtitles or watermarks. '
            'GENERATE NO AUDIO; original audio is restored locally after render. '
            + (f'Additional owner direction: {extra}. ' if extra else '') +
            'FINAL PRIORITY ORDER: Nicole identity AND outfit = IMAGE 1; '
            'Nicole exact face = IMAGE 2; full environment ONLY = IMAGE 3; '
            'dance movement and camera timing ONLY = VIDEO 1. '
            'Never inherit the background from either Nicole image or Video 1.'
        )
    motion = (
        'VIDEO 1 IS THE MOTION AND CAMERA TIMING AUTHORITY. Reproduce the source '
        'choreography pose by pose, including hand movements, steps, limb trajectories, '
        'head direction, rhythm, framing and camera path. Do not borrow the Video 1 '
        'performer\'s identity. Use Video 1 clothing or scenery ONLY when ' 
        'the owner specifically requests keeping them, as stated below. '
    )
    identity = (
        'IMAGE 1 IS THE ONLY IDENTITY AUTHORITY: Nicole is the same single adult woman '
        'throughout, with identical facial geometry, eyes, nose, jaw, hairline, hair color, '
        'skin tone, body proportions and stature. Do not blend, average, morph, or swap '
        'Nicole with the Video 1 performer or with Image 2. '
        'Every segment independently resets to the original Nicole reference ' 
        'photo, never to an imperfect AI-generated version of Nicole. ' 
        'Every frame must look like one continuous real video of the same person. '
    )
    continuity = (
        'IMAGE 2 is the final generated pose from the preceding segment. Use it ONLY '
        'to align starting pose, screen position, camera framing and physical continuity; '
        'Image 1 still controls identity and wardrobe, and the scenery rule below '
        'controls the location. Continue the physical dance without resetting, '
        'teleporting or morphing between poses. '
    ) if chained else (
        ('IMAGE 2 is a close-up crop of NICOLE FROM IMAGE 1, not another woman. '
         'Its sole purpose is to reinforce her exact facial identity: same eyes, '
         'eyelids, nose bridge, lips, cheeks, jaw, facial proportions, skin texture '
         'and hairline in every frame. Image 1 controls body, hair, outfit and '
         'full-body appearance. NEVER substitute the face from Video 1. '
         if face_anchor_present else
         'Only Image 1 is used for Nicole identity; no facial close-up was provided. ')
        + 'NO previous generated frame is supplied. Start at the opening pose '
        'of the current Video 1 motion interval, using original Nicole in Image 1. '
        'Maintain her exact proportions and wardrobe across the entire clip. '
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
    return (identity + motion + scene_instruction(job,scene_image_number=scene_image_number) + outfit_instruction(job)
            + continuity + stability
            + (f' Additional owner directions: {extra}.' if extra else ''))
