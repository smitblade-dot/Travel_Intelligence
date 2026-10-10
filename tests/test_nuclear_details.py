import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/nuclear_refresh'))
from collect_details import collect,validate_directory,parse
class Tests(unittest.TestCase):
 def directory(self):return {'sourceId':'iaea-nfcis','fetchStatus':'SUCCESS','complete':True,'observedTotal':1,'pages':[{'visibleRange':'1-1 of 1','hrefs':['facility/Details/0']}]}
 def test_authoritative_quality_retained(self):
  from unittest.mock import patch
  class ParsedText:
   out=['Example','Download PDF']
   def feed(self,_):pass
  with patch('collect_details.Text',ParsedText),patch('collect_details.fields',return_value={'Country':'CANADA','IAEA Ref No':'0 - Example'}):
   import collect_details
   with patch.dict(collect_details.ISO,{'CANADA':'ca'}):
    row=parse('iaea-nfcis','0','https://infcis.iaea.org/NFCFDB/facility/Details/0',b'fixture','2026-09-29T00:00:00Z')
  self.assertEqual(row['sourceQuality'],'A');self.assertEqual(row['publicationStatus'],'INTERNAL_REVIEW')
 def test_zero_identity(self):self.assertEqual(validate_directory(self.directory())[0][0],'0')
 def test_stale_partial_rejected(self):
  d=self.directory();d['complete']=False
  with self.assertRaises(ValueError):validate_directory(d)
 def test_redirect_or_unobserved_origin_rejected(self):
  d=self.directory();d['pages'][0]['hrefs']=['https://example.org/facility/Details/0']
  with self.assertRaises(ValueError):validate_directory(d)
 def test_capture_failure_never_complete(self):
  with tempfile.TemporaryDirectory() as t:
   r=collect(self.directory(),Path(t)/'run',lambda *_:(None,{'error':'timeout'}));self.assertEqual(r['coverage']['scopeStatus'],'SOURCE_DIRECTORY_EXCEPTIONS');self.assertFalse(r['releaseReady'])
 def test_no_data_html_rejected(self):
  with self.assertRaises((KeyError,ValueError)):parse('iaea-nfcis','0','https://infcis.iaea.org/NFCFDB/facility/Details/0',b'<html>No data</html>','2026-09-29T00:00:00Z')
if __name__=='__main__':unittest.main()
