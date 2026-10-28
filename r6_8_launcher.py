"""Explicit backend controller. No retry, credentials, native imports or terminals."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT.parent/'MarketRadar_recorded_offline_R1/_review_deps')]
from recorded_ledger import safe_path
from offline_integration import single_instance
from r6_8_job import run_owned

LOCK=ROOT.parent/'r6_8_session.lock'
def launch(backend,output,*,seconds=60,mock_case='success'):
    if backend not in {'mock','native'}:raise ValueError('EXPLICIT_BACKEND_REQUIRED')
    if type(seconds) not in (int,float) or not 0<seconds<=60:raise ValueError('TIME_LIMIT_INVALID')
    out=safe_path(output)
    if out==ROOT or out.is_relative_to(ROOT):raise ValueError('SEPARATE_OUTPUT_REQUIRED')
    if out.exists() and any(out.iterdir()):raise ValueError('FRESH_OUTPUT_REQUIRED')
    with single_instance(LOCK):
        out.mkdir(parents=True,exist_ok=True)
        if backend=='native':
            result=dict(exit_code=20,status='BLOCKED_NATIVE_INITIALIZE_SEMANTICS',child_started=False,
                reason='No documented attach-only/no-auto-login initialize; no native import or call performed')
        else:
            command=[sys.executable,'-I','-B',str(ROOT/'r6_8_worker.py'),'--backend',backend,
                     '--output',str(out),'--mock-case',mock_case]
            try:result=run_owned(command,cwd=ROOT,seconds=seconds)
            except KeyboardInterrupt:result=dict(exit_code=130,status='CANCELLED')
            except Exception:result=dict(exit_code=70,status='SUPERVISOR_FAILURE')
        result.update(backend=backend,limit_seconds=seconds,automatic_restarts=0)
        (out/'supervisor.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        return result
def main():
    p=argparse.ArgumentParser();p.add_argument('--backend',required=True,choices=['mock','native'])
    p.add_argument('--output',required=True);p.add_argument('--seconds',type=float,default=60)
    args=p.parse_args()
    try:r=launch(args.backend,args.output,seconds=args.seconds)
    except (ValueError,RuntimeError,OSError):return 20
    print(json.dumps(r));return r['exit_code']
if __name__=='__main__':raise SystemExit(main())
