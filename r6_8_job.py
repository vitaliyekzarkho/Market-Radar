"""Windows owned process-tree lifetime. No process enumeration or PID-based kills."""
import ctypes as c
from ctypes import wintypes as w
import _winapi,subprocess,time,os

class Basic(c.Structure):
    _fields_=[('process_time',c.c_int64),('job_time',c.c_int64),('flags',w.DWORD),
        ('min_working',c.c_size_t),('max_working',c.c_size_t),('active_limit',w.DWORD),
        ('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
class IO(c.Structure):
    _fields_=[(n,c.c_uint64) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
class Extended(c.Structure):
    _fields_=[('basic',Basic),('io',IO),('process_memory',c.c_size_t),('job_memory',c.c_size_t),
              ('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
class Accounting(c.Structure):
    _fields_=[(n,c.c_int64) for n in ('user','kernel','period_user','period_kernel')]+[(n,w.DWORD) for n in ('faults','total','active','terminated')]
class Startup(c.Structure):
    _fields_=[('cb',w.DWORD),('reserved',w.LPWSTR),('desktop',w.LPWSTR),('title',w.LPWSTR),
        *[(n,w.DWORD) for n in ('x','y','xsize','ysize','xchars','ychars','fill','flags')],
        ('show',w.WORD),('reserved_size',w.WORD),('reserved_bytes',c.c_void_p),
        ('stdin',w.HANDLE),('stdout',w.HANDLE),('stderr',w.HANDLE)]
class StartupEx(c.Structure):_fields_=[('base',Startup),('attributes',c.c_void_p)]
class ProcessInfo(c.Structure):_fields_=[('process',w.HANDLE),('thread',w.HANDLE),('pid',w.DWORD),('tid',w.DWORD)]
k=c.WinDLL('kernel32',use_last_error=True)
def api(name,args,result=w.BOOL):
    f=getattr(k,name);f.argtypes=args;f.restype=result;return f
create=api('CreateJobObjectW',[c.c_void_p,w.LPCWSTR],w.HANDLE)
set_info=api('SetInformationJobObject',[w.HANDLE,c.c_int,c.c_void_p,w.DWORD])
resume=api('ResumeThread',[w.HANDLE],w.DWORD)
terminate=api('TerminateJobObject',[w.HANDLE,w.UINT])
query=api('QueryInformationJobObject',[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.c_void_p])
close=api('CloseHandle',[w.HANDLE])
init_attrs=api('InitializeProcThreadAttributeList',[c.c_void_p,w.DWORD,w.DWORD,c.POINTER(c.c_size_t)])
update_attrs=api('UpdateProcThreadAttribute',[c.c_void_p,w.DWORD,c.c_size_t,c.c_void_p,c.c_size_t,c.c_void_p,c.c_void_p])
delete_attrs=api('DeleteProcThreadAttributeList',[c.c_void_p],None)
create_process=api('CreateProcessW',[w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.POINTER(StartupEx),c.POINTER(ProcessInfo)])

def checked(ok):
    if not ok:raise c.WinError(c.get_last_error())

def clean_env():
    return {key:value for key,value in os.environ.items() if key.upper() in
            {'SYSTEMROOT','WINDIR','TEMP','TMP','PATH','COMSPEC','PATHEXT'}}

def create_owned(command,cwd,job):
    # Atomic job assignment at creation closes the supervisor-crash window
    # between CreateProcess and a later AssignProcessToJobObject call.
    size=c.c_size_t();init_attrs(None,1,0,c.byref(size))
    storage=c.create_string_buffer(size.value);checked(init_attrs(storage,1,0,c.byref(size)))
    try:
        jobs=(w.HANDLE*1)(job)
        checked(update_attrs(storage,0,0x0002000D,c.byref(jobs),c.sizeof(jobs),None,None))
        si=StartupEx();si.base.cb=c.sizeof(si);si.base.flags=1;si.base.show=0
        si.attributes=c.cast(storage,c.c_void_p)
        info=ProcessInfo();cmd=c.create_unicode_buffer(subprocess.list2cmdline(command))
        env=c.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(clean_env().items()))+'\0\0')
        checked(create_process(command[0],cmd,None,None,False,0x4|0x08000000|0x400|0x80000,
            env,str(cwd),c.byref(si),c.byref(info)))
        return info.process,info.thread,info.pid
    finally:delete_attrs(storage)

def run_owned(command,*,cwd,seconds):
    if type(seconds) not in (int,float) or not 0<seconds<=60:raise ValueError('TIME_LIMIT_0_TO_60_REQUIRED')
    started=time.monotonic();job=create(None,None);checked(job)
    hp=ht=None;assigned=False
    try:
        limits=Extended();limits.basic.flags=0x2000 # KILL_ON_JOB_CLOSE; NO breakaway flags
        checked(set_info(job,9,c.byref(limits),c.sizeof(limits)))
        hp,ht,pid=create_owned(command,cwd,job)
        assigned=True
        if resume(ht)==0xffffffff:raise c.WinError(c.get_last_error())
        remaining=max(0,seconds-(time.monotonic()-started))
        wait=_winapi.WaitForSingleObject(hp,max(0,int(remaining*1000)))
        timed_out=wait==258
        if wait not in (0,258):raise RuntimeError('WAIT_FAILED')
        code=124 if timed_out else _winapi.GetExitCodeProcess(hp)
        checked(terminate(job,code)) # remove descendants even when parent exits first
        accounting=Accounting()
        deadline=time.monotonic()+5
        while True:
            checked(query(job,1,c.byref(accounting),c.sizeof(accounting),None))
            if accounting.active==0:break
            if time.monotonic()>deadline:raise RuntimeError('PROCESS_TREE_DID_NOT_EXIT')
            time.sleep(.01)
        return dict(exit_code=code,timed_out=timed_out,created_pid=pid,
            elapsed_seconds=time.monotonic()-started,owned_total_processes=accounting.total,
            remaining_owned_processes=accounting.active)
    finally:
        # On supervisor exception/keyboard interrupt the only handles targeted
        # are the ones created above. Closing job also kills on supervisor death.
        if assigned:terminate(job,70)
        close(job) # final owned handle: KILL_ON_JOB_CLOSE even on cleanup error
        if hp is not None:
            try:
                if not assigned:_winapi.TerminateProcess(hp,70)
                _winapi.WaitForSingleObject(hp,5000)
            finally:_winapi.CloseHandle(hp)
        if ht is not None:_winapi.CloseHandle(ht)
