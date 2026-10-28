import unittest,json,tempfile,copy
from pathlib import Path
from unittest.mock import patch
from cdr_operational import mapped,safe_observe,observe
ROOT=Path(__file__).resolve().parent/'verification_inputs'

class OperationalAuditTests(unittest.TestCase):
    def setUp(self):
        self.s=json.loads((ROOT/'recorded_data/scenario_states/GOLD_pro.json').read_text('utf-8-sig'))
    def test_mapping_retains_primary(self):
        self.assertEqual(mapped(self.s)['legacy_primary'],self.s['primary_scenario'])
    def test_mapping_no_mutation(self):
        before=copy.deepcopy(self.s);mapped(self.s);self.assertEqual(before,self.s)
    def test_nested_and_alternative_not_dropped(self):
        m=mapped(self.s);self.assertTrue(m['nested']);self.assertTrue(m['alternatives'])
    def test_target_progression_not_identity_change(self):
        old=mapped(self.s)
        self.s['market_state']['primary']['point_b']['price']=3900
        new=mapped(self.s)
        self.assertEqual(old['primary']['id'],new['primary']['id'])
        self.assertNotEqual(old['point_b'],new['point_b'])
    def test_missing_permission_denies_in_audit_only(self):
        self.s['primary_scenario'].pop('permissions',None)
        self.assertFalse(mapped(self.s)['permissions']['entry'])
        self.assertNotIn('permissions',self.s['primary_scenario'])
    def test_error_is_explicit_and_non_throwing(self):
        with tempfile.TemporaryDirectory() as d,patch('cdr_operational.observe',side_effect=ValueError('test')):
            r=safe_observe(Path(d),'scenario','test_cycle')
            self.assertEqual(r['status'],'DEGRADED')
            self.assertTrue((Path(d)/'data/cdr/degraded.json').exists())
    def test_real_snapshot_full_stage_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=r/'data/scenario_states/GOLD_pro.json';p.parent.mkdir(parents=True);p.write_text(json.dumps(self.s),encoding='utf-8')
            first=observe(r,'scenario','test_cycle')
            again=observe(r,'scenario','test_cycle')
            self.assertEqual(first,again)
            self.assertEqual(first['eligibility']['status'],'DEFERRED')
            self.assertFalse(first['eligibility']['trade_authorized'])
            self.assertEqual(first['technical']['legacy_primary'],self.s['primary_scenario'])
    def test_candidate_capture_hash(self):
        from cdr_operational import snapshot,sha
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=r/'data/raw/GOLD_pro/H1.csv';p.parent.mkdir(parents=True);p.write_bytes(b'known bytes')
            s=snapshot(r,r/'data/cdr')
            self.assertEqual(s['raw/GOLD_pro/H1.csv']['sha256'],sha(b'known bytes'))

if __name__=='__main__':unittest.main()
