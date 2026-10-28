"""Cross-installation startup gate. No terminal or transport access."""
import ctypes,json,os,subprocess,re,hashlib
from pathlib import Path
from legacy_instance_lock import LegacyLock

def competing_radars(rows,current_pid):
    ancestors={current_pid};by_pid={int(x['ProcessId']):x for x in rows}
    parent=current_pid
    while parent in by_pid:
        parent=int(by_pid[parent].get('ParentProcessId') or 0)
        if not parent or parent in ancestors:break
        ancestors.add(parent)
    return [int(x['ProcessId']) for x in rows if int(x['ProcessId']) not in ancestors
            and str(x.get('Name','')).lower() in {'python.exe','pythonw.exe'}
            and re.search(r'(?i)(?:^|[\\/\s\"])run_radar\.py(?:\"|\s|$)',str(x.get('CommandLine') or ''))]

LEGACY_ENTRY_SHA256='c7eb31894f92fb027967795b09b7a430edaa8fe1bec41501772caa548df26d13'

class StartupGate:
    def __init__(self):
        self.legacy=None
    def __enter__(self):
        if os.name!='nt':raise RuntimeError('WINDOWS_STARTUP_GATE_REQUIRED')
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.k.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p];self.k.CreateMutexW.restype=ctypes.c_void_p
        self.k.CloseHandle.argtypes=[ctypes.c_void_p]
        self.handle=self.k.CreateMutexW(None,False,'Local\\MarketRadar_Operational_AllVersions')
        error=ctypes.get_last_error()
        if not self.handle:raise OSError(error,'Mutex failed')
        try:
            if error==183:raise RuntimeError('RADAR_ALREADY_RUNNING')
            legacy=Path('C:/Users/User/MarketRadar')
            if hashlib.sha256((legacy/'run_radar.py').read_bytes()).hexdigest()!=LEGACY_ENTRY_SHA256:
                raise RuntimeError('LEGACY_LOCK_CONTRACT_CHANGED')
            self.legacy=LegacyLock(legacy/'data/runtime/radar.lock')
            self.legacy.__enter__()
            script='@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match "^pythonw?\\.exe$"} | Select-Object ProcessId,ParentProcessId,Name,CommandLine) | ConvertTo-Json -Compress'
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,check=True,timeout=15,creationflags=0x08000000)
            rows=json.loads(result.stdout or '[]');rows=[rows] if isinstance(rows,dict) else rows
            if competing_radars(rows,os.getpid()):raise RuntimeError('OTHER_INSTALLATION_RADAR_RUNNING')
            return self
        except BaseException:
            if self.legacy and getattr(self.legacy, "acquired", False):self.legacy.__exit__()
            self.k.CloseHandle(self.handle);raise
    def __exit__(self,*args):
        if self.legacy:self.legacy.__exit__(*args)
        self.k.CloseHandle(self.handle)
