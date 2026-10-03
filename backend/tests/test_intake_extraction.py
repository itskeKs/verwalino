"""Bounded logical-source extraction; synthetic documents, no providers."""
import copy
import io
import xml.etree.ElementTree as ET

import pytest
from reportlab.pdfgen import canvas

from app.core.intake_extraction import extract_logical, run_isolated_extraction, valid_result, valid_locators


def document(*page_lines):
    stream=io.BytesIO(); pdf=canvas.Canvas(stream)
    for lines in page_lines:
        for index,line in enumerate(lines):pdf.drawString(60,750-index*35,line)
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


LINES=['Stadtwerke Test GmbH','Rechnungsdatum: 01.06.2025','Gesamtbetrag: 119,00 EUR']


def test_selected_page_has_original_number_and_no_other_page_values():
    data=document(['Other original GmbH','Rechnungsdatum: 01.01.2024','Gesamtbetrag: 999,00 EUR'],LINES)
    result=extract_logical(data,'application/pdf',2,2,2)
    assert valid_result(result,data,'application/pdf',2,2,2)
    assert result['payload']['gross_amount']=='119.00'
    assert result['payload']['source_pages']==[2]
    assert '999' not in result['payload']['raw_text']
    assert result['evidence']['gross_amount']=={'locator_status':'page_quote','page_number':2,
        'quotation':'Gesamtbetrag: 119,00 EUR','xml_path':None,'method':'text_layer'}


def test_repeated_support_remains_unlocated():
    data=document(LINES,LINES)
    result=extract_logical(data,'application/pdf',1,2,2)
    assert result['evidence']['gross_amount']['locator_status']=='unlocated'
    assert result['evidence']['gross_amount']['page_number'] is None
    assert result['evidence']['gross_amount']['quotation'] is None


def test_xml_locators_resolve_exact_original_elements():
    from tests.test_m5_einvoice import UBL,CII
    for data in (UBL,CII):
        result=extract_logical(data,'application/xml',None,None,None)
        assert valid_result(result,data,'application/xml',None,None,None),result
        assert result['status']=='succeeded'
        root=ET.fromstring(data)
        for field,locator in result['evidence'].items():
            assert locator['locator_status']=='xml_path',field
            assert root.find(locator['xml_path']).text.strip()==locator['quotation']
            assert locator['page_number'] is None


def test_xml_semantic_check_rejects_invented_path_or_quote():
    from tests.test_m5_einvoice import UBL
    original=extract_logical(UBL,'application/xml',None,None,None)
    assert valid_locators(original,UBL)
    for mutation in ({'xml_path':'./Missing[1]'},{'quotation':'Invented quote'}):
        result=copy.deepcopy(original)
        result['evidence']['gross_amount'].update(mutation)
        assert not valid_locators(result,UBL)


def test_parent_validation_does_not_parse_source_or_reproduce_heuristics(monkeypatch):
    from tests.test_m5_einvoice import UBL
    data=document(LINES)
    pdf_result=extract_logical(data,'application/pdf',1,1,1)
    xml_result=extract_logical(UBL,'application/xml',None,None,None)
    def forbidden(*args,**kwargs):raise AssertionError('Semantic work must stay in isolated child')
    monkeypatch.setattr('app.core.intake_extraction.text_evidence',forbidden)
    monkeypatch.setattr('app.core.intake_extraction.xml_evidence',forbidden)
    monkeypatch.setattr('app.core.intake_extraction.ET.fromstring',forbidden)
    assert valid_result(pdf_result,data,'application/pdf',1,1,1)
    assert valid_result(xml_result,UBL,'application/xml',None,None,None)


@pytest.mark.parametrize('start,end,count',[(0,1,2),(1,3,2),(2,1,2),(1,1,1),(True,1,2),(None,None,2)])
def test_invalid_or_stale_page_selection_is_rejected(start,end,count):
    result=extract_logical(document(LINES,LINES),'application/pdf',start,end,count)
    assert result['status']=='unsupported' and result['payload'] is None
    assert result['evidence']=={}


def test_xml_rejects_entities_and_page_selection():
    malicious=b'<!DOCTYPE Invoice [<!ENTITY s "secret">]><Invoice><ID>&s;</ID></Invoice>'
    assert extract_logical(malicious,'application/xml',None,None,None)['payload'] is None
    from tests.test_m5_einvoice import UBL
    assert extract_logical(UBL,'application/xml',1,1,1)['payload'] is None


