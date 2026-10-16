"""Strict static publisher. All paths are relative to a caller-selected D-drive directory."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]

def _validate(value,name):
 from jsonschema import Draft202012Validator, FormatChecker
 schema=json.loads((ROOT/'contracts'/f'{name}.schema.json').read_text(encoding='utf-8-sig'))
 Draft202012Validator.check_schema(schema)
 errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(value))
 if errors:raise ValueError(str(errors[0]))
 # URI format allows arbitrary schemes; the wire contract additionally requires HTTPS.
 def urls(node):
  if isinstance(node,dict):
   for key,item in node.items():
    if key=='url':
     u=urlsplit(item)
     if u.scheme!='https' or not u.hostname or u.username or u.password or any(c.isspace() for c in item):raise ValueError('invalid HTTPS URL')
    urls(item)
  elif isinstance(node,list):
   for item in node:urls(item)
 urls(value)

def _time(value):return dt.datetime.fromisoformat(value.replace('Z','+00:00'))

def _unique(pairs):
 result={}
 for key,value in pairs:
  if key in result:raise ValueError('duplicate JSON key: '+key)
  result[key]=value
 return result

def _decode(raw):return json.loads(raw,object_pairs_hook=_unique)

def validate_edition(edition):
 _validate(edition,'edition')
 published=_time(edition['published_at'])
 if _time(edition['generated_at'])>published or _time(edition['sections']['news']['event_at'])>published or _time(edition['sections']['github']['checked_at'])>published:raise ValueError('edition timestamp exceeds publication')
 for source in edition['sources']:
  retrieved=_time(source['retrieved_at'])
  if retrieved>published or (source['published_at'] is not None and _time(source['published_at'])>retrieved):raise ValueError('source timestamp chronology')
 sources=[s['id'] for s in edition['sources']];claims=[c['id'] for c in edition['claims']]
 if len(set(sources))!=len(sources) or len(set(claims))!=len(claims):raise ValueError('duplicate IDs')
 for claim in edition['claims']:
  for evidence in claim['evidence']:
   if evidence['source_id'] not in sources:raise ValueError('unknown source reference')
 for name in ('news','technology'):
  if not set(edition['sections'][name]['claim_ids'])<=set(claims):raise ValueError('unknown claim reference')
 github=edition['sections']['github'];repository=urlsplit(github['url'])
 if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',github['full_name']) or repository.hostname!='github.com' or repository.path.rstrip('/')!='/'+github['full_name'] or repository.query:raise ValueError('repository URL/name mismatch')
 if edition['is_sample'] and ('SAMPLE' not in edition['title'] or edition['sections']['practice']['verification_status']!='proposed'):raise ValueError('samples require visible label and proposed practice')
 return edition

def _bytes(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
def _read(path):return _decode(path.read_text(encoding='utf-8'))
def _atomic(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def validate_release(output_dir):
 root=Path(output_dir);latest=_read(root/'latest.json');history=_read(root/'history/index.json')
 _validate(latest,'latest');_validate(history,'history')
 seen=set();last=0;previous=None;max_revision={}
 for release in history['releases']:
  seq=release['release_sequence'];key=(release['edition_id'],release['revision'])
  if seq<=last or key in seen:raise ValueError('history sequence/identity conflict')
  if previous is not None and (release['edition_id']<previous['edition_id'] or _time(release['published_at'])<_time(previous['published_at'])):raise ValueError('history publication regression')
  if release['revision']<=max_revision.get(release['edition_id'],0):raise ValueError('history revision regression')
  max_revision[release['edition_id']]=release['revision'];previous=release
  seen.add(key);last=seq
  expected=f"editions/{release['edition_id']}/r{release['revision']}.json"
  if release['article_path']!=expected:raise ValueError('article path mismatch')
  raw=(root/expected).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=release['sha256']:raise ValueError('article hash mismatch')
  e=validate_edition(_decode(raw))
  if any(e[k]!=release[k] for k in ('edition_id','revision','published_at')):raise ValueError('pointer/content mismatch')
 if latest not in history['releases']:raise ValueError('latest absent from history')
 return latest

def build_release(edition,output_dir):
 validate_edition(edition);root=Path(output_dir);raw=_bytes(edition)
 latest_path=root/'latest.json';history_path=root/'history/index.json'
 if latest_path.exists():validate_release(root)
 elif history_path.exists():raise ValueError('history without latest requires recovery')
 history=_read(history_path) if history_path.exists() else {'schema_version':'1.0','releases':[]}
 article_path=f"editions/{edition['edition_id']}/r{edition['revision']}.json";target=root/article_path
 for existing in history['releases']:
  if existing['article_path']==article_path:
   if existing['sha256']!=hashlib.sha256(raw).hexdigest():raise ValueError('immutable revision conflict')
   return existing
 if history['releases']:
  current=history['releases'][-1]
  if edition['edition_id']<current['edition_id'] or _time(edition['published_at'])<_time(current['published_at']):raise ValueError('publication regression')
  prior=[r['revision'] for r in history['releases'] if r['edition_id']==edition['edition_id']]
  if prior and edition['revision']<=max(prior):raise ValueError('revision regression')
 if target.exists() and target.read_bytes()!=raw:raise ValueError('immutable revision conflict')
 release=dict(schema_version='1.0',release_sequence=max([r['release_sequence'] for r in history['releases']],default=0)+1,edition_id=edition['edition_id'],revision=edition['revision'],published_at=edition['published_at'],article_path=article_path,sha256=hashlib.sha256(raw).hexdigest())
 _validate(release,'latest');history['releases'].append(release);_validate(history,'history')
 old_history=history_path.read_bytes() if history_path.exists() else None
 if not target.exists():_atomic(target,raw)
 # Verify exact persisted bytes before switching either manifest.
 if hashlib.sha256(target.read_bytes()).hexdigest()!=release['sha256']:raise ValueError('persisted hash mismatch')
 try:
  _atomic(history_path,_bytes(history));_atomic(latest_path,_bytes(release))
 except Exception:
  if old_history is not None:_atomic(history_path,old_history)
  elif history_path.exists():history_path.unlink()
  raise
 return release

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['publish','validate']);parser.add_argument('output');parser.add_argument('edition',nargs='?');args=parser.parse_args()
 if args.action=='publish':print(json.dumps(build_release(_read(Path(args.edition)),args.output),indent=2))
 else:print(json.dumps(validate_release(args.output),indent=2))
