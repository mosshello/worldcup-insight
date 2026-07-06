"""世界杯淘汰赛概率榜测试。"""

import unittest

from worldcup_mvp.tournament_forecast import build_tournament_forecast


class TournamentForecastTests(unittest.TestCase):
    def test_probability_totals_and_rankings(self) -> None:
        report = build_tournament_forecast()
        rankings = report["rankings"]
        self.assertEqual(len(rankings), 16)
        self.assertAlmostEqual(sum(item["champion_probability"] for item in rankings), 1.0, places=3)
        self.assertAlmostEqual(sum(item["final_probability"] for item in rankings), 2.0, places=3)
        self.assertGreaterEqual(rankings[0]["champion_probability"], rankings[1]["champion_probability"])

    def test_final_pairs_follow_opposite_halves(self) -> None:
        report = build_tournament_forecast()
        left = {"加拿大", "摩洛哥", "巴拉圭", "法国", "葡萄牙", "西班牙", "美国", "比利时"}
        right = {"巴西", "挪威", "墨西哥", "英格兰", "瑞士", "哥伦比亚", "阿根廷", "埃及"}
        for pair in report["final_pairs"]:
            self.assertIn(pair["left"], left)
            self.assertIn(pair["right"], right)


if __name__ == "__main__":
    unittest.main()
