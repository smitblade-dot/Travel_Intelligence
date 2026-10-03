import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/nuclear_refresh'))
from refresh_review import run,digest,EXPECTED,envelope,atomic_json
class Tests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.p=Path(self.t.name);self.inv=self.p/'inventory.json';self.pro=self.p/'profiles.json';self.inv.write_bytes(b'{"facilities":[{"id":"preserve"}]}');self.pro.write_bytes(b'{"profiles":[{"iso2":"ca"}]}');self.acc=self.p/'accepted';self.acc.mkdir();self.captures={}
  for source in EXPECTED:
   row={'id':source+'-0','sourceId':source,'sourceRecordId':'0','name':'Name','countryCode':'ca','facilityClass':'POWER_REACTOR','sourceUrl':'https://example.org/0','observedAt':'2026-09-29T00:00:00Z'}
   s={'sourceId':source,'fetchStatus':'SUCCESS','complete':True,'reviewStatus':'ACCEPTED','rows':[row],'rowsSha256':digest([row]),'observedSourceTotal':1};atomic_json(self.acc/(source+'.json'),s);p=self.p/(source+'.capture.json');atomic_json(p,s);self.captures[source]=p
  self.old=(self.inv.read_bytes(),self.pro.read_bytes())
 def tearDown(self):self.t.cleanup()
 def run_it(self):
  r=run(self.inv,self.pro,self.acc,self.captures,self.p/'run');self.assertEqual(self.old,(self.inv.read_bytes(),self.pro.read_bytes()));self.assertFalse(r['releaseReady']);return r
 def test_complete(self):self.assertTrue(self.run_it()['captureComplete'])
 def test_missing_source(self):del self.captures['iaea-piedb'];self.assertFalse(self.run_it()['captureComplete'])
 def test_truncated_capture(self):self.captures['iaea-pris'].write_text('{');self.assertFalse(self.run_it()['captureComplete'])
 def test_no_accepted(self):(self.acc/'iaea-pris.json').unlink();self.assertEqual(self.run_it()['errors'][0]['type'],'ACCEPTED_BASELINE_UNAVAILABLE')
 def test_pending_not_accepted(self):
  p=self.acc/'iaea-pris.json';s=json.loads(p.read_text());s['reviewStatus']='PENDING';atomic_json(p,s);self.assertFalse(self.run_it()['captureComplete'])
 def test_changed_status_queued(self):
  p=self.captures['iaea-pris'];s=json.loads(p.read_text());s['rows'][0]['sourceReportedStatus']={'value':'Operating'};s['rowsSha256']=digest(s['rows']);atomic_json(p,s);r=self.run_it();self.assertEqual(r['sources']['iaea-pris']['pending'],1);self.assertEqual(r['events'],[])
 def test_repeat_output_refused(self):self.run_it();self.assertRaises(ValueError,self.run_it)
 def test_empty_capture(self):
  p=self.captures['iaea-pris'];s=json.loads(p.read_text());s.update(rows=[],rowsSha256=digest([]),observedSourceTotal=0);atomic_json(p,s);self.assertFalse(self.run_it()['captureComplete'])
 def test_envelope_denominator(self):self.assertRaises(ValueError,envelope,{'releaseReady':False,'facilities':[]},'iaea-pris',2,{'sourceId':'iaea-pris','fetchStatus':'SUCCESS','complete':True,'observedSourceTotal':1})
 def test_legacy_success_preserves(self):
  spec=importlib.util.spec_from_file_location('legacy',Path(__file__).resolve().parents[1]/'scripts/refresh_nuclear_global.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.ROOT=self.p;m.OUT=self.inv;m.PROFILES=self.pro;m.load_json=lambda _: {'sources':[]};m.fetch=lambda _:'';m.parse_pris_country_table=lambda _:[('CANADA',19,13000)];self.assertEqual(m.main(),0);self.assertEqual(self.old,(self.inv.read_bytes(),self.pro.read_bytes()));f=list((self.p/'output/nuclear_reviews').glob('*/aggregate_inventory.candidate.json'))[0];self.assertFalse(json.loads(f.read_text())['releaseReady'])
 def test_legacy_failure_visible_preserves(self):
  spec=importlib.util.spec_from_file_location('legacy',Path(__file__).resolve().parents[1]/'scripts/refresh_nuclear_global.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.ROOT=self.p;m.OUT=self.inv;m.PROFILES=self.pro;m.load_json=lambda _: {'sources':[]};m.fetch=lambda _:(_ for _ in ()).throw(OSError('failed'));self.assertEqual(m.main(),2);self.assertEqual(self.old,(self.inv.read_bytes(),self.pro.read_bytes()))
if __name__=='__main__':unittest.main()

class AdapterTests(unittest.TestCase):
 def test_partial_not_upgraded(self):
  from capture_public_inventory import adapt
  d={'coverage':{'sourceCoverage':[{'sourceId':'iaea-pris','imported':1,'denominator':2,'scopeStatus':'PARTIAL'}]}}
  with self.assertRaises(ValueError):adapt(d)
 def test_missing_manifest_not_upgraded(self):
  from capture_public_inventory import adapt
  d={'coverage':{'sourceCoverage':[{'sourceId':'iaea-pris','imported':1,'denominator':1,'scopeStatus':'SOURCE_LIST_COMPLETE_NOT_GLOBAL_COMPLETENESS_PROOF','countryQueriesCompleted':1,'countryQueriesExpected':1}]},'sourceSnapshots':[]}
  with self.assertRaises(ValueError):adapt(d)

class BootstrapTests(unittest.TestCase):
 setUp=Tests.setUp
 tearDown=Tests.tearDown
 def test_bootstrap_is_pending_not_published(self):
  for f in self.acc.glob('*.json'):f.unlink()
  r=run(self.inv,self.pro,self.acc,self.captures,self.p/'bootstrap',True)
  self.assertTrue(r['captureComplete']);self.assertFalse(r['releaseReady']);self.assertEqual(self.old,(self.inv.read_bytes(),self.pro.read_bytes()))
  for s in r['sources'].values():self.assertEqual(s['reviewStatus'],'INITIAL_BASELINE_REVIEW');self.assertTrue(s['acceptanceRequired'])
 def test_bootstrap_invalid_capture_rejected(self):
  (self.acc/'iaea-pris.json').unlink();self.captures['iaea-pris'].write_text('{}')
  r=run(self.inv,self.pro,self.acc,self.captures,self.p/'bootstrap',True);self.assertFalse(r['captureComplete'])

class DiagnosticTests(unittest.TestCase):
 def test_source_failure_visible_without_payload(self):
  from run_refresh import diagnostics
  r=diagnostics({'captureComplete':False,'errors':[{'sourceId':'iaea-piedb','type':'SOURCE_CAPTURE_REJECTED','reason':'Missing capture'}]},[{'sources':['iaea-nfcis'],'error':'Timeout 30000ms exceeded at https://example.org/?token=secret'}])
  self.assertFalse(r['captureComplete']);self.assertFalse(r['releaseReady']);self.assertEqual(r['captureFailures'][0]['sources'],['iaea-nfcis']);self.assertIn('Timeout',r['captureFailures'][0]['reason']);self.assertNotIn('secret',str(r));self.assertEqual(r['reviewErrors'][0]['sourceId'],'iaea-piedb')
 def test_raw_html_and_credentials_not_logged(self):
  from run_refresh import diagnostic_reason
  self.assertNotIn('private payload',diagnostic_reason('<html>private payload</html>'))
  self.assertNotIn('hidden',diagnostic_reason('token=hidden password: hidden'))

class DirectoryFailureTests(unittest.TestCase):
 def test_browser_ledger_reason_not_just_exit_code(self):
  from run_refresh import directory_failure
  import subprocess
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'failure.json').write_text(json.dumps({'sourceId':'iaea-nfcis','fetchStatus':'FAILED','error':'Timeout waiting for Next page at https://example.org/?token=private'}))
   message=directory_failure('iaea-nfcis',p,subprocess.CalledProcessError(2,['node','collector']))
   self.assertIn('Timeout waiting for Next page',message);self.assertNotIn('private',message)
   (p/'failure.json').write_text('{')
   self.assertIn('ledger unavailable',directory_failure('iaea-nfcis',p,'exit2'))