def test_ocr_uses_original_page_and_discards_partial_failure(monkeypatch):
    data=document(LINES,[''],[''])
    calls=[]
    def ocr(path,page,directory):
        calls.append(page)
        if page==3:raise FileNotFoundError('unavailable')
        return '\n'.join(LINES)
    monkeypatch.setattr('app.core.intake_extraction.ocr_page',ocr)
    result=extract_logical(data,'application/pdf',2,3,3)
    assert calls==[2,3]
    assert result['status']=='unavailable' and result['payload'] is None and result['evidence']=={}


def test_isolated_processor_and_deadline():
    data=document(LINES)
    result=run_isolated_extraction(data,'application/pdf',1,1,1)
    assert valid_result(result,data,'application/pdf',1,1,1)
    assert result['status']=='succeeded'
    result=run_isolated_extraction(data,'application/pdf',1,1,1,timeout=.001)
    assert result['status']=='timeout' and result['payload'] is None


def test_real_image_original_ocr():
    from PIL import Image,ImageDraw,ImageFont
    from pathlib import Path
    import reportlab
    image=Image.new('RGB',(1654,2339),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf'),36)
    for index,line in enumerate(LINES):draw.text((100,150+index*90),line,fill='black',font=font)
    stream=io.BytesIO();image.save(stream,format='PNG');data=stream.getvalue()
    result=run_isolated_extraction(data,'image/png',1,1,1)
    assert result['status']=='succeeded',result
    assert result['payload']['gross_amount']=='119.00'
    assert result['evidence']['gross_amount']['method']=='ocr'
    assert result['evidence']['gross_amount']['page_number']==1


def test_child_environment_does_not_inherit_credentials(monkeypatch):
    import subprocess
    original=subprocess.Popen;captured=[]
    def launch(*args,**kwargs):
        captured.append(kwargs['env'])
        return original(*args,**kwargs)
    monkeypatch.setenv('USER','synthetic-user')
    monkeypatch.setenv('HV_OPENAI_API_KEY','synthetic-not-a-key')
    monkeypatch.setenv('PYTHONPATH','/synthetic/untrusted/modules')
    monkeypatch.setattr('app.core.intake_extraction.subprocess.Popen',launch)
    assert run_isolated_extraction(document(LINES),'application/pdf',1,1,1)['status']=='succeeded'
    assert set(captured[0])<= {'PATH','LANG','LC_ALL','TESSDATA_PREFIX','TMPDIR'}


def test_launch_failure_returns_typed_result(monkeypatch):
    def fail(*args,**kwargs):raise OSError('synthetic launch failure')
    monkeypatch.setattr('app.core.intake_extraction.subprocess.Popen',fail)
    result=run_isolated_extraction(document(LINES),'application/pdf',1,1,1)
    assert result['status']=='failed' and result['error_code']=='processor_failed'


def test_timeout_exit_race_still_reaps_child(monkeypatch):
    import subprocess
    class Process:
        pid=999999
        calls=0
        def communicate(self,*args,**kwargs):
            self.calls+=1
            if self.calls==1:raise subprocess.TimeoutExpired('synthetic',1)
            return b'',None
    child=Process()
    monkeypatch.setattr('app.core.intake_extraction.subprocess.Popen',lambda *args,**kwargs:child)
    def already_exited(*args):raise ProcessLookupError('already exited')
    monkeypatch.setattr('app.core.intake_extraction.os.killpg',already_exited)
    result=run_isolated_extraction(document(LINES),'application/pdf',1,1,1)
    assert result['status']=='timeout' and child.calls==2


@pytest.mark.parametrize('mutation',[
    lambda r:r['evidence']['gross_amount'].update(page_number=2),
    lambda r:r['evidence']['gross_amount'].update(quotation='Invented quotation'),
    lambda r:r['evidence']['gross_amount'].update(xml_path='./ID'),
    lambda r:r.update(content_sha256='0'*64),
    lambda r:r['payload'].update(source_pages=[2]),
    lambda r:r['payload'].update(confidence=10**1000),
    lambda r:r['evidence']['gross_amount'].update(quotation='\ud800'),
])
def test_malformed_ipc_is_rejected(mutation):
    data=document(LINES)
    result=copy.deepcopy(extract_logical(data,'application/pdf',1,1,1))
    mutation(result)
    assert not valid_result(result,data,'application/pdf',1,1,1)
