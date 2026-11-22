"""The decoding of an uploaded PNG or JPEG for the reading model, in a bounded, killable child process (issue #501).

An uploaded image is untrusted bytes: decoding it can take a long time and a lot of memory, so the worker never decodes it
itself. ``run_isolated_image_reading`` is the one worker entry point; it starts ``python -m app.core.image_reading`` in a
child of its own (session, address-space limit on Linux, an alarm that ends the whole process group, and the caller's
deadline enforced by killing the child), sends the image over a pipe and gets the PNG for the model back over another.
The child checks the pixel bound from the header before anything is decoded, applies the rotation of the EXIF header,
flattens transparency on white and scales a picture above the output bound down, exactly as a rendered page is
(``declarations/preflight.py``). A page of a PDF is rendered by poppler, a process of its own the same way.
"""
from __future__ import annotations

import io
import math
import os
from pathlib import Path
import signal
import subprocess
import sys

from app.core.document_limits import MAX_UPLOAD_BYTES

# The child's address space: far above a 20 million pixel picture and its copies (about 250 MB), far below the worker's.
CHILD_MEMORY_BYTES = 1024**3
# The pipe carries a PNG of at most the output bound; anything much larger is not what the child was asked for.
MAX_OUTPUT_BYTES = 64 * 1024**2
# Exit status of a child that refused the picture for its size, before decoding it.
PIXELS_EXIT = 3
# Why a picture was not read: ``pixels`` (more than the bound allows), ``timeout`` (the deadline), ``failed`` (anything else).
PIXELS, TIMEOUT, FAILED = 'pixels', 'timeout', 'failed'
# The command of the child; one place so that a test can stand in a child that never returns.
CHILD_COMMAND = (sys.executable, '-m', 'app.core.image_reading')


class ImageReadingError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _PixelBound(Exception):
    pass


def fit_pixels(image, max_pixels: int):
    """The image itself when it has at most ``max_pixels``, else the same picture scaled down to fit them."""
    pixels = image.width * image.height
    if pixels <= max_pixels:
        return image
    from PIL import Image

    scale = math.sqrt(max_pixels / pixels)
    size = (max(1, math.floor(image.width * scale)), max(1, math.floor(image.height * scale)))
    return image.resize(size, Image.LANCZOS)


def prepare_image(data: bytes, max_pixels: int, max_output_pixels: int) -> bytes:
    """The PNG of an image for the reading model. Runs only in the child: this is the decode of untrusted bytes."""
    from PIL import Image, ImageOps

    # The pixel bound below is the guard before a decode; the library's own bomb check would end a huge header in an error
    # that does not say why.
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > max_pixels:
            raise _PixelBound
        image.load()
        upright = ImageOps.exif_transpose(image)
        if upright.mode in ('RGBA', 'LA') or (upright.mode == 'P' and 'transparency' in upright.info):
            flattened = upright.convert('RGBA')
            page = Image.new('RGB', flattened.size, (255, 255, 255))
            page.paste(flattened, mask=flattened.getchannel('A'))
        elif upright.mode in ('RGB', 'L'):
            page = upright
        else:
            page = upright.convert('RGB')
        encoded = io.BytesIO()
        fit_pixels(page, max_output_pixels).save(encoded, format='PNG')
    return encoded.getvalue()


def run_isolated_image_reading(data: bytes, *, max_pixels: int, max_output_pixels: int, timeout: float) -> bytes:
    """The PNG of an uploaded PNG or JPEG, decoded in a child that is killed at ``timeout`` seconds."""
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ImageReadingError(FAILED)
    process = None
    try:
        process = subprocess.Popen(
            [*CHILD_COMMAND, str(max_pixels), str(max_output_pixels), str(max(1, math.ceil(timeout)))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
            env={key: value for key, value in os.environ.items() if key in {'PATH', 'LANG', 'LC_ALL', 'TMPDIR'}},
            cwd=Path(__file__).resolve().parents[2])
        output, _ = process.communicate(data, timeout=max(0.001, timeout))
        if process.returncode == PIXELS_EXIT:
            raise ImageReadingError(PIXELS)
        if process.returncode != 0 or not output.startswith(b'\x89PNG\r\n\x1a\n') or len(output) > MAX_OUTPUT_BYTES:
            raise ImageReadingError(FAILED)
        return output
    except subprocess.TimeoutExpired:
        raise ImageReadingError(TIMEOUT) from None
    except OSError:
        raise ImageReadingError(FAILED) from None
    finally:
        if process is not None:
            # Reap the child and stop any descendants even if the pipe or the answer is bad. Exiting between the deadline
            # and the kill is an ordinary race.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()


if __name__ == '__main__':
    signal.signal(signal.SIGALRM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
    signal.alarm(int(sys.argv[3]))
    if sys.platform == 'linux':
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_BYTES, CHILD_MEMORY_BYTES))
    try:
        png = prepare_image(sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1), int(sys.argv[1]), int(sys.argv[2]))
    except _PixelBound:
        sys.exit(PIXELS_EXIT)
    sys.stdout.buffer.write(png)
