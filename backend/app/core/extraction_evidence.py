"""Validate the private invoice IPC boundary before accepting any proposal."""
from datetime import date
import json
import math
import re
from app.core.extraction import ExtractionResult

FIELD_TYPES={
    'vendor_name':'text','document_number':'text','suggested_category_code':'text',
    'invoice_date':'date','service_period_from':'date','service_period_to':'date',
    'gross_amount':'decimal','vat_amount':'decimal','vat_rate':'decimal',
}
STATUSES={'succeeded','needs_review','unsupported','unavailable','timeout','failed','canceled'}


def valid_invoice_outcome(result):
    try:
        return _valid_invoice_outcome(result)
    except (TypeError,ValueError,OverflowError,UnicodeError,RecursionError):
        # Decoded private IPC is still untrusted input. Validation must never
        # strand a claimed attempt on unusual numeric or Unicode values.
        return False


def _valid_invoice_outcome(result):
    if not isinstance(result,dict) or set(result)!={'status','error_code','payload','processor_version'}:
        return False
    status=result['status'];error=result['error_code'];payload=result['payload']
    if not isinstance(status,str) or status not in STATUSES or result['processor_version']!='invoice-local-v1':
        return False
    if status=='succeeded':
        if error is not None or payload is None:return False
    elif not isinstance(error,str) or re.fullmatch(r'[a-z_]{1,64}',error) is None:
        return False
    if payload is None:return status!='succeeded'
    if status not in {'succeeded','needs_review'} or not isinstance(payload,dict) or set(payload)!=set(ExtractionResult().to_dict())|{'source_pages'}:
        return False
    for field,kind in FIELD_TYPES.items():
        value=payload[field]
        if value is None:continue
        if not isinstance(value,str) or not 1<=len(value)<=4000 or '\x00' in value:return False
        if kind=='decimal' and re.fullmatch(r'-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,6})?',value) is None:return False
        if kind=='date':
            try:
                if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value) is None:return False
                date.fromisoformat(value)
            except ValueError:return False
    def confidence(value):
        return type(value) in (int,float) and math.isfinite(value) and 0<=value<=1
    if not confidence(payload['confidence']) or not isinstance(payload['field_confidences'],dict):return False
    if any(key not in FIELD_TYPES or not confidence(value) for key,value in payload['field_confidences'].items()):return False
    if type(payload['is_einvoice']) is not bool:return False
    for key,limit in [('raw_text',1_000_000),('source_format',100)]:
        if payload[key] is not None and (not isinstance(payload[key],str) or len(payload[key])>limit or '\x00' in payload[key]):return False
    pages=payload['source_pages']
    if not isinstance(pages,list) or len(pages)>200 or any(type(page) is not int or not 1<=page<=200 for page in pages):return False
    if pages!=sorted(set(pages)):return False
    return len(json.dumps(result,ensure_ascii=False).encode())<=5*1024*1024
