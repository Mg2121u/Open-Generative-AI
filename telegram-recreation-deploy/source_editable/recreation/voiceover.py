"""Opt-in ElevenLabs v4 voiceover for the *independent* recreation app.

The source soundtrack remains unchanged unless the owner explicitly sends
/voiceover <text>. Voiceover uses persistent write-ahead steps; ambiguous
network outcomes are paused rather than charged twice.
"""
import os
import tempfile
from pathlib import Path

import requests

from . import db, storage
from .provider import NeedsReview

ELEVENLABS_API_URL = 'https://api.elevenlabs.io/v1/text-to-speech'
MODEL_ID = 'eleven_v4'
# Voice IDs are not API secrets; environment variables allow a controlled update.
KNOWN_VOICES = {
    'NICOLE': 'DWQWHtcQpAHZJPTy19u1',
    'ARIA': 'CLyJ3auZqUC7B8ZKF0cF',
}
MAX_TEXT_LENGTH = 1500


class VoiceConfigurationError(RuntimeError):
    pass


def voice_id_for(character, selection='AUTO'):
    character = str(character or '').strip().upper()
    selection = str(selection or 'AUTO').strip().upper()
    if character not in ('NICOLE', 'ARIA', 'ARIA_NICOLE'):
        raise ValueError('Unknown character mode')
    if selection not in ('NICOLE', 'ARIA', 'AUTO'):
        raise ValueError('Choose Nicole, Aria, or Auto')
    if character in ('NICOLE', 'ARIA'):
        if selection not in ('AUTO', character):
            raise ValueError('Voice and character must match. Nicole and Aria voices cannot be swapped.')
        selection = character
    elif selection == 'AUTO':
        raise ValueError('For duo mode, select /voice Nicole or /voice Aria')
    env_key = f'ELEVENLABS_VOICE_ID_{selection}'
    voice_id = os.environ.get(env_key, KNOWN_VOICES[selection]).strip()
    if not voice_id or len(voice_id) > 128 or not voice_id.isascii() or not voice_id.isalnum():
        raise VoiceConfigurationError(f'{env_key} is invalid')
    return selection, voice_id


def validate_text(text):
    text = str(text or '').strip()
    if not 1 <= len(text) <= MAX_TEXT_LENGTH:
        raise ValueError(f'Voiceover script must be 1–{MAX_TEXT_LENGTH} characters')
    return text


def synthesize_once(job_id, character, selection, text):
    """Persist audio before recording completion; never resubmit uncertain charges.

    Returns durable storage key. Call from worker, never Telegram webhook.
    """
    name, voice_id = voice_id_for(character, selection)
    text = validate_text(text)
    step_key = 'elevenlabs_v4_voiceover'
    prior = db.get_step(job_id, step_key)
    if prior:
        if prior['state'] == 'COMPLETED':
            key = (prior.get('result') or {}).get('audio_key')
            if key:
                return key
        raise NeedsReview('ElevenLabs v4 submission already exists or is uncertain; check ElevenLabs history to avoid duplicate charges')

    if os.environ.get('ENABLE_PAID_VOICE_GENERATION', 'false').lower() != 'true':
        raise VoiceConfigurationError('ElevenLabs paid voice generation is disabled in staging')
    api_key = os.environ.get('ELEVENLABS_API_KEY', '').strip()
    if not api_key:
        raise VoiceConfigurationError('ELEVENLABS_API_KEY missing')

    if not db.reserve_one_shot(job_id, step_key, 'elevenlabs',
                               {'model': MODEL_ID, 'voice': name, 'characters': len(text)}):
        raise NeedsReview('Voiceover already submitted by another worker; do not pay twice')
    # A timeout or connection loss after provider submission is ambiguous.
    # The step stays SUBMITTING, and we must not resubmit automatically.
    try:
        with requests.post(
            f'{ELEVENLABS_API_URL}/{voice_id}',
            headers={'xi-api-key': api_key, 'Accept': 'audio/mpeg',
                     'Content-Type': 'application/json'},
            params={'output_format': 'mp3_44100_128'},
            json={'text': text, 'model_id': MODEL_ID},
            stream=True, timeout=(15, 150),
        ) as response:
            response.raise_for_status()
            with tempfile.TemporaryDirectory() as temp:
                audio_file = Path(temp) / 'voiceover.mp3'
                size = 0
                with audio_file.open('wb') as out:
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > 20_000_000:
                            raise ValueError('ElevenLabs response exceeded audio size limit')
                        out.write(chunk)
                if size < 128:
                    raise RuntimeError('ElevenLabs returned an empty audio response')
                audio_key = f'recreation/{job_id}/voice/elevenlabs_{name.lower()}_v4.mp3'
                storage.upload_key(audio_key, audio_file, mime='audio/mpeg')
    except Exception as exc:
        raise NeedsReview('ElevenLabs voice submission outcome uncertain; check provider history before retrying') from exc

    db.external_finished(job_id, step_key,
                         {'audio_key': audio_key, 'model': MODEL_ID, 'voice': name})
    return audio_key
