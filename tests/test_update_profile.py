from __future__ import annotations

import html
import importlib.util
import json
import re
import sys
import unittest
import xml.etree.ElementTree as ET
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
        for locale in ("en", "zh"):
            with self.subTest(locale=locale):
                weekly = PROFILE.weekly_card(
                    self.user["weekly"], self.windows, locale
                ).splitlines()
                yearly = PROFILE.yearly_card(
                    self.user["yearly"],
                    self.user["repositories"],
                    self.windows,
                    locale,
                ).splitlines()

                self.assertEqual(len(weekly), len(yearly))
                self.assertEqual(
                    {PROFILE.display_width(line) for line in weekly}, {44}
                )
                self.assertEqual(
                    {PROFILE.display_width(line) for line in yearly}, {44}
                )

    def test_every_cat_fits_the_fixed_art_slot(self) -> None:
        self.assertEqual(len(PROFILE.CAT_VARIANTS), 29)
        names = [name for name, _ in PROFILE.CAT_VARIANTS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(PROFILE.CAT_LABELS_ZH))
        for name, art in PROFILE.CAT_VARIANTS:
            with self.subTest(cat=name):
                self.assertEqual(len(art), 9)
                self.assertEqual({len(line) for line in art}, {PROFILE.CAT_WIDTH})
                self.assertIn(name, art[-1])

    def test_reading_cat_is_centered_as_one_pose(self) -> None:
        reading = dict(PROFILE.CAT_VARIANTS)["READING CAT"][:-1]
        occupied = [line for line in reading if line.strip()]
        left_margin = min(len(line) - len(line.lstrip()) for line in occupied)
        right_margin = min(len(line) - len(line.rstrip()) for line in occupied)
        self.assertLessEqual(abs(left_margin - right_margin), 1)

    def test_chinese_cat_art_is_localized_and_keeps_its_shape(self) -> None:
        localized_text = ""
        for name, art in PROFILE.CAT_VARIANTS:
            with self.subTest(cat=name):
                localized = PROFILE.localized_cat(name, art, "zh")
                self.assertEqual(len(localized), 9)
                self.assertEqual(
                    {PROFILE.display_width(line) for line in localized},
                    {PROFILE.CAT_WIDTH},
                )
                self.assertIn(PROFILE.CAT_LABELS_ZH[name], localized[-1])
                localized_text += "\n".join(localized)

        for english_fragment in (
            "do not ship",
            "BOOK",
            "one more",
            "outside?",
            "shhh",
            "purr",
            "vroom",
        ):
            self.assertNotIn(english_fragment, localized_text)

    def test_updating_one_scope_preserves_the_other(self) -> None:
        source = (ROOT / "README.md").read_text(encoding="utf-8")
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

    def test_weekly_footer_is_derived_from_contributions(self) -> None:
        collection = {
            "totalCommitContributions": 1,
            "commitContributionsByRepository": [
                {
                    "repository": {"name": "one-repo"},
                    "contributions": {
                        "nodes": [
                            {
                                "occurredAt": "2026-09-03T10:00:00Z",
                                "commitCount": 1,
                            }
                        ]
                    },
                }
            ],
        }
        card = PROFILE.weekly_card(collection, self.windows)
        self.assertIn("average: 1.0 commits / active day", card)
        self.assertIn("top share: 100% · one-repo", card)

    def test_toolbox_uses_repository_languages(self) -> None:
        rendered = PROFILE.toolbox(self.user["repositories"])
        self.assertIn('alt="Python ×2"', rendered)
        self.assertIn('alt="Markdown ×2"', rendered)
        self.assertIn('alt="Shell ×1"', rendered)
        self.assertIn('alt="GraphQL"', rendered)
        self.assertIn('alt="SVG"', rendered)

        rendered_zh = PROFILE.toolbox(self.user["repositories"], "zh")
        self.assertIn("公开仓库语言", rendered_zh)
        self.assertIn("本页自动化", rendered_zh)

    def test_bilingual_documents_use_the_same_profile_data(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README_zh.md").read_text(encoding="utf-8")

        updated_en = PROFILE.update_document(
            english, self.user, self.windows, "all", "en"
        )
        updated_zh = PROFILE.update_document(
            chinese, self.user, self.windows, "all", "zh"
        )

        self.assertIn("WEEKLY SIGNAL", updated_en)
        self.assertIn("每周信号", updated_zh)
        self.assertIn("profile-lab", updated_en)
        self.assertIn("profile-lab", updated_zh)

    def test_readme_language_switches_and_copy_are_complete(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README_zh.md").read_text(encoding="utf-8")

        self.assertIn("[简体中文](./README_zh.md)", english)
        self.assertIn("[English](./README.md)", chinese)
        english_without_switch = english.replace(
            "[简体中文](./README_zh.md)", ""
        )
        self.assertNotRegex(english_without_switch, r"[\u4e00-\u9fff]")

        untranslated = (
            "CURRENT QUEST",
            "SIGNAL CARDS",
            "## TOOLBOX",
            "WEEKLY SIGNAL",
            "YEARLY ORBIT",
            "total commits",
            "active days",
            "repos touched",
            "top share",
            "BOOK",
            "personal coordinate",
        )
        for phrase in untranslated:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, chinese)

        self.assertNotIn("personal coordinate", english.lower())
        self.assertNotIn("个人坐标", chinese)

    def test_rendered_readme_cards_keep_the_fixed_dimensions(self) -> None:
        for name in ("README.md", "README_zh.md"):
            document = (ROOT / name).read_text(encoding="utf-8")
            cards = re.findall(r"<pre>(.*?)</pre>", document, re.DOTALL)
            self.assertEqual(len(cards), 2)
            for card in cards:
                lines = html.unescape(card).splitlines()
                self.assertEqual(len(lines), 16)
                self.assertEqual(
                    {PROFILE.display_width(line) for line in lines}, {44}
                )

    def test_chinese_svg_set_is_valid_and_localized(self) -> None:
        names = (
            "profile-class-zh.svg",
            "profile-status-zh.svg",
            "profile-alignment-zh.svg",
            "profile-quest-zh.svg",
            "profile-inventory-zh.svg",
            "profile-coordinate-zh.svg",
        )
        visible_text = ""
        for name in names:
            with self.subTest(asset=name):
                path = ROOT / "assets" / name
                root = ET.parse(path).getroot()
                visible_text += "".join(root.itertext())
        self.assertIn("构建", visible_text)

    def test_umbra_assets_only_keep_one_horizontal_mark(self) -> None:
        names = sorted(path.name for path in (ROOT / "assets").glob("umbra-*.svg"))
        self.assertEqual(names, ["umbra-moon.svg"])

        root = ET.parse(ROOT / "assets" / "umbra-moon.svg").getroot()
        view_box = [float(value) for value in root.attrib["viewBox"].split()]
        self.assertGreater(view_box[2], view_box[3])

    def test_production_content_contains_no_draft_language(self) -> None:
        paths = [ROOT / "README.md", ROOT / "README_zh.md"]
        paths.extend((ROOT / "assets").glob("*.svg"))
        content = "\n".join(
            path.read_text(encoding="utf-8") for path in paths
        ).lower()
        banned = (
            "placeholder",
            "future implementation",
            "add when ready",
            "work in progress",
            "still in orbit",
            "under construction",
            "personal coordinate",
            "看个意思就好",
            "待补充",
            "个人坐标",
            "c2pa",
        )
        for phrase in banned:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, content)

if __name__ == "__main__":
    unittest.main()
