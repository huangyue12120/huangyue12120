#!/usr/bin/env python3
"""Refresh the generated signal cards in the profile README."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
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
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
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


def fit(value: str, width: int, align: str = "left") -> str:
    if len(value) > width:
        value = value[: max(0, width - 1)] + "…"
    return value.rjust(width) if align == "right" else value.ljust(width)


def top_border(title: str) -> str:
    prefix = f"╭─ {title} "
    return prefix + "─" * (CARD_INNER_WIDTH + 1 - len(prefix)) + "╮"


def full_row(value: str) -> str:
    return f"│{fit(value, CARD_INNER_WIDTH)}│"


def full_divider() -> str:
    return "├" + "─" * CARD_INNER_WIDTH + "┤"


def bottom_border() -> str:
    return "╰" + "─" * CARD_INNER_WIDTH + "╯"


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
        return "░" * width
    filled = max(1, round(value / maximum * width))
    return "█" * filled + "░" * (width - filled)


def weekly_card(collection: dict[str, Any], windows: DateWindows) -> str:
    repos = repository_commits(collection)
    total = int(collection.get("totalCommitContributions") or 0)
    daily = contribution_days(collection)
    active_days = sum(1 for count in daily.values() if count)
    busiest = max(daily.items(), key=lambda item: (item[1], item[0]), default=None)
    iso_week = windows.week_end.isocalendar().week
    cat_name, cat = CAT_VARIANTS[
        (iso_week + CAT_ROTATION_OFFSET) % len(CAT_VARIANTS)
    ]

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
            line = "-- no public commits --"
        else:
            line = ""
        repo_lines.append(fit(line, REPO_WIDTH))

    busiest_text = (
        f"busiest {busiest[0].strftime('%m-%d')} · {busiest[1]}"
        if busiest
        else "busiest --"
    )
    repo_lines.extend(
        (
            fit(f"total commits {total}", REPO_WIDTH),
            fit(f"active days {active_days} / 7", REPO_WIDTH),
            fit(f"repos touched {len(repos)}", REPO_WIDTH),
            fit(busiest_text, REPO_WIDTH),
        )
    )

    lines = [top_border(f"WEEKLY SIGNAL · W{iso_week:02}")]
    lines.append(full_row("observed public commit activity"))
    lines.append(
        "├" + "─" * CAT_WIDTH + "┬" + "─" * REPO_WIDTH + "┤"
    )
    for cat_line, repo_line in zip(cat, repo_lines, strict=True):
        lines.append(f"│{fit(cat_line, CAT_WIDTH)}│{repo_line}│")
    lines.append(
        "├" + "─" * CAT_WIDTH + "┴" + "─" * REPO_WIDTH + "┤"
    )
    commit_word = "commit" if total == 1 else "commits"
    repo_word = "repo" if len(repos) == 1 else "repos"
    lines.append(
        full_row(f"summary: {total} {commit_word} across {len(repos)} {repo_word}")
    )
    lines.append(
        full_row(
            f"window: {windows.week_start.isoformat()} -> "
            f"{windows.week_end.isoformat()}"
        )
    )
    lines.append(bottom_border())
    assert_card_shape(lines)
    # Keep the selected name available to tests and future copy changes.
    assert cat_name in cat[-1]
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


def language_stats(repositories: dict[str, Any]) -> tuple[int, list[tuple[str, int]]]:
    counts: Counter[str] = Counter()
    for repository in repositories.get("nodes") or []:
        language = repository.get("primaryLanguage") or {}
        name = language.get("name")
        if name:
            counts[str(name)] += 1
    return sum(counts.values()), counts.most_common(3)


def yearly_card(
    collection: dict[str, Any], repositories: dict[str, Any], windows: DateWindows
) -> str:
    commits = int(collection.get("totalCommitContributions") or 0)
    repos = repository_commits(collection)
    days = calendar_days(collection)
    active_days = sum(1 for _, count in days if count > 0)
    streak = longest_streak(days)
    top_repo = f"{repos[0][0]} · {repos[0][1]}" if repos else "--"
    language_total, languages = language_stats(repositories)

    def metric(label: str, value: str | int) -> str:
        return f"{label:<23}{str(value):>19}"

    body = [
        metric("public commits", commits),
        metric("active repositories", len(repos)),
        metric("public active days", active_days),
        metric("longest streak", f"{streak} days"),
        metric("most active repo", top_repo),
        metric("language-tagged repos", language_total),
    ]
    for index in range(3):
        if index < len(languages):
            language, count = languages[index]
            percentage = round(count / language_total * 100) if language_total else 0
            body.append(
                f"language {index + 1:02}  {fit(language, 12)} {percentage:>3}% · {count}"
            )
        else:
            body.append(f"language {index + 1:02}  --")

    lines = [top_border(f"YEARLY ORBIT · {windows.as_of.year}")]
    lines.append(full_row("year-to-date / learning log"))
    lines.append(full_divider())
    lines.extend(full_row(item) for item in body)
    lines.append(full_divider())
    lines.append(full_row("source: public GitHub activity"))
    lines.append(full_row(f"through: {windows.week_end.isoformat()} · UTC+8"))
    lines.append(bottom_border())
    assert_card_shape(lines)
    return "\n".join(lines)


def assert_card_shape(lines: list[str]) -> None:
    if len(lines) != 16:
        raise ValueError(f"signal card must be 16 lines, got {len(lines)}")
    expected = CARD_INNER_WIDTH + 2
    bad_lines = [index + 1 for index, line in enumerate(lines) if len(line) != expected]
    if bad_lines:
        raise ValueError(
            f"signal card lines must be {expected} characters; bad lines: {bad_lines}"
        )


def card_cell(card: str) -> str:
    safe_card = html.escape(card, quote=False)
    return (
        '    <td width="50%" valign="top">\n'
        f"      <pre>{safe_card}</pre>\n"
        "    </td>"
    )


def replace_region(document: str, name: str, replacement: str) -> str:
    start = f"<!-- {name}_SIGNAL_START -->"
    end = f"<!-- {name}_SIGNAL_END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if len(pattern.findall(document)) != 1:
        raise RuntimeError(f"expected exactly one {name.lower()} signal marker pair")
    return pattern.sub(f"{start}\n{replacement}\n    {end}", document)


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

    original = args.readme.read_text(encoding="utf-8")
    updated = original
    if args.scope in ("all", "weekly"):
        updated = replace_region(
            updated, "WEEKLY", card_cell(weekly_card(user["weekly"], windows))
        )
    if args.scope in ("all", "yearly"):
        updated = replace_region(
            updated,
            "YEARLY",
            card_cell(yearly_card(user["yearly"], user["repositories"], windows)),
        )

    if updated != original:
        args.readme.write_text(updated, encoding="utf-8")
        print(f"updated {args.scope} signal data in {args.readme}")
    else:
        print(f"no {args.scope} signal changes in {args.readme}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        print(f"profile update failed: {error}", file=sys.stderr)
        raise SystemExit(1)
