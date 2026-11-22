"""The local stage of the inbox's extraction: structured originals are parsed, the rest is handed to the models.

Synthetic documents, no providers: the stage reads text layers and sizes and never recognizes text.
"""
import copy
import io
import xml.etree.ElementTree as ET

import pytest
from reportlab.pdfgen import canvas

from app.core.intake_extraction import (
    MODEL_VERSION,
    UNSTRUCTURED,
    extract_logical,
    outcome,
    run_isolated_extraction,
    unlocated_evidence,
    valid_inspection,
    valid_locators,
    valid_result,
)


def document(*page_lines):
    stream=io.BytesIO(); pdf=canvas.Canvas(stream)
    for lines in page_lines:
        for index,line in enumerate(lines):pdf.drawString(60,750-index*35,line)
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


LINES=['Stadtwerke Test GmbH','Rechnungsdatum: 01.06.2025','Gesamtbetrag: 119,00 EUR']


def picture(fmt):
    from PIL import Image
    stream=io.BytesIO();Image.new('RGB',(400,500),'white').save(stream,format=fmt)
    return stream.getvalue()


def test_selected_page_has_original_number_and_no_other_page_values():
    data=document(['Other original GmbH','Rechnungsdatum: 01.01.2024','Gesamtbetrag: 999,00 EUR'],LINES)
    result=extract_logical(data,'application/pdf',2,2,2)
    assert result['status']==UNSTRUCTURED and valid_inspection(result,data,'application/pdf',2,2,2)
    assert [page['number'] for page in result['pages']]==[2]
    assert result['pages'][0]['text']=='\n'.join(LINES) and '999' not in str(result)
    assert (result['page_start'],result['page_end'],result['verified_page_count'])==(2,2,2)
    assert not {'payload','evidence','error_code'}&set(result),'a hand-over proposes nothing: the models read the pages'
    assert result['pages'][0]['width']==pytest.approx(595.28,abs=.01) and result['pages'][0]['height']==pytest.approx(841.89,abs=.01)


def test_the_selection_of_several_pages_is_handed_over_in_order_and_only_it():
    data=document(['First page, not selected'],LINES,['Third page text, also selected'],['Fourth page, not selected'])
    result=extract_logical(data,'application/pdf',2,3,4)
    assert [page['number'] for page in result['pages']]==[2,3] and valid_inspection(result,data,'application/pdf',2,3,4)
    assert 'not selected' not in str(result)


def test_an_image_is_handed_over_as_its_one_page_without_a_text_layer():
    for fmt,mime in (('PNG','image/png'),('JPEG','image/jpeg')):
        data=picture(fmt)
        result=extract_logical(data,mime,1,1,1)
        assert result['status']==UNSTRUCTURED and valid_inspection(result,data,mime,1,1,1)
        (page,)=result['pages']
        assert (page['number'],page['text']) == (1,'') and page['width']==pytest.approx(400*72/180) and page['height']==pytest.approx(500*72/180)


def test_a_pdf_with_valid_structured_data_is_parsed_locally_for_the_whole_original_only():
    from tests.test_invoice_extraction_worker import zugferd_pdf
    whole=zugferd_pdf(pages=2)
    result=extract_logical(whole,'application/pdf',1,2,2)
    assert valid_result(result,whole,'application/pdf',1,2,2) and valid_locators(result,whole)
    assert (result['processor_version'],result['status'],result['payload']['source_format'],result['payload']['source_pages'])==('intake-local-v1','succeeded','ZUGFERD',[1,2])
    assert [page['method'] for page in result['pages']]==['text_layer','text_layer']
    assert set(result['evidence'])==set(unlocated_evidence(result['payload'])) and 'gross_amount' in result['evidence']
    assert all(locator['locator_status']=='unlocated' for locator in result['evidence'].values()),'the embedded XML is not the hashed original'
    # The data describes the whole original: a part of it is not a structured invoice, and no model is asked about it.
    part=extract_logical(whole,'application/pdf',2,2,2)
    assert (part['status'],part['error_code'],part['payload'],part['processor_version'])==('needs_review','structured_selection_partial',None,'intake-local-v1')
    assert valid_result(part,whole,'application/pdf',2,2,2)


