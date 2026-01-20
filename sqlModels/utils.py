# sqlModels/utils.py
from __future__ import annotations
import hashlib
import os
import datetime

def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")

def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def stat_file(path: str) -> tuple[float, int]:
    st = os.stat(path)
    return (st.st_mtime, st.st_size)
