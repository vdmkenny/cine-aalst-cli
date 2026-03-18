"""
Cine Aalst CLI tool to fetch and display movie schedules.

Scrapes the cine-aalst.be website for current programming and
displays movie info with showtimes in the terminal.
"""

import argparse
import re
import sys
import textwrap
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://cine-aalst.be"
PROGRAMMA_URL = f"{BASE_URL}/programma"
FILMINFO_URL = f"{BASE_URL}/filminfo"
TICKETS_URL = f"{BASE_URL}/tickets"

# Dutch day abbreviations used on the site (2-letter) -> weekday index
DUTCH_DAY_ABBR = {
    "ma": 0,
    "di": 1,
    "wo": 2,
    "do": 3,
    "vr": 4,
    "za": 5,
    "zo": 6,
}

REQUEST_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def parse_dutch_date(date_str):
    """
    Parse a Dutch date string from the website into a datetime.date.

    Handles formats like:
        "Wo 18/03"
        "Do 19/03"
        "Vandaag"
        "Morgen"
        "Za 21/03/2026"

    Returns a datetime.date or None if parsing fails.
    """
    text = date_str.strip()

    if text.lower() == "vandaag":
        return datetime.today().date()
    if text.lower() == "morgen":
        return (datetime.today() + timedelta(days=1)).date()

    # Try to extract day/month (and optional year) from the string
    match = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
    if not match:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year_str = match.group(3)

    if year_str:
        year = int(year_str)
        if year < 100:
            year += 2000
    else:
        # Infer year: use current year, but if the date has already passed
        # more than 2 months ago, assume next year.
        today = datetime.today().date()
        year = today.year
        try:
            candidate = datetime(year, month, day).date()
        except ValueError:
            return None
        if candidate < today - timedelta(days=60):
            year += 1

    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def parse_cli_date(date_str):
    """
    Parse a date argument from the command line.

    Accepts 'today', 'tomorrow', or YYYY-MM-DD.
    """
    lower = date_str.lower()
    if lower == "today":
        return datetime.today().date()
    if lower == "tomorrow":
        return (datetime.today() + timedelta(days=1)).date()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Invalid date format. Please use YYYY-MM-DD, 'today' or 'tomorrow'."
        ) from exc


# ---------------------------------------------------------------------------
# Fetching helpers
# ---------------------------------------------------------------------------


def _fetch_page(url):
    """
    Fetch a URL and return a BeautifulSoup object.

    Exits gracefully on network errors.
    """
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Error fetching {url}: {exc}", file=sys.stderr)
        sys.exit(1)

    return BeautifulSoup(response.text, "lxml")


# ---------------------------------------------------------------------------
# Scraping: programme page
# ---------------------------------------------------------------------------


def _parse_showtime(chip):
    """
    Parse a single showchip <a> element into a screening dict.

    Expected HTML:
        <a class="showchip" href="/tickets/354/4978">
            <span class="t">20:00</span>
            <span class="meta">
                <span class="loc">... Palace</span> ·
                <span class="zaal">Zaal 3</span>
            </span>
        </a>
    """
    time_el = chip.find("span", class_="t")
    loc_el = chip.find("span", class_="loc")
    zaal_el = chip.find("span", class_="zaal")

    time_str = time_el.get_text(strip=True) if time_el else ""
    location = loc_el.get_text(strip=True) if loc_el else ""
    # Strip leading emoji/pin characters from location
    location = re.sub(r"^[^\w]+", "", location).strip()
    zaal = zaal_el.get_text(strip=True) if zaal_el else ""

    href = chip.get("href", "")
    ticket_url = f"{BASE_URL}{href}" if href else ""

    return {
        "time": time_str,
        "location": location,
        "zaal": zaal,
        "ticket_url": ticket_url,
    }


def _parse_schedule_block(week_block):
    """
    Parse a week-block div into a list of schedule entries.

    Each entry is a dict: {"date": date|None, "date_label": str, "screenings": [...]}.
    """
    schedule = []
    week_rows = week_block.find_all("div", class_="week-row")

    for row in week_rows:
        day_el = row.find("div", class_="week-day")
        times_el = row.find("div", class_="week-times")

        day_label = day_el.get_text(strip=True) if day_el else ""
        parsed_date = parse_dutch_date(day_label)

        screenings = []
        if times_el:
            for chip in times_el.find_all("a", class_="showchip"):
                screenings.append(_parse_showtime(chip))

        if screenings:
            schedule.append(
                {
                    "date": parsed_date,
                    "date_label": day_label,
                    "screenings": screenings,
                }
            )

    return schedule


