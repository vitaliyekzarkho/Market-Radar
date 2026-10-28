"""Explicit offline regression entry point, including isolated-process integration tests."""
import json
import platform
from pathlib import Path
import sys
import tempfile
import unittest
import os

root=Path(__file__).resolve().parent
os.chdir(root)
tempfile.tempdir=str(Path(os.environ['TEMP'])/'MarketRadar_D5_OfflineTests')
Path(tempfile.tempdir).mkdir(exist_ok=True)
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.getaddrinfo'} or (event=='import' and args[0]=='MetaTrader5'):
        blocked.append(event)
        raise PermissionError('OFFLINE_EXTERNAL_ACTION_FORBIDDEN')
sys.addaudithook(audit)
suite=unittest.defaultTestLoader.discover(str(root),pattern='test_*.py')
def ids(s):
    for item in s:
        if isinstance(item,unittest.TestSuite):yield from ids(item)
        else:yield item.id()
names=list(ids(suite))
out=root/'verification';out.mkdir(exist_ok=True)
with (out/'full.log').open('w',encoding='utf-8') as log:
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
summary=dict(base_sha='a3c4f27f5f2f9307e7e4c645b54e55ee18668caa',python=sys.version,platform=platform.platform(),
    executable=sys.executable,total=result.testsRun,failures=len(result.failures),errors=len(result.errors),skips=len(result.skipped),
    d5=sum(x.startswith('test_offline_shadow_assessment.') for x in names),
    integration=sum(x.startswith('test_offline_integration.') for x in names),blocked_external_attempts=blocked,
    success=result.wasSuccessful())
(out/'results.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
raise SystemExit(not result.wasSuccessful())
