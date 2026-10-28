"""Only runnable R6 entrypoint in this package: explicit MOCK MT5, one batch."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'MarketRadar_recorded_offline_R1/_review_deps'))
from r6_safety import install_offline_guard

def main():
    install_offline_guard()
    import argparse
    from r6_fixtures import FakeMT5,Clock
    from r6_adapter import XAUUSD
    from r6_pipeline import acquire_and_replay
    p=argparse.ArgumentParser(description='MOCK ONLY — no native MT5 connection')
    p.add_argument('output');args=p.parse_args()
    r=acquire_and_replay(FakeMT5(),XAUUSD,args.output,session_id='mock_demo',count=40,clock=Clock())
    print('MOCK ONLY:',r['version'],r['availability'],'delivery=False trade=False')

if __name__=='__main__':main()
