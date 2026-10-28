"""Explicit authority domain and pinned assessment policy for recorded mode."""
from dataclasses import dataclass
from copy import deepcopy
import json
from recorded_authority import verify, decode, sha, Rejected, interval

ROLES = frozenset({'technical','news','usd','yields','volume'})
MODES = {'TEST_AUTHORITY':'test:', 'CAPTURED_AUTHORITY':'captured:'}

def canonical(v):
    return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)

@dataclass(frozen=True)
class AuthorityContext:
    mode: str
    trust: object

    def policy(self):
        p=decode(self.trust.policy_bytes)
        if self.mode not in MODES or p.get('authority_class') != self.mode:
            raise Rejected('AUTHORITY_CLASS_MISMATCH')
        prefix=MODES[self.mode]
        if not self.trust.admission_reference or not self.trust.authority_ids or not self.trust.source_ids:
            raise Rejected('EXPLICIT_TRUST_ADMISSION_REQUIRED')
        if any(not x.startswith(prefix) for x in (*self.trust.authority_ids,*self.trust.source_ids)):
            raise Rejected('AUTHORITY_PROMOTION_FORBIDDEN')
        if p.get('classification')!='recorded_offline' or p.get('conflict_behavior')!='WAIT' or p.get('missing_behavior')!='DEFERRED':
            raise Rejected('RECORDED_POLICY_REQUIRED')
        if p.get('transport_enabled') is not False or p.get('trading_enabled') is not False:
            raise Rejected('OFFLINE_ONLY')
        if type(p.get('require_entry_permission')) is not bool:
            raise Rejected('PERMISSION_POLICY_REQUIRED')
        if type(p.get('max_clock_uncertainty_seconds')) not in (int,float) or not 0 <= p['max_clock_uncertainty_seconds'] <= 86400:
            raise Rejected('CLOCK_POLICY_INVALID')
        if set(p.get('inputs',{})) != ROLES: raise Rejected('ROLE_COVERAGE_REQUIRED')
        for r,rule in p['inputs'].items():
            if (set(rule)!={'required','max_age_seconds','age_anchor','source_ids'} or type(rule['required']) is not bool
                or type(rule['max_age_seconds']) not in (int,float) or not 0 <= rule['max_age_seconds'] <= 315576000
                or rule['age_anchor'] not in {'market_event_at_utc','captured_at_utc','available_at_utc'}
                or not isinstance(rule['source_ids'],list) or not rule['source_ids']
                or not set(rule['source_ids']) <= self.trust.source_ids):
                raise Rejected('ROLE_POLICY_INVALID')
        return deepcopy(p)

    def fingerprint(self):
        self.policy()
        return sha(canonical({'mode':self.mode,'policy':sha(self.trust.policy_bytes),
            'authorities':sorted(self.trust.authority_ids),'sources':sorted(self.trust.source_ids),
            'pins':sorted(self.trust.audited_receipt_hashes),'admission':self.trust.admission_reference}).encode())

    def assess(self,raw,artifacts):
        self.policy()
        env=decode(raw); prefix=MODES[self.mode]
        if not isinstance(env,dict): raise Rejected('ENVELOPE_REQUIRED')
        if not str(env.get('dataset_id','')).startswith(prefix): raise Rejected('AUTHORITY_PROMOTION_FORBIDDEN')
        for r in env.get('receipts',[]):
            if not isinstance(r,dict) or not str(r.get('authority_id','')).startswith(prefix):
                raise Rejected('AUTHORITY_PROMOTION_FORBIDDEN')
        # Bound dates and uncertainty before timedelta arithmetic, including
        # untrusted claims. Strict format/calendar validation remains in R1.
        for o in env.get('observations',[]):
            if not isinstance(o,dict): raise Rejected('OBSERVATION_REQUIRED')
            for k in ('market_event_at_utc','captured_at_utc','available_at_utc','scenario_established_at_utc'):
                t=o.get(k,{})
                if isinstance(t,dict) and t.get('state')=='KNOWN':
                    u=t.get('uncertainty_seconds')
                    if type(u) not in (int,float) or not 0 <= u <= 86400: raise Rejected('CLOCK_BOUND_INVALID')
                    try: interval(t)
                    except (OverflowError,ValueError,KeyError,TypeError) as e: raise Rejected('TIME_INVALID') from e
        result=verify(raw,artifacts=artifacts,trust=self.trust)
        result['authority_class']=self.mode
        result['historical_claims_allowed']=self.mode=='CAPTURED_AUTHORITY'
        return result
