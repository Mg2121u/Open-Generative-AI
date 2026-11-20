"""Durable S3-compatible media storage. All provider-facing links are short-lived presigned URLs."""
import os
import tempfile
import shutil
from pathlib import Path
import boto3
import requests
from botocore.config import Config


def client():
    return boto3.client('s3',endpoint_url=os.environ.get('S3_ENDPOINT_URL') or None,
        aws_access_key_id=os.environ.get('S3_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('S3_SECRET_ACCESS_KEY'),
        region_name=os.environ.get('S3_REGION','auto'),
        config=Config(signature_version='s3v4',s3={'addressing_style':'path'},
                      connect_timeout=8,read_timeout=30,
                      retries={'max_attempts':2}))


def bucket():
    value=os.environ.get('S3_BUCKET')
    if not value: raise RuntimeError('S3_BUCKET is required to prevent data loss')
    return value


def store_telegram(job_id, file_id):
    # Import lazily: telegram -> voiceover -> storage during application startup.
    # A module-level import here causes a circular import and prevents web startup.
    from .telegram import BOT_API_URL, BOT_TOKEN, telegram
    info=telegram('getFile',{'file_id':file_id})
    file_path=info.get('file_path')
    if not file_path: raise RuntimeError('Telegram getFile returned no file_path')
    key=f'recreation/{job_id}/original.mp4'
    with tempfile.TemporaryDirectory() as td:
        file=Path(td)/'original.mp4'
        # Local Bot API in --local mode returns absolute paths on shared storage.
        # Otherwise use its HTTP file endpoint (cloud Bot API: <=20MB).
        if Path(file_path).is_absolute():
            if not Path(file_path).is_file():
                raise RuntimeError('Local Bot API returned an absolute path inaccessible to this worker. Share the Bot API download directory with this worker.')
            shutil.copyfile(file_path,file)
        else:
            url=f'{BOT_API_URL}/file/bot{BOT_TOKEN}/{file_path.lstrip("/")}'
            with requests.get(url,stream=True,timeout=(20,90)) as result:
                result.raise_for_status()
                with file.open('wb') as out:
                    for chunk in result.iter_content(1024*1024):
                        if chunk: out.write(chunk)
        if file.stat().st_size<128: raise ValueError('Source download empty')
        client().upload_file(str(file),bucket(),key,
             ExtraArgs={'ContentType':'video/mp4','Metadata':{'job-id':str(job_id)}})
    return key


def presign(key,seconds=7200):
    return client().generate_presigned_url('get_object',Params={'Bucket':bucket(),'Key':key},ExpiresIn=seconds)


def download_key(key,path):
    client().download_file(bucket(),key,str(path))


def upload_key(key,path,mime='video/mp4'):
    client().upload_file(str(path),bucket(),key,ExtraArgs={'ContentType':mime})
    return key



def final_video_max_bytes():
    """Bound only the FINAL assembled MP4 (source and paid clips untouched).

    The S3-compatible backend may enforce a 50 MB per-object cap even when
    multipart upload is available. Allow workspace operators to raise it
    after confirming their own storage policy, without committing credentials.
    """
    raw=os.getenv('RECREATION_FINAL_S3_MAX_MB','44').strip()
    try:
        mb=float(raw)
    except (TypeError,ValueError):
        raise RuntimeError('Invalid final video storage limit configuration') from None
    if not 12<=mb<=2000:
        raise RuntimeError('Final video storage limit must be between 12 and 2000 MB')
    return int(mb*1_000_000)


def store_provider_url(url, key, *, max_bytes=250_000_000):
    """Persist a generated provider video before its signed URL expires."""
    from urllib.parse import urlsplit
    if not url.startswith('https://') or not urlsplit(url).hostname:
        raise ValueError('Provider returned invalid non-HTTPS media URL')
    with tempfile.TemporaryDirectory() as td:
        file=Path(td)/'provider.mp4'
        total=0
        with requests.get(url,stream=True,timeout=(20,90)) as response:
            response.raise_for_status()
            with file.open('wb') as out:
                for chunk in response.iter_content(1024*1024):
                    if not chunk: continue
                    total+=len(chunk)
                    if total>max_bytes: raise ValueError('Generated video exceeds storage limit')
                    out.write(chunk)
        if total<128: raise ValueError('Provider output empty')
        upload_key(key,file)
    return key


def store_telegram_reference_image(job_id, file_id, *, role):
    """Download a private character or scenery reference as a clean JPEG.

    No image pixels, Telegram token, or presigned URL are logged or placed in Git.
    """
    if role not in ('identity','scenery'):
        raise ValueError('Unknown media reference role')
    import hashlib
    from PIL import Image, ImageOps, UnidentifiedImageError
    from .telegram import BOT_API_URL, BOT_TOKEN, telegram
    info=telegram('getFile',{'file_id':file_id})
    path=info.get('file_path')
    if not path:
        raise RuntimeError('Telegram identity reference has no file path')
    if info.get('file_size',0)>10_000_000:
        raise ValueError('Identity image exceeds 10 MB')
    name='nicole' if role=='identity' else 'scenery'
    key=f"recreation/{job_id}/{role}/{name}_{hashlib.sha256(file_id.encode()).hexdigest()[:16]}.jpg"
    with tempfile.TemporaryDirectory() as td:
        raw=Path(td)/'reference_in'
        clean=Path(td)/'clean_reference.jpg'
        if Path(path).is_absolute():
            if not Path(path).is_file():
                raise RuntimeError('Telegram local file missing')
            if Path(path).stat().st_size>10_000_000:
                raise ValueError('Identity image too large')
            shutil.copyfile(path,raw)
        else:
            url=f'{BOT_API_URL}/file/bot{BOT_TOKEN}/{path.lstrip("/")}'
            n=0
            with requests.get(url,stream=True,timeout=(20,65)) as res:
                res.raise_for_status()
                with raw.open('wb') as sink:
                    for chunk in res.iter_content(256*1024):
                        if not chunk:continue
                        n+=len(chunk)
                        if n>10_000_000:
                            raise ValueError('Identity image exceeds 10 MB')
                        sink.write(chunk)
        # Decompression-bomb protection and deliberate format whitelist.
        Image.MAX_IMAGE_PIXELS=20_000_000
        with Image.open(raw) as im:
            if im.format not in {'JPEG','PNG','WEBP'}:
                raise ValueError('Identity photo must be JPEG, PNG or WEBP')
            if min(im.size)<256 or im.width*im.height>20_000_000:
                raise ValueError('Identity photo dimensions not supported')
            im=ImageOps.exif_transpose(im).convert('RGB')
            im.thumbnail((1600,1600),Image.Resampling.LANCZOS)
            im.save(clean,format='JPEG',quality=90,optimize=True)
        upload_key(key,clean,mime='image/jpeg')
    return key


def store_telegram_identity(job_id,file_id):
    """Backward-compatible Nicole-only reference archive."""
    return store_telegram_reference_image(job_id,file_id,role='identity')


def store_telegram_scenery(job_id,file_id):
    """Never store a scene under Nicole's canonical identity key."""
    return store_telegram_reference_image(job_id,file_id,role='scenery')
