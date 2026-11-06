import unittest
from scripts.audit_big_five_quality import roster_index,compare

def fixture():
    records=[{'league_code':'ENG','understat_id':'1','player_id':str(i),'team_id':str(i),'h_a':s,'x_g':v,'season_input':'2026'}
        for i,s,v in [(1,'h','1.2'),(2,'a','0.0')]]
    fact={'source_id':'understat:1','facts':{'league_code':'ENG','understat_id':'1','home_team_id':'1','away_team_id':'2','home_xg':1.2,'away_xg':0}}
    return records,fact

class QualityAuditTests(unittest.TestCase):
    def test_numeric_difference_does_not_assert_the_measurement_is_wrong(self):
        rows,fact=fixture();groups,conflicts=roster_index(rows)
        result=compare(fact,groups,conflicts);self.assertEqual(result['status'],'numerically_equal')
        fact['facts']['home_xg']=1.0;result=compare(fact,groups,conflicts)
        self.assertEqual(result['status'],'aggregate_difference_definition_unverified')
        self.assertFalse(result['aggregation_definitions_verified']);self.assertTrue(result['diagnostic_only'])
        self.assertEqual(fact['facts']['home_xg'],1.0)

    def test_duplicate_requested_seasons_deduplicate_but_different_values_do_not(self):
        rows,fact=fixture();rows.append({**rows[0],'season_input':'2025'})
        groups,conflicts=roster_index(rows);self.assertFalse(conflicts)
        self.assertEqual(compare(fact,groups,conflicts)['status'],'numerically_equal')
        rows[-1]['x_g']='2.2';groups,conflicts=roster_index(rows)
        self.assertEqual(compare(fact,groups,conflicts)['status'],'conflicting_player_records')

    def test_missing_team_and_nonfinite_value_are_not_zero(self):
        rows,fact=fixture();rows[1]['team_id']='wrong';groups,c=roster_index(rows)
        self.assertEqual(compare(fact,groups,c)['status'],'missing_or_conflicting_player_identity')
        rows,fact=fixture();rows[0]['x_g']='NaN';groups,c=roster_index(rows)
        self.assertEqual(compare(fact,groups,c)['status'],'invalid_numeric_value')

if __name__=='__main__':unittest.main()
