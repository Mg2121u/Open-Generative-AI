"""Temporary single-container storage for the no-extra-account Salad pilot.

Ephemeral output can disappear when Salad preempts/replaces a worker.
DO NOT use this driver with >1 replica or for production deliveries.
"""
from __future__ import annotations

import io
import os
import re
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile


class EphemeralStore:
    def __init__(self, root: str | Path = "/tmp/aria-nicole-pilot"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{1,200}", key):
            raise ValueError("Invalid storage key")
        if ".." in key.split("/"):
            raise ValueError("Invalid storage key")
        return self.root / key

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs):
        if len(Body) > 12_000_000:
            raise ValueError("Output exceeds temporary storage limit")
        path = self._path(Key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            with NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".tmp-", delete=False) as f:
                f.write(Body)
                temp = f.name
            os.replace(temp, path)
        return {}

    def get_object(self, *, Bucket: str, Key: str):
        path = self._path(Key)
        with self.lock:
            return {"Body": io.BytesIO(path.read_bytes())}
