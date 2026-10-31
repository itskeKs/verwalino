"""Quarantine admission never confuses unavailable inspection with a clean file."""
import io
import subprocess
import pytest
from tests.test_invoice_extraction_worker import pdf_bytes
from app.core import intake_inspection as inspection


@pytest.mark.parametrize('result,expected', [('clean','clean'),('detected','rejected'),('unavailable','blocked'),('timeout','blocked'),('failed','blocked')])
def test_scanner_outcomes_precede_parsing(monkeypatch,result,expected):
    monkeypatch.setattr(inspection,'scan_malware',lambda _:result)
    if result!='clean':
        monkeypatch.setattr(inspection,'inspect_structure',lambda *a:pytest.fail('Quarantined bytes reached parser'))
    answer=inspection.inspect_document(pdf_bytes(),'application/pdf')
    assert answer['status']==expected
    assert (answer['page_count']==1) is (result=='clean')
    assert len(answer['content_sha256'])==64


@pytest.mark.parametrize('data,mime,code',[(b'MZbad','application/pdf','mime_mismatch'),(b'','application/pdf','empty_file'),(b'x'*(25*1024*1024+1),'application/pdf','file_size_limit')],ids=['signature','empty','size'])
def test_basic_limits_precede_scanning(monkeypatch,data,mime,code):
    monkeypatch.setattr(inspection,'scan_malware',lambda _:pytest.fail('Invalid envelope reached scanner'))
    assert inspection.inspect_document(data,mime)['error_code']==code


@pytest.mark.parametrize('data,code',[(b'%PDF-broken','invalid_document'),(pdf_bytes(encrypted=True),'encrypted_pdf'),(pdf_bytes(201),'pdf_page_limit')],ids=['corrupt','encrypted','pages'])
def test_pdf_bounds(monkeypatch,data,code):
    monkeypatch.setattr(inspection,'scan_malware',lambda _:'clean')
    assert inspection.inspect_document(data,'application/pdf')['error_code']==code


def test_xml_and_image_validation_without_network_or_render(monkeypatch):
    from PIL import Image
    monkeypatch.setattr(inspection,'scan_malware',lambda _:'clean')
    for data in [b'<invoice><amount>12</amount></invoice>',b'\xef\xbb\xbf<?xml version="1.0"?><invoice/>']:
        result=inspection.inspect_document(data,'application/xml')
        assert result['status']=='clean' and result['page_count'] is None
    for data in [b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',b'<x>'*65+b'</x>'*65,b'<x>',b'<x>'+b'a'*1_000_001+b'</x>']:
        assert inspection.inspect_document(data,'application/xml')['status']=='rejected'
    out=io.BytesIO();Image.new('RGB',(100,100)).save(out,format='PNG')
    result=inspection.inspect_document(out.getvalue(),'image/png')
    assert result['status']=='clean' and result['page_count']==1
    assert inspection.inspect_document(out.getvalue(),'image/jpeg')['error_code']=='mime_mismatch'
    monkeypatch.setattr(inspection,'MAX_PIXELS',9999)
    assert inspection.inspect_document(out.getvalue(),'image/png')['error_code']=='image_pixel_limit'


def test_clamav_failure_and_limits_are_fail_closed(monkeypatch):
    monkeypatch.setattr(inspection.shutil,'which',lambda _:None)
    assert inspection.scan_malware(b'x')=='unavailable'
    monkeypatch.setattr(inspection.shutil,'which',lambda _:'/trusted/clamscan')
    def run(args,**kwargs):
        assert '--alert-exceeds-max=yes' in args and '--alert-encrypted=yes' in args
        assert kwargs['input']==b'x' and kwargs['timeout']<=60
        assert kwargs['stdout']==subprocess.DEVNULL and kwargs['stderr']==subprocess.DEVNULL
        assert 'USER' not in kwargs['env'] and 'HV_OPENAI_API_KEY' not in kwargs['env']
        return subprocess.CompletedProcess(args,1)
    monkeypatch.setattr(inspection.subprocess,'run',run)
    assert inspection.scan_malware(b'x')=='detected'
    monkeypatch.setattr(inspection.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,2))
    assert inspection.scan_malware(b'x')=='failed'
    def timeout(*a,**k):raise subprocess.TimeoutExpired('scanner',60)
    monkeypatch.setattr(inspection.subprocess,'run',timeout)
    assert inspection.scan_malware(b'x')=='timeout'


