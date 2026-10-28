"""Public, synthetic-only development checks. Never starts native/runtime code."""
import sys,unittest,json,tempfile
from pathlib import Path
def guard(event,args):
    if event in {'socket.connect','socket.getaddrinfo'} or (event=='import' and args[0].split('.')[0]=='MetaTrader5'):
        raise PermissionError('PORTABLE_OFFLINE_ONLY')
sys.addaudithook(guard)
modules=['test_live_history_admission','test_news_pa_context_adapter','test_offline_evidence_ledger','test_offline_scenario_ledger','test_offline_notification_audit','test_offline_validation']
if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps(dict(profile='portable-public-synthetic',tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skips=len(result.skipped),python=sys.version,full_private_regression=False)))
    raise SystemExit(not result.wasSuccessful())
