"""Safe configured-file and evidence streaming with single-range HTTP support."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Iterator

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..errors import APIError

RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _reader(path: Path, start: int, end: int, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def stream_file(
    request: Request,
    path: Path,
    *,
    media_type: str | None,
    unavailable_code: str,
    preview_label: str | None = None,
) -> StreamingResponse:
    try:
        size = path.stat().st_size
        if not path.is_file() or size == 0:
            raise OSError("File is absent or empty")
    except OSError as exc:
        raise APIError(404, unavailable_code, "The local media file is unavailable") from exc
    start, end, status = 0, size - 1, 200
    range_header = request.headers.get("range")
    if range_header:
        match = RANGE.fullmatch(range_header.strip())
        if not match or ("," in range_header):
            raise APIError(416, "INVALID_RANGE", "Only one valid byte range is supported", headers={"Content-Range": f"bytes */{size}"})
        first, last = match.groups()
        if not first and not last:
            raise APIError(416, "INVALID_RANGE", "Byte range is empty", headers={"Content-Range": f"bytes */{size}"})
        if not first:
            suffix = int(last)
            if suffix <= 0:
                raise APIError(416, "INVALID_RANGE", "Suffix range must be positive", headers={"Content-Range": f"bytes */{size}"})
            start = max(0, size - suffix)
        else:
            start = int(first)
            if last:
                end = min(int(last), size - 1)
        if start >= size or end < start:
            raise APIError(416, "RANGE_NOT_SATISFIABLE", "Byte range exceeds the media size", headers={"Content-Range": f"bytes */{size}"})
        status = 206
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if preview_label:
        # HTTP headers are ASCII-compatible; the exact Unicode label is also present in snapshot notices.
        headers["X-Preview-Label"] = preview_label.replace(" · ", " - ")
        headers["X-Preview-Label-Encoded"] = preview_label.replace(" ", "%20").replace("·", "%C2%B7")
        headers["X-Preview-Mode"] = "source-only"
        headers["X-Analytics-Available"] = "false"
    return StreamingResponse(
        _reader(path, start, end),
        status_code=status,
        media_type=media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers=headers,
    )
