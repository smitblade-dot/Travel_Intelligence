#!/usr/bin/env python3
"""Build a review-only nuclear inventory from official public source snapshots.
Never writes production data. Unknown coordinates and lifecycle values stay unknown.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urljoin

PRIS = 'https://pris-stats.iaea.org/'
RRDB = 'https://nucleus.iaea.org/rrdb/api/ReactorListSearch/getreactorlistdetails'
PIE = 'https://infcis.iaea.org/piedb/facilities'

def read(path):
    return json.loads(path.read_text())

def require(value, message):
    if not value:
        raise ValueError(message)

def evidence(path, url, root):
    manifest=read(root/'retrieval_manifest.json')
    entries=[item for item in manifest if item['url']==url]
    require(len(entries)==1, 'Missing or duplicate capture metadata: '+url)
    capture=entries[0]
    require(capture.get('retrievedAt'), 'Missing retrieval timestamp')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    require(capture.get('snapshotSha256')==digest, 'Snapshot hash mismatch: '+str(path))
    return {'url':url,'sha256':digest,'rawSha256':capture['rawSha256'],
            'retrievedAt':capture['retrievedAt'], 'sourceUpdatedAt':capture.get('sourceUpdatedAt')}

def facility(source, key, name, country, country_name, status, url, observed):
    require(key is not None and bool(name) and bool(country), 'Missing facility identity')
    return {'id': f'{source}-{key}', 'sourceId': source, 'sourceRecordId': str(key),
            'name': name, 'countryCode': country.lower(), 'countryName': country_name,
            'lifecycleStatus': status, 'sourceUrl': url, 'observedAt': observed,
            'sourceQuality': 'A', 'contentRights': 'IAEA_GENERAL_TERMS_ATTRIBUTION_REQUIRED_SPECIFIC_RIGHTS_UNRESOLVED',
            'rightsEvidenceUrls': ['https://www.iaea.org/about/terms-of-use',
                                  'https://nucleus.iaea.org/Pages/Others/Disclaimer.aspx'],
            'automationPermission': 'UNKNOWN', 'accessMethod': 'PUBLIC_WEB',
            'publicationStatus': 'INTERNAL_REVIEW', 'latitude': None, 'longitude': None}

class PieParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows=[]; self.row=None; self.cell=None; self.href=None; self.text=[]
    def handle_starttag(self, tag, attrs):
        if tag=='tr': self.row=[]
        if tag=='td' and self.row is not None: self.cell=[]; self.href=None
        if tag=='a' and self.cell is not None: self.href=dict(attrs).get('href')
    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag=='td' and self.cell is not None:
            self.row.append((' '.join(''.join(self.cell).split()), self.href)); self.cell=None
        if tag=='tr' and self.row is not None:
            if self.row:self.rows.append(self.row)
            self.row=None

def build(root):
    facilities=[]; sites={}; snapshots=[]; counts=[]
    countries=read(root/'pris_countries.json')['items']
    require(countries, 'Empty PRIS country manifest')
    codes=[c['countryCode'] for c in countries]
    require(len(codes)==len(set(codes)), 'Duplicate country manifest entries')
    snapshots.append(evidence(root/'pris_countries.json', PRIS+'country/countries/', root))
    for c in countries:
        code=c['countryCode']; path=root/'pris'/f'{code}.json'
        url=PRIS+'reactor/reactors-by-code/'+code
        ev=evidence(path,url,root); snapshots.append(ev)
        rows=read(path)['items']; require(rows, 'Empty PRIS country '+code)
        for row in rows:
            require(row['countryCode']==code, 'PRIS country mismatch')
            require(row.get('informationStatusCode')=='PUB', 'Non-public PRIS record')
            f=facility('iaea-pris',row['id'],row['unitName'],code,row['countryName'],row['statusName'],url,ev['retrievedAt'])
            f['facilityClass']='NUCLEAR_POWER'
            f['recordKind']='REACTOR_UNIT'
            if row.get('siteId') is not None and row.get('siteName'):
                sid=f"iaea-pris-site-{row['siteId']}"
                site={'id':sid,'name':row['siteName'],'countryCode':code.lower(),
                      'sourceId':'iaea-pris','sourceRecordId':str(row['siteId']),
                      'sourceUrl':url,'observedAt':ev['retrievedAt'],
                      'latitude':None,'longitude':None,'coordinateStatus':'NOT_IN_SOURCE',
                      'publicationStatus':'INTERNAL_REVIEW'}
                if sid in sites:
                    require((sites[sid]['name'],sites[sid]['countryCode'])==(site['name'],site['countryCode']), 'Conflicting site identity')
                sites[sid]=site;f['siteId']=sid
            facilities.append(f)
    pris_count=len(facilities)
    counts.append({'sourceId':'iaea-pris','imported':pris_count,'denominator':pris_count,
                   'denominatorBasis':'All records returned by all countries in the retrieved public PRIS country manifest',
                   'countryQueriesCompleted':len(codes),'countryQueriesExpected':len(codes),
                   'scopeStatus':'SOURCE_LIST_COMPLETE_NOT_GLOBAL_COMPLETENESS_PROOF'})
    path=root/'rrdb_data.json'; doc=read(path);require(doc.get('success') is True,'RRDB response failed')
    ev=evidence(path,RRDB,root);snapshots.append(ev);accepted=[];excluded=[]
    for row in doc['data']:
        # Match the public report's accepted-data filter. Retain cancelled/historical
        # entities in this infrastructure layer; no lifecycle is treated as an event.
        if row.get('tempRreactorId')==row['rreactorId'] or row.get('rrReactorStatus')!=6:
            excluded.append(str(row['rreactorId']));continue
        f=facility('iaea-rrdb',row['rreactorId'],row['facName'],row['isoCode'],row['country'],row['statusLngDesc'],RRDB,ev['retrievedAt'])
        f['facilityClass']='RESEARCH_TEST_REACTOR';f['recordKind']='RESEARCH_REACTOR'
        f['city']=row.get('closestCity') or None; f['iaeaCode']=row.get('iaeaCode')
        accepted.append(f)
    require(accepted,'No accepted RRDB reactors');facilities.extend(accepted)
    counts.append({'sourceId':'iaea-rrdb','imported':len(accepted),'denominator':len(accepted),
                   'denominatorBasis':'All accepted RR public identities in retrieved search-list response, including historical and cancelled records',
                   'excludedUnacceptedIds':excluded,'scopeStatus':'SOURCE_LIST_COMPLETE_NOT_GLOBAL_COMPLETENESS_PROOF'})
    path=root/'piedb.html';parser=PieParser();parser.feed(path.read_text());ev=evidence(path,PIE,root);snapshots.append(ev)
    country_map={c['name'].lower():c['countryCode'] for c in countries}
    country_map.update({'czech republic':'CZ'})
    pie=[]
    for row in parser.rows:
        if len(row)<2 or not row[1][1] or 'facility/Details/' not in row[1][1]:continue
        country=row[0][0]; name,link=row[1];key=link.rstrip('/').split('/')[-1]
        require(country.lower() in country_map,'Unmapped PIE country: '+country)
        f=facility('iaea-piedb',key,name,country_map[country.lower()],country,None,urljoin(PIE,link),ev['retrievedAt'])
        f['facilityClass']='POST_IRRADIATION';f['recordKind']='FACILITY';f['statusUncertainty']='Directory presence does not establish current operating status'
        pie.append(f)
    total=re.search(r'\d+\s*[-–]\s*\d+\s+of\s+(\d+)', ' '.join(parser.text))
    require(pie and total,'PIEDB table or pagination total missing'); facilities.extend(pie)
    counts.append({'sourceId':'iaea-piedb','imported':len(pie),'denominator':int(total.group(1)),
                   'denominatorBasis':'Public directory pagination total','scopeStatus':'PARTIAL_FIRST_PAGE'})
    counts.append({'sourceId':'iaea-nfcis','imported':0,'denominator':None,
                   'denominatorBasis':'Not measured; public page returns filters without facility rows',
                   'scopeStatus':'NOT_CONNECTED'})
    ids=[f['id'] for f in facilities];require(len(ids)==len(set(ids)), 'Duplicate source facility identity')
    return {'schemaVersion':'recovery-candidate-1.0','generatedAt':datetime.now(timezone.utc).isoformat(),
            'status':'INTERNAL_REVIEW','releaseReady':False,
            'coverage':{'sourceFacilityRecords':len(facilities),'distinctCountries':len({f['countryCode'] for f in facilities}),
                        'sourceNamedSites':len(sites),'geocodedSites':0,'sourceCoverage':counts,
                        'note':'Counts are source entities, not globally deduplicated physical facilities. No global completion percentage is asserted.'},
            'facilities':sorted(facilities,key=lambda x:x['id']),'locations':sorted(sites.values(),key=lambda x:x['id']),
            'sourceSnapshots':snapshots,
            'releaseBlockers':['Independent QA pending','NFCIS ingestion incomplete','PIEDB pagination incomplete',
              'Cross-source identity reconciliation pending','Coordinate enrichment pending',
              'Source-specific restrictions and third-party rights unresolved; general IAEA reuse terms checked; recurring automation permission unverified','Monitoring freshness and failure tests not integrated',
              'Production schema/UI integration pending; this file is not production data']}

def write_candidate(root, output):
    candidate=build(root)  # Validate every response before replacing any prior candidate.
    temp=output.with_suffix('.tmp');temp.write_text(json.dumps(candidate,indent=2,ensure_ascii=False)+'\n');temp.replace(output)
    return candidate

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshots',type=Path,default=Path(__file__).parent/'verified-capture');p.add_argument('--output',type=Path,default=Path(__file__).with_name('inventory.candidate.json'));a=p.parse_args()
    c=write_candidate(a.snapshots,a.output)
    print(json.dumps(c['coverage'],indent=2))
