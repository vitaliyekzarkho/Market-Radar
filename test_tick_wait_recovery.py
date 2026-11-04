import unittest
from unittest.mock import patch,Mock
from types import SimpleNamespace
import clock_capture_adapter as c
import run_radar as r
class TickWaitTests(unittest.TestCase):
 def capture(self,sequence,**kwargs):
  ticks=iter(sequence);last=[None];calls=[]
  def tick(symbol):
   calls.append(symbol)
   try:last[0]=next(ticks)
   except StopIteration:pass
   v=last[0];return None if v is None else SimpleNamespace(time=v//1000,time_msc=v)
  mt=SimpleNamespace(symbol_info_tick=tick,copy_rates_from_pos=Mock(return_value=None))
  with patch.object(c.time,'sleep'),patch.object(c.time,'monotonic',return_value=0):
   result=c.collect(mt,{},lambda:[],max_wait_seconds=1,poll_seconds=.25,**kwargs)
  return result,calls
 def test_delayed_ticks_preserve_all_polls_and_three_distinct_samples(self):
  value,calls=self.capture([1000,1000,2000,2000,3000])
  self.assertEqual(len(value['samples']),3);self.assertEqual(len(calls),5)
  self.assertEqual([s['measurements'][0]['value']['time_msc'] for s in value['samples']],[1000,2000,3000])
  self.assertEqual(len(value['tick_poll_observations']),5)
 def test_frozen_feed_attempts_are_bounded(self):
  value,calls=self.capture([1000]);self.assertEqual(len(calls),5);self.assertEqual(len(value['samples']),1)
 def test_missing_ticks_never_fabricated(self):
  value,calls=self.capture([None]);self.assertEqual(len(calls),5);self.assertEqual(value['samples'],[])
 def test_reverse_tick_is_rejected_even_if_seen_before(self):
  for sequence in ([2000,1000,3000],[1000,2000,1000,3000]):
   with self.assertRaisesRegex(ValueError,'TICK_REVERSED'):self.capture(sequence)
 def test_monotonic_deadline_wins(self):
  mt=SimpleNamespace(symbol_info_tick=Mock())
  with patch.object(c.time,'monotonic',side_effect=[0,16]):v=c.collect(mt,{},lambda:[])
  mt.symbol_info_tick.assert_not_called();self.assertEqual(v['samples'],[])
 def test_no_other_authority_failure_is_retryable(self):
  h={'status':'BLOCKED','step':'integrated_admission','error':'ValueError: FRESH_TICKS_REQUIRED'}
  self.assertTrue(r.retryable_observation_gap(h))
  for e in ['ValueError: TICK_REVERSED','ValueError: CLOCK_CHANGED','ValueError: SOURCE_ORDER_INVALID']:
   self.assertFalse(r.retryable_observation_gap(dict(h,error=e)))
 def test_actual_runner_waits_without_restarting_or_reaching_transport(self):
  args=SimpleNamespace(interval=60,once=False,all_hours=False,verbose=False,full=False,no_telegram=False)
  h={'status':'BLOCKED','step':'integrated_admission','error':'ValueError: FRESH_TICKS_REQUIRED'}
  with patch.object(r,'parse_args',return_value=args),patch.object(r,'validate_project',return_value=[]),patch.object(r,'inside_active_window',return_value=True),patch.object(r,'run_pipeline',return_value=False) as run,patch.object(r,'load_json_file',return_value=h),patch.object(r,'atomic_write_json') as write,patch.object(r.time,'sleep',side_effect=KeyboardInterrupt) as sleep:
   self.assertEqual(r.main(),0)
  run.assert_called_once();sleep.assert_called_once_with(60)
  self.assertEqual(write.call_args.args[1]['status'],'WAITING_FOR_FRESH_TICKS')
 def test_once_still_fails_without_false_success(self):
  args=SimpleNamespace(interval=60,once=True,all_hours=False,verbose=False,full=False,no_telegram=True)
  h={'status':'BLOCKED','step':'integrated_admission','error':'ValueError: FRESH_TICKS_REQUIRED'}
  with patch.object(r,'parse_args',return_value=args),patch.object(r,'validate_project',return_value=[]),patch.object(r,'run_pipeline',return_value=False),patch.object(r,'load_json_file',return_value=h):self.assertEqual(r.main(),2)
if __name__=='__main__':unittest.main()
