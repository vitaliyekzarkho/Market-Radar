"""Read-only byte lock interoperable with the legacy msvcrt lock."""
import ctypes
from ctypes import wintypes
from pathlib import Path
class Overlapped(ctypes.Structure):
    _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),('Offset',wintypes.DWORD),('OffsetHigh',wintypes.DWORD),('hEvent',wintypes.HANDLE)]
class LegacyLock:
    def __init__(self,path):self.path=Path(path)
    def __enter__(self):
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];self.k.CreateFileW.restype=wintypes.HANDLE
        self.k.LockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
        self.k.UnlockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
        self.k.CloseHandle.argtypes=[wintypes.HANDLE]
        # OPEN_EXISTING, GENERIC_READ, share read/write but prohibit deletion.
        self.handle=self.k.CreateFileW(str(self.path),0x80000000,3,None,3,0,None)
        if self.handle==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_last_error(),'LEGACY_LOCK_UNAVAILABLE')
        self.overlap=Overlapped()
        if not self.k.LockFileEx(self.handle,3,0,1,0,ctypes.byref(self.overlap)):
            error=ctypes.get_last_error();self.k.CloseHandle(self.handle);raise OSError(error,'LEGACY_RADAR_RUNNING_OR_LOCK_DENIED')
        self.acquired=True
        return self
    def __exit__(self,*args):
        self.k.UnlockFileEx(self.handle,0,1,0,ctypes.byref(self.overlap));self.k.CloseHandle(self.handle)
