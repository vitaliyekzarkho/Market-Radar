"""R1 only: offline shape/byte/receipt verification; no ledger or transport.

TrustStore is supplied by an independent offline admission process, never by
the envelope. Pinned receipts require prior audit; hashes alone are not trust.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = json.loads((Path(__file__).parent / 'recorded_contract' / 'RECORDED_OFFLINE_SCHEMA.json').read_text('utf-8'))
Draft202012Validator.check_schema(SCHEMA)
FORMATS = FormatChecker()
@FORMATS.checks('date-time', raises=(ValueError, TypeError))
def strict_utc(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z',value):
        return False
    datetime.fromisoformat(value.replace('Z','+00:00'))
    return True
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
FIELDS = {'market_event_at_utc':'source_event', 'captured_at_utc':'capture',
          'available_at_utc':'availability', 'scenario_established_at_utc':'scenario_establishment'}

class Rejected(ValueError):
    pass

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def decode(raw):
    if not isinstance(raw, bytes) or len(raw) > 8_000_000:
        raise Rejected('INPUT_SIZE_OR_TYPE')
    def pairs(items):
        result = {}
        for k,v in items:
            if k in result: raise Rejected('DUPLICATE_JSON_KEY')
            result[k] = v
        return result
    def constant(value):
        raise Rejected('NONFINITE_JSON')
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        def finite(v):
            if isinstance(v,float) and not math.isfinite(v): raise Rejected('NONFINITE_JSON')
            if isinstance(v,dict):
                for x in v.values(): finite(x)
            if isinstance(v,list):
                for x in v: finite(x)
        finite(result)
        return result
    except (ValueError, RecursionError, UnicodeError) as e:
        raise Rejected('INVALID_JSON: '+str(e)) from e

@dataclass(frozen=True)
class TrustStore:
    # Pin exact policy bytes and independently audited receipts. Never derive
    # these entries from claims carried by a received dataset.
    policy_bytes: bytes
    authority_ids: frozenset
    source_ids: frozenset
    audited_receipt_hashes: frozenset
    admission_reference: str

def instant(s):
    return datetime.fromisoformat(s.replace('Z','+00:00'))

def interval(t):
    d = timedelta(seconds=t['uncertainty_seconds'])
    at = instant(t['at_utc'])
    return at-d, at+d

def verify(raw, *, artifacts, trust):
    """Artifacts are caller-staged exact bytes, not paths/URLs to be fetched.

    Returns R1 verification only, never an ELIGIBLE decision. Derived lineage
    is deliberately blocked until R2-R4 are implemented and independently tested.
    """
    env = decode(raw)
    errors = sorted(VALIDATOR.iter_errors(env), key=lambda e:str(list(e.path)))
    if errors: raise Rejected('SCHEMA: '+errors[0].message)
    policy = decode(trust.policy_bytes)
    if (policy.get('classification') != 'recorded_offline'
        or policy.get('contract_version') != env['contract_version']
        or policy.get('policy_id') != env['policy_id']
        or sha(trust.policy_bytes) != env['policy_sha256']
        or policy.get('decision_system_id') != env['decision_system_id']
        or policy.get('transport_enabled') is not False
        or policy.get('trading_enabled') is not False):
        raise Rejected('POLICY_MISMATCH')
    bound = policy.get('max_clock_uncertainty_seconds')
    if type(bound) not in (int,float) or not math.isfinite(bound) or bound < 0:
        raise Rejected('CLOCK_POLICY_INVALID')
    receipts = {}
    for receipt in env['receipts']:
        rid = receipt['receipt_id']
        if rid in receipts: raise Rejected('DUPLICATE_RECEIPT_ID')
        receipts[rid] = receipt
    cutoff = instant(env['historical_cutoff_utc'])
    seen = set()
    findings = []
    for obs in env['observations']:
        oid = obs['observation_id']
        if oid in seen: raise Rejected('DUPLICATE_OBSERVATION_ID')
        seen.add(oid)
        reasons = []
        states = {}
        file = artifacts.get(obs['file_ref'])
        if file is None:
            reasons.append('PAYLOAD_UNAVAILABLE')
        elif not isinstance(file,bytes) or sha(file) != obs['file_sha256']:
            raise Rejected('FILE_HASH_CONFLICT')
        locator = re.fullmatch(r'bytes:(0|[1-9][0-9]*):([1-9][0-9]*)',obs['payload_locator'])
        if not locator: raise Rejected('LOCATOR_INVALID')
        start,length = map(int,locator.groups())
        if file is not None:
            if start+length > len(file): raise Rejected('LOCATOR_RANGE')
            if sha(file[start:start+length]) != obs['payload_sha256']:
                raise Rejected('PAYLOAD_HASH_CONFLICT')
        if obs['source_id'] not in trust.source_ids: reasons.append('SOURCE_UNVERIFIED')
        for field,kind in FIELDS.items():
            claim = obs[field]
            state = 'UNVERIFIED'
            if claim['state'] == 'KNOWN':
                receipt = receipts.get(claim['evidence_ref'])
                if receipt is not None:
                    expected = {'kind':kind,'subject_id':oid,'subject_payload_sha256':obs['payload_sha256'],
                                'source_id':obs['source_id'],'decision_system_id':env['decision_system_id'],
                                'authority_id':claim['authority_id'],'at_utc':claim['at_utc'],
                                'uncertainty_seconds':claim['uncertainty_seconds']}
                    if any(receipt[k] != v for k,v in expected.items()):
                        raise Rejected('RECEIPT_BINDING_CONFLICT')
                    if kind == 'capture' and receipt['receipt_id'] != obs['capture_id']:
                        raise Rejected('CAPTURE_ID_CONFLICT')
                    proof = artifacts.get(receipt['artifact_ref'])
                    if proof is not None:
                        if sha(proof) != receipt['artifact_sha256']:
                            raise Rejected('RECEIPT_HASH_CONFLICT')
                        # Audited receipt artifact must bind the entire receipt
                        # assertion, excluding its own external locator/hash.
                        body = decode(proof)
                        binding = {k:v for k,v in receipt.items() if k not in {'artifact_ref','artifact_sha256'}}
                        if body != binding: raise Rejected('RECEIPT_ARTIFACT_CONFLICT')
                        if (trust.admission_reference and receipt['artifact_sha256'] in trust.audited_receipt_hashes
                            and claim['authority_id'] in trust.authority_ids
                            and claim['uncertainty_seconds'] <= bound):
                            state = 'VERIFIED'
            states[field] = state
        for first,last in [('market_event_at_utc','captured_at_utc'),('captured_at_utc','available_at_utc'),
                           ('available_at_utc','scenario_established_at_utc')]:
            if states[first] == states[last] == 'VERIFIED':
                a,b = interval(obs[first]),interval(obs[last])
                if a[0] > b[1]: raise Rejected('CHRONOLOGY_CONFLICT')
                if a[1] > b[0]: reasons.append('CLOCK_ORDER_UNPROVEN')
        for field in ('market_event_at_utc','captured_at_utc','available_at_utc'):
            if states[field] != 'VERIFIED': reasons.append(field+':UNKNOWN')
        if states['available_at_utc'] == 'VERIFIED' and interval(obs['available_at_utc'])[1] > cutoff:
            reasons.append('AFTER_CUTOFF')
        if obs['kind'] != 'raw': reasons.append('DERIVED_LINEAGE_NOT_IMPLEMENTED')
        if obs['supersedes'] is not None: reasons.append('CORRECTION_SELECTION_REQUIRES_R2')
        findings.append({'observation_id':oid,'authority':states,'status':'VERIFIED' if not reasons else 'UNVERIFIED',
                         'reasons':reasons})
    return {'stage':'R1_ONLY','observations':findings,'notification_eligibility':'NOT_EVALUATED',
            'generation_allowed':False,'delivery_allowed':False}
