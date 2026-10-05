import json
import os
import re

from bs4 import BeautifulSoup

from logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------
# Layout schemas
# ---------------------------------------------------------
# Supercell has shipped two distinct export layouts. Rather than trying to make
# one set of regexes tolerate both, each layout is described in full and the
# parser tries them in turn at runtime, keeping the first that yields the core
# fields (see extract_hay_day_data). This makes a future format change easy to
# absorb: add a new LAYOUT entry instead of editing shared regexes.
#
#   "legacy"  -- the original export. Game sections are <h2>; per-field lines
#                live in <p> tags; sentences are phrased "Your name is ...",
#                "You have played ...", etc.
#   "v2026"   -- the export seen from ~2026-07 onwards. Game sections dropped a
#                level to <h3> (subsections became <h4>); per-field lines moved
#                into <ul>/<li>; sentences were reworded ("Player name is ...",
#                "Played ...", "Reputation level is ...", and name/age split
#                across two lines).
#
# Each layout exposes:
#   section_tag  -- heading tag that introduces the "Hay Day" game section
#   content_tags -- tags whose text carries the field lines
#   parse(lines) -> dict of the fields matched in that layout's phrasing
# ---------------------------------------------------------

# Core fields that must be present for a parse to be considered successful.
# If neither layout produces all of these, the export layout has drifted again.
CORE_FIELDS = ("name", "level", "gems")


def _parse_legacy(lines):
    """Parse the field lines of the original ('legacy') export layout."""
    data = {}
    for line in lines:
        m = re.search(r"Your name is (.+?) and age is (\d+)", line)
        if m:
            data["name"] = m.group(1)
            data["age"] = int(m.group(2))

        m = re.search(r"Your farm was created on (.+?) in (.+?) \((.+?)\)", line)
        if m:
            data["farm_created"] = m.group(1)
            data["farm_country"] = m.group(2)
            data["farm_ip"] = m.group(3)

        m = re.search(r"Your account is (.+?) and (.+?)\.", line)
        if m:
            data["banned"] = m.group(1)
            data["locked"] = m.group(2)

        m = re.search(r"You have played (\d+) sessions", line)
        if m:
            data["total_sessions"] = int(m.group(1))

        m = re.search(
            r"You are a member of a neighborhood called (.+?)\. Your rank is \"(.+?)\"",
            line,
        )
        if m:
            data["neighborhood"] = m.group(1)
            data["rank"] = m.group(2)

        m = re.search(r"You have (\d+) gems", line)
        if m:
            data["gems"] = int(m.group(1))

        m = re.search(r"reputation level (\d+) and (\d+) experience points", line)
        if m:
            data["reputation_level"] = int(m.group(1))
            data["experience_points"] = int(m.group(2))

        m = re.search(r"experience level is (\d+)", line)
        if m:
            data["level"] = int(m.group(1))

        _match_coins(data, line)
        _match_valley(data, line)
        _match_gamecenter(data, line)

    return data


def _parse_v2026(lines):
    """Parse the field lines of the 2026-07 export layout.

    Wording differs from legacy, and name/age are split across two <li> lines
    instead of sharing one sentence, so those get separate patterns here.
    """
    data = {}
    for line in lines:
        m = re.search(r"Player name is (.+)", line)
        if m:
            data["name"] = m.group(1).strip()

        m = re.search(r"Player age is (\d+)", line)
        if m:
            data["age"] = int(m.group(1))

        m = re.search(r"Farm was created on (.+?) in (.+?) \((.+?)\)", line)
        if m:
            data["farm_created"] = m.group(1)
            data["farm_country"] = m.group(2)
            data["farm_ip"] = m.group(3)

        m = re.search(r"Account is (.+?) and (.+?)\.", line)
        if m:
            data["banned"] = m.group(1)
            data["locked"] = m.group(2)

        m = re.search(r"Played (\d+) sessions", line)
        if m:
            data["total_sessions"] = int(m.group(1))

        m = re.search(
            r"Member of a neighborhood called (.+?)\.\s*and rank is \"(.+?)\"",
            line,
        )
        if m:
            data["neighborhood"] = m.group(1)
            data["rank"] = m.group(2)

        m = re.search(r"(\d+) gems", line)
        if m:
            data["gems"] = int(m.group(1))

        m = re.search(r"Reputation level is (\d+) and (\d+) experience points", line)
        if m:
            data["reputation_level"] = int(m.group(1))
            data["experience_points"] = int(m.group(2))

        m = re.search(r"Experience level is (\d+)", line)
        if m:
            data["level"] = int(m.group(1))

        _match_coins(data, line)
        _match_valley(data, line)
        _match_gamecenter(data, line)

    return data


