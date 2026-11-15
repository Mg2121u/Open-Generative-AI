"""Conservative local logo/handle cleanup for motion references.

The original archived source and its soundtrack stay untouched.
A reference is only re-encoded when a stable, compact graphic is detected;
otherwise the caller uses the unchanged source clip. No external API calls.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
import cv2
import numpy as np


def _upper_color_logo_center(frame):
    small=cv2.resize(frame,(480,854),interpolation=cv2.INTER_AREA)
    h,w=small.shape[:2]
    x0,x1=int(.61*w),int(.99*w)
    y0,y1=int(.17*h),int(.36*h)
    hsv=cv2.cvtColor(small[y0:y1,x0:x1],cv2.COLOR_BGR2HSV)
    hue,sat,val=cv2.split(hsv)
    colored=((sat>75)&(val>120)&(((hue>129)&(hue<180))|
                                   ((hue<18)&(sat>105))))
    binary=cv2.morphologyEx(colored.astype(np.uint8)*255,
                            cv2.MORPH_CLOSE,np.ones((4,4),np.uint8))
    count,_,stats,_=cv2.connectedComponentsWithStats(binary)
    found=[]
    for i in range(1,count):
        x,y,bw,bh,area=(int(z) for z in stats[i])
        cx=x0+x+bw/2
        cy=y0+y+bh/2
        if (4<=bw<=34 and 4<=bh<=36 and 8<=area<=450
                and .67*w<cx<.98*w and .19*h<cy<.34*h):
            found.append((area,cx/w,cy/h))
    return (max(found)[1],max(found)[2]) if found else None


def _detect_stable_overlay(capture,frames):
    found=[]
    stride=max(1,frames//15)
    for i in range(0,frames,stride):
        capture.set(cv2.CAP_PROP_POS_FRAMES,i)
        ok,frame=capture.read()
        if ok:
            center=_upper_color_logo_center(frame)
            if center:found.append(center)
    if len(found)<2:return None
    positions=np.asarray(found)
    median=np.median(positions,axis=0)
    if np.max(np.abs(positions-median))>.045:return None
    return tuple(float(z) for z in median)


def _lower_companion_present(frame):
    h,w=frame.shape[:2]
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    icon=gray[int(.537*h):int(.585*h),int(.073*w):int(.148*w)]
    title=gray[int(.593*h):int(.625*h),int(.07*w):int(.36*w)]
    if icon.size==0 or title.size==0:return False
    return (float(np.mean(icon>210))>.055
            and float(np.mean(title>215))>.013)


def _mask(frame,center):
    h,w=frame.shape[:2]
    mask=np.zeros((h,w),dtype=np.uint8)
    cx,cy=(int(center[0]*w),int(center[1]*h))
    def rect(x1,y1,x2,y2):
        cv2.rectangle(mask,(max(0,int(x1)),max(0,int(y1))),
                      (min(w-1,int(x2)),min(h-1,int(y2))),255,-1)
    scale=w/480.0
    # A compact colored icon and its small adjacent account-name strip.
    rect(cx-17*scale,cy-18*scale,cx+17*scale,cy+18*scale)
    rect(cx-100*scale,cy+18*scale,cx+28*scale,cy+51*scale)
    if _lower_companion_present(frame):
        # White companion marks sit over textured scenery. Erase only bright
        # strokes, not whole rectangular chunks of the architecture or dancer.
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        smooth=cv2.GaussianBlur(gray,(9,9),0)
        fine=gray.astype(np.int16)-smooth.astype(np.int16)
        x1,x2=int(.065*w),int(.16*w)
        y1,y2=int(.525*h),int(.595*h)
        region=(gray[y1:y2,x1:x2]>178)&(fine[y1:y2,x1:x2]>9)
        mask[y1:y2,x1:x2][region]=255
        x1,x2=int(.065*w),int(.375*w)
        y1,y2=int(.59*h),int(.634*h)
        region=(gray[y1:y2,x1:x2]>172)&(fine[y1:y2,x1:x2]>8)
        mask[y1:y2,x1:x2][region]=255
        mask=cv2.dilate(mask,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)),
                        iterations=1)
    return mask


def clean_motion_reference(source,dest):
    """Return True if a stable overlay is removed, otherwise leave source alone.

    This handles a common upper-right colored logo plus an optional white
    lower-left companion. It is NOT a general text detector. Unknown overlays
    must be reviewed rather than falsely claimed clean.
    """
    source,dest=Path(source),Path(dest)
    video=cv2.VideoCapture(str(source))
    if not video.isOpened():
        raise RuntimeError('Could not inspect motion reference')
    try:
        frames=int(video.get(cv2.CAP_PROP_FRAME_COUNT))
        fps=float(video.get(cv2.CAP_PROP_FPS))
        width=int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
        height=int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if frames<=0 or not (8<=fps<=120) or width<=0 or height<=0:
            raise ValueError('Invalid motion reference metadata')
        center=_detect_stable_overlay(video,frames)
        if not center:return False
        video.set(cv2.CAP_PROP_POS_FRAMES,0)
        dest.parent.mkdir(parents=True,exist_ok=True)
        cmd=['ffmpeg','-hide_banner','-loglevel','error','-y',
             '-f','rawvideo','-pix_fmt','bgr24','-s',f'{width}x{height}',
             '-r',f'{fps:.6f}','-i','pipe:0','-an','-c:v','libx264',
             '-preset','veryfast','-crf','16','-pix_fmt','yuv420p',
             '-movflags','+faststart',str(dest)]
        process=subprocess.Popen(cmd,stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        count=0
        try:
            while True:
                ok,frame=video.read()
                if not ok:break
                mask=_mask(frame,center)
                cleaned=cv2.inpaint(frame,mask,3,cv2.INPAINT_TELEA)
                process.stdin.write(cleaned.tobytes())
                count+=1
            process.stdin.close()
            err=process.stderr.read()
            status=process.wait(timeout=180)
            if status or not dest.is_file() or dest.stat().st_size<1024:
                raise RuntimeError('Overlay cleanup encode failed: '+
                                   err.decode(errors='replace')[-200:])
        except Exception:
            process.kill()
            process.wait()
            dest.unlink(missing_ok=True)
            raise
        if count!=frames:
            dest.unlink(missing_ok=True)
            raise RuntimeError('Reference cleanup lost decoded frames')
        return True
    finally:
        video.release()