def test_a_text_layer_beyond_the_bound_of_one_voucher_ends_for_review_before_any_request():
    sheet=io.BytesIO();pdf=canvas.Canvas(sheet);pdf.setFont('Helvetica',6)
    for _ in range(10):
        for row in range(100):pdf.drawString(20,820-row*8,'x'*70)
        pdf.showPage()
    pdf.save();data=sheet.getvalue()
    result=extract_logical(data,'application/pdf',1,10,10)
    assert (result['status'],result['error_code'],result['payload'])==('needs_review','invoice_text_limit',None) and valid_result(result,data,'application/pdf',1,10,10)


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
    from tests.test_invoice_extraction_worker import zugferd_pdf
    data=document(LINES);structured=zugferd_pdf()
    handover=extract_logical(data,'application/pdf',1,1,1)
    pdf_result=extract_logical(structured,'application/pdf',1,1,1)
    xml_result=extract_logical(UBL,'application/xml',None,None,None)
    def forbidden(*args,**kwargs):raise AssertionError('Semantic work must stay in isolated child')
    monkeypatch.setattr('app.core.intake_extraction.unlocated_evidence',forbidden)
    monkeypatch.setattr('app.core.intake_extraction.xml_evidence',forbidden)
    monkeypatch.setattr('app.core.intake_extraction.ET.fromstring',forbidden)
    monkeypatch.setattr('app.core.intake_extraction.EInvoiceExtractor',forbidden)
    assert valid_inspection(handover,data,'application/pdf',1,1,1)
    assert valid_result(pdf_result,structured,'application/pdf',1,1,1)
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


def test_isolated_processor_and_deadline():
    data=document(LINES)
    result=run_isolated_extraction(data,'application/pdf',1,1,1)
    assert valid_inspection(result,data,'application/pdf',1,1,1)
    assert result['status']==UNSTRUCTURED and result['pages'][0]['text']=='\n'.join(LINES)
    result=run_isolated_extraction(data,'application/pdf',1,1,1,timeout=.001)
    assert result['status']=='timeout' and result['payload'] is None


def test_real_image_original_is_inspected_in_the_child_and_handed_over():
    from PIL import Image,ImageDraw
    image=Image.new('RGB',(1654,2339),'white');draw=ImageDraw.Draw(image)
    for index,line in enumerate(LINES):draw.text((100,150+index*90),line,fill='black')
    stream=io.BytesIO();image.save(stream,format='PNG');data=stream.getvalue()
    result=run_isolated_extraction(data,'image/png',1,1,1)
    assert result['status']==UNSTRUCTURED and valid_inspection(result,data,'image/png',1,1,1),result
    assert result['pages']==[{'number':1,'text':'','width':pytest.approx(1654*72/180),'height':pytest.approx(2339*72/180)}]


def test_the_stage_never_runs_a_text_recognition_tool(monkeypatch):
    import sys
    import types
    blocked=types.ModuleType('pytesseract')
    def refuse(name):raise AssertionError('pytesseract.'+name+' was used')
    blocked.__getattr__=refuse
    monkeypatch.setitem(sys.modules,'pytesseract',blocked)
    for data,mime in ((document(['']),'application/pdf'),(picture('PNG'),'image/png')):
        assert extract_logical(data,mime,1,1,1)['status']==UNSTRUCTURED
    source=open(extract_logical.__code__.co_filename,encoding='utf-8').read().lower()
    assert 'tesseract' not in source


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
    assert run_isolated_extraction(document(LINES),'application/pdf',1,1,1)['status']==UNSTRUCTURED
    assert set(captured[0])<= {'PATH','LANG','LC_ALL','TMPDIR'}


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


