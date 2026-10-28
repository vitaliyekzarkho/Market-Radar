"""Explicit staged offline test runner. No live modules imported."""
import sys, os, json, unittest, platform, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'MarketRadar_recorded_offline_R1/_review_deps'))
sys.dont_write_bytecode=True
os.chdir(ROOT)
stage=sys.argv[1]
scratch=(Path('C:/Users/User/AppData/Local/Temp/MarketRadar_RecoveryTests') if stage=='full' else ROOT.parent/'recorded_recovery_test_tmp');scratch.mkdir(exist_ok=True)
tempfile.tempdir=str(scratch)
os.environ['TEMP']=str(scratch);os.environ['TMP']=str(scratch)
out=ROOT/'recovery_verification';out.mkdir(exist_ok=True)
blocked=[]
def guard(event,args):
    forbidden={'MetaTrader5'} if stage=='full' else {'MetaTrader5','telegram_notifier','run_radar','run_cycle'}
    if event in {'socket.connect','socket.getaddrinfo'} or (event=='import' and args[0].split('.')[0] in forbidden):
        blocked.append(event);raise PermissionError('RECOVERY_LIVE_FORBIDDEN')
    if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
        p=os.path.abspath(os.fsdecode(args[0])).replace('/','\\').casefold()
        if p.startswith('c:\\users\\user\\marketradar\\') or Path(p).name.startswith('.env'):
            blocked.append('private_file');raise PermissionError('PRODUCTION_OR_SECRET_FORBIDDEN')
sys.addaudithook(guard)
if stage=='full': suite=unittest.defaultTestLoader.discover(str(ROOT),pattern='test_*.py')
else: suite=unittest.defaultTestLoader.loadTestsFromNames(['test_recorded_authority']+['test_recorded_recovery_'+s.lower() for s in 'ABCDEF' if s<=stage])
def ids(s):
    for t in s:
        if isinstance(t,unittest.TestSuite): yield from ids(t)
        else: yield t.id()
names=list(ids(suite))
with (out/(stage+'.log')).open('w',encoding='utf-8') as log:
    r=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
summary=dict(stage=stage,python=sys.version,platform=platform.platform(),command=f'{sys.executable} -B {__file__} {stage}',
             total=r.testsRun,errors=len(r.errors),failures=len(r.failures),skips=len(r.skipped),success=r.wasSuccessful(),
             test_ids=names,blocked_attempts=blocked)
(out/(stage+'.json')).write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k!='test_ids'},indent=2));raise SystemExit(not r.wasSuccessful())
