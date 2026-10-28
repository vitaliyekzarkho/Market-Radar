"""Offline entrypoint boundary including importlib-based module loading."""
import sys
from offline_integration import install_guard

FORBIDDEN={'MetaTrader5','telegram_notifier','mt5_reader','run_radar','run_cycle'}

class DenyImports:
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in FORBIDDEN:raise ImportError('R6_OFFLINE_MODULE_FORBIDDEN')
        return None

def install_offline_guard():
    if any(k.split('.')[0] in FORBIDDEN for k in sys.modules):raise RuntimeError('R6_REQUIRES_CLEAN_PROCESS')
    install_guard()
    if not any(isinstance(x,DenyImports) for x in sys.meta_path):sys.meta_path.insert(0,DenyImports())