def _parse_film_id_and_poster(block):
    """
    Extract film ID, filminfo URL, tickets URL and poster URL from a movie block.

    Returns a dict with keys: id, filminfo_url, tickets_url, poster_url.
    """
    result = {"id": None, "filminfo_url": "", "tickets_url": "", "poster_url": ""}
    link = block.find("a", href=re.compile(r"/filminfo/\d+"))
    if not link:
        return result

    href = link.get("href", "")
    match = re.search(r"/filminfo/(\d+)", href)
    if match:
        result["id"] = match.group(1)
        result["filminfo_url"] = f"{BASE_URL}{href}"
        result["tickets_url"] = f"{TICKETS_URL}/{result['id']}"

    img = link.find("img")
    if img:
        result["poster_url"] = img.get("src", "")

    return result


def _parse_heading(block):
    """
    Extract title, language and runtime from the <h3> inside a movie block.

    Returns a dict with keys: title, language, runtime.
    """
    result = {"title": "", "language": "", "runtime": ""}
    h3 = block.find("h3")
    if not h3:
        return result

    # Title = direct text nodes of <h3>, excluding child spans
    title_parts = [
        child.strip()
        for child in h3.children
        if isinstance(child, str) and child.strip()
    ]
    result["title"] = " ".join(title_parts).strip()

    # Language badge (OV / NV)
    lang_badge = h3.find("span", class_="badge")
    if lang_badge:
        result["language"] = lang_badge.get_text(strip=True)

    # Runtime — first span containing "min" that is not a badge
    for span in h3.find_all("span"):
        span_text = span.get_text(strip=True)
        if "min" in span_text.lower() and "badge" not in (span.get("class") or []):
            result["runtime"] = span_text
            break

    return result


def _parse_movie_block(block):
    """
    Parse a single movie-tabs row div into a movie dict.

    Returns a dict with keys:
        id, title, genre, language, runtime, description, poster_url,
        label, schedule, filminfo_url, tickets_url
    """
    ids = _parse_film_id_and_poster(block)
    heading = _parse_heading(block)

    label_el = block.find("span", class_="movie-label")
    genre_el = block.find("span", class_="title")
    desc_el = block.find("p")
    week_block = block.find("div", class_="week-block")

    return {
        **ids,
        **heading,
        "genre": genre_el.get_text(strip=True) if genre_el else "",
        "label": label_el.get_text(strip=True) if label_el else "",
        "description": desc_el.get_text(strip=True) if desc_el else "",
        "schedule": _parse_schedule_block(week_block) if week_block else [],
    }


def fetch_programme():
    """
    Fetch and parse the full programme from cine-aalst.be/programma.

    Returns a list of movie dicts.
    """
    soup = _fetch_page(PROGRAMMA_URL)

    # The "allfilms" section contains all movies with full week schedules
    allfilms = soup.find("div", id="allfilms")
    if not allfilms:
        # Fallback: search entire page
        allfilms = soup

    blocks = allfilms.find_all("div", class_="movie-tabs")
    if not blocks:
        print("No movies found on the programme page.", file=sys.stderr)
        return []

    movies = []
    seen_ids = set()
    for block in blocks:
        movie = _parse_movie_block(block)
        if not movie["title"]:
            continue
        # Deduplicate: the page may repeat movies across date-tab sections
        key = movie["id"] or movie["title"]
        if key in seen_ids:
            continue
        seen_ids.add(key)
        movies.append(movie)

    return movies


# ---------------------------------------------------------------------------
# Scraping: film detail page (optional, for extra info)
# ---------------------------------------------------------------------------


def _parse_info_list(soup):
    """
    Extract director, actors and release date from a filminfo page's <ul class="movie-info">.

    Returns a dict with keys: director, actors, release_date.
    """
    result = {"director": "", "actors": "", "release_date": ""}
    info_list = soup.find("ul", class_="movie-info")
    if not info_list:
        return result

    label_key_map = {"regie": "director", "acteur": "actors", "release": "release_date"}

    for li in info_list.find_all("li"):
        label_el = li.find("i")
        if not label_el:
            continue
        label = label_el.get_text(strip=True).lower()
        value = li.get_text(strip=True)[len(label_el.get_text(strip=True)) :].strip()

        for keyword, key in label_key_map.items():
            if keyword in label:
                result[key] = value
                break

    return result


def _parse_full_description(soup):
    """
    Extract the full synopsis text from a filminfo page.

    Returns the description string (may be empty).
    """
    single_movie = soup.find("div", class_="single-movie")
    if not single_movie:
        return ""

    col7 = single_movie.find("div", class_="col-sm-7")
    if not col7:
        return ""

    target = col7.find("div", class_="col-sm-7") or col7
    text_parts = [
        child.strip()
        for child in target.children
        if isinstance(child, str) and child.strip()
    ]
    return " ".join(text_parts)