# ---------------------------------------------------------
# Field matchers shared by both layouts (identical phrasing across versions)
# ---------------------------------------------------------
def _match_coins(data, line):
    m = re.search(
        r"Resources: (\d+) coins and vouchers: "
        r"(\d+) Blue, (\d+) Green, (\d+) Purple and (\d+) Gold",
        line,
    )
    if m:
        data["coins"] = int(m.group(1))
        data["vouchers"] = {
            "blue": int(m.group(2)),
            "green": int(m.group(3)),
            "purple": int(m.group(4)),
            "gold": int(m.group(5)),
        }


def _match_valley(data, line):
    m = re.search(
        r"Your Valley resources: (\d+) fuel, (\d+) chickens, "
        r"(\d+) sanctuary animals, (\d+) sun points and vouchers: "
        r"(\d+) Blue, (\d+) Green, (\d+) Red",
        line,
    )
    if m:
        data["valley"] = {
            "fuel": int(m.group(1)),
            "chickens": int(m.group(2)),
            "sanctuary_animals": int(m.group(3)),
            "sun_points": int(m.group(4)),
            "vouchers": {
                "blue": int(m.group(5)),
                "green": int(m.group(6)),
                "red": int(m.group(7)),
            },
        }


def _match_gamecenter(data, line):
    m = re.search(r"GameCenter: (.+)", line)
    if m:
        data["gamecenter"] = m.group(1)


LAYOUTS = (
    {
        "name": "legacy",
        "section_tag": "h2",
        "content_tags": ("p",),
        "parse": _parse_legacy,
    },
    {
        "name": "v2026",
        "section_tag": "h3",
        "content_tags": ("li",),
        "parse": _parse_v2026,
    },
)


# ---------------------------------------------------------
# Collect the field lines of the Hay Day section for a given layout
# ---------------------------------------------------------
def _collect_hay_day_lines(soup, section_tag, content_tags):
    """Return the normalized text of ``content_tags`` inside the Hay Day
    section introduced by ``section_tag``, or ``None`` if this layout has no
    such section.

    Walks forward from the "Hay Day" heading and stops at the next heading of
    the same or higher level (a different game / top-level section), so values
    from later sections never leak in. Subsection headings (a deeper level) are
    stepped over, since they still belong to Hay Day.
    """
    header = soup.find(section_tag, string="Hay Day")
    if header is None:
        return None

    section_level = int(section_tag[1])
    lines = []
    for tag in header.find_all_next():
        if tag.name and re.fullmatch(r"h[1-6]", tag.name):
            level = int(tag.name[1])
            if level <= section_level and tag.get_text(strip=True) != "Hay Day":
                break
        if tag.name in content_tags:
            # Collapse internal whitespace (the export wraps long lines, so a
            # single sentence can span newlines) to keep the regexes simple.
            text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
            if text:
                lines.append(text)
    return lines


