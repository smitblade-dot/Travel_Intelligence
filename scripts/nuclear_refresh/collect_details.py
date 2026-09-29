"""Read only exact newly observed public URLs; persist projected facility data only."""
import argparse,concurrent.futures,hashlib,json,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin,urlparse
from facility_fields import fields,Text,ISO
SOURCES={'iaea-nfcis':'https://infcis.iaea.org/NFCFDB/facilities','iaea-piedb':'https://infcis.iaea.org/piedb/facilities'}
def validate_directory(d):
 source=d['sourceId'];base=SOURCES[source]
 if d.get('fetchStatus')!='SUCCESS' or d.get('complete') is not True:raise ValueError('Incomplete directory')
 ids=[];end=0;total=None;urls=[]
 for page in d['pages']:
  m=re.fullmatch(r'(\d+)\s*[-–]\s*(\d+)\s+of\s+(\d+)',page['visibleRange'].strip())
  if not m:raise ValueError('Bad page range')
  start,stop,n=map(int,m.groups());total=n if total is None else total
  if start!=end+1 or n!=total or stop<start or stop>n or len(page['hrefs'])!=stop-start+1:raise ValueError('Directory accounting mismatch')
  for href in page['hrefs']:
   url=urljoin(base,href);u=urlparse(url);prefix=urlparse(base).path.replace('facilities','facility/Details/')
   if u.scheme!='https' or u.netloc!='infcis.iaea.org' or not u.path.startswith(prefix) or not u.path[len(prefix):].isdigit() or u.query or u.fragment:raise ValueError('Unexpected detail link')
   ids.append(u.path[len(prefix):]);urls.append(url)
  end=stop
 if not ids or len(set(ids))!=len(ids) or end!=total or len(ids)!=total or d['observedTotal']!=total:raise ValueError('Incomplete/repeated directory')
 return list(zip(ids,urls))
def parse(source,key,url,raw,observed):
 t=Text();t.feed(raw.decode());v=t.out;f=fields(raw.decode())
 def before(label):
  i=v.index(label)
  if not i:raise ValueError('Missing field '+label)
  return v[i-1]
 if source=='iaea-nfcis':
  name=before('Download PDF');country=f['Country'];ref=f['IAEA Ref No']
  if not ref or ref.split(' - ')[0]!=key:raise ValueError('Source identity mismatch')
  extra={'facilityClass':'FUEL_CYCLE','sourceFacilityCategory':f.get('Facility Category Type'),'sourceFacilityType':f.get('Facility Type'),'lifecycleStatus':f.get('Status'),'siteName':f.get('Site'),'sourceUpdatedAtText':f.get('Last updated data source'),'databaseUpdatedAtText':f.get('Last Update')}
 else:
  name=before('Facility Name');country=before('Country');ref=before('IAEA Ref No')
  if ref!=key+'-PIE':raise ValueError('Source identity mismatch')
  updated=before('Last Update');extra={'facilityClass':'POST_IRRADIATION','lifecycleStatus':None,'databaseUpdatedAtText':None if updated=='Country' else updated}
 if not name or name in ('Help','Facilities') or country not in ISO:raise ValueError('Missing identity or unknown country')
 return {'id':source+'-'+key,'sourceId':source,'sourceRecordId':key,'sourceUrl':url,'observedAt':observed,'evidenceSha256':hashlib.sha256(raw).hexdigest(),'name':name,'countryCode':ISO[country],'countryName':country,'recordKind':'FACILITY','latitude':None,'longitude':None,'publicationStatus':'INTERNAL_REVIEW','statusUncertainty':'Source report is dated evidence, not current operating verification','contentRights':'IAEA_GENERAL_TERMS_ATTRIBUTION_REQUIRED_SPECIFIC_RIGHTS_UNRESOLVED','automationPermission':'UNKNOWN',**extra}
def fetch(source,pair):
 key,url=pair
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'Travel-Intelligence/1.0 (public inventory review)'})
  with urllib.request.urlopen(req,timeout=40) as response:
   if response.url!=url:raise ValueError('Unexpected redirect')
   raw=response.read();observed=datetime.now(timezone.utc).isoformat()
  return parse(source,key,url,raw,observed),None
 except Exception as exc:return None,{'sourceRecordId':key,'sourceUrl':url,'error':str(exc),'observedAt':datetime.now(timezone.utc).isoformat()}
def collect(directory,out,fetcher=fetch):
 pairs=validate_directory(directory);out=Path(out);out.mkdir(parents=True,exist_ok=False);rows=[];failures=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  for row,error in pool.map(lambda pair:fetcher(directory['sourceId'],pair),pairs):
   if error:failures.append(error)
   else:rows.append(row)
 result={'schemaVersion':'recovery-supplement-1.0','releaseReady':False,'publicationStatus':'INTERNAL_REVIEW','coverage':{'imported':len(rows),'observedDirectoryTotal':directory['observedTotal'],'unresolved':len(failures),'scopeStatus':'SOURCE_DIRECTORY_EXCEPTIONS' if failures else 'SOURCE_DIRECTORY_COMPLETE_NOT_GLOBAL_COMPLETENESS_PROOF'},'discoveryEvidence':directory,'facilities':rows,'unresolved':failures}
 (out/'candidate.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=collect(json.loads(a.directory.read_text()),a.out);raise SystemExit(2 if r['unresolved'] else 0)
