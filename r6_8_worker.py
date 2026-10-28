"""One controlled mock session; native path fails closed before native import."""
import sys,json,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT.parent/'MarketRadar_recorded_offline_R1/_review_deps')]

def main():
    p=argparse.ArgumentParser();p.add_argument('--backend',required=True,choices=['mock','native'])
    p.add_argument('--output',required=True);p.add_argument('--mock-case',default='success',choices=['success','connect_failure','forbidden_api','missing','short','crash'])
    args=p.parse_args()
    from r6_safety import install_offline_guard
    install_offline_guard()
    from recorded_ledger import safe_path
    from recorded_authority import Rejected
    out=safe_path(args.output)
    if args.backend=='native':
        # No runtime override: MT5's documented initialize semantics cannot
        # meet strict no-terminal-launch/no-auto-login contract.
        return 20
    from r6_adapter import XAUUSD,ReadOnlyAdapter
    from r6_pipeline import acquire_and_replay
    from r6_fixtures import FakeMT5,Clock
    class AuditedMock(FakeMT5):
        def copy_rates_from_pos(self,*values):
            rows=super().copy_rates_from_pos(*values)
            if args.mock_case=='short':rows=rows[:2]
            if len(rows)<7:raise Rejected('INSUFFICIENT_CLOSED_HISTORY')
            return rows
    api=AuditedMock('connect' if args.mock_case=='connect_failure' else 'empty' if args.mock_case=='missing' else None)
    code=0;reason='MOCK_COMPLETE'
    try:
        if args.mock_case=='crash':raise RuntimeError('MOCK_CRASH')
        if args.mock_case=='forbidden_api':getattr(ReadOnlyAdapter(api),'order_send')()
        acquire_and_replay(api,XAUUSD,out/'pipeline',session_id='supervised_mock',count=40,clock=Clock())
    except Rejected as e:code=10 if str(e)=='CONNECTION_FAILED' else 20;reason=str(e)
    except AttributeError:code=20;reason='FORBIDDEN_API_UNAVAILABLE'
    except Exception:code=70;reason='WORKER_CRASH'
    finally:
        (out/'worker.json').write_text(json.dumps(dict(exit_code=code,reason=reason,backend='mock',
            calls=api.calls,broker_parameters='MOCK_ONLY; ACTUAL_NOT_VERIFIED',telegram=False,trading=False),indent=2),encoding='utf-8')
    return code
if __name__=='__main__':raise SystemExit(main())