def _parse_trailer_url(soup):
    """
    Extract the YouTube trailer URL from a filminfo page.

    Returns the URL string (may be empty).
    """
    trailer_tile = soup.find("div", class_="trailer-tile")
    if not trailer_tile:
        return ""
    yt_id = trailer_tile.get("data-yt", "")
    return f"https://www.youtube.com/watch?v={yt_id}" if yt_id else ""


def fetch_film_details(film_id):
    """
    Fetch extra details from /filminfo/{id}.

    Returns a dict with: director, actors, release_date, trailer_url, full_description.
    Returns None on failure.
    """
    url = f"{FILMINFO_URL}/{film_id}"
    try:
        soup = _fetch_page(url)
    except SystemExit:
        return None

    info = _parse_info_list(soup)
    return {
        **info,
        "full_description": _parse_full_description(soup),
        "trailer_url": _parse_trailer_url(soup),
    }


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def filter_movies_by_date(movies, target_date):
    """
    Filter movies to only include screenings on the target_date.

    Returns a new list of movies (with filtered schedules). Movies with
    no screenings on the target date are excluded.
    """
    filtered = []
    for movie in movies:
        matching_entries = [
            entry for entry in movie["schedule"] if entry["date"] == target_date
        ]
        if matching_entries:
            filtered_movie = dict(movie)
            filtered_movie["schedule"] = matching_entries
            filtered.append(filtered_movie)
    return filtered


def filter_movies_by_title(movies, query):
    """
    Filter movies whose title contains the query (case-insensitive).
    """
    query_lower = query.lower()
    return [m for m in movies if query_lower in m["title"].lower()]


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


# ANSI escape helpers
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_ITALIC = "\033[3m"
_UNDERLINE = "\033[4m"
_FG_RED = "\033[31m"
_FG_GREEN = "\033[32m"
_FG_YELLOW = "\033[33m"
_FG_BLUE = "\033[34m"
_FG_MAGENTA = "\033[35m"
_FG_CYAN = "\033[36m"
_FG_WHITE = "\033[37m"
_FG_BRIGHT_BLACK = "\033[90m"  # grey
_FG_BRIGHT_YELLOW = "\033[93m"
_FG_BRIGHT_BLUE = "\033[94m"
_FG_BRIGHT_CYAN = "\033[96m"
_BG_BLUE = "\033[44m"
_BG_GREEN = "\033[42m"


def _hyperlink(url, text):
    """
    Return an OSC 8 terminal hyperlink if url is non-empty, otherwise plain text.
    """
    if url:
        return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"
    return text


def _lang_tag(language):
    """
    Return a colored language tag string, or empty string if no language.

    OV  = Original Version  -> cyan
    NV  = Dutch Version     -> green
    """
    if not language:
        return ""
    tag = language.strip()
    upper = tag.upper()
    if "OV" in upper:
        return f" {_FG_CYAN}{_BOLD}OV{_RESET}"
    if "NV" in upper:
        return f" {_FG_GREEN}{_BOLD}NV{_RESET}"
    # Unknown language tag — show as-is in yellow
    return f" {_FG_YELLOW}{tag}{_RESET}"


def _label_tag(label):
    """
    Return a colored label badge (e.g. Nieuw, Verwacht, Laatste kans).
    """
    if not label:
        return ""
    lower = label.lower()
    if "nieuw" in lower:
        color = _FG_GREEN
    elif "verwacht" in lower:
        color = _FG_MAGENTA
    elif "laatste" in lower:
        color = _FG_RED
    else:
        color = _FG_BRIGHT_YELLOW
    return f"  {color}{_BOLD}{label}{_RESET}"


def _section(label, value):
    """Format a labelled section line with dimmed label."""
    return f"  {_DIM}{label}:{_RESET} {value}"


def _wrap_text(text, width=80, indent=4):
    """
    Wrap long text to *width* columns, with *indent* spaces on continuation lines.
    """
    prefix = " " * indent
    lines = textwrap.wrap(text, width=width - indent)
    if not lines:
        return ""
    # First line has no extra indent (caller already indents via _section)
    return ("\n" + prefix).join(lines)


def _print_links(movie):
    """Print the links row (poster + more info) for a movie."""
    links = []
    if movie.get("poster_url"):
        links.append(_hyperlink(movie["poster_url"], f"{_UNDERLINE}Poster{_RESET}"))
    if movie.get("filminfo_url"):
        links.append(
            _hyperlink(movie["filminfo_url"], f"{_UNDERLINE}More info{_RESET}")
        )
    if links:
        print(f"  {_DIM}Links:{_RESET} {f'{_DIM} | {_RESET}'.join(links)}")


