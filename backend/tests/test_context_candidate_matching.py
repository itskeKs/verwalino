"""A source line is a proposal with a locator, never validated property truth."""
from datetime import date
from app.core.context_candidates import source_signals,candidate_matches

PROPERTY='00000000-0000-4000-8000-000000000001'
SCOPE={'id':'scope','property_id':PROPERTY,'property_name':'Test property','label':'WEG Test','scope_type':'WEG','whole_property':True,
       'street':'Hauptstraße 1','zip_code':'10115','city':'Berlin','valid_from':date(2020,1,1),'valid_to':None}
PERIOD={'service_period_from':'2026-01-01','service_period_to':'2026-01-31'}

def signals(text):return source_signals([{'number':3,'method':'text_layer','text':text}])

def test_only_labelled_source_assertions_with_exact_locators():
    result=signals(f'Supplier GmbH\nHauptstraße 1\n10115 Berlin\nObjekt-ID: {PROPERTY}\nZählernummer: A-123\nLeistungsort: Hauptstrasse 1, 10115 BERLIN')
    assert len(result)==3 and all(s['page_number']==3 for s in result)
    rows,conflict=candidate_matches([SCOPE],[{'property_id':PROPERTY,'meter_number':'A-123'}],result,PERIOD)
    assert not conflict and len(rows)==1 and len(rows[0]['evidence'])==3
    assert rows[0]['requires_manual_review'] and rows[0]['meter_period_unverified']
    assert rows[0]['exclusions']==[]


def test_unlabelled_supplier_or_partial_address_never_matches_and_identifiers_are_exact():
    assert signals('Supplier GmbH\nHauptstraße 1, 10115 Berlin\nRechnungsnummer: A-123')==[]
    result=signals('Zählernummer: a-123\nLieferstelle: Hauptstraße 1\nObjekt-ID: not-a-uuid')
    assert candidate_matches([SCOPE],[{'property_id':PROPERTY,'meter_number':'A-123'}],result,PERIOD)==([],False)


def test_duplicates_and_period_conflicts_stay_visible_for_manual_review():
    second={**SCOPE,'id':'scope-2','property_id':'second','property_name':'Other','valid_from':date(2027,1,1)}
    rows,conflict=candidate_matches([SCOPE,second],[],signals('Objektadresse: Hauptstraße 1, 10115 Berlin'),PERIOD,hint='scope')
    assert conflict and len(rows)==2
    assert rows[1]['exclusions']==['period_outside_scope','hint_disagrees']
    rows,_=candidate_matches([SCOPE],[],signals(f'Objekt-ID: {PROPERTY}'),{})
    assert rows[0]['exclusions']==['missing_period']


def test_bounded_source_signals_never_claim_complete_results():
    assert signals('\n'.join('Zählernummer: A-'+str(n) for n in range(51))) is None
    assert len(signals('\n'.join('Zählernummer: A-'+str(n) for n in range(50))))==50


def test_duplicate_meter_numbers_and_conflicting_property_uuid_preserve_all_alternatives():
    second={**SCOPE,'id':'scope-2','property_id':'second','property_name':'Other'}
    result=signals(f'Objekt-ID: {PROPERTY}\nZählernummer: X-001')
    rows,conflict=candidate_matches([SCOPE,second],[{'property_id':PROPERTY,'meter_number':'X-001'},{'property_id':'second','meter_number':'X-001'}],result,PERIOD)
    assert len(rows)==2 and conflict and len(rows[0]['evidence'])==2 and len(rows[1]['evidence'])==1
    assert all(row['requires_manual_review'] and row['meter_period_unverified'] for row in rows)
