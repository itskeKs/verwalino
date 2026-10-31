"""Fail-closed, bounded quarantine inspection; no preview, OCR or provider calls.

Only run_isolated_inspection is intended for worker use. A clean result is an
inspection fact for these exact bytes, never a grant or confirmation decision.
Existing source routes are not switched to this gate until intake integration.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from xml.parsers import expat

from app.core.document_limits import MAX_UPLOAD_BYTES

VERSION = 'intake-inspection-v1'

MAX_BYTES = MAX_UPLOAD_BYTES
MAX_PAGES = 200
MAX_PIXELS = 20_000_000
MAX_TEXT = 1_000_000
TOTAL_SECONDS = 90
SCAN_SECONDS = 60
# Current official signatures need more than 1 GiB; containers reserve 4 GiB.
# https://docs.clamav.net/manual/Installing/Docker.html#memory-ram-requirements
INSPECTOR_MEMORY_BYTES = 3 * 1024**3


def child_environment():
    return {key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL','TMPDIR','HV_CLAMAV_DATABASE_DIR'}}


def scanner_database_dir():
    return Path(os.environ.get('HV_CLAMAV_DATABASE_DIR','/var/lib/clamav'))


def check_scanner_readiness():
    """Verify official archives without loading a second signature engine."""
    scanner,tool=shutil.which('clamscan'),shutil.which('sigtool')
    if not scanner or not tool:raise RuntimeError('scanner_unavailable')
    subprocess.run([scanner,'--version'],check=True,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,timeout=10,env=child_environment())
    directory=scanner_database_dir()
    for name in ('main','daily','bytecode'):
        archives=[directory/(name+suffix) for suffix in ('.cvd','.cld') if (directory/(name+suffix)).is_file()]
        if not archives and name!='bytecode':raise RuntimeError('scanner_database_unavailable')
        for archive in archives:
            subprocess.run([tool,'--quiet','--info='+str(archive)],check=True,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,env=child_environment())


def result(data, status, code=None, *, mime=None, pages=None):
    return {'status':status,'error_code':code,'content_sha256':hashlib.sha256(data).hexdigest(),
            'content_byte_size':len(data),'mime_type':mime,'page_count':pages,
            'inspector_version':VERSION}


def scan_malware(data: bytes) -> str:
    scanner=shutil.which('clamscan')
    if scanner is None:
        return 'unavailable'
    try:
        completed=subprocess.run([scanner,'--database='+str(scanner_database_dir()),'--no-summary','--max-filesize=25M','--max-scansize=100M',
            '--max-recursion=10','--max-files=100','--alert-exceeds-max=yes','--alert-encrypted=yes','-'],
            input=data,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            timeout=SCAN_SECONDS,env=child_environment())
        return {0:'clean',1:'detected'}.get(completed.returncode,'failed')
    except subprocess.TimeoutExpired:
        return 'timeout'
    except OSError:
        return 'unavailable'


def sniff(data: bytes) -> str | None:
    if data.startswith(b'%PDF-'):
        return 'application/pdf'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.removeprefix(b'\xef\xbb\xbf').lstrip().startswith(b'<'):
        return 'application/xml'
    return None


def inspect_structure(data: bytes, mime: str) -> int | None:
    """Called only in the bounded inspector after scanner approval."""
    if mime=='application/pdf':
        import pdfplumber
        from pdfminer.pdfdocument import PDFPasswordIncorrect
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                if pdf.doc.encryption:
                    raise ValueError('encrypted_pdf')
                pages=pdf.pages
                if not pages or len(pages)>MAX_PAGES:
                    raise ValueError('pdf_page_limit')
                for page in pages:
                    # Future 200 dpi previews cannot exceed the same pixel cap.
                    width,height=(math.ceil(float(size)*200/72) for size in (page.width,page.height))
                    if width<=0 or height<=0 or width*height>MAX_PIXELS:
                        raise ValueError('image_pixel_limit')
                return len(pages)
        except Exception as error:
            if isinstance(error,PDFPasswordIncorrect) or isinstance(error.__context__,PDFPasswordIncorrect):
                raise ValueError('encrypted_pdf') from None
            raise
    if mime in {'image/png','image/jpeg'}:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height>MAX_PIXELS or image.width<=0 or image.height<=0:
                raise ValueError('image_pixel_limit')
            if getattr(image,'n_frames',1)!=1:
                raise ValueError('unsupported_image_frames')
            image.verify()
        # JPEG verify() does not decode scan data. Keep truncated-image loading
        # disabled and validate pixels only after enforcing dimensions.
        from PIL import ImageFile
        ImageFile.LOAD_TRUNCATED_IMAGES=False
        with Image.open(io.BytesIO(data)) as image:
            image.load()
        return 1
    if mime=='application/xml':
        parser=expat.ParserCreate()
        depth=elements=characters=0
        def start(*_):
            nonlocal depth,elements
            depth+=1;elements+=1
            if depth>64 or elements>100_000:
                raise ValueError('xml_expansion_limit')
        def end(*_):
            nonlocal depth
            depth-=1
        def chars(value):
            nonlocal characters
            characters+=len(value)
            if characters>MAX_TEXT:
                raise ValueError('xml_expansion_limit')
        def forbidden(*_):
            raise ValueError('xml_declaration_forbidden')
        parser.StartElementHandler=start
        parser.EndElementHandler=end
        parser.CharacterDataHandler=chars
        parser.StartDoctypeDeclHandler=forbidden
        parser.EntityDeclHandler=forbidden
        parser.ExternalEntityRefHandler=forbidden
        parser.Parse(data,True)
        return None
    raise ValueError('unsupported_format')


def inspect_document(data: bytes, declared_mime: str) -> dict:
    if not data:
        return result(data,'rejected','empty_file')
    if len(data)>MAX_BYTES:
        return result(data,'rejected','file_size_limit')
    mime=sniff(data)
    declared=declared_mime.split(';',1)[0].strip().lower()
    declared={'text/xml':'application/xml','application/x-pdf':'application/pdf'}.get(declared,declared)
    if mime is None or mime!=declared:
        return result(data,'rejected','mime_mismatch')
    scan=scan_malware(data)
    if scan!='clean':
        return result(data,'rejected' if scan=='detected' else 'blocked','scanner_'+scan,mime=mime)
    try:
        pages=inspect_structure(data,mime)
    except ImportError:
        return result(data,'blocked','parser_unavailable',mime=mime)
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) else ''
        if code not in {'encrypted_pdf','pdf_page_limit','image_pixel_limit','unsupported_image_frames','xml_expansion_limit','xml_declaration_forbidden'}:
            code='invalid_document'
        return result(data,'rejected',code,mime=mime)
    return result(data,'clean',mime=mime,pages=pages)


def valid_result(answer, data: bytes, declared_mime: str) -> bool:
    keys={'status','error_code','content_sha256','content_byte_size','mime_type','page_count','inspector_version'}
    if not isinstance(answer,dict) or set(answer)!=keys:
        return False
    state=answer['status'];mime=answer['mime_type'];pages=answer['page_count'];code=answer['error_code']
    if not isinstance(state,str) or state not in {'clean','blocked','rejected'}:
        return False
    if answer['content_sha256']!=hashlib.sha256(data).hexdigest() or type(answer['content_byte_size']) is not int or answer['content_byte_size']!=len(data) or answer['inspector_version']!=VERSION:
        return False
    if mime is not None and (not isinstance(mime,str) or mime not in {'application/pdf','application/xml','image/png','image/jpeg'}):
        return False
    if state!='clean':
        return pages is None and isinstance(code,str) and state==('blocked' if code in {'scanner_failed','scanner_timeout','scanner_unavailable','parser_unavailable','inspector_failed','inspection_timeout'} else 'rejected') and code in {
            'empty_file','file_size_limit','mime_mismatch','scanner_detected','scanner_failed',
            'scanner_timeout','scanner_unavailable','parser_unavailable','inspector_failed','inspection_timeout','encrypted_pdf',
            'pdf_page_limit','image_pixel_limit','unsupported_image_frames','xml_expansion_limit',
            'xml_declaration_forbidden','invalid_document'}
    declared=declared_mime.split(';',1)[0].strip().lower()
    declared={'text/xml':'application/xml','application/x-pdf':'application/pdf'}.get(declared,declared)
    if code is not None or mime is None or mime!=sniff(data) or mime!=declared:
        return False
    if mime=='application/xml':
        return pages is None
    return type(pages) is int and 1<=pages<=(MAX_PAGES if mime=='application/pdf' else 1)


def run_isolated_inspection(data: bytes, declared_mime: str, *, timeout: float=TOTAL_SECONDS) -> dict:
    if not data or len(data)>MAX_BYTES:
        return result(data,'rejected','empty_file' if not data else 'file_size_limit')
    process=None
    try:
        process=subprocess.Popen([sys.executable,'-m','app.core.intake_inspection',declared_mime],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            start_new_session=True,env=child_environment(),cwd=Path(__file__).resolve().parents[2])
        output,_=process.communicate(data,timeout=max(0.001,min(timeout,TOTAL_SECONDS)))
        if process.returncode!=0:
            return result(data,'blocked','inspector_failed')
        answer=json.loads(output)
        # Reject malformed IPC and results that do not describe the submitted bytes.
        if not valid_result(answer,data,declared_mime):
            return result(data,'blocked','inspector_failed')
        return answer
    except subprocess.TimeoutExpired:
        return result(data,'blocked','inspection_timeout')
    except (OSError,ValueError):
        return result(data,'blocked','inspector_failed')
    finally:
        if process is not None:
            # Also retire scanner descendants when the inspector exits abnormally.
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()


if __name__=='__main__':
    signal.signal(signal.SIGALRM,lambda *_:os.killpg(os.getpgrp(),signal.SIGKILL))
    signal.alarm(TOTAL_SECONDS)
    if sys.platform=='linux':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(INSPECTOR_MEMORY_BYTES,INSPECTOR_MEMORY_BYTES))
    print(json.dumps(inspect_document(sys.stdin.buffer.read(MAX_BYTES+1),sys.argv[1])))
