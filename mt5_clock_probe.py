"""Read-only external UTC probes; never adjusts local time."""
import socket,struct,time,secrets,urllib.request,email.utils
from datetime import datetime,timezone
def utc(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
def ntp(host):
    t1=time.time();m1=time.perf_counter();request=bytearray(48);request[0]=0x23
    stamp=t1+2208988800;struct.pack_into('!II',request,40,int(stamp),int(stamp%1*2**32))
    result={'method':'NTP_UDP_UNAUTHENTICATED','source':host,'start_utc':utc(t1)}
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.settimeout(2);s.connect((host,123));s.send(request);reply=s.recv(512)
        t4=time.time();m4=time.perf_counter()
        if len(reply)<48 or reply[24:32]!=request[40:48]:raise ValueError('NTP_RESPONSE_ORIGIN_MISMATCH')
        leap=reply[0]>>6;mode=reply[0]&7;stratum=reply[1]
        if leap==3 or mode!=4 or not 1<=stratum<=15:raise ValueError('NTP_SERVER_NOT_SYNCHRONIZED')
        def decode(pos):
            seconds,fraction=struct.unpack_from('!II',reply,pos);return seconds-2208988800+fraction/2**32
        t2,t3=decode(32),decode(40)
        if t3<t2:raise ValueError('NTP_SERVER_TIMES_REVERSED')
        delay=(m4-m1)-(t3-t2);offset=((t2-t1)+(t3-t4))/2
        result.update(status='OK',end_utc=utc(t4),elapsed_seconds=m4-m1,server_receive_epoch=t2,server_transmit_epoch=t3,
            server_transmit_utc=utc(t3),offset_seconds=offset,network_delay_seconds=delay,stratum=stratum,
            local_clock_step_seconds=(t4-t1)-(m4-m1),uncertainty_minimum_seconds=max(0,delay)/2,
            authenticated=False,root_dispersion_seconds=struct.unpack_from('!I',reply,8)[0]/65536,
            conservative_offset_interval_seconds=[t3-t4-struct.unpack_from('!I',reply,8)[0]/65536,t3-t1+struct.unpack_from('!I',reply,8)[0]/65536])
    except Exception as e:result.update(status='ERROR',error=type(e).__name__+': '+str(e),end_utc=utc(time.time()),elapsed_seconds=time.perf_counter()-m1)
    return result

def https_date(base):
    t1=time.time();m1=time.perf_counter();result={'method':'HTTPS_DATE_TLS','source':base,'start_utc':utc(t1)}
    try:
        url=base+'?clock_probe='+secrets.token_hex(12)
        req=urllib.request.Request(url,headers={'Cache-Control':'no-cache','Pragma':'no-cache','User-Agent':'MarketRadar-ReadOnly-TimeCheck/1'})
        with urllib.request.urlopen(req,timeout=4) as response:
            date=response.headers.get('Date');age=response.headers.get('Age');status=response.status
        t4=time.time();m4=time.perf_counter();server=email.utils.parsedate_to_datetime(date).timestamp()
        if age is not None and float(age)>0:raise ValueError('CACHED_DATE_REJECTED')
        result.update(status='OK',http_status=status,end_utc=utc(t4),elapsed_seconds=m4-m1,
            date_header=date,age_header=age,server_epoch=server,
            offset_interval_seconds=[server-t4,server+1-t1],local_clock_step_seconds=(t4-t1)-(m4-m1),
            limitation='TLS authenticates origin; HTTP Date is second-granularity evidence, not a signed precision-time attestation')
    except Exception as e:result.update(status='ERROR',error=type(e).__name__+': '+str(e),end_utc=utc(time.time()),elapsed_seconds=time.perf_counter()-m1)
    return result

def probes():
    return [ntp('time.cloudflare.com'),ntp('time.google.com'),https_date('https://www.cloudflare.com/cdn-cgi/trace'),https_date('https://www.google.com/generate_204')]

if __name__=='__main__':
    import json
    from pathlib import Path
    result=probes();Path(__file__).with_name('external_preflight.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
