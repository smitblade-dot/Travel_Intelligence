"""Capture all public nuclear source lists and stage a review, never publish.
Run this on the reviewed CI browser runtime. No credentials required.
"""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
from capture_public_inventory import adapt
from collect_details import collect as collect_details
from refresh_review import envelope,atomic_json,run
HERE=Path(__file__).resolve().parent

def execute(repo,out,accepted,bootstrap=False,node='node'):
 out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);paths={};failures=[]
 try:
  from collect_snapshots import collect
  collect(out/'pris-rrdb')
  base=json.loads((out/'pris-rrdb/inventory.candidate.json').read_text())
  for source,capture in adapt(base).items():
   p=out/(source+'.capture.json');atomic_json(p,capture);paths[source]=str(p)
 except Exception as exc:failures.append({'sources':['iaea-pris','iaea-rrdb'],'error':str(exc)})
 for source in ('iaea-nfcis','iaea-piedb'):
  try:
   directory_path=out/(source+'-directory')
   subprocess.run([node,str(HERE/'browser/collect-directory.mjs'),source,str(directory_path)],check=True,timeout=1800)
   directory=json.loads((directory_path/'directory.json').read_text())
   doc=collect_details(directory,out/(source+'-details'))
   if doc['unresolved']:raise ValueError('Detail capture incomplete; see per-source candidate failure ledger')
   evidence={'sourceId':source,'fetchStatus':'SUCCESS','complete':True,'observedSourceTotal':directory['observedTotal'],
      'retrievedAt':max(r['observedAt'] for r in doc['facilities']),
      'evidenceSha256':hashlib.sha256((directory_path/'directory.json').read_bytes()).hexdigest(),
      'directoryEvidence':directory}
   capture=envelope(doc,source,directory['observedTotal'],evidence)
   p=out/(source+'.capture.json');atomic_json(p,capture);paths[source]=str(p)
  except Exception as exc:failures.append({'sources':[source],'error':str(exc)})
 atomic_json(out/'capture-failures.json',failures);atomic_json(out/'captures.json',paths)
 return run(Path(repo)/'data/nuclear_global.json',Path(repo)/'data/nuclear_country_profiles.json',accepted,paths,out/'review',bootstrap)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--accepted-snapshots',type=Path,required=True);p.add_argument('--bootstrap-review',action='store_true');p.add_argument('--node',default='node');a=p.parse_args();r=execute(a.repo,a.out,a.accepted_snapshots,a.bootstrap_review,a.node);print(json.dumps({'captureComplete':r['captureComplete'],'releaseReady':False,'errors':len(r['errors'])}));raise SystemExit(0 if r['captureComplete'] else 2)