# ---------------------------------------------------------
# Extract Hay Day data from a single HTML file
# ---------------------------------------------------------
def extract_hay_day_data(html_path):
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    # ---------------------------------------------------------
    # Extract EMAIL_DATE from appended HTML comment (same in every layout)
    # ---------------------------------------------------------
    email_date = None
    email_date_comment = soup.find(string=re.compile(r"EMAIL_DATE"))
    if email_date_comment:
        m = re.search(r"EMAIL_DATE:\s*(.+)", email_date_comment)
        if m:
            email_date = m.group(1)

    # ---------------------------------------------------------
    # Try each known layout and keep the one that parses best. A layout that
    # can't even find a Hay Day section is skipped; among those that do, the
    # first to yield every core field wins. Both formats have distinct heading
    # tags, so in practice exactly one layout finds a section -- but validating
    # on the core fields means a half-recognized section still fails loudly
    # rather than emitting a half-empty row.
    # ---------------------------------------------------------
    section_found = False
    best = None  # (matched_core_count, layout_name, data)
    for layout in LAYOUTS:
        lines = _collect_hay_day_lines(
            soup, layout["section_tag"], layout["content_tags"]
        )
        if lines is None:
            continue
        section_found = True
        data = layout["parse"](lines)
        matched_core = sum(1 for f in CORE_FIELDS if f in data)
        if all(f in data for f in CORE_FIELDS):
            best = (matched_core, layout["name"], data)
            break
        if best is None or matched_core > best[0]:
            best = (matched_core, layout["name"], data)

    if not section_found:
        raise ValueError(f"Unexpected HTML format in {html_path}: no Hay Day section")

    _, layout_name, data = best
    logger.info(f"{html_path}: parsed with '{layout_name}' layout")

    if email_date is not None:
        data["email_date"] = email_date

    # ---------------------------------------------------------
    # Surface partial format drift: log any expected field whose regex
    # matched nothing, so a layout change is visible in the logs before it
    # silently produces incomplete rows (or trips the hard check below).
    # ---------------------------------------------------------
    expected_fields = (
        "email_date",
        "name",
        "age",
        "farm_created",
        "farm_country",
        "farm_ip",
        "banned",
        "locked",
        "total_sessions",
        "rank",
        "gems",
        "reputation_level",
        "experience_points",
        "level",
        "coins",
        "vouchers",
        "valley",
    )
    # Fields that are legitimately absent for accounts that never used them.
    # Their absence isn't format drift, so keep it at info level to avoid noise.
    optional_fields = (
        "neighborhood",
        "gamecenter",
    )
    unmatched = [f for f in expected_fields if f not in data]
    if unmatched:
        logger.warning(
            f"{html_path}: no match for fields {unmatched} "
            "(possible export format drift)"
        )
    unmatched_optional = [f for f in optional_fields if f not in data]
    if unmatched_optional:
        logger.info(f"{html_path}: no match for optional fields {unmatched_optional}")

    # ---------------------------------------------------------
    # Sanity-check the parse: core fields must be present, otherwise
    # the export layout has changed and downstream data would be garbage.
    # ---------------------------------------------------------
    missing = [f for f in CORE_FIELDS if f not in data]
    if missing:
        raise ValueError(
            f"Unexpected HTML format in {html_path}: missing core fields {missing}"
        )

    # ---------------------------------------------------------
    # Save JSON next to HTML
    # ---------------------------------------------------------
    json_path = os.path.splitext(html_path)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    logger.info(f"Saved Hay Day JSON to {json_path}")
    return data


# ---------------------------------------------------------
# Process all HTML files missing JSON
# ---------------------------------------------------------
def process(directory="downloads"):
    if not os.path.isdir(directory):
        logger.warning(f"Directory '{directory}' does not exist.")
        return

    files = os.listdir(directory)
    html_files = [f for f in files if f.endswith(".html")]

    if not html_files:
        logger.info("No HTML files found.")
        return

    errors = []
    for filename in html_files:
        html_path = os.path.join(directory, filename)
        json_path = os.path.splitext(html_path)[0] + ".json"

        if os.path.exists(json_path):
            logger.info(f"JSON already exists for {filename}, skipping.")
            continue

        logger.info(f"Processing {filename}...")
        try:
            extract_hay_day_data(html_path)
        except Exception as e:
            logger.error(f"Failed to process {filename}: {e}")
            errors.append(filename)

    if errors:
        raise ValueError(
            f"Failed to parse {len(errors)} HTML file(s) with unexpected format: "
            f"{errors}"
        )


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------
if __name__ == "__main__":
    from logger import setup_console_logging

    setup_console_logging()
    process()
