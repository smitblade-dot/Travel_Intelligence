"""Create an evidence-backed review queue; never mutate accepted data or publish events."""
import argparse,copy,hashlib,json,math
from datetime import datetime
from urllib.parse import urlparse
from pathlib import Path

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def material(row):
 status=row.get('sourceReportedStatus',{})
 return {'name':row.get('name'),'countryCode':row.get('countryCode'),
  'facilityClass':row.get('facilityClass'),'facilityClasses':sorted(row.get('facilityClasses',[])),
  'sourceFacilityCategory':row.get('sourceFacilityCategory'),'sourceFacilityType':row.get('sourceFacilityType'),
  'sourceReportedLifecycle':status.get('value',row.get('lifecycleStatus')),
  'siteId':row.get('siteId'),'siteName':row.get('siteName'),'city':row.get('city'),
  'latitude':row.get('latitude'),'longitude':row.get('longitude')}
def validate(snapshot,require_accepted=False):
 if not isinstance(snapshot,dict):raise ValueError('Snapshot must be an object')
 if not isinstance(snapshot.get('sourceId'),str) or not snapshot['sourceId']:raise ValueError('Invalid snapshot source')
 if require_accepted and snapshot.get('reviewStatus')!='ACCEPTED':raise ValueError('Previous snapshot has not been accepted')
 if snapshot.get('fetchStatus')!='SUCCESS' or snapshot.get('complete') is not True:raise ValueError('Source capture failed or incomplete')
 rows=snapshot.get('rows')
 if not isinstance(rows,list) or not rows:raise ValueError('Empty or invalid source rows')
 if snapshot.get('observedSourceTotal')!=len(rows):raise ValueError('Source denominator mismatch')
 if snapshot.get('rowsSha256')!=digest(rows):raise ValueError('Source row hash mismatch')
 ids=[]
 for row in rows:
  if not isinstance(row,dict):raise ValueError('Source row must be an object')
  if row.get('sourceId')!=snapshot.get('sourceId'):raise ValueError('Mixed sources in snapshot')
  if not row.get('id') or row.get('sourceRecordId') is None:raise ValueError('Missing source identity')
  if not isinstance(row['sourceRecordId'],str) or not row['sourceRecordId'].strip():raise ValueError('Invalid source record identity')
  if row['id']!=row['sourceId']+'-'+row['sourceRecordId']:raise ValueError('Inconsistent source identity')
  for field in ('name','countryCode','facilityClass','sourceUrl','observedAt'):
   if not isinstance(row.get(field),str) or not row[field].strip():raise ValueError('Invalid row field: '+field)
  parsed=urlparse(row['sourceUrl'])
  if parsed.scheme!='https' or not parsed.netloc:raise ValueError('Invalid source URL')
  try: stamp=datetime.fromisoformat(row['observedAt'].replace('Z','+00:00'))
  except ValueError:raise ValueError('Invalid observedAt timestamp')
  if stamp.tzinfo is None:raise ValueError('observedAt requires timezone')
  classes=row.get('facilityClasses',[])
  if not isinstance(classes,list) or any(not isinstance(x,str) or not x for x in classes):raise ValueError('Invalid facility classes')
  status=row.get('sourceReportedStatus',{})
  if not isinstance(status,dict):raise ValueError('Invalid source reported status')
  if 'value' in status and status['value'] is not None and not isinstance(status['value'],str):raise ValueError('Invalid status value')
  for field in ('siteId','siteName','city','lifecycleStatus','sourceFacilityCategory','sourceFacilityType'):
   if row.get(field) is not None and not isinstance(row[field],str):raise ValueError('Invalid optional field: '+field)
  for field,limit in (('latitude',90),('longitude',180)):
   value=row.get(field)
   if value is not None and (isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or abs(value)>limit):raise ValueError('Invalid coordinate')
  ids.append(row['id'])
 if len(set(ids))!=len(ids):raise ValueError('Duplicate source identity')
 return {r['id']:r for r in rows}
def queue(accepted,capture):
 before=validate(accepted,True)
 result={'schemaVersion':'nuclear-change-review-1.0','sourceId':accepted['sourceId'],
  'acceptedSnapshot':copy.deepcopy(accepted),'acceptedSnapshotChanged':False,'events':[],
  'queue':[],'errors':[],'publicationStatus':'INTERNAL_REVIEW','releaseReady':False}
 try:
  if not isinstance(capture,dict):raise ValueError('Capture must be an object')
  if capture.get('sourceId')!=accepted['sourceId']:raise ValueError('Capture source differs from accepted source')
  after=validate(capture)
 except (ValueError,TypeError,KeyError) as exc:
  result['errors']=[{'type':'SOURCE_CAPTURE_REJECTED','reason':str(exc)}];return result
 for key in sorted(set(before)|set(after)):
  old=before.get(key);new=after.get(key)
  kind='SOURCE_ADDED' if old is None else 'SOURCE_MISSING' if new is None else 'SOURCE_CHANGED'
  changes={}
  if old is not None and new is not None:
   left,right=material(old),material(new);changes={k:{'before':left[k],'after':right[k]} for k in left if left[k]!=right[k]}
   if not changes:continue
  item={'sourceId':accepted['sourceId'],'sourceEntityId':key,'changeType':kind,'changes':changes,
   'before':copy.deepcopy(old),'after':copy.deepcopy(new),'reviewStatus':'PENDING',
   'publicationStatus':'INTERNAL_REVIEW','eventId':None,
   'note':'Source change is a review signal, not an incident or independently confirmed operational change'}
  item['id']='nuclear-review-'+digest({'sourceId':item['sourceId'],'id':key,'type':kind,
    'before':material(old) if old else None,'after':material(new) if new else None})[:24]
  result['queue'].append(item)
 if len(after)<len(before):
  result['errors'].append({'type':'SOURCE_INVENTORY_SHRINKAGE_REVIEW','before':len(before),'after':len(after),
   'note':'Missing source rows are not treated as facility closure or deletion'})
 result['captureEvidence']={'rowsSha256':capture['rowsSha256'],'retrievedAt':capture.get('retrievedAt'),
  'observedSourceTotal':capture['observedSourceTotal']}
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--accepted',type=Path,required=True);p.add_argument('--capture',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 if a.out.exists():raise SystemExit('Review output already exists; preserve it and choose a new path')
 result=queue(json.loads(a.accepted.read_text()),json.loads(a.capture.read_text()))
 a.out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps({'pending':len(result['queue']),'errors':len(result['errors']),'acceptedSnapshotChanged':False,'eventsPublished':0}))
