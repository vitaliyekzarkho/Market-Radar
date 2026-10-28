"""Artificial process-tree probe. Never imports Radar or MT5."""
import sys,subprocess,time,os
from pathlib import Path
mode=sys.argv[1];root=Path(sys.argv[2])
if mode=='tree':
    child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(120)'],creationflags=subprocess.CREATE_NO_WINDOW)
    (root/'descendant.pid').write_text(str(child.pid))
    time.sleep(120)
elif mode=='exit':raise SystemExit(70)
elif mode=='orphan':
    child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(120)'],creationflags=subprocess.CREATE_NO_WINDOW)
    (root/'descendant.pid').write_text(str(child.pid))
elif mode=='controller':
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from r6_8_job import run_owned
    run_owned([sys.executable,'-I',__file__,'tree',str(root)],cwd=root,seconds=60)
