import sys,json,unittest
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from operating_status import classify
from refresh_oilgas import build_commodity_entry
NOW=datetime(2026,10,8,tzinfo=timezone.utc)
class OperatingStatusTests(unittest.TestCase):
 def test_policy_cases(self):
  normal=dict(map_status='Green',status='Normal',verification='confirmed',source='Operator',verified_at='2026-10-07')
  for row,expected in [(normal,'Green'),({**normal,'status':'Presumed normal'},'Unknown'),({**normal,'verified_at':'2026-09-01'},'Unknown'),({**normal,'verified_at':'2026-10'},'Unknown'),({},'Unknown'),({'map_status':'Red','status':'Unknown restart'},'Red')]:
   self.assertEqual(classify(row,NOW),expected)
 def test_summary_mixed_and_unknown_only(self):
  for rows,expected in [([{'map_status':'Green','status':'Presumed normal'}],'Unknown'),([{'map_status':'Red'},{'map_status':'Green','status':'Presumed normal'}],'Red')]:
   d={'countries':[{'country':'Test'}],'infrastructure':[dict(i,country='Test',infrastructure_name=str(n)) for n,i in enumerate(rows)],'meta':{'generated':'2026-10-08'}}
   result=build_commodity_entry(d,'Test',gas=True,now=NOW)
   self.assertEqual(result['worst_status'],expected)
   self.assertEqual(result['unverified_infrastructure_count'],1)
   self.assertIn('Unknown is not evidence of disruption',result['operating_summary'])
   self.assertEqual(len(result['operating_evidence']),len(rows))
 def test_crude_unchanged(self):
  d={'countries':[{'country':'Test'}],'infrastructure':[{'country':'Test','map_status':'Green'}]}
  self.assertEqual(build_commodity_entry(d,'Test')['worst_status'],'Green')
if __name__=='__main__':unittest.main()
