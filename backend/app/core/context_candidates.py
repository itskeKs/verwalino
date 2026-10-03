"""Bounded, labelled source assertions; every match still requires human review."""
from datetime import date
import re
import unicodedata
import uuid

MAX_SIGNALS=50
MAX_SCOPES=200
LABELS=re.compile(r'^\s*(Objekt[- ]?ID|Zählernummer|Zähler[- ]?Nr\.?|Leistungsort|Objektadresse|Lieferstelle)\s*:\s*(\S.*?)\s*$',re.IGNORECASE)


def normalize_address(value):
    return ' '.join(unicodedata.normalize('NFKC',value).casefold().replace(',',' ').split())


def source_signals(pages):
    if sum(len(page['text'].encode('utf-8')) for page in pages)>1_000_000:return None
    signals=[]
    for page in pages:
        for line in page['text'].splitlines():
            if len(line)>1000:
                if LABELS.match(line):return None
                continue
            matched=LABELS.fullmatch(line)
            if not matched:continue
            label,value=matched.groups()
            kind='property_id' if label.casefold().startswith('objekt') and 'id' in label.casefold() else 'meter_number' if label.casefold().startswith('zähler') else 'service_address'
            if kind=='property_id':
                try:value=str(uuid.UUID(value))
                except ValueError:continue
            elif kind=='meter_number' and not 1<=len(value)<=100:continue
            elif kind=='service_address':value=normalize_address(value)
            signals.append({'kind':kind,'value':value,'page_number':page['number'],'quotation':line,'method':page['method']})
            if len(signals)>MAX_SIGNALS:return None
    return signals


def candidate_matches(scopes,meters,signals,payload,hint=None):
    rows=[]
    try:
        start=date.fromisoformat(payload['service_period_from']);end=date.fromisoformat(payload['service_period_to'])
        if start>end:raise ValueError()
    except (KeyError,TypeError,ValueError):start=end=None
    for scope in scopes:
        property_id=str(scope['property_id']);evidence=[]
        address=normalize_address(f"{scope['street']} {scope['zip_code']} {scope['city']}") if all(scope[key].strip() for key in ('street','zip_code','city')) else None
        numbers={meter['meter_number'] for meter in meters if str(meter['property_id'])==property_id}
        for signal in signals:
            if ((signal['kind']=='property_id' and signal['value']==property_id)
                or (signal['kind']=='meter_number' and signal['value'] in numbers)
                or (signal['kind']=='service_address' and address and signal['value']==address)):
                evidence.append(signal)
        if not evidence:continue
        exclusions=[]
        if scope['scope_type'] not in ('WEG','MIET') or not scope['whole_property']:exclusions.append('unsupported_scope')
        if start is None:exclusions.append('missing_period')
        elif start<scope['valid_from'] or scope['valid_to'] and end>scope['valid_to']:exclusions.append('period_outside_scope')
        if hint and str(scope['id'])!=str(hint):exclusions.append('hint_disagrees')
        rows.append({'scope_id':str(scope['id']),'scope_label':scope['label'],'scope_type':scope['scope_type'],
          'property_id':property_id,'property_name':scope['property_name'],'evidence':evidence,'exclusions':exclusions,
          'requires_manual_review':True,'meter_period_unverified':any(item['kind']=='meter_number' for item in evidence)})
    conflicting=len({row['property_id'] for row in rows})>1
    # Rank deterministic evidence types, never numerical model confidence.
    weights={'property_id':0,'meter_number':1,'service_address':2}
    rows.sort(key=lambda row:(min(weights[item['kind']] for item in row['evidence']),row['scope_label'],row['scope_id']))
    return rows,conflicting
