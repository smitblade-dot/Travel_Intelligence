import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
s=importlib.util.spec_from_file_location('nuclear',Path(__file__).with_name('refresh_nuclear_global.py'))
n=importlib.util.module_from_spec(s);s.loader.exec_module(n)

class PreserveTests(unittest.TestCase):
    def run_case(self, inventory, profiles):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);out=root/'nuclear.json';prof=root/'profiles.json';scope=root/'scope.json'
            out.write_text(inventory if isinstance(inventory,str) else json.dumps(inventory));prof.write_text(json.dumps(profiles));scope.write_text('{"sources":[]}')
            before=(out.read_bytes(),prof.read_bytes())
            with patch.multiple(n,OUT=out,PROFILES=prof,SCOPE=scope,COUNTRY_SCOPE=scope),patch.object(n,'fetch',side_effect=AssertionError('must not fetch')):
                self.assertEqual(n.main(),1)
            self.assertEqual(before,(out.read_bytes(),prof.read_bytes()))
    def test_facility_identity_is_preserved(self):
        self.run_case({'facilities':[{'id':'accepted'}],'locations':[]},{'profiles':[]})
    def test_site_identity_is_preserved(self):
        self.run_case({'facilities':[],'locations':[{'id':'site'}]},{'profiles':[]})
    def test_corrupt_inventory_is_preserved(self):self.run_case('{broken',{'profiles':[]})
    def test_wrong_collection_type_is_preserved(self):self.run_case({'facilities':{}},{'profiles':[]})
    def test_richer_country_profile_is_preserved(self):
        self.run_case({'facilities':[],'locations':[]},{'profiles':[{'iso2':'xx','sourceStatus':'FACILITY_LEVEL'}]})
    def test_legacy_aggregate_refresh_still_runs(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);out=root/'nuclear.json';prof=root/'profiles.json';scope=root/'scope.json'
            out.write_text('{"facilities":[],"locations":[]}');prof.write_text('{"profiles":[{"sourceStatus":"COUNTRY_AGGREGATE_ONLY"}]}');scope.write_text('{"sources":[]}')
            with patch.multiple(n,OUT=out,PROFILES=prof,SCOPE=scope,COUNTRY_SCOPE=scope),patch.object(n,'fetch',return_value='fixture'),patch.object(n,'parse_pris_country_table',return_value=[('FRANCE',56,61000)]):
                self.assertEqual(n.main(),0)
            self.assertEqual(json.loads(out.read_text())['countries'][0]['iso2'],'fr')

if __name__=='__main__':unittest.main()
