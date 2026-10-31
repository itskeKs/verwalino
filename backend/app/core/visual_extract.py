"""Isolated visual PDF derivatives. No clearance, publication or access grants.

Only run_isolated_visual_extract is a caller entry point. A future durable
adapter must require a clean original inspection and review these exact bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile

from app.core.document_limits import MAX_UPLOAD_BYTES

VERSION = 'visual-extract-v1'

MAX_INPUT_BYTES = MAX_UPLOAD_BYTES
MAX_OUTPUT_BYTES = MAX_UPLOAD_BYTES
MAX_SELECTED_PAGES = 20
MAX_EDGE = 3200
MAX_TOTAL_PIXELS = 100_000_000
MAX_TEMP_IMAGE_BYTES = 40 * 1024 * 1024
PAGE_SECONDS = 10
TOTAL_SECONDS = 60
FOOTER_POINTS = 20
ERROR_CODES = frozenset({'file_size_limit', 'source_hash_changed', 'unsupported_format', 'invalid_page_selection',
    'source_page_count_changed', 'unsupported_page_geometry', 'aggregate_pixel_limit',
    'image_size_limit', 'raster_geometry_mismatch', 'output_size_limit', 'encrypted_pdf',
    'pdf_page_limit', 'image_pixel_limit', 'renderer_failed', 'renderer_unavailable'})


def _fixed_profile():
    return {'version': VERSION, 'max_edge_pixels': MAX_EDGE, 'box': 'CropBox',
            'annotations': 'visible_appearances', 'text_layer': False, 'footer_points': FOOTER_POINTS}


def _valid_success(result, expected):
    if not isinstance(result, dict) or set(result) != set(expected) | {'status', 'renderer_profile'} or result['status'] != 'succeeded':
        return False
    if any(type(result[key]) is not type(value) or result[key] != value for key, value in expected.items()):
        return False
    profile = result['renderer_profile']
    fixed = _fixed_profile()
    if not isinstance(profile, dict) or set(profile) != set(fixed) | {'rasterizer', 'pdf_writer', 'image_library'}:
        return False
    if any(type(profile[key]) is not type(value) or profile[key] != value for key, value in fixed.items()):
        return False
    return all(isinstance(profile[key], str) and 0 < len(profile[key]) <= 160
               and all(32 <= ord(character) < 127 for character in profile[key])
               for key in ('rasterizer', 'pdf_writer', 'image_library'))


@dataclass(frozen=True)
class VisualExtractResult:
    status: str
    error_code: str | None
    source_sha256: str
    page_start: int
    page_end: int
    verified_page_count: int
    pdf_bytes: bytes | None = None
    content_sha256: str | None = None
    content_byte_size: int | None = None
    output_page_count: int | None = None
    renderer_profile: dict | None = None


def child_environment():
    return {key: value for key, value in os.environ.items()
            if key in {'PATH', 'LANG', 'LC_ALL', 'TMPDIR'}}


def selection_error(data, expected_hash, start, end, count):
    if not data or len(data) > MAX_INPUT_BYTES:
        return 'file_size_limit'
    if hashlib.sha256(data).hexdigest() != expected_hash:
        return 'source_hash_changed'
    if not data.startswith(b'%PDF-'):
        return 'unsupported_format'
    if (not all(type(value) is int for value in (start, end, count))
            or not 1 <= start <= end <= count <= 200 or end - start + 1 > MAX_SELECTED_PAGES):
        return 'invalid_page_selection'
    return None


def _geometry(page):
    """Use the displayed CropBox, rejecting ambiguous/inverted geometry."""
    from pdfminer.pdftypes import resolve1
    attrs = page.page_obj.attrs
    def box(key, fallback=None):
        values = resolve1(attrs.get(key, fallback))
        if not isinstance(values, (list, tuple)) or len(values) != 4:
            raise ValueError('unsupported_page_geometry')
        numbers = tuple(float(resolve1(value)) for value in values)
        if not all(math.isfinite(value) for value in numbers) or numbers[2] <= numbers[0] or numbers[3] <= numbers[1]:
            raise ValueError('unsupported_page_geometry')
        return numbers
    media = box('MediaBox')
    crop = box('CropBox', media)
    rotation = resolve1(attrs.get('Rotate', 0))
    unit = resolve1(attrs.get('UserUnit', 1))
    if (type(rotation) is not int or rotation % 90 or abs(rotation) > 360 or unit != 1
            or crop[0] < media[0] or crop[1] < media[1] or crop[2] > media[2] or crop[3] > media[3]):
        raise ValueError('unsupported_page_geometry')
    width, height = crop[2] - crop[0], crop[3] - crop[1]
    if rotation % 180:
        width, height = height, width
    return width, height


class _BoundedWriter:
    def __init__(self, stream):
        self.stream, self.size = stream, 0

    def write(self, data):
        if self.size + len(data) > MAX_OUTPUT_BYTES:
            raise ValueError('output_size_limit')
        self.size += len(data)
        return self.stream.write(data)


def _render(data, expected_hash, start, end, count, output):
    """Private child function: no caller-side document parsing."""
    import pdfplumber
    import PIL
    from PIL import Image
    import reportlab
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from app.core.intake_inspection import inspect_structure

    error = selection_error(data, expected_hash, start, end, count)
    if error:
        raise ValueError(error)
    if inspect_structure(data, 'application/pdf') != count:
        raise ValueError('source_page_count_changed')
    renderer = shutil.which('pdftoppm')
    if renderer is None:
        raise FileNotFoundError('renderer_unavailable')
    version = subprocess.run([renderer, '-v'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             timeout=2, check=True, env=child_environment()).stderr.decode('utf-8', errors='replace').splitlines()[0][:160]
    profile = {**_fixed_profile(), 'rasterizer': version, 'pdf_writer': str(reportlab.Version),
               'image_library': str(PIL.__version__)}
    # Parent-owned scratch survives a killed child's context managers and is
    # removed by the parent after the complete process group has been reaped.
    with tempfile.TemporaryDirectory(prefix='pages-', dir=output.parent) as temp, pdfplumber.open(io.BytesIO(data)) as source:
        directory = Path(temp)
        path = directory / 'source.pdf'
        path.write_bytes(data)
        dimensions = [_geometry(source.pages[index]) for index in range(start - 1, end)]
        estimated = sum(math.ceil(width / max(width, height) * MAX_EDGE)
                        * math.ceil(height / max(width, height) * MAX_EDGE) for width, height in dimensions)
        if estimated > MAX_TOTAL_PIXELS:
            raise ValueError('aggregate_pixel_limit')
        total_pixels = 0
        with output.open('wb') as target:
            pdf = canvas.Canvas(_BoundedWriter(target), invariant=1, pageCompression=1)
            pdf.setTitle('Visual extract'); pdf.setAuthor(''); pdf.setSubject('Reviewed page appearance required')
            pdf.setCreator('Hausverwaltung ' + VERSION)
            for number, (width, height) in zip(range(start, end + 1), dimensions, strict=True):
                stem = directory / 'page'
                subprocess.run([renderer, '-f', str(number), '-l', str(number), '-singlefile',
                                '-cropbox', '-scale-to', str(MAX_EDGE), '-png', str(path), str(stem)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                               timeout=PAGE_SECONDS, env=child_environment())
                image_path = stem.with_suffix('.png')
                if image_path.stat().st_size > MAX_TEMP_IMAGE_BYTES:
                    raise ValueError('image_size_limit')
                with Image.open(image_path) as rendered:
                    pixels = rendered.width * rendered.height
                    total_pixels += pixels
                    if (rendered.width < 1 or rendered.height < 1 or max(rendered.size) > MAX_EDGE
                            or total_pixels > MAX_TOTAL_PIXELS
                            or abs(rendered.width / rendered.height - width / height) > max(0.01, width / height * 0.01)):
                        raise ValueError('raster_geometry_mismatch')
                    # Fresh pixels remove PNG metadata, profiles and ancillary chunks.
                    rgb = rendered.convert('RGB')
                    clean = Image.frombytes('RGB', rgb.size, rgb.tobytes())
                    pdf.setPageSize((width, height + FOOTER_POINTS))
                    pdf.drawImage(ImageReader(clean), 0, FOOTER_POINTS, width=width, height=height)
                    pdf.setFillColorRGB(0, 0, 0); pdf.setFont('Helvetica', 8)
                    pdf.drawString(5, 6, f'Visual extract - source page {number}')
                    pdf.showPage()
                    clean.close(); rgb.close()
                image_path.unlink()
            pdf.save()
    result = output.read_bytes()
    return {'status': 'succeeded', 'version': VERSION, 'source_sha256': expected_hash, 'page_start': start, 'page_end': end,
            'verified_page_count': count, 'output_page_count': end - start + 1,
            'content_sha256': hashlib.sha256(result).hexdigest(), 'content_byte_size': len(result),
            'renderer_profile': profile}


def run_isolated_visual_extract(data, expected_hash, start, end, count, *, timeout=TOTAL_SECONDS):
    source_hash = hashlib.sha256(data).hexdigest()
    def fail(status, code):
        return VisualExtractResult(status, code, source_hash, start, end, count)
    error = selection_error(data, expected_hash, start, end, count)
    if error:
        return fail('unsupported', error)
    with tempfile.TemporaryDirectory(prefix='hv-visual-extract-') as temp:
        output = Path(temp) / 'extract.pdf'
        process = None
        try:
            process = subprocess.Popen([sys.executable, '-m', 'app.core.visual_extract', expected_hash,
                str(start), str(end), str(count), str(output)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=child_environment(), start_new_session=True,
                cwd=Path(__file__).resolve().parents[2])
            answer, _ = process.communicate(data, timeout=max(0.001, min(timeout, TOTAL_SECONDS)))
            if process.returncode != 0 or len(answer) > 4096:
                return fail('failed', 'renderer_failed')
            result = json.loads(answer)
            if not isinstance(result, dict):
                return fail('failed', 'renderer_failed')
            if result.get('status') == 'failed':
                code = result.get('error_code')
                if set(result) != {'status', 'error_code'} or not isinstance(code, str) or code not in ERROR_CODES:
                    return fail('failed', 'renderer_failed')
                return fail('failed', code)
            size = output.stat().st_size
            if not 0 < size <= MAX_OUTPUT_BYTES:
                return fail('failed', 'output_size_limit')
            pdf_bytes = output.read_bytes()
            expected = {'version': VERSION, 'source_sha256': source_hash, 'page_start': start,
                        'page_end': end, 'verified_page_count': count, 'output_page_count': end - start + 1,
                        'content_sha256': hashlib.sha256(pdf_bytes).hexdigest(), 'content_byte_size': len(pdf_bytes)}
            if not pdf_bytes.startswith(b'%PDF-') or not _valid_success(result, expected):
                return fail('failed', 'renderer_result_mismatch')
            return VisualExtractResult('succeeded', None, source_hash, start, end, count,
                                       pdf_bytes, expected['content_sha256'], len(pdf_bytes),
                                       end - start + 1, result['renderer_profile'])
        except subprocess.TimeoutExpired:
            return fail('timeout', 'render_timeout')
        except (OSError, ValueError, TypeError, AttributeError):
            return fail('failed', 'renderer_failed')
        finally:
            # Terminate leftover descendants even if the leader already exited.
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()


if __name__ == '__main__':
    signal.signal(signal.SIGALRM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
    signal.alarm(TOTAL_SECONDS)
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    if sys.platform == 'linux':
        resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
    output = Path(sys.argv[5])
    try:
        result = _render(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1), sys.argv[1],
                         int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), output)
        print(json.dumps(result))
    except Exception as error:
        output.unlink(missing_ok=True)
        code = str(error) if isinstance(error, (ValueError, FileNotFoundError)) and str(error) in ERROR_CODES else 'renderer_failed'
        print(json.dumps({'status': 'failed', 'error_code': code}))
