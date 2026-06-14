import unittest

import ctrip_scraper


class CtripUrlHelpersTest(unittest.TestCase):
    def test_extract_sight_id_from_ctrip_url(self):
        cases = {
            "https://you.ctrip.com/sight/beijing1/232.html?renderPlatform=": 232,
            "https://you.ctrip.com/sight/beijing1/69342270.html?renderPlatform=#ctm_ref=www_hp_bs_lst": 69342270,
            "https://you.ctrip.com/sight/beijing1/1483951.html": 1483951,
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(ctrip_scraper._extract_sight_id_from_url(url), expected)

    def test_extract_sight_id_ignores_non_ctrip_urls(self):
        self.assertIsNone(ctrip_scraper._extract_sight_id_from_url("https://example.com/sight/beijing1/232.html"))
        self.assertIsNone(ctrip_scraper._extract_sight_id_from_url(""))


if __name__ == "__main__":
    unittest.main()
