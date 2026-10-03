"""Local proposals for a verified logical original; no provider or financial writes.

Only run_isolated_extraction is a worker entry point. Page/quote locators identify
unique lines reproducing a heuristic proposal, not human-validated facts. XML
locators use ElementTree paths relative to the exact hashed original root.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from app.core.einvoice import parse_en16931_xml, _amount, _norm_date
from app.core.extraction_evidence import FIELD_TYPES, valid_invoice_outcome
from app.core.intake_inspection import MAX_BYTES, MAX_TEXT, inspect_structure, sniff
from app.core.invoice_ocr import MAX_OCR_PAGES, PAGE_SECONDS, TOTAL_SECONDS, ocr_page
from app.core.pdf_extraction import PdfTextExtractor

VERSION='intake-local-v1'


def outcome(data,mime,start,end,count,status,error=None,payload=None,evidence=None,pages=None):
    return {'processor_version':VERSION,'content_sha256':hashlib.sha256(data).hexdigest(),
        'content_byte_size':len(data),'mime_type':mime,'page_start':start,'page_end':end,
        'verified_page_count':count,'status':status,'error_code':error,'payload':payload,
        'evidence':evidence or {},'pages':pages or []}


def unlocated():
    return {'locator_status':'unlocated','page_number':None,'quotation':None,'xml_path':None,'method':'unlocated'}


def text_evidence(payload,pages):
    candidates={key:[] for key in FIELD_TYPES if payload[key] is not None}
    parser=PdfTextExtractor()
    for page in pages:
        for line in page['text'].splitlines():
            if not line.strip() or len(line)>4000:continue
            reproduced=parser._parse_german_invoice(line).to_dict()
            for key in candidates:
                if reproduced[key]==payload[key]:
                    candidates[key].append({'locator_status':'page_quote','page_number':page['number'],
                        'quotation':line,'xml_path':None,'method':page['method']})
    return {key:matches[0] if len(matches)==1 else unlocated() for key,matches in candidates.items()}


# Paths mirror the structured parser's ordered fallbacks. Evidence is emitted
# only when the selected element normalizes to the actual returned field value.
UBL_PATHS={
    'document_number':['{*}ID'],'invoice_date':['{*}IssueDate'],
    'vendor_name':['.//{*}AccountingSupplierParty/{*}Party/{*}PartyName/{*}Name',
                   './/{*}AccountingSupplierParty/{*}Party/{*}PartyLegalEntity/{*}RegistrationName'],
    'gross_amount':['.//{*}LegalMonetaryTotal/{*}TaxInclusiveAmount','.//{*}LegalMonetaryTotal/{*}PayableAmount'],
    'vat_amount':['.//{*}TaxTotal/{*}TaxAmount'],
    'vat_rate':['.//{*}TaxTotal/{*}TaxSubtotal/{*}TaxCategory/{*}Percent'],
    'service_period_from':['.//{*}InvoicePeriod/{*}StartDate'],
    'service_period_to':['.//{*}InvoicePeriod/{*}EndDate'],
}
CII_PATHS={
    'document_number':['.//{*}ExchangedDocument/{*}ID'],
    'invoice_date':['.//{*}ExchangedDocument/{*}IssueDateTime/{*}DateTimeString'],
    'vendor_name':['.//{*}SellerTradeParty/{*}Name'],
    'gross_amount':['.//{*}SpecifiedTradeSettlementHeaderMonetarySummation/{*}GrandTotalAmount'],
    'vat_amount':['.//{*}SpecifiedTradeSettlementHeaderMonetarySummation/{*}TaxTotalAmount','.//{*}ApplicableTradeTax/{*}CalculatedAmount'],
    'vat_rate':['.//{*}ApplicableTradeTax/{*}RateApplicablePercent'],
    'service_period_from':['.//{*}BillingSpecifiedPeriod/{*}StartDateTime/{*}DateTimeString'],
    'service_period_to':['.//{*}BillingSpecifiedPeriod/{*}EndDateTime/{*}DateTimeString'],
}


def xml_evidence(data,payload):
    root=ET.fromstring(data); paths={root:'.'}
    def visit(parent):
        counts={}
        for child in parent:
            counts[child.tag]=counts.get(child.tag,0)+1
            paths[child]=f'{paths[parent]}/{child.tag}[{counts[child.tag]}]'
            visit(child)
    visit(root)
    schema=UBL_PATHS if payload['source_format']=='XRECHNUNG_UBL' else CII_PATHS
    evidence={}
    for key,kind in FIELD_TYPES.items():
        if payload[key] is None:continue
        evidence[key]=unlocated()
        for pattern in schema.get(key,[]):
            element=root.find(pattern)
            if element is None or not element.text or not element.text.strip():continue
            quote=element.text.strip()
            value=_amount(quote) if kind=='decimal' else _norm_date(quote,element.get('format')) if kind=='date' else quote
            try:resolves=root.find(paths[element]) is element
            except (SyntaxError,KeyError):resolves=False
            if value==payload[key] and resolves and len(quote)<=4000 and len(paths[element])<=16000:
                evidence[key]={'locator_status':'xml_path','page_number':None,'quotation':quote,
                    'xml_path':paths[element],'method':'xml'}
            break
    return evidence


def ocr_image(data,mime,directory):
    executable=shutil.which('tesseract')
    if executable is None:raise FileNotFoundError('ocr_unavailable')
    path=directory/('source.png' if mime=='image/png' else 'source.jpg')
    path.write_bytes(data)
    result=subprocess.run([executable,str(path),'stdout','-l','deu'],stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,check=True,timeout=PAGE_SECONDS)
    return result.stdout.decode('utf-8',errors='replace')


def extract_logical(data,mime,start,end,count):
    def result(status,error=None,payload=None,evidence=None,pages=None):
        return outcome(data,mime,start,end,count,status,error,payload,evidence,pages)
    if not data or len(data)>MAX_BYTES:return result('unsupported','file_size_limit')
    if sniff(data)!=mime:return result('unsupported','mime_mismatch')
    if mime=='application/xml':
        if any(value is not None for value in (start,end,count)):return result('unsupported','invalid_page_selection')
    elif not (all(type(value) is int for value in (start,end,count)) and 1<=start<=end<=count<=200):
        return result('unsupported','invalid_page_selection')
    try:
        # Structural checks are repeated inside isolation. This is not a malware
        # check; the durable adapter must require a prior clean inspection.
        actual_count=inspect_structure(data,mime)
        if actual_count!=count:return result('unsupported','source_page_count_changed')
        if mime=='application/xml':
            parsed=parse_en16931_xml(data)
            if parsed is None:return result('unsupported','unsupported_format')
            payload=parsed.to_dict();payload['source_pages']=[]
            evidence=xml_evidence(data,payload);pages=[]
        else:
            pages=[]
            with tempfile.TemporaryDirectory(prefix='hv-intake-extract-') as temp:
                directory=Path(temp)
                if mime=='application/pdf':
                    import pdfplumber
                    with pdfplumber.open(io.BytesIO(data)) as pdf:
                        for number in range(start,end+1):
                            text=pdf.pages[number-1].extract_text() or ''
                            pages.append({'number':number,'text':text,'method':'text_layer'})
                    missing=[page for page in pages if len(page['text'].strip())<20]
                    if len(missing)>MAX_OCR_PAGES:return result('needs_review','ocr_page_limit')
                    path=directory/'source.pdf'
                    if missing:path.write_bytes(data)
                    for page in missing:
                        page.update(text=ocr_page(path,page['number'],directory),method='ocr')
                else:
                    pages=[{'number':1,'text':ocr_image(data,mime,directory),'method':'ocr'}]
            source_text='\n'.join(page['text'] for page in pages)
            if len(source_text)>MAX_TEXT:return result('needs_review','text_size_limit')
            if any(len(page['text'].strip())<20 for page in pages):return result('needs_review','incomplete_text')
            parsed=PdfTextExtractor()._parse_german_invoice(source_text)
            payload=parsed.to_dict();payload['source_pages']=[page['number'] for page in pages]
            evidence=text_evidence(payload,pages)
        state='succeeded' if payload['gross_amount'] is not None and payload['invoice_date'] is not None else 'needs_review'
        answer=result(state,None if state=='succeeded' else 'missing_fields',payload,evidence,pages)
        if not valid_result(answer,data,mime,start,end,count) or not valid_locators(answer,data):return result('failed','processor_failed')
        return answer
    except (ImportError,FileNotFoundError,subprocess.CalledProcessError):return result('unavailable','ocr_unavailable')
    except subprocess.TimeoutExpired:return result('timeout','ocr_timeout')
    except Exception:return result('unsupported','invalid_document')


def valid_result(answer,data,mime,start,end,count):
    """Bounded parent IPC checks. No document or heuristic parser runs here.

    Content truth is checked in the isolated child by valid_locators; this
    boundary validates its typed result without extending the parsing deadline.
    """
    try:
        expected=outcome(data,mime,start,end,count,'failed','processor_failed')
        if not isinstance(answer,dict) or set(answer)!=set(expected):return False
        for key in ('processor_version','content_sha256','content_byte_size','mime_type','page_start','page_end','verified_page_count'):
            if type(answer[key]) is not type(expected[key]) or answer[key]!=expected[key]:return False
        legacy={key:answer[key] for key in ('status','error_code','payload')}
        legacy['processor_version']='invoice-local-v1'
        if not valid_invoice_outcome(legacy):return False
        payload=answer['payload'];evidence=answer['evidence'];pages=answer['pages']
        if not isinstance(evidence,dict) or not isinstance(pages,list) or len(pages)>200:return False
        if payload is None:return evidence=={} and pages==[]
        wanted=[] if mime=='application/xml' else list(range(start,end+1))
        if payload['source_pages']!=wanted:return False
        if set(evidence)!={key for key in FIELD_TYPES if payload[key] is not None}:return False
        if mime=='application/xml':
            if pages:return False
        else:
            if len(pages)!=len(wanted):return False
            for page,number in zip(pages,wanted):
                if not isinstance(page,dict) or set(page)!={'number','text','method'}:return False
                if type(page['number']) is not int or page['number']!=number:return False
                if not isinstance(page['text'],str) or not 20<=len(page['text'].strip())<=MAX_TEXT or '\x00' in page['text']:return False
                if page['method'] not in ('text_layer','ocr'):return False
            if payload['raw_text']!='\n'.join(page['text'] for page in pages):return False
        for locator in evidence.values():
            if not isinstance(locator,dict) or set(locator)!=set(unlocated()):return False
            if locator['locator_status']=='unlocated':
                if locator!=unlocated():return False
            elif locator['locator_status']=='xml_path':
                if mime!='application/xml' or locator['method']!='xml' or locator['page_number'] is not None:return False
                if not isinstance(locator['xml_path'],str) or not locator['xml_path'].startswith('./') or len(locator['xml_path'])>16000:return False
            elif locator['locator_status']=='page_quote':
                if mime=='application/xml' or type(locator['page_number']) is not int or locator['page_number'] not in wanted or locator['xml_path'] is not None:return False
                page=pages[wanted.index(locator['page_number'])]
                if locator['method']!=page['method'] or locator['quotation'] not in page['text'].splitlines():return False
            else:return False
            if locator['locator_status']!='unlocated' and (not isinstance(locator['quotation'],str) or not 1<=len(locator['quotation'])<=4000):return False
        encoded=json.dumps(answer,ensure_ascii=False).encode('utf-8')
        return len(encoded)<=5*1024*1024 and b'\\u0000' not in encoded
    except (TypeError,ValueError,OverflowError,UnicodeError,RecursionError,KeyError):return False


def valid_locators(answer,data):
    """Semantic evidence check, called only inside processor isolation or tests."""
    try:
        if answer['payload'] is None:return answer['evidence']=={}
        expected=xml_evidence(data,answer['payload']) if answer['mime_type']=='application/xml' else text_evidence(answer['payload'],answer['pages'])
        return answer['evidence']==expected
    except (TypeError,ValueError,KeyError,ET.ParseError,RecursionError):return False


def run_isolated_extraction(data,mime,start,end,count,*,timeout=TOTAL_SECONDS):
    env={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL','TESSDATA_PREFIX','TMPDIR'}}
    process=None
    def failure(status,code):return outcome(data,mime,start,end,count,status,code)
    try:
        process=subprocess.Popen([sys.executable,'-m','app.core.intake_extraction',mime,json.dumps([start,end,count])],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True,
            env=env,cwd=Path(__file__).resolve().parents[2])
        output,_=process.communicate(data,timeout=min(timeout,TOTAL_SECONDS))
        if process.returncode!=0:return failure('failed','processor_failed')
        answer=json.loads(output)
        return answer if valid_result(answer,data,mime,start,end,count) else failure('failed','processor_failed')
    except subprocess.TimeoutExpired:
        return failure('timeout','extraction_timeout')
    except (ValueError,OSError):return failure('failed','processor_failed')
    finally:
        if process is not None:
            # Reap the child and stop any descendants even if IPC/validation
            # fails. Exiting between timeout and kill is an ordinary race.
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.communicate()


if __name__=='__main__':
    signal.signal(signal.SIGALRM,lambda *_:os.killpg(os.getpgrp(),signal.SIGKILL))
    signal.alarm(TOTAL_SECONDS)
    if sys.platform=='linux':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3))
    print(json.dumps(extract_logical(sys.stdin.buffer.read(MAX_BYTES+1),sys.argv[1],*json.loads(sys.argv[2]))))
