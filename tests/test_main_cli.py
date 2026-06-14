import unittest
import sys
import types

charts = types.ModuleType("charts")
charts.generate_all_wordclouds = lambda *args, **kwargs: None
charts.plot_bar_comparison = lambda *args, **kwargs: None
charts.plot_scatter = lambda *args, **kwargs: None
sys.modules.setdefault("charts", charts)

import main


class MainCliTest(unittest.TestCase):
    def test_platform_defaults_to_all(self):
        args = main._build_parser().parse_args([])
        self.assertEqual(args.platform, "all")
        self.assertEqual(main._selected_platforms(args.platform), set(main.PLATFORMS))

    def test_accepts_single_platform_and_profile(self):
        args = main._build_parser().parse_args(["--platform", "xhs", "--profile"])
        self.assertEqual(args.platform, "xhs")
        self.assertTrue(args.profile)
        self.assertEqual(main._selected_platforms(args.platform), {"xhs"})


if __name__ == "__main__":
    unittest.main()