def _print_details(movie):
    """Print extra filminfo details (director, actors, release, trailer, synopsis)."""
    details = movie.get("details")
    if not details:
        return

    for label, key in (
        ("Director", "director"),
        ("Actors", "actors"),
        ("Release", "release_date"),
    ):
        if details.get(key):
            print(_section(label, details[key]))

    if details.get("trailer_url"):
        print(
            _section(
                "Trailer",
                _hyperlink(details["trailer_url"], f"{_UNDERLINE}YouTube{_RESET}"),
            )
        )

    full_desc = details.get("full_description", "")
    if full_desc and full_desc != movie.get("description", ""):
        print(_section("Synopsis", _wrap_text(full_desc)))


def _format_screening_line(day_str, screening):
    """Format a single screening as a terminal line string."""
    time_display = f"{_BOLD}{screening['time']}{_RESET}"

    loc_parts = [p for p in (screening["zaal"], screening["location"]) if p]
    loc_info = f"  {_DIM}{' - '.join(loc_parts)}{_RESET}" if loc_parts else ""

    ticket = screening["ticket_url"]
    ticket_link = (
        f"  {_hyperlink(ticket, f'{_FG_YELLOW}Tickets{_RESET}')}" if ticket else ""
    )

    return (
        f"    {_FG_BRIGHT_BLACK}{day_str:<12}{_RESET}"
        f"{time_display}{loc_info}{ticket_link}"
    )


def _print_schedule(schedule):
    """Print the schedule table for a movie."""
    if not schedule:
        return
    print(f"  {_DIM}Schedule:{_RESET}")
    for entry in schedule:
        for screening in entry["screenings"]:
            print(_format_screening_line(entry["date_label"], screening))


def print_movie(movie, show_details=False):
    """
    Print a single movie with its schedule to the terminal.
    """
    lang = _lang_tag(movie.get("language", ""))
    runtime = f"  {_DIM}{movie['runtime']}{_RESET}" if movie.get("runtime") else ""
    label = _label_tag(movie.get("label", ""))

    print(f"{_BOLD}{_FG_BRIGHT_BLUE}{movie['title']}{_RESET}{lang}{runtime}{label}")

    if movie["genre"]:
        print(_section("Genre", f"{_ITALIC}{movie['genre']}{_RESET}"))
    if movie["description"]:
        print(_section("Description", _wrap_text(movie["description"])))

    _print_links(movie)

    if show_details:
        _print_details(movie)

    _print_schedule(movie["schedule"])
    print()


def print_movies(movies, heading=None, show_details=False):
    """
    Print a list of movies with an optional heading.
    """
    if heading:
        print()
        print(f"  {_BOLD}{_UNDERLINE}{heading}{_RESET}")
        print()

    if not movies:
        print("  No movies found.")
        return

    for movie in movies:
        print_movie(movie, show_details=show_details)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    """
    Main entry point: parse arguments and display movie schedules.
    """
    parser = argparse.ArgumentParser(description="Get movie schedules for Cine Aalst.")
    parser.add_argument(
        "-d",
        "--date",
        type=parse_cli_date,
        help=(
            "Date for which to get movie schedules. "
            "Can be 'today', 'tomorrow', or YYYY-MM-DD format. "
            "Can be combined with -m."
        ),
    )
    parser.add_argument(
        "-m",
        "--movie",
        type=str,
        help="Search for a movie by title. Can be combined with -d.",
    )
    args = parser.parse_args()

    # Fetch all movies from the programme page
    movies = fetch_programme()

    if not movies:
        print("Could not retrieve any movie data.", file=sys.stderr)
        sys.exit(1)

    # Apply filters
    if args.date:
        movies = filter_movies_by_date(movies, args.date)

    if args.movie:
        movies = filter_movies_by_title(movies, args.movie)
        # For title-searched movies, fetch extra details from filminfo pages
        for movie in movies:
            if movie["id"]:
                details = fetch_film_details(movie["id"])
                if details:
                    movie["details"] = details

    # Build heading
    heading_parts = []
    if args.movie:
        heading_parts.append(f"Movies matching '{args.movie}'")
    else:
        heading_parts.append("Movies and Schedules")

    if args.date:
        heading_parts.append(f"for {args.date.strftime('%Y-%m-%d')}")
    else:
        heading_parts.append("(all dates)")

    heading = " ".join(heading_parts)

    print_movies(movies, heading=heading, show_details=bool(args.movie))


if __name__ == "__main__":
    main()
