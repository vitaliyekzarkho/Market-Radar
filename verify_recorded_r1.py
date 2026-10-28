"""Bounded R1 tests, network/transport/process forbidden during execution."""
import sys, os, unittest, json, platform
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/'_review_deps'))
sys.dont_write_bytecode=True
blocked=[]
def guard(event,args):
    if event in {'socket.connect','socket.getaddrinfo','subprocess.Popen','os.system'}:
        blocked.append(event); raise PermissionError('R1_EXTERNAL_ACTION_FORBIDDEN')
    if event=='import' and args[0].split('.')[0] in {'MetaTrader5','telegram_notifier','mt5_reader','run_radar','run_cycle'}:
        blocked.append(event); raise ImportError('R1_LIVE_IMPORT_FORBIDDEN')
    if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
        p=os.path.abspath(os.fsdecode(args[0])).replace('/','\\').casefold()
        if p.startswith('c:\\users\\user\\marketradar\\') or Path(p).name.startswith('.env'):
            blocked.append('private_file'); raise PermissionError('R1_PRODUCTION_OR_SECRET_ACCESS_FORBIDDEN')
sys.addaudithook(guard)
out=root/'recorded_verification';out.mkdir(exist_ok=True)
suite=unittest.defaultTestLoader.loadTestsFromName('test_recorded_authority')
with (out/'r1.log').open('w',encoding='utf-8') as log:
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
summary=dict(stage='R1_SUBSET',python=sys.version,platform=platform.platform(),executable=sys.executable,
    command='work/MarketRadar_integration_a3c4f27/.offline-venv/Scripts/python.exe work/MarketRadar_recorded_offline_R1/verify_recorded_r1.py',
    total=result.testsRun,errors=len(result.errors),failures=len(result.failures),skips=len(result.skipped),
    success=result.wasSuccessful(),blocked_attempts=blocked,full_regression='NOT RUN',
    limitation='Test receipts are fabricated verifier fixtures; no actual capture authority admitted. Not R1-R6 acceptance.')
(out/'r1.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2));raise SystemExit(not result.wasSuccessful())
