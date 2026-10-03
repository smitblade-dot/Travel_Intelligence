"""Scheduled nuclear review coordinator. Never writes accepted or public data.
Source adapters deliver complete capture envelopes, not unverified facility counts.
"""
import argparse,copy,hashlib,json,os,tempfile
from pathlib import Path
from review_changes import queue,validate,digest
EXPECTED={'iaea-pris','iaea-rrdb','iaea-nfcis','iaea-piedb'}

def atomic_json(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 fd,name=tempfile.mkstemp(dir=path.parent,prefix='.pending-')
 try:
  with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
  os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)

def envelope(candidate,source,total,evidence):
 """Adapt a validated collector/consolidator candidate to monitoring input.
 evidence is an explicit capture manifest; no directory inference from old IDs.
 """
 if candidate.get('releaseReady') is not False:raise ValueError('Only review candidates accepted')
 if source not in EXPECTED:raise ValueError('Unsupported source')
 if evidence.get('sourceId')!=source or evidence.get('fetchStatus')!='SUCCESS' or evidence.get('complete') is not True:raise ValueError('Incomplete capture evidence')
 if evidence.get('observedSourceTotal')!=total:raise ValueError('Evidence denominator mismatch')
 if not evidence.get('retrievedAt') or not evidence.get('evidenceSha256'):raise ValueError('Missing capture provenance')
 rows=copy.deepcopy([r for r in candidate['facilities'] if r['sourceId']==source])
 for r in rows:
  if r.get('publicationStatus')!='INTERNAL_REVIEW':raise ValueError('Unexpected publication status')
  r['sourceRecordId']=str(r['sourceRecordId'])
 result={'sourceId':source,'fetchStatus':'SUCCESS','complete':True,'reviewStatus':'PENDING',
  'rows':rows,'rowsSha256':digest(rows),'observedSourceTotal':total,
  'retrievedAt':evidence['retrievedAt'],'captureEvidence':copy.deepcopy(evidence)}
 validate(result);return result

def run(accepted_inventory,accepted_profiles,accepted_snapshots,capture_paths,out,bootstrap=False):
 out=Path(out)
 if out.exists():raise ValueError('Run output already exists')
 # Exclusive directory prevents concurrent jobs replacing one another's reviews.
 out.mkdir(parents=True)
 inputs=[Path(accepted_inventory),Path(accepted_profiles)]
 before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
 report={'schemaVersion':'nuclear-refresh-review-1.0','publicationStatus':'INTERNAL_REVIEW',
  'releaseReady':False,'acceptedSnapshotChanged':False,'events':[],
  'acceptedFileHashes':before,'sources':{},'errors':[]}
 for source in sorted(EXPECTED):
  try:
   accepted_path=Path(accepted_snapshots)/(source+'.json')
   if bootstrap and not accepted_path.exists():
    capture=json.loads(Path(capture_paths[source]).read_text());validate(capture)
    if capture['sourceId']!=source:raise ValueError('Bootstrap source mismatch')
    initial={'schemaVersion':'nuclear-initial-review-1.0','sourceId':source,
      'publicationStatus':'INTERNAL_REVIEW','releaseReady':False,'reviewStatus':'INITIAL_BASELINE_REVIEW',
      'candidateSnapshot':capture,'acceptedSnapshotChanged':False,'events':[]}
    atomic_json(out/(source+'.initial-review.json'),initial)
    report['sources'][source]={'pending':len(capture['rows']),'errors':[],
      'reviewStatus':'INITIAL_BASELINE_REVIEW','acceptanceRequired':True}
    continue
   accepted=json.loads(accepted_path.read_text());validate(accepted,True)
   if accepted['sourceId']!=source:raise ValueError('Accepted source mismatch')
   try:
    capture=json.loads(Path(capture_paths[source]).read_text())
   except (KeyError,OSError,ValueError) as exc:
    capture={'sourceId':source,'fetchStatus':'FAILED','reason':str(exc)}
   result=queue(accepted,capture)
   atomic_json(out/(source+'.review.json'),result)
   report['sources'][source]={'pending':len(result['queue']),'errors':result['errors'],
     'acceptedSnapshotSha256':hashlib.sha256(accepted_path.read_bytes()).hexdigest()}
   report['errors'].extend({'sourceId':source,**e} for e in result['errors'])
  except (OSError,ValueError,TypeError,KeyError) as exc:
   error={'sourceId':source,'type':'ACCEPTED_BASELINE_UNAVAILABLE','reason':str(exc)}
   report['sources'][source]={'pending':0,'errors':[error]};report['errors'].append(error)
 after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
 if before!=after:raise RuntimeError('Accepted files changed concurrently; discard this review run')
 report['captureComplete']=not report['errors']
 report['note']='Review artifacts only. Missing rows are not closures; new observations require independent acceptance.'
 atomic_json(out/'report.json',report)
 return report

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--inventory',type=Path,required=True);p.add_argument('--profiles',type=Path,required=True);p.add_argument('--accepted-snapshots',type=Path,required=True);p.add_argument('--captures',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--bootstrap-review',action='store_true');a=p.parse_args()
 report=run(a.inventory,a.profiles,a.accepted_snapshots,json.loads(a.captures.read_text()),a.out,a.bootstrap_review)
 print(json.dumps({'complete':report['captureComplete'],'errors':len(report['errors']),'acceptedSnapshotChanged':False}))
 raise SystemExit(0 if report['captureComplete'] else 2)
