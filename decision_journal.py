"""Append-only, hash-linked decision projections. SQLite remains authoritative.

Incomplete final writes are preserved in place. A new segment links their exact
bytes; only complete validated events participate in the record chain.
Single writer is provided by the Radar process lock.
"""
import hashlib,json,os
from pathlib import Path
ZERO='0'*64
def encoded(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def digest(x):return hashlib.sha256(x).hexdigest()
class DecisionJournal:
 def __init__(self,path,max_bytes=1024*1024):
  self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True)
  if max_bytes<1024:raise ValueError('SEGMENT_LIMIT_TOO_SMALL')
  self.max_bytes=max_bytes
 def scan(self):
  records={};previous_file=ZERO;previous_record=ZERO;tail=None;partial=False;date=None
  files=sorted(self.path.glob('segment-*.jsonl'))
  for index,p in enumerate(files,1):
   if p.name!=f'segment-{index:06d}.jsonl':raise ValueError('JOURNAL_SEGMENT_GAP')
   raw=p.read_bytes();lines=raw.splitlines(keepends=True);partial=bool(raw and not raw.endswith(b'\n'))
   complete=lines[:-1] if partial else lines
   if complete:
    header=json.loads(complete[0]);date=header.get('date')
    if header.get('kind')!='SEGMENT' or header.get('previous_file_sha256')!=previous_file or header.get('previous_record_sha256')!=previous_record:raise ValueError('JOURNAL_HEADER_CORRUPT')
    for line in complete[1:]:
     row=json.loads(line);h=row.pop('sha256')
     if row.get('previous')!=previous_record or h!=digest(encoded(row)):raise ValueError('JOURNAL_HASH_CORRUPT')
     if row['key'] in records:raise ValueError('JOURNAL_DUPLICATE_KEY')
     records[row['key']]=row['body'];previous_record=h
   elif not partial and raw:raise ValueError('JOURNAL_HEADER_MISSING')
   previous_file=digest(raw);tail=p
  return records,previous_file,previous_record,tail,partial,date
 def sync(self,events):
  records,prev_file,prev_record,tail,partial,date=self.scan()
  expected={e['audit_key']:e for e in events}
  if len(expected)!=len(events):raise ValueError('SOURCE_DUPLICATE_KEY')
  if list(records)!=list(expected)[:len(records)]:raise ValueError('JOURNAL_SOURCE_ORDER_MISMATCH')
  for k,v in records.items():
   if k not in expected or encoded(v)!=encoded(expected[k]):raise ValueError('JOURNAL_SOURCE_MISMATCH')
  added=0
  for e in events:
   key=e['audit_key']
   if key in records:continue
   row={'key':key,'previous':prev_record,'body':e};h=digest(encoded(row));line=encoded({**row,'sha256':h})+b'\n';day=e['at_utc'][:10]
   if tail is None or partial or date!=day or tail.stat().st_size+len(line)>self.max_bytes:
    if tail:prev_file=digest(tail.read_bytes())
    number=len(list(self.path.glob('segment-*.jsonl')))+1;tail=self.path/f'segment-{number:06d}.jsonl'
    header={'kind':'SEGMENT','date':day,'previous_file_sha256':prev_file,'previous_record_sha256':prev_record,'preceding_partial_write':partial}
    with tail.open('xb') as f:f.write(encoded(header)+b'\n');f.flush();os.fsync(f.fileno())
    partial=False;date=day
   with tail.open('ab') as f:f.write(line);f.flush();os.fsync(f.fileno())
   records[key]=e;prev_record=h;added+=1
  return {'records':len(records),'added':added,'segments':len(list(self.path.glob('segment-*.jsonl'))),'bytes':sum(p.stat().st_size for p in self.path.glob('segment-*.jsonl')),'head_sha256':prev_record,'oversized_single_event_allowed':True}
