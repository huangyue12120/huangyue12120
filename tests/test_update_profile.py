from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "update_profile", ROOT / "scripts" / "update_profile.py"
)
assert SPEC and SPEC.loader
PROFILE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROFILE
SPEC.loader.exec_module(PROFILE)


class ProfileUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = ROOT / "tests" / "fixtures" / "github_profile.json"
        cls.user = PROFILE.extract_user(json.loads(fixture.read_text(encoding="utf-8")))
        cls.windows = PROFILE.DateWindows.ending_before(date(2026, 9, 9))

    def test_signal_cards_have_identical_dimensions(self) -> None:
        weekly = PROFILE.weekly_card(self.user["weekly"], self.windows).splitlines()
        yearly = PROFILE.yearly_card(
            self.user["yearly"], self.user["repositories"], self.windows
        ).splitlines()

        self.assertEqual(len(weekly), len(yearly))
        self.assertEqual({len(line) for line in weekly}, {44})
        self.assertEqual({len(line) for line in yearly}, {44})

    def test_every_cat_fits_the_fixed_art_slot(self) -> None:
        self.assertEqual(len(PROFILE.CAT_VARIANTS), 29)
        names = [name for name, _ in PROFILE.CAT_VARIANTS]
        self.assertEqual(len(names), len(set(names)))
        for name, art in PROFILE.CAT_VARIANTS:
            with self.subTest(cat=name):
                self.assertEqual(len(art), 9)
                self.assertEqual({len(line) for line in art}, {PROFILE.CAT_WIDTH})
                self.assertIn(name, art[-1])

    def test_updating_one_scope_preserves_the_other(self) -> None:
        source = (ROOT / "draft" / "files" / "README.md").read_text(encoding="utf-8")
        weekly_cell = PROFILE.card_cell(
            PROFILE.weekly_card(self.user["weekly"], self.windows)
        )
        updated = PROFILE.replace_region(source, "WEEKLY", weekly_cell)

        old_yearly = source.split("<!-- YEARLY_SIGNAL_START -->", 1)[1]
        new_yearly = updated.split("<!-- YEARLY_SIGNAL_START -->", 1)[1]
        self.assertEqual(old_yearly, new_yearly)
        self.assertIn("[READING CAT]", updated)

    def test_longest_streak_resets_on_empty_day(self) -> None:
        days = [
            (date(2026, 1, 1), 1),
            (date(2026, 1, 2), 2),
            (date(2026, 1, 3), 0),
            (date(2026, 1, 4), 1),
        ]
        self.assertEqual(PROFILE.longest_streak(days), 2)

    def test_weekly_summary_uses_singular_words(self) -> None:
        collection = dict(self.user["weekly"])
        collection["totalCommitContributions"] = 1
        collection["commitContributionsByRepository"] = [
            collection["commitContributionsByRepository"][0]
        ]
        card = PROFILE.weekly_card(collection, self.windows)
        self.assertIn("summary: 1 commit across 1 repo", card)


if __name__ == "__main__":
    unittest.main()
