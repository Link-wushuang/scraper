import unittest

import pandas as pd

import main


class MainDisplayNamesTest(unittest.TestCase):
    def test_uses_config_names_for_analysis_labels(self):
        raw = {
            "公园A": pd.DataFrame({"content": ["a"]}),
            "公园B": pd.DataFrame({"content": ["b"]}),
        }
        configs = {
            "公园A": {"name": "北海公园"},
            "公园B": {"name": "天坛公园"},
        }

        labeled = main._with_display_names(raw, configs)

        self.assertEqual(list(labeled.keys()), ["北海公园", "天坛公园"])
        self.assertIs(labeled["北海公园"], raw["公园A"])
        self.assertIs(labeled["天坛公园"], raw["公园B"])


if __name__ == "__main__":
    unittest.main()
