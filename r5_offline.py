"""Explicit CLI; one process, one replay, no restart loop."""
import sys,os,json,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'MarketRadar_recorded_offline_R1/_review_deps'))
sys.dont_write_bytecode=True

def install_guard():
    def audit(event,args):
        if event in {'socket.connect','socket.connect_ex','socket.getaddrinfo','subprocess.Popen','os.system'}:
            raise PermissionError('R5_EXTERNAL_ACTION_FORBIDDEN')
        if event=='import' and args[0].split('.')[0] in {'MetaTrader5','mt5_reader','telegram_notifier','run_radar','run_cycle'}:
            raise ImportError('R5_LIVE_MODULE_FORBIDDEN')
        if event in {'open','sqlite3.connect'} and args and isinstance(args[0],(str,bytes,os.PathLike)):
            p=os.path.abspath(os.fsdecode(args[0])).replace('/','\\').casefold()
            if p.startswith('c:\\users\\user\\marketradar\\') or Path(p).name.startswith('.env'):
                raise PermissionError('R5_PRODUCTION_OR_CREDENTIAL_FORBIDDEN')
    sys.addaudithook(audit)
    for key in list(os.environ):
        if key.upper() not in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP','PATHEXT'}:del os.environ[key]

def main():
    p=argparse.ArgumentParser();p.add_argument('manifest');p.add_argument('output');p.add_argument('--admission',help='Explicit independently reviewed trust file; never inferred from market manifest')
    a=p.parse_args();install_guard()
    from r5_bridge import run_replay
    context=None
    if a.admission:
        from recorded_context import AuthorityContext,canonical
        from recorded_authority import TrustStore
        t=json.loads(Path(a.admission).read_text('utf-8'))
        context=AuthorityContext(t['authority_class'],TrustStore(canonical(t['policy']).encode(),frozenset(t['authority_ids']),
             frozenset(t['source_ids']),frozenset(t['audited_receipt_hashes']),t['admission_reference']))
    report=run_replay(a.manifest,a.output,context=context)
    print(json.dumps({'report':str(Path(a.output).resolve()/'report.json'),'events':len(report['events']),'live_actions':0}))

if __name__=='__main__':main()
