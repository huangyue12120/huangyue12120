#!/usr/bin/env python3
"""Refresh generated signal cards in the English and Chinese profile READMEs."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("Asia/Shanghai")
CARD_INNER_WIDTH = 42
CAT_WIDTH = 15
REPO_WIDTH = CARD_INNER_WIDTH - CAT_WIDTH - 1
CAT_ROTATION_OFFSET = 3

GRAPHQL_QUERY = """
query ProfileSignals(
  $login: String!
  $weekFrom: DateTime!
  $weekTo: DateTime!
  $yearFrom: DateTime!
  $yearTo: DateTime!
) {
  user(login: $login) {
    weekly: contributionsCollection(from: $weekFrom, to: $weekTo) {
      totalCommitContributions
      commitContributionsByRepository(maxRepositories: 100) {
        repository { name nameWithOwner }
        contributions(first: 100) {
          nodes { occurredAt commitCount }
        }
      }
    }
    yearly: contributionsCollection(from: $yearFrom, to: $yearTo) {
      totalCommitContributions
      commitContributionsByRepository(maxRepositories: 100) {
        repository { name nameWithOwner }
        contributions(first: 100) {
          nodes { occurredAt commitCount }
        }
      }
      contributionCalendar {
        weeks {
          contributionDays { date contributionCount }
        }
      }
    }
    repositories(
      first: 100
      privacy: PUBLIC
      ownerAffiliations: OWNER
      isFork: false
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      totalCount
      nodes { name primaryLanguage { name } }
    }
  }
}
"""


@dataclass(frozen=True)
class DateWindows:
    as_of: date
    week_start: date
    week_end: date
    year_start: date

    @classmethod
    def ending_before(cls, as_of: date) -> "DateWindows":
        week_end = as_of - timedelta(days=1)
        return cls(
            as_of=as_of,
            week_start=as_of - timedelta(days=7),
            week_end=week_end,
            year_start=date(as_of.year, 1, 1),
        )

    def graphql_variables(self, login: str) -> dict[str, str]:
        end_exclusive = datetime.combine(self.as_of, time.min, TIMEZONE)
        year_from = datetime.combine(self.year_start, time.min, TIMEZONE)
        # GitHub requires `from` to precede `to`, including just after New Year.
        if end_exclusive <= year_from:
            end_exclusive = year_from + timedelta(seconds=1)
        return {
            "login": login,
            "weekFrom": datetime.combine(
                self.week_start, time.min, TIMEZONE
            ).isoformat(),
            "weekTo": (
                datetime.combine(self.as_of, time.min, TIMEZONE)
                - timedelta(seconds=1)
            ).isoformat(),
            "yearFrom": year_from.isoformat(),
            "yearTo": max(
                end_exclusive - timedelta(seconds=1),
                year_from + timedelta(seconds=1),
            ).isoformat(),
        }


def cat_variant(
    name: str, *pose: str, label: str | None = None
) -> tuple[str, tuple[str, ...]]:
    """Center a complete pose as one block inside the fixed cat canvas."""
    if len(pose) != 8:
        raise ValueError(f"{name} must contain exactly 8 pose lines")
    pose_lines = tuple(line.rstrip() for line in pose)
    for index, line in enumerate(pose_lines, start=1):
        if len(line) > CAT_WIDTH:
            raise ValueError(
                f"{name} line {index} exceeds {CAT_WIDTH} characters: {line!r}"
            )
    block_width = max(len(line) for line in pose_lines)
    left_margin = (CAT_WIDTH - block_width) // 2
    centered_pose = tuple(
        (" " * left_margin + line).ljust(CAT_WIDTH) for line in pose_lines
    )
    label_line = f"[{label or name}]"
    if len(label_line) > CAT_WIDTH:
        raise ValueError(f"{name} label exceeds {CAT_WIDTH} characters")
    return name, (*centered_pose, label_line.center(CAT_WIDTH))


CAT_VARIANTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "BLACK CAT",
        (
            "     /\\_/\\     ",
            "    /#####\\    ",
            "   (##o#o##)   ",
            "   /###^###\\   ",
            "  /#########\\  ",
            " (###########) ",
            "  \\###|###/    ",
            "   `--m-m--'   ",
            "  [BLACK CAT]  ",
        ),
    ),
    (
        "WHITE CAT",
        (
            "     /\\_/\\     ",
            "    /     \\    ",
            "   (  o o  )   ",
            "   /   ^   \\   ",
            "  /         \\  ",
            " (           ) ",
            "  \\   |   /    ",
            "   `--m-m--'   ",
            "  [WHITE CAT]  ",
        ),
    ),
    (
        "COW CAT",
        (
            "     /\\_/\\     ",
            "    /@@   @\\   ",
            "   ( o   o )   ",
            "   /   ^   \\   ",
            "  / @@   @  \\  ",
            " (    @@     ) ",
            "  \\ @ | @@ /   ",
            "   `--m-m--'   ",
            "   [COW CAT]   ",
        ),
    ),
    (
        "SIAMESE CAT",
        (
            "     /\\_/\\     ",
            "    /#####\\    ",
            "   ( #o#o# )   ",
            "   /   ^   \\   ",
            "  /         \\  ",
            " (    ###    ) ",
            "  \\   |   /    ",
            "   `--m-m--'   ",
            " [SIAMESE CAT] ",
        ),
    ),
    (
        "SLEEPY CAT",
        (
            "               ",
            "      |\\       ",
            "  /\\_/  \\___   ",
            " ( -.-     `\\  ",
            "  > ^ <  _  /  ",
            " (______/ (_/  ",
            "    z  z       ",
            "     z         ",
            " [SLEEPY CAT]  ",
        ),
    ),
    (
        "BOX CAT",
        (
            "  +---------+  ",
            "  |  /\\_/\\  |  ",
            "  | ( o.o ) |  ",
            "  |  > ^ <  |  ",
            "  |         |  ",
            "  +---------+  ",
            "      ||       ",
            "   do not ship ",
            "   [BOX CAT]   ",
        ),
    ),
    (
        "PEEK CAT",
        (
            "               ",
            "               ",
            "   |\\     /|   ",
            "   | \\___/ |   ",
            "   |  o o  |   ",
            "---|   ^   |---",
            "   |______/    ",
            "               ",
            "  [PEEK CAT]   ",
        ),
    ),
    (
        "KEYBOARD CAT",
        (
            "     /\\_/\\     ",
            "    ( o.o )    ",
            "     > ^ <     ",
            "    /|   |\\    ",
            " __/ |___| \\__ ",
            " | q w e r t | ",
            " | a s d f g | ",
            " `-----------' ",
            " [KEYBOARD CAT]",
        ),
    ),
    (
        "STRETCH CAT",
        (
            "               ",
            "  /\\_/\\        ",
            " ( o.o )____   ",
            "  > ^ <     `-.",
            " /            )",
            "(__/------(__/ ",
            "               ",
            "      ~~~      ",
            " [STRETCH CAT] ",
        ),
    ),
    cat_variant(
        "YAWNING CAT",
        "",
        "    /\\_/\\",
        "   ( -.- )",
        "    > O <",
        "   /|   |\\",
        "  (_|___|_)",
        "     ||",
        "    ...",
    ),
    cat_variant(
        "FISHING CAT",
        "       /|",
        "  /\\_/| |",
        " ( o.o ) |",
        "  > ^ <  |",
        "   /|\\   |",
        "__/ | \\__|",
        "       ><(((('>",
        "",
    ),
    cat_variant(
        "READING CAT",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "   /|   |\\",
        "  / |___| \\",
        " /__BOOK___\\",
        "|  01  10  |",
        "`----------'",
    ),
    cat_variant(
        "COFFEE CAT",
        "    /\\_/\\",
        "   ( -.- )",
        "    > ^ <",
        "   /|   |\\",
        "  (_|___|_)",
        "     c[_]",
        "      ~~~",
        "   one more",
    ),
    cat_variant(
        "MUSIC CAT",
        "    .---.",
        "   / /_\\ \\",
        "  | /\\_/\\ |",
        "  |( o.o )|",
        "  | > ^ < |",
        "   \\|   |/",
        "    |___|",
        "   <|  |>",
    ),
    cat_variant(
        "GAMER CAT",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "   /|   |\\",
        "  (_|___|_)",
        "   .-----.",
        "  / o x o \\",
        "  `--+--'",
    ),
    cat_variant(
        "GARDEN CAT",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "   /|   |\\",
        "  (_|___|_)",
        "    \\ | /",
        "     \\|/",
        "     /_\\",
    ),
    cat_variant(
        "WINDOW CAT",
        "+-----+-----+",
        "|     |     |",
        "|  /\\_/\\   |",
        "| ( o.o )  |",
        "|  > ^ <   |",
        "+-----+-----+",
        "   outside?",
        "",
    ),
    cat_variant(
        "MOON CAT",
        "      _..._",
        "    .:::::::.",
        "   :::::::::",
        "    `:::::'",
        "  /\\_/\\  `",
        " ( o.o )",
        "  > ^ <",
        "   /   \\",
    ),
    cat_variant(
        "STAR CAT",
        "      *",
        "  *       *",
        "     /\\_/\\",
        "    ( o.o )",
        "     > ^ <",
        "    /|   |\\",
        "     |___|",
        "  *       *",
    ),
    cat_variant(
        "ROLLING CAT",
        "       ___",
        "    .-'   `-.",
        "   /  /\\_/\\  \\",
        "  |  ( o.o ) |",
        "  |   > ^ <  |",
        "   \\         /",
        "    `-.___.-'",
        "       ~",
    ),
    cat_variant(
        "UPSIDE CAT",
        "      ||",
        "      ||",
        "     /  \\",
        "    (    )",
        "    /|  |\\",
        "   ( o..o )",
        "    \\_v_/",
        "     /\\",
    ),
    cat_variant(
        "WAVING CAT",
        "      _",
        "     / )",
        " /\\_/ /",
        "( o.o )",
        " > ^ <",
        "/|   |\\",
        " |___|",
        "  / \\",
    ),
    cat_variant(
        "JUMPING CAT",
        "\\           /",
        " \\  /\\_/\\ /",
        "  \\( o.o )/",
        "    > ^ <",
        "  _/|   |\\_",
        " /  |___|  \\",
        "    /   \\",
        "   /     \\",
    ),
    cat_variant(
        "HIDING CAT",
        "_____________",
        "|           |",
        "|   /\\_/\\   |",
        "|  ( o.o )  |",
        "|   > ^ <   |",
        "|  /     \\  |",
        "   shhh...",
        "",
    ),
    cat_variant(
        "SNOW CAT",
        "*    *     *",
        "    /\\_/\\",
        " * ( o.o ) *",
        "    > ^ <",
        "   /|   |\\",
        "  (_|___|_)",
        "*    / \\",
        "   *     *",
    ),
    cat_variant(
        "RAIN CAT",
        "   .-~~~~~-.",
        "  /_________\\",
        "      /",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "   /|   |\\",
        "  ~~~~~~~~~",
    ),
    cat_variant(
        "HUNTING CAT",
        "       ...",
        "   /\\_/\\",
        "  ( o.o )__",
        "   > ^ <   `.",
        "  /        _/",
        " /  /-----'",
        "(__/",
        "       *",
    ),
    cat_variant(
        "KNEADING CAT",
        "    /\\_/\\",
        "   ( ^.^ )",
        "    > ^ <",
        "  .-------.",
        " /  o o o  \\",
        "|  o o o o  |",
        " \\_________/",
        "   purr...",
    ),
    cat_variant(
        "ZOOMIES CAT",
        "        /\\_/\\",
        "   ____/ o.o )",
        " _/     > ^ <",
        "/  _/|      \\",
        "\\_/  |__|\\__\\",
        "       \\ \\",
        "  >>>>>>>>>",
        "   vroom!",
    ),
    cat_variant(
        "NINJA CAT",
        "    /\\_/\\",
        "   /#####\\",
        "  ( #o#o# )",
        "   ># ^ #<",
        "  /|#####|\\",
        " (_|_____|_)",
        "   / / \\ \\",
        "  *       *",
    ),
    cat_variant(
        "GHOST CAT",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "   /     \\",
        "  /       \\",
        " /  ~   ~  \\",
        "(____/\\____)",
        "    boo...",
    ),
    cat_variant(
        "ROBOT CAT",
        "   .-------.",
        "  /  o   o  \\",
        " |     ^     |",
        " |   \\___/   |",
        "  \\    O    /",
        "   |  ___  |",
        "   | /___\\ |",
        "   '-------'",
    ),
    cat_variant(
        "MODEM CAT",
        "  .---------.",
        "  | FIBER   |",
        "  |PON *    |",
        "  |LOS .    |",
        "  |LAN *    |",
        "  |____o____|",
        "     \\__/",
        "   ==fiber==",
    ),
    cat_variant(
        "OWL CAT",
        "    .---.",
        "   / o o \\",
        "  |   V   |",
        "  |  /|\\  |",
        "  | /_|_\\ |",
        "   \\     /",
        "    '---'",
        "    _/ \\_",
    ),
    cat_variant(
        "PANDA CAT",
        "    .---.",
        "   /@   @\\",
        "  |  o o  |",
        "  |   ^   |",
        "  |  '-'  |",
        "   \\     /",
        "   /|___|\\",
        "    /   \\",
    ),
    cat_variant(
        "TERMINAL CAT",
        "$ cat cat.txt",
        "meow meow",
        "meow meow",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "   /|   |\\",
        "    |___|",
    ),
    cat_variant(
        "CATERPILLAR",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "  (o)-(o)-(o)",
        " (o)-(o)-(o)",
        "  (o)-(o)-(o)",
        "       \\  \\",
        "        '--'",
    ),
    cat_variant(
        "BUG CAT",
        "$ run cat.py",
        "ERROR: BUG",
        "meow meow",
        "    /\\_/\\",
        "   ( x.x )",
        "    > ! <",
        "   /|   |\\",
        "  trace: meow",
    ),
    cat_variant(
        "404 CAT",
        "+--- 404 ---+",
        "| CAT NOT   |",
        "| FOND      |",
        "+-----------+",
        "    /\\_/\\ ?",
        "   ( o.o )",
        "    > ^ <",
        "   /     \\",
    ),
    cat_variant(
        "QUANTUM CAT",
        ".-----------.",
        "|  SEALED   |",
        "|     ?     |",
        "|     ?     |",
        "|     ?     |",
        "'-----------'",
        " OBSERVATION:",
        " [UNOBSERVED]",
        label="??? CAT",
    ),
    cat_variant(
        "LIQUID CAT",
        " .----------.",
        " |  /\\_/\\   |__",
        " | ( o.o )  | )",
        " |  > ^ <   | /",
        " |~~~~~~~~~~|/",
        " '----------'",
        " cats=liquid",
        "    drip...",
    ),
    cat_variant(
        "ASCII CAT",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "   /|   |\\",
        " /+----------+",
        " (|I AM ASCII|",
        " \\+----------+",
        "    /   \\",
    ),
    cat_variant(
        "ROOT CAT",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "   /| # |\\",
        "  (_|___|_)",
        "    uid=0",
        "$ whoami",
        "root",
    ),
    cat_variant(
        "VANISH CAT",
        "",
        "      o o",
        "       .",
        "    o o",
        "     .",
        "  o o",
        "   .",
        "      ...",
    ),
    cat_variant(
        "BOSS CAT",
        "[########...]",
        "    /\\_/\\",
        "   /#####\\",
        "  ( >.< )",
        "   > ^ <",
        "  /|   |\\",
        " (_|___|_)",
        "   PHASE 2",
    ),
    cat_variant(
        "TMALL CAT",
        "  .---------.",
        "  |  SALE!  |",
        "  |  /\\_/\\  |",
        "  | ( o.o ) |",
        "  |  > ^ <  |",
        "  | [CART]  |",
        "  '---------'",
        "    BUY NOW",
    ),
    cat_variant(
        "MOP CAT",
        "  MOP.COM BBS",
        "  [LOGIN...]",
        "    /\\_/\\",
        "   ( o.o )",
        "    > ^ <",
        "  /| 56K |\\",
        "   |_____|",
        "  dial-up...",
    ),
    cat_variant(
        "LUCKY CAT",
        "    /\\_/\\  _",
        "   ( ^.^ ) /",
        "    > ^ < /",
        "   /|   |/",
        "  (_|___|)",
        "    | $ |",
        "    |___|",
        "    /   \\",
    ),
    cat_variant(
        "MAODIE CAT",
        "   .-#####-.",
        "  /##/\\_/\\##\\",
        " |##( >.< )##|",
        " |###> ^ <###|",
        " |###########|",
        "  \\#########/",
        "   '#######'",
        "    HSSSS!",
    ),
)

CAT_LABELS_ZH = {
    "BLACK CAT": "黑猫",
    "WHITE CAT": "白猫",
    "COW CAT": "奶牛猫",
    "SIAMESE CAT": "暹罗猫",
    "SLEEPY CAT": "睡猫",
    "BOX CAT": "纸箱猫",
    "PEEK CAT": "偷看猫",
    "KEYBOARD CAT": "键盘猫",
    "STRETCH CAT": "伸展猫",
    "YAWNING CAT": "哈欠猫",
    "FISHING CAT": "钓鱼猫",
    "READING CAT": "读书猫",
    "COFFEE CAT": "咖啡猫",
    "MUSIC CAT": "音乐猫",
    "GAMER CAT": "游戏猫",
    "GARDEN CAT": "园艺猫",
    "WINDOW CAT": "窗边猫",
    "MOON CAT": "月亮猫",
    "STAR CAT": "星星猫",
    "ROLLING CAT": "翻滚猫",
    "UPSIDE CAT": "倒挂猫",
    "WAVING CAT": "挥手猫",
    "JUMPING CAT": "跳跃猫",
    "HIDING CAT": "躲藏猫",
    "SNOW CAT": "雪地猫",
    "RAIN CAT": "雨伞猫",
    "HUNTING CAT": "捕猎猫",
    "KNEADING CAT": "踩奶猫",
    "ZOOMIES CAT": "疯跑猫",
    "NINJA CAT": "忍者猫",
    "GHOST CAT": "幽灵猫",
    "ROBOT CAT": "机器猫",
    "MODEM CAT": "光猫",
    "OWL CAT": "猫头鹰",
    "PANDA CAT": "熊猫",
    "TERMINAL CAT": "终端猫",
    "CATERPILLAR": "猫猫虫",
    "BUG CAT": "故障猫",
    "404 CAT": "404猫",
    "QUANTUM CAT": "??? 猫",
    "LIQUID CAT": "液体猫",
    "ASCII CAT": "ASCII 猫",
    "ROOT CAT": "ROOT 猫",
    "VANISH CAT": "消失猫",
    "BOSS CAT": "BOSS 猫",
    "TMALL CAT": "天猫",
    "MOP CAT": "猫扑",
    "LUCKY CAT": "招财猫",
    "MAODIE CAT": "耄耋",
}

CAT_ART_ZH_OVERRIDES = {
    "BOX CAT": {7: "暂勿发货"},
    "READING CAT": {5: "/___书本___\\"},
    "COFFEE CAT": {7: "再来一杯"},
    "WINDOW CAT": {6: "窗外？"},
    "HIDING CAT": {6: "嘘……"},
    "KNEADING CAT": {7: "呼噜……"},
    "ZOOMIES CAT": {7: "冲呀！"},
    "GHOST CAT": {7: "呜..."},
    "MODEM CAT": {1: "   | 光纤    | ", 7: "==光纤=="},
    "TERMINAL CAT": {1: "喵 喵", 2: "喵 喵"},
    "BUG CAT": {1: "错误：BUG", 2: "喵 喵", 7: "追踪：喵"},
    "404 CAT": {1: "| 猫未找到  |", 2: "| 404 页面  |"},
    "QUANTUM CAT": {1: "|   密封    |", 6: "观测结果：", 7: "[未观测]"},
    "LIQUID CAT": {6: "猫是液体", 7: "滴答……"},
    "ASCII CAT": {5: " (|我是ASCII |"},
    "BOSS CAT": {7: "阶段 2"},
    "TMALL CAT": {1: "   | 促销!   | ", 5: "   | [购物车]| ", 7: "立即购买"},
    "MOP CAT": {1: "[登录...]", 7: "拨号中..."},
    "MAODIE CAT": {7: "哈气!!!"},
}

QUANTUM_OBSERVATIONS: tuple[tuple[str, str], ...] = (
    ("ALIVE CAT", "活猫"),
    ("DEAD CAT", "死猫"),
    ("MODEM CAT", "光猫"),
    ("OWL CAT", "猫头鹰"),
    ("PANDA CAT", "熊猫"),
    ("TMALL CAT", "天猫"),
    ("EMPTY BOX", "空箱子"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument(
        "--zh-readme",
        type=Path,
        help="Refresh a Simplified Chinese README from the same GitHub response.",
    )
    parser.add_argument(
        "--scope", choices=("all", "weekly", "yearly"), default="all"
    )
    parser.add_argument(
        "--login",
        default=os.environ.get("GITHUB_REPOSITORY_OWNER", "huangyue12120"),
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=datetime.now(TIMEZONE).date(),
        help="Local date whose current, incomplete day is excluded (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Read a saved GraphQL response instead of contacting GitHub.",
    )
    return parser.parse_args()


def fetch_profile_data(token: str, variables: dict[str, str]) -> dict[str, Any]:
    request_body = json.dumps(
        {"query": GRAPHQL_QUERY, "variables": variables}
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=request_body,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "huangyue12120-profile-workflow",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"GitHub GraphQL request failed: {error}") from error

    if payload.get("errors"):
        messages = "; ".join(
            str(item.get("message", "unknown GraphQL error"))
            for item in payload["errors"]
        )
        raise RuntimeError(f"GitHub GraphQL returned errors: {messages}")
    return extract_user(payload)


def extract_user(payload: dict[str, Any]) -> dict[str, Any]:
    user = payload.get("data", {}).get("user")
    if not isinstance(user, dict):
        raise RuntimeError("GitHub response did not contain the requested user")
    return user


def display_width(value: str) -> int:
    return sum(
        0
        if unicodedata.combining(character)
        else 2
        if unicodedata.east_asian_width(character) in ("W", "F")
        else 1
        for character in value
    )


def truncate_to_width(value: str, width: int) -> str:
    if display_width(value) <= width:
        return value
    target = max(0, width - display_width("…"))
    rendered: list[str] = []
    used = 0
    for character in value:
        character_width = display_width(character)
        if used + character_width > target:
            break
        rendered.append(character)
        used += character_width
    return "".join(rendered) + "…"


def fit(value: str, width: int, align: str = "left") -> str:
    value = truncate_to_width(value, width)
    padding = " " * (width - display_width(value))
    if align == "right":
        return padding + value
    if align == "center":
        left = len(padding) // 2
        return " " * left + value + " " * (len(padding) - left)
    return value + padding


def localized_cat(
    name: str, art: tuple[str, ...], locale: str
) -> tuple[str, ...]:
    if locale != "zh":
        return art
    localized = list(art[:-1])
    for index, line in CAT_ART_ZH_OVERRIDES.get(name, {}).items():
        localized[index] = fit(line, CAT_WIDTH, "center")
    localized.append(fit(f"[{CAT_LABELS_ZH[name]}]", CAT_WIDTH, "center"))
    return tuple(localized)


def quantum_observation(iso_year: int, iso_week: int, locale: str = "en") -> str:
    """Return a repeatable observation for one ISO week."""
    seed = f"quantum-cat:{iso_year}-W{iso_week:02d}".encode("ascii")
    digest = hashlib.sha256(seed).digest()
    observation = QUANTUM_OBSERVATIONS[digest[0] % len(QUANTUM_OBSERVATIONS)]
    return observation[1] if locale == "zh" else observation[0]


def cat_for_iso_week(
    iso_year: int, iso_week: int, locale: str = "en"
) -> tuple[str, tuple[str, ...]]:
    """Select and localize one cat deterministically for an ISO week."""
    name, art = CAT_VARIANTS[
        (iso_week + CAT_ROTATION_OFFSET) % len(CAT_VARIANTS)
    ]
    rendered = list(localized_cat(name, art, locale))
    if name == "QUANTUM CAT":
        heading = "观测结果：" if locale == "zh" else "OBSERVATION:"
        state = quantum_observation(iso_year, iso_week, locale)
        rendered[6] = fit(heading, CAT_WIDTH, "center")
        rendered[7] = fit(f"[{state}]", CAT_WIDTH, "center")
    return name, tuple(rendered)


def top_border(title: str) -> str:
    prefix = f"+- {title} "
    return prefix + "-" * (CARD_INNER_WIDTH + 1 - display_width(prefix)) + "+"


def full_row(value: str) -> str:
    return f"|{fit(value, CARD_INNER_WIDTH)}|"


def full_divider() -> str:
    return "+" + "-" * CARD_INNER_WIDTH + "+"


def bottom_border() -> str:
    return "+" + "-" * CARD_INNER_WIDTH + "+"


def contribution_rows(collection: dict[str, Any]) -> list[dict[str, Any]]:
    return collection.get("commitContributionsByRepository") or []


def repository_commits(collection: dict[str, Any]) -> list[tuple[str, int]]:
    totals: list[tuple[str, int]] = []
    for item in contribution_rows(collection):
        repository = item.get("repository") or {}
        name = repository.get("name") or repository.get("nameWithOwner") or "unknown"
        nodes = (item.get("contributions") or {}).get("nodes") or []
        total = sum(int(node.get("commitCount") or 0) for node in nodes)
        if total:
            totals.append((name, total))
    return sorted(totals, key=lambda pair: (-pair[1], pair[0].lower()))


def contribution_days(collection: dict[str, Any]) -> dict[date, int]:
    by_day: defaultdict[date, int] = defaultdict(int)
    for item in contribution_rows(collection):
        nodes = (item.get("contributions") or {}).get("nodes") or []
        for node in nodes:
            occurred_at = str(node.get("occurredAt", ""))[:10]
            try:
                day = date.fromisoformat(occurred_at)
            except ValueError:
                continue
            by_day[day] += int(node.get("commitCount") or 0)
    return dict(by_day)


def bar(value: int, maximum: int, width: int = 5) -> str:
    if value <= 0 or maximum <= 0:
        return "." * width
    filled = max(1, round(value / maximum * width))
    return "#" * filled + "." * (width - filled)


def weekly_card(
    collection: dict[str, Any], windows: DateWindows, locale: str = "en"
) -> str:
    repos = repository_commits(collection)
    total = int(collection.get("totalCommitContributions") or 0)
    daily = contribution_days(collection)
    active_days = sum(1 for count in daily.values() if count)
    busiest = max(daily.items(), key=lambda item: (item[1], item[0]), default=None)
    iso_calendar = windows.week_end.isocalendar()
    iso_week = iso_calendar.week
    cat_name, cat = cat_for_iso_week(iso_calendar.year, iso_week, locale)

    maximum = repos[0][1] if repos else 0
    repo_lines: list[str] = []
    for index in range(5):
        if index < len(repos):
            name, count = repos[index]
            line = (
                f"{index + 1:02} {fit(name, 13)} "
                f"{bar(count, maximum)} {count:>3}"
            )
        elif index == 0:
            line = "— 本周无公开提交 —" if locale == "zh" else "-- no public commits --"
        else:
            line = ""
        repo_lines.append(fit(line, REPO_WIDTH))

    if locale == "zh":
        busiest_text = (
            f"峰值 {busiest[0].strftime('%m-%d')} · {busiest[1]}"
            if busiest
            else "峰值 —"
        )
        metrics = (
            f"总提交 {total}",
            f"活跃日 {active_days} / 7",
            f"涉及仓库 {len(repos)}",
            busiest_text,
        )
    else:
        busiest_text = (
            f"busiest {busiest[0].strftime('%m-%d')} · {busiest[1]}"
            if busiest
            else "busiest --"
        )
        metrics = (
            f"total commits {total}",
            f"active days {active_days} / 7",
            f"repos touched {len(repos)}",
            busiest_text,
        )
    repo_lines.extend(fit(metric, REPO_WIDTH) for metric in metrics)

    title = "每周信号" if locale == "zh" else "WEEKLY SIGNAL"
    lines = [top_border(f"{title} · W{iso_week:02}")]
    lines.append(
        full_row(
            f"{windows.week_start.isoformat()} -> {windows.week_end.isoformat()} · UTC+8"
        )
    )
    lines.append(
        "+" + "-" * CAT_WIDTH + "+" + "-" * REPO_WIDTH + "+"
    )
    for cat_line, repo_line in zip(cat, repo_lines, strict=True):
        lines.append(f"|{fit(cat_line, CAT_WIDTH)}|{repo_line}|")
    lines.append(
        "+" + "-" * CAT_WIDTH + "+" + "-" * REPO_WIDTH + "+"
    )
    average = total / active_days if active_days else 0.0
    top_share = round(repos[0][1] / total * 100) if repos and total else 0
    top_name = repos[0][0] if repos else "--"
    if locale == "zh":
        lines.append(full_row(f"活跃日均：{average:.1f} 次提交"))
        lines.append(full_row(f"首位占比：{top_share}% · {top_name}"))
    else:
        lines.append(full_row(f"average: {average:.1f} commits / active day"))
        lines.append(full_row(f"top share: {top_share}% · {top_name}"))
    lines.append(bottom_border())
    assert_card_shape(lines)
    if locale == "zh":
        cat_label = CAT_LABELS_ZH[cat_name]
    else:
        cat_label = "??? CAT" if cat_name == "QUANTUM CAT" else cat_name
    assert cat_label in cat[-1]
    return "\n".join(lines)


def calendar_days(collection: dict[str, Any]) -> list[tuple[date, int]]:
    days: list[tuple[date, int]] = []
    calendar = collection.get("contributionCalendar") or {}
    for week in calendar.get("weeks") or []:
        for item in week.get("contributionDays") or []:
            try:
                day = date.fromisoformat(str(item.get("date")))
            except ValueError:
                continue
            days.append((day, int(item.get("contributionCount") or 0)))
    return sorted(days)


def longest_streak(days: Iterable[tuple[date, int]]) -> int:
    longest = current = 0
    previous: date | None = None
    for day, count in days:
        if count <= 0:
            current = 0
        elif previous is not None and day == previous + timedelta(days=1):
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = day
    return longest


def language_counts(repositories: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for repository in repositories.get("nodes") or []:
        language = repository.get("primaryLanguage") or {}
        name = language.get("name")
        if name:
            counts[str(name)] += 1
    return counts


def language_stats(repositories: dict[str, Any]) -> tuple[int, list[tuple[str, int]]]:
    counts = language_counts(repositories)
    return sum(counts.values()), counts.most_common(3)


LANGUAGE_BADGES = {
    "Python": ("python", "3776AB", "python"),
    "C#": ("c_sharp", "99CC00", "sharp"),
    "JavaScript": ("javascript", "F7DF1E", "javascript"),
    "TypeScript": ("typescript", "3178C6", "typescript"),
    "Markdown": ("markdown", "000000", "markdown"),
    "Shell": ("shell", "4EAA25", "gnubash"),
    "HTML": ("html5", "E34F26", "html5"),
    "CSS": ("css3", "1572B6", "css3"),
    "Java": ("java", "ED8B00", "openjdk"),
    "Go": ("go", "00ADD8", "go"),
    "Rust": ("rust", "000000", "rust"),
    "C++": ("cplusplus", "00599C", "cplusplus"),
}


def language_badges(repositories: dict[str, Any]) -> str:
    badges: list[str] = []
    for name, count in language_counts(repositories).most_common():
        label, color, logo = LANGUAGE_BADGES.get(
            name, (quote(name.lower().replace(" ", "_")), "4B5563", "")
        )
        badges.append(shields_badge(f"{name} ×{count}", label, color, logo))
    return " ".join(badges)


def shields_badge(label: str, slug: str, color: str, logo: str = "") -> str:
    query = (
        f"style=for-the-badge&logo={logo}&logoColor=white"
        if logo
        else "style=for-the-badge"
    )
    source = f"https://img.shields.io/badge/{slug}-{color}?{query}"
    return (
        f'<img src="{html.escape(source, quote=True)}" '
        f'alt="{html.escape(label, quote=True)}"/>'
    )


def toolbox(repositories: dict[str, Any], locale: str = "en") -> str:
    languages = language_badges(repositories)
    if not languages:
        languages = (
            "`未标注主要语言`"
            if locale == "zh"
            else "`no primary language reported`"
        )
    automation = " ".join(
        (
            shields_badge("Python", "python", "3776AB", "python"),
            shields_badge("GraphQL", "graphql", "E10098", "graphql"),
            shields_badge(
                "GitHub Actions", "github_actions", "2088FF", "githubactions"
            ),
        )
    )
    formats = " ".join(
        (
            shields_badge("Markdown", "markdown", "000000", "markdown"),
            shields_badge("SVG", "svg", "FFB13B", "svg"),
        )
    )
    if locale == "zh":
        return "\n\n".join(
            (
                f"公开仓库语言：{languages}",
                f"本页自动化：{automation}",
                f"页面格式：{formats}",
            )
        )
    return "\n\n".join(
        (
            f"Public repository languages: {languages}",
            f"Profile automation: {automation}",
            f"Profile format: {formats}",
        )
    )


def yearly_card(
    collection: dict[str, Any],
    repositories: dict[str, Any],
    windows: DateWindows,
    locale: str = "en",
) -> str:
    commits = int(collection.get("totalCommitContributions") or 0)
    repos = repository_commits(collection)
    days = calendar_days(collection)
    active_days = sum(1 for _, count in days if count > 0)
    streak = longest_streak(days)
    top_repo = f"{repos[0][0]} · {repos[0][1]}" if repos else "--"
    language_total, languages = language_stats(repositories)

    def metric(label: str, value: str | int) -> str:
        return fit(label, 23) + fit(str(value), 19, "right")

    if locale == "zh":
        body = [
            metric("贡献图提交", commits),
            metric("活跃仓库", len(repos)),
            metric("贡献活跃日", active_days),
            metric("最长连续", f"{streak} 天"),
            metric("首位仓库", top_repo),
            metric("标注语言仓库", language_total),
        ]
    else:
        body = [
            metric("profile commits", commits),
            metric("active repositories", len(repos)),
            metric("profile active days", active_days),
            metric("longest streak", f"{streak} days"),
            metric("top repository", top_repo),
            metric("language-tagged repos", language_total),
        ]
    for index in range(3):
        if index < len(languages):
            language, count = languages[index]
            percentage = round(count / language_total * 100) if language_total else 0
            label = "语言" if locale == "zh" else "language"
            body.append(
                f"{label} {index + 1:02}  {fit(language, 12)} "
                f"{percentage:>3}% · {count}"
            )
        else:
            label = "语言" if locale == "zh" else "language"
            body.append(f"{label} {index + 1:02}  --")

    title = "年度轨道" if locale == "zh" else "YEARLY ORBIT"
    lines = [top_border(f"{title} · {windows.as_of.year}")]
    lines.append(
        full_row(
            f"{windows.year_start.isoformat()} -> {windows.week_end.isoformat()} · UTC+8"
        )
    )
    lines.append(full_divider())
    lines.extend(full_row(item) for item in body)
    lines.append(full_divider())
    source = (
        "来源：GitHub 贡献图"
        if locale == "zh"
        else "source: GitHub contribution graph"
    )
    through = "截至" if locale == "zh" else "through"
    lines.append(full_row(source))
    through_line = (
        f"{through}：{windows.week_end.isoformat()} · UTC+8"
        if locale == "zh"
        else f"{through}: {windows.week_end.isoformat()} · UTC+8"
    )
    lines.append(full_row(through_line))
    lines.append(bottom_border())
    assert_card_shape(lines)
    return "\n".join(lines)


def assert_card_shape(lines: list[str]) -> None:
    if len(lines) != 16:
        raise ValueError(f"signal card must be 16 lines, got {len(lines)}")
    expected = CARD_INNER_WIDTH + 2
    bad_lines = [
        index + 1 for index, line in enumerate(lines) if display_width(line) != expected
    ]
    if bad_lines:
        raise ValueError(
            f"signal card lines must be {expected} characters; bad lines: {bad_lines}"
        )


def card_cell(card: str) -> str:
    safe_card = html.escape(card, quote=False)
    return (
        '    <td width="50%" valign="top">\n'
        '      <pre style="font-family:monospace;white-space:pre;overflow-x:auto;">'
        f"{safe_card}</pre>\n"
        "    </td>"
    )


def replace_region(document: str, name: str, replacement: str) -> str:
    start = f"<!-- {name}_SIGNAL_START -->"
    end = f"<!-- {name}_SIGNAL_END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if len(pattern.findall(document)) != 1:
        raise RuntimeError(f"expected exactly one {name.lower()} signal marker pair")
    return pattern.sub(f"{start}\n{replacement}\n    {end}", document)


def replace_toolbox(document: str, replacement: str) -> str:
    start = "<!-- TOOLBOX_START -->"
    end = "<!-- TOOLBOX_END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if len(pattern.findall(document)) != 1:
        raise RuntimeError("expected exactly one toolbox marker pair")
    return pattern.sub(f"{start}\n{replacement}\n{end}", document)


def update_document(
    original: str,
    user: dict[str, Any],
    windows: DateWindows,
    scope: str,
    locale: str,
) -> str:
    updated = original
    if scope in ("all", "weekly"):
        updated = replace_region(
            updated,
            "WEEKLY",
            card_cell(weekly_card(user["weekly"], windows, locale)),
        )
    if scope in ("all", "yearly"):
        updated = replace_region(
            updated,
            "YEARLY",
            card_cell(
                yearly_card(
                    user["yearly"], user["repositories"], windows, locale
                )
            ),
        )
        updated = replace_toolbox(updated, toolbox(user["repositories"], locale))
    return updated


def main() -> int:
    args = parse_args()
    windows = DateWindows.ending_before(args.as_of)

    if args.fixture:
        with args.fixture.open(encoding="utf-8") as fixture_file:
            user = extract_user(json.load(fixture_file))
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("GITHUB_TOKEN is required unless --fixture is used")
        user = fetch_profile_data(token, windows.graphql_variables(args.login))

    targets = [(args.readme, "en")]
    if args.zh_readme:
        targets.append((args.zh_readme, "zh"))
    for readme, locale in targets:
        original = readme.read_text(encoding="utf-8")
        updated = update_document(original, user, windows, args.scope, locale)
        if updated != original:
            readme.write_text(updated, encoding="utf-8")
            print(f"updated {args.scope} signal data in {readme}")
        else:
            print(f"no {args.scope} signal changes in {readme}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        print(f"profile update failed: {error}", file=sys.stderr)
        raise SystemExit(1)
