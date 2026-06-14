import unittest

import dianping_scraper


class DianpingOpenPageHelpersTest(unittest.TestCase):
    def test_extract_shop_id_from_dianping_urls(self):
        cases = {
            "https://www.dianping.com/shop/G2CauBeHQ9je4IAb": "G2CauBeHQ9je4IAb",
            "https://www.dianping.com/shop/G2CauBeHQ9je4IAb/review_all/p2": "G2CauBeHQ9je4IAb",
            "https://m.dianping.com/shopshare/G2CauBeHQ9je4IAb": "G2CauBeHQ9je4IAb",
            "/shop/GaGMofn91UrCLmzu": "GaGMofn91UrCLmzu",
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(dianping_scraper._extract_shop_id_from_url(url), expected)

    def test_extract_shop_id_ignores_non_shop_urls(self):
        self.assertIsNone(dianping_scraper._extract_shop_id_from_url("https://www.dianping.com/search/keyword/11/0_%E5%85%AC%E5%9B%AD"))
        self.assertIsNone(dianping_scraper._extract_shop_id_from_url("https://example.com/shop/G2CauBeHQ9je4IAb"))

    def test_add_unique_reviews_skips_duplicates(self):
        rows = []
        seen = set()

        dianping_scraper._add_unique_review(
            rows,
            seen,
            {"content": "环境很好", "date": "2026-06-01", "rating": 5, "likes": 2},
        )
        dianping_scraper._add_unique_review(
            rows,
            seen,
            {"content": "环境很好", "date": "2026-06-01", "rating": 5, "likes": 2},
        )

        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
