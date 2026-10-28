"""Default is planning only. --execute is a separately authorized read-only run."""
import argparse,json,time,subprocess
from pathlib import Path
from datetime import datetime,timezone
from mt5_clock_authority import epoch,utc

def main():
    p=argparse.ArgumentParser();p.add_argument('--boundary',required=True);p.add_argument('--output',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    boundary=epoch(a.boundary)
    if boundary%3600:raise ValueError('WHOLE_UTC_HOUR_REQUIRED')
    plan=dict(boundary_utc=a.boundary,start_window_utc=[utc(boundary-20),utc(boundary-5)],stop_by_utc=utc(boundary+10),native_limit_seconds=40,symbol='GOLD.pro',timeframes=['W1','D1','H4','H1','M15'],mode='PREPARED_NOT_EXECUTED',requires='Independent explicit permission before --execute; no trading, Telegram or production analysis')
    print(json.dumps(plan,indent=2))
    if not a.execute:return
    if not boundary-20<=time.time()<=boundary-5:raise RuntimeError('OUTSIDE_START_WINDOW_NO_NATIVE_CONNECTION')
    output=Path(a.output).resolve()
    if output.exists():raise ValueError('OUTPUT_ALREADY_EXISTS_NO_REPEAT')
    from operational_startup_gate import StartupGate
    from mt5_clock_probe import probes
    terminal=r'C:\Program Files\OANDA TMS MT5 Terminal\terminal64.exe'
    command='@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq "terminal64.exe"} | Select-Object ExecutablePath) | ConvertTo-Json -Compress'
    proc=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,check=True,timeout=10)
    terminals=json.loads(proc.stdout or '[]');terminals=[terminals] if isinstance(terminals,dict) else terminals
    if len(terminals)!=1 or terminals[0]['ExecutablePath']!=terminal:raise RuntimeError('EXISTING_TERMINAL_REQUIRED')
    with StartupGate():
        import MetaTrader5 as mt5
        def deny(*args,**kw):raise RuntimeError('TRADING_ACCOUNT_TRANSPORT_FORBIDDEN')
        for name in ('order_send','order_check','login','account_info','positions_get','orders_get','symbol_select'):setattr(mt5,name,deny)
        capture=dict(symbol='GOLD.pro',source='OANDA_MT5_LOCAL',external_before=probes(),samples=[])
        capture['initialize_start_utc']=utc(time.time());started=time.monotonic()
        capture['initialize_success']=bool(mt5.initialize(terminal,timeout=5000))
        if not capture['initialize_success']:raise RuntimeError('INITIALIZE_FAILED_NO_RETRY')
        try:
            while time.monotonic()-started<40 and time.time()<boundary+8:
                measurements=[];s=utc(time.time());v=mt5.symbol_info_tick('GOLD.pro')
                measurements.append(dict(kind='tick',start_utc=s,end_utc=utc(time.time()),value=None if v is None else {'time':v.time,'time_msc':v.time_msc}))
                for name in plan['timeframes']:
                    s=utc(time.time());rows=mt5.copy_rates_from_pos('GOLD.pro',getattr(mt5,'TIMEFRAME_'+name),0,5)
                    measurements.append(dict(kind='bars',timeframe=name,start_utc=s,end_utc=utc(time.time()),rows=[] if rows is None else [{k:r[k].item() for k in rows.dtype.names} for r in rows]))
                capture['samples'].append(dict(measurements=measurements));time.sleep(.5)
        finally:
            mt5.shutdown();capture['shutdown_end_utc']=utc(time.time())
            output.parent.mkdir(parents=True,exist_ok=True)
            capture['external_after']=probes();output.write_text(json.dumps(capture,indent=2),encoding='utf-8')
if __name__=='__main__':main()