def test_scanner_readiness_verifies_official_archives_without_loading_the_engine(monkeypatch,tmp_path):
    monkeypatch.setenv('HV_CLAMAV_DATABASE_DIR',str(tmp_path))
    for name in ('main.cvd','daily.cld'):(tmp_path/name).write_bytes(b'synthetic archive')
    monkeypatch.setattr(inspection.shutil,'which',lambda name:'/trusted/'+name)
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        assert kwargs['check'] and kwargs['timeout']<=10
        assert 'USER' not in kwargs['env']
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(inspection.subprocess,'run',run)
    inspection.check_scanner_readiness()
    assert calls==[['/trusted/clamscan','--version'],
                  ['/trusted/sigtool','--quiet','--info='+str(tmp_path/'main.cvd')],
                  ['/trusted/sigtool','--quiet','--info='+str(tmp_path/'daily.cld')]]
    def invalid(*args,**kwargs):raise subprocess.CalledProcessError(2,args[0])
    monkeypatch.setattr(inspection.subprocess,'run',invalid)
    with pytest.raises(subprocess.CalledProcessError):inspection.check_scanner_readiness()


def test_scanner_readiness_rejects_missing_signatures_before_any_job(monkeypatch,tmp_path):
    monkeypatch.setenv('HV_CLAMAV_DATABASE_DIR',str(tmp_path))
    monkeypatch.setattr(inspection.shutil,'which',lambda name:'/trusted/'+name)
    monkeypatch.setattr(inspection.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,0))
    with pytest.raises(RuntimeError,match='scanner_database_unavailable'):inspection.check_scanner_readiness()


def test_isolated_inspection_has_explicit_missing_scanner_outcome(monkeypatch):
    monkeypatch.setenv('PATH','/nonexistent-hv-inspection-fixture')
    result=inspection.run_isolated_inspection(pdf_bytes(),'application/pdf')
    assert result['status']=='blocked' and result['error_code']=='scanner_unavailable'
    assert result['page_count'] is None
    result=inspection.run_isolated_inspection(pdf_bytes(),'application/pdf',timeout=0.001)
    assert result['status']=='blocked' and result['error_code']=='inspection_timeout'


def test_isolated_clean_path_with_explicit_scanner_stub(tmp_path,monkeypatch):
    """A protocol stub proves process wiring, not actual malware detection."""
    import hashlib
    import sys
    data=pdf_bytes(2)
    scanner=tmp_path/'clamscan'
    scanner.write_text(f'''#!{sys.executable}
import hashlib,os,sys
assert 'USER' not in os.environ and 'HV_OPENAI_API_KEY' not in os.environ
assert '--alert-exceeds-max=yes' in sys.argv
sys.exit(0 if hashlib.sha256(sys.stdin.buffer.read()).hexdigest()=={hashlib.sha256(data).hexdigest()!r} else 2)
''')
    scanner.chmod(0o700)
    monkeypatch.setenv('PATH',str(tmp_path))
    monkeypatch.setenv('HV_OPENAI_API_KEY','synthetic-do-not-forward')
    answer=inspection.run_isolated_inspection(data,'application/pdf')
    assert answer['status']=='clean' and answer['page_count']==2
    assert answer['content_sha256']==hashlib.sha256(data).hexdigest()


def test_truncated_jpeg_cannot_be_admitted(monkeypatch):
    from PIL import Image
    monkeypatch.setattr(inspection,'scan_malware',lambda _:'clean')
    out=io.BytesIO();Image.new('RGB',(100,100),'white').save(out,format='JPEG')
    data=out.getvalue()
    assert inspection.inspect_document(data,'image/jpeg')['status']=='clean'
    assert inspection.inspect_document(data[:-20],'image/jpeg')['error_code']=='invalid_document'


@pytest.mark.parametrize('patch',[{'status':[]},{'mime_type':None},{'mime_type':[]},{'page_count':None},{'page_count':True},{'page_count':201},{'page_count':0},{'error_code':'scanner_failed'},{'content_byte_size':True},{'status':'rejected','error_code':'scanner_unavailable','page_count':None},{'status':'blocked','error_code':'scanner_detected','page_count':None}])
def test_malformed_child_result_is_blocked(monkeypatch,patch):
    import json
    data=pdf_bytes()
    answer={**inspection.result(data,'clean',mime='application/pdf',pages=1),**patch}
    class Child:
        pid=123456
        returncode=0
        def communicate(self,*args,**kwargs):return json.dumps(answer).encode(),None
    monkeypatch.setattr(inspection.subprocess,'Popen',lambda *a,**k:Child())
    monkeypatch.setattr(inspection.os,'killpg',lambda *a:None)
    assert inspection.run_isolated_inspection(data,'application/pdf')['error_code']=='inspector_failed'
    del answer['page_count']
    assert inspection.run_isolated_inspection(data,'application/pdf')['error_code']=='inspector_failed'
