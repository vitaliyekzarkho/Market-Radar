import unittest
from dataclasses import replace
from recovery_fixtures import case,raw
from recorded_context import AuthorityContext
from recorded_authority import Rejected

class RecoveryA(unittest.TestCase):
    def test_valid_test_context(self):
        e,a,c=case(); r=c.assess(raw(e),a); self.assertFalse(r['historical_claims_allowed']); self.assertEqual(r['authority_class'],'TEST_AUTHORITY')
    def test_no_promotion(self):
        e,a,c=case()
        with self.assertRaises(Rejected): AuthorityContext('CAPTURED_AUTHORITY',c.trust).assess(raw(e),a)
    def test_missing_policy_role(self):
        e,a,c=case(); p=c.policy(); del p['inputs']['yields']
        with self.assertRaises(Rejected): AuthorityContext(c.mode,replace(c.trust,policy_bytes=raw(p))).policy()
    def test_snapshot_not_proof(self):
        e,a,c=case(); a.pop('test:ready'); r=c.assess(raw(e),a)
        self.assertEqual(r['observations'][0]['authority']['available_at_utc'],'UNVERIFIED')
    def test_huge_uncertainty(self):
        e,a,c=case(); e['observations'][0]['available_at_utc']['uncertainty_seconds']=1e99
        with self.assertRaises(Rejected): c.assess(raw(e),a)
    def test_context_fingerprint_stable(self):
        e,a,c=case(); self.assertEqual(c.fingerprint(),c.fingerprint())
