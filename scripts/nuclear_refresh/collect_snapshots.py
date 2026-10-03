#!/usr/bin/env python3
"""Public read-only source capture into review artifacts.
Never overwrites a successful snapshot set: creates a new directory and fails closed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
from datetime import datetime, timezone

PRIS='https://pris-stats.iaea.org/'
RRDB='https://nucleus.iaea.org/rrdb/api/ReactorListSearch/getreactorlistdetails'
# Keep only public inventory fields; do not retain user GUIDs or fuel data returned
# incidentally in the source response.
RR_FIELDS={'rreactorId','tempRreactorId','rrReactorStatus','isoCode','facName','iaeaCode','country','closestCity','statusLngDesc'}
PRIS_FIELDS={'id','countryCode','unitName','countryName','statusName','siteId','siteName','informationStatusCode'}

def collect(out):
    out.mkdir(exist_ok=False,parents=True);(out/'pris').mkdir()
    manifest=[]
    def fetch(url,name,keep=None):
        request=urllib.request.Request(url,headers={'User-Agent':'Travel-Intelligence/1.0 (manual source verification)'})
        with urllib.request.urlopen(request,timeout=40) as response:
            raw=response.read()
            manifest.append({'url':url,'resolvedUrl':response.url,'retrievedAt':datetime.now(timezone.utc).isoformat(),
                             'rawSha256':hashlib.sha256(raw).hexdigest(), 'sourceUpdatedAt':response.headers.get('Last-Modified')})
        if keep:
            data=json.loads(raw);key='items' if 'items' in data else 'data'
            data[key]=[{k:v for k,v in row.items() if k in keep} for row in data[key]]
            raw=(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode()
        manifest[-1]['snapshotSha256']=hashlib.sha256(raw).hexdigest()
        manifest[-1]['snapshotPath']=name
        (out/name).write_bytes(raw)
        return raw
    try:
        countries=json.loads(fetch(PRIS+'country/countries/','pris_countries.json'))['items']
        if not countries:raise ValueError('Empty country manifest')
        for country in countries:
            code=country['countryCode']
            if not isinstance(code,str) or not code.isalpha() or len(code)!=2:raise ValueError('Invalid country code')
            fetch(PRIS+'reactor/reactors-by-code/'+code,'pris/'+code+'.json',PRIS_FIELDS)
        fetch(RRDB,'rrdb_data.json',RR_FIELDS)
        fetch('https://infcis.iaea.org/piedb/facilities','piedb.html')
    finally:
        (out/'retrieval_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    from build_inventory import write_candidate
    write_candidate(out,out/'inventory.candidate.json')
    print('Captured and validated review candidate:',out)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args();collect(a.output)
