"""Public PRIS/RRDB capture adapter for the review-only coordinator.
Uses the reviewed capture/parser package supplied explicitly; no canonical writes.
PIE/NFC must supply their own newly complete enumeration evidence.
"""
import argparse,hashlib,json,sys
from pathlib import Path
from refresh_review import envelope,atomic_json

def adapt(document):
 coverage={r['sourceId']:r for r in document['coverage']['sourceCoverage']};result={}
 for source in ('iaea-pris','iaea-rrdb'):
  c=coverage[source]
  if c['imported']!=c['denominator'] or c['scopeStatus']!='SOURCE_LIST_COMPLETE_NOT_GLOBAL_COMPLETENESS_PROOF':raise ValueError('Source list incomplete: '+source)
  if source=='iaea-pris' and c['countryQueriesCompleted']!=c['countryQueriesExpected']:raise ValueError('PRIS country query gap')
  snapshots=[s for s in document['sourceSnapshots'] if ('pris-stats.iaea.org' in s['url'])==(source=='iaea-pris') and (source=='iaea-pris' or '/rrdb/' in s['url'])]
  if not snapshots:raise ValueError('Missing source manifest')
  evidence={'sourceId':source,'fetchStatus':'SUCCESS','complete':True,'observedSourceTotal':c['denominator'],
   'retrievedAt':max(s['retrievedAt'] for s in snapshots),'evidenceSha256':hashlib.sha256(json.dumps(snapshots,sort_keys=True).encode()).hexdigest(),'sourceSnapshots':snapshots,'denominatorBasis':c['denominatorBasis']}
  result[source]=envelope(document,source,c['denominator'],evidence)
 return result

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--collector-package',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 # Package is the checked-in reviewed collector, not a remote downloaded module.
 sys.path.insert(0,str(a.collector_package.resolve()))
 from collect_snapshots import collect
 collect(a.out/'raw')
 document=json.loads((a.out/'raw/inventory.candidate.json').read_text());paths={}
 for source,snapshot in adapt(document).items():
  dest=a.out/(source+'.capture.json');atomic_json(dest,snapshot);paths[source]=str(dest.resolve())
 atomic_json(a.out/'captures.json',paths)
