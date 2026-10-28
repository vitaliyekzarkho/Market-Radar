"""Offline verification only; never imports native MT5."""
import sys,os,json,unittest,tempfile,platform,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'MarketRadar_recorded_offline_R1/_review_deps'))
sys.dont_write_bytecode=True;os.chdir(ROOT)
stage=sys.argv[1] if len(sys.argv)>1 else 'launch'
if stage not in {'launch','full'}:raise SystemExit('launch | full')
scratch=ROOT.parent/'r6_8_test_tmp' if stage=='launch' else Path('C:/Users/User/AppData/Local/Temp/MarketRadar_R6_8_Tests')
scratch.mkdir(exist_ok=True);tempfile.tempdir=str(scratch);os.environ['TEMP']=str(scratch);os.environ['TMP']=str(scratch)
blocked=[]
def guard(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.getaddrinfo'} or (event=='import' and args[0].split('.')[0]=='MetaTrader5'):
        blocked.append(event);raise PermissionError('R6_EXTERNAL_FORBIDDEN')
    if event in {'open','sqlite3.connect'} and args and isinstance(args[0],(str,bytes,os.PathLike)):
        p=os.path.abspath(os.fsdecode(args[0])).replace('/','\\').casefold()
        if p=='c:\\users\\user\\marketradar' or p.startswith('c:\\users\\user\\marketradar\\') or Path(p).name.startswith('.env'):
            blocked.append('private_file');raise PermissionError('R6_PRIVATE_FORBIDDEN')
sys.addaudithook(guard)
suite=unittest.defaultTestLoader.discover(str(ROOT),pattern='test_r6_8_*.py' if stage=='launch' else 'test_*.py')
def leaves(s):
    for t in s:
        if isinstance(t,unittest.TestSuite):yield from leaves(t)
        else:yield t
suite=unittest.TestSuite(leaves(suite))
names=[t.id() for t in leaves(suite)];out=ROOT/'r6_8_verification';out.mkdir(exist_ok=True);started=time.monotonic()
with (out/(stage+'.log')).open('w',encoding='utf-8') as log:r=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
result=dict(command=f'{sys.executable} -B {__file__} {stage}',python=sys.version,platform=platform.platform(),
    total=r.testsRun,failures=len(r.failures),errors=len(r.errors),skips=len(r.skipped),success=r.wasSuccessful(),
    duration_seconds=time.monotonic()-started,test_ids=names,blocked_attempts=blocked)
(out/(stage+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='test_ids'},indent=2));raise SystemExit(not r.wasSuccessful())