def model_result(data,mime='application/pdf',start=1,end=1,count=1):
    """A run of the models as the pipeline reports it: model version, the original's page numbers, unlocated evidence."""
    from app.declarations.fake_provider import synthetic_invoice_output
    from app.declarations.invoice_pipeline import _proposal
    numbers=list(range(start,end+1))
    texts=['Seite '+str(number)+' of a synthetic voucher' for number in numbers]
    payload=_proposal(synthetic_invoice_output(),{'GEBAEUDEVERSICHERUNG'},'\n'.join(texts),numbers)['payload']
    stored=[{'number':number,'text':text,'method':'reading_model'} for number,text in zip(numbers,texts)]
    return outcome(data,mime,start,end,count,'succeeded',None,payload,unlocated_evidence(payload),stored,version=MODEL_VERSION)


def test_a_model_result_is_valid_with_its_own_pages_and_only_unlocated_evidence():
    data=document(LINES)
    result=model_result(data)
    assert valid_result(result,data,'application/pdf',1,1,1) and valid_locators(result,data)
    assert result['payload']['vendor_iban']=='DE89370400440532013000'
    assert all(locator['locator_status']=='unlocated' for locator in result['evidence'].values())
    empty=copy.deepcopy(result);empty['pages'][0]['text']='';empty['payload']['raw_text']=''
    assert valid_result(empty,data,'application/pdf',1,1,1),'a blank page reads to nothing'
    from tests.test_m5_einvoice import UBL
    parsed=extract_logical(UBL,'application/xml',None,None,None)
    assert valid_result(parsed,UBL,'application/xml',None,None,None)
    assert not valid_result({**parsed,'processor_version':MODEL_VERSION},UBL,'application/xml',None,None,None),'a model never reads XML'


@pytest.mark.parametrize('mutation',[
    lambda r:r['evidence']['gross_amount'].update(page_number=2),
    lambda r:r['evidence']['gross_amount'].update(locator_status='page_quote',page_number=1,quotation='Seite 1 of a synthetic voucher',method='reading_model'),
    lambda r:r['evidence']['gross_amount'].update(xml_path='./ID'),
    lambda r:r.update(content_sha256='0'*64),
    lambda r:r.update(processor_version='intake-model-v2'),
    lambda r:r.update(processor_version='intake-local-v1'),
    lambda r:r['payload'].update(source_pages=[2]),
    lambda r:r['payload'].update(confidence=10**1000),
    lambda r:r['payload'].update(vendor_iban='DE00370400440532013000'),
    lambda r:r['payload'].update(vendor_iban='de89370400440532013000'),
    lambda r:r['payload']['field_confidences'].update(vendor_iban=7),
    lambda r:r['pages'][0].update(method='ocr'),
    lambda r:r['pages'][0].update(text='\x00'),
    lambda r:r['pages'][0].update(number=2),
    lambda r:r['pages'].append(dict(r['pages'][0])),
    lambda r:r['evidence'].pop('gross_amount'),
])
def test_malformed_ipc_is_rejected(mutation):
    data=document(LINES)
    result=copy.deepcopy(model_result(data))
    mutation(result)
    assert not valid_result(result,data,'application/pdf',1,1,1)


@pytest.mark.parametrize('mutation',[
    lambda r:r['pages'][0].update(number=2),
    lambda r:r['pages'][0].update(width=0),
    lambda r:r['pages'][0].update(height=float('nan')),
    lambda r:r['pages'][0].update(text=None),
    lambda r:r['pages'][0].update(unexpected=1),
    lambda r:r['pages'].append(dict(r['pages'][0])),
    lambda r:r.update(payload=None),
    lambda r:r.update(content_sha256='0'*64),
    lambda r:r.update(page_end=2),
    lambda r:r['pages'][0].update(text='x'*60001),
])
def test_malformed_handover_is_rejected(mutation):
    data=document(LINES)
    result=copy.deepcopy(extract_logical(data,'application/pdf',1,1,1))
    mutation(result)
    assert not valid_inspection(result,data,'application/pdf',1,1,1)


def test_a_handover_of_an_image_carries_no_text_and_xml_has_none():
    data=picture('PNG')
    result=copy.deepcopy(extract_logical(data,'image/png',1,1,1))
    result['pages'][0]['text']='invented text layer'
    assert not valid_inspection(result,data,'image/png',1,1,1)
    from tests.test_m5_einvoice import UBL
    assert not valid_inspection({**result,'pages':[]},UBL,'application/xml',None,None,None)
