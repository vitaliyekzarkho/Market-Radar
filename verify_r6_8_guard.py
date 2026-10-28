"""Active negative probes: each operation must be rejected BEFORE execution."""
import sys,json,socket,subprocess,os,importlib
from pathlib import Path
from r6_safety import install_offline_guard
install_offline_guard()
def production_write():
    with open('C:/Users/User/MarketRadar/R6_MUST_NOT_EXIST.txt','w') as f:f.write('FORBIDDEN')
def credential_read():
    with open('.env','rb') as f:return f.read()
def network():
    with socket.socket() as s:s.connect(('127.0.0.1',9))
probes={
    'native_mt5_import':lambda:importlib.import_module('MetaTrader5'),
    'telegram_import':lambda:importlib.import_module('telegram_notifier'),
    'production_write':production_write,'credential_read':credential_read,
    'socket_connection':network,'child_process':lambda:subprocess.run([sys.executable,'-c','pass'])}
results={}
for name,fn in probes.items():
    try:fn()
    except (PermissionError,ImportError) as e:results[name]={'status':'PASS' if 'FORBIDDEN' in str(e) else 'FAIL','blocked_by':str(e)}
    else:results[name]={'status':'FAIL'}
out=Path(__file__).resolve().parent/'r6_8_verification';out.mkdir(exist_ok=True)
(out/'guard.json').write_text(json.dumps({'command':f'{sys.executable} -B {__file__}','checks':results},indent=2),encoding='utf-8')
print(json.dumps(results,indent=2));raise SystemExit(any(r['status']!='PASS' for r in results.values()))
