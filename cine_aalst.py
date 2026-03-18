"""
Cine Aalst CLI tool to fetch and display movie schedules.

Scrapes the cine-aalst.be website for current programming and
displays movie info with showtimes in the terminal.
"""

import argparse
import locale
import re
import sys
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


def _parse_movie_block(block):
    """
    Parse a single movie-tabs row div into a movie dict.

    Returns a dict with keys:
        id, title, genre, language, runtime, description, poster_url,
        label, schedule, filminfo_url, tickets_url
    """
    movie = {
        "id": None,
        "title": "",
        "genre": "",
        "language": "",
        "runtime": "",
        "description": "",
        "poster_url": "",
        "label": "",
        "schedule": [],
        "filminfo_url": "",
        "tickets_url": "",
    }

    # --- Film ID & poster ---
    poster_link = block.find("a", href=re.compile(r"/filminfo/\d+"))
    if poster_link:
        href = poster_link.get("href", "")
        match = re.search(r"/filminfo/(\d+)", href)
        if match:
            movie["id"] = match.group(1)
            movie["filminfo_url"] = f"{BASE_URL}{href}"
            movie["tickets_url"] = f"{TICKETS_URL}/{movie['id']}"

        img = poster_link.find("img")
        if img:
            movie["poster_url"] = img.get("src", "")

    # --- Label (e.g. "Nieuw") ---
    label_el = block.find("span", class_="movie-label")
    if label_el:
        movie["label"] = label_el.get_text(strip=True)

    # --- Genre ---
    genre_el = block.find("span", class_="title")
    if genre_el:
        movie["genre"] = genre_el.get_text(strip=True)

    # --- Title, language, runtime from <h3> ---
    h3 = block.find("h3")
    if h3:
        # The title is the direct text of <h3>, excluding child spans
        # We need to extract text that is NOT inside a <span>
        title_parts = []
        for child in h3.children:
            if isinstance(child, str):
                text = child.strip()
                if text:
                    title_parts.append(text)
        movie["title"] = " ".join(title_parts).strip()

        # Language badge (OV / NV)
        lang_badge = h3.find("span", class_="badge")
        if lang_badge:
            movie["language"] = lang_badge.get_text(strip=True)

        # Runtime - look for a span that contains "min"
        for span in h3.find_all("span"):
            span_text = span.get_text(strip=True)
            if "min" in span_text.lower() and "badge" not in (span.get("class") or []):
                movie["runtime"] = span_text
                break

    # --- Description ---
    desc_el = block.find("p")
    if desc_el:
        movie["description"] = desc_el.get_text(strip=True)

    # --- Schedule ---
    week_block = block.find("div", class_="week-block")
    if week_block:
        movie["schedule"] = _parse_schedule_block(week_block)

    return movie


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

    details = {
        "director": "",
        "actors": "",
        "release_date": "",
        "trailer_url": "",
        "full_description": "",
    }

    # --- Movie info list ---
    info_list = soup.find("ul", class_="movie-info")
    if info_list:
        for li in info_list.find_all("li"):
            label_el = li.find("i")
            if not label_el:
                continue
            label = label_el.get_text(strip=True).lower()
            # The value is the text after the <i> element
            value = li.get_text(strip=True)
            # Remove the label prefix from the value
            value = value[len(label_el.get_text(strip=True)) :].strip()

            if "regie" in label:
                details["director"] = value
            elif "acteur" in label:
                details["actors"] = value
            elif "release" in label:
                details["release_date"] = value
            elif "duur" in label:
                pass  # We already have runtime from the programme page

    # --- Full description ---
    single_movie = soup.find("div", class_="single-movie")
    if single_movie:
        # Description is in a col-sm-7 div, as direct text (not in a tag)
        col7 = single_movie.find("div", class_="col-sm-7")
        if col7:
            inner_col7 = col7.find("div", class_="col-sm-7")
            target = inner_col7 if inner_col7 else col7
            # Collect text nodes that are direct children (the synopsis text)
            text_parts = []
            for child in target.children:
                if isinstance(child, str):
                    text = child.strip()
                    if text:
                        text_parts.append(text)
            if text_parts:
                details["full_description"] = " ".join(text_parts)

    # --- Trailer (YouTube) ---
    trailer_tile = soup.find("div", class_="trailer-tile")
    if trailer_tile:
        yt_id = trailer_tile.get("data-yt", "")
        if yt_id:
            details["trailer_url"] = f"https://www.youtube.com/watch?v={yt_id}"

    return details


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
    import textwrap

    prefix = " " * indent
    lines = textwrap.wrap(text, width=width - indent)
    if not lines:
        return ""
    # First line has no extra indent (caller already indents via _section)
    return ("\n" + prefix).join(lines)


def print_movie(movie, show_details=False):
    """
    Print a single movie with its schedule to the terminal.
    """
    # --- Title line ---
    title = movie["title"]
    lang = _lang_tag(movie.get("language", ""))
    runtime = f"  {_DIM}{movie['runtime']}{_RESET}" if movie.get("runtime") else ""
    label = _label_tag(movie.get("label", ""))

    print(f"{_BOLD}{_FG_BRIGHT_BLUE}{title}{_RESET}{lang}{runtime}{label}")

    # --- Genre ---
    if movie["genre"]:
        print(_section("Genre", f"{_ITALIC}{movie['genre']}{_RESET}"))

    # --- Description ---
    if movie["description"]:
        wrapped = _wrap_text(movie["description"])
        print(_section("Description", wrapped))

    # --- Links row (poster + more info on one line) ---
    links = []
    if movie.get("poster_url"):
        links.append(_hyperlink(movie["poster_url"], f"{_UNDERLINE}Poster{_RESET}"))
    if movie.get("filminfo_url"):
        links.append(
            _hyperlink(movie["filminfo_url"], f"{_UNDERLINE}More info{_RESET}")
        )
    if links:
        print(f"  {_DIM}Links:{_RESET} {f'{_DIM} | {_RESET}'.join(links)}")

    # --- Extra details (only when searching by title) ---
    if show_details and movie.get("details"):
        d = movie["details"]
        if d.get("director"):
            print(_section("Director", d["director"]))
        if d.get("actors"):
            print(_section("Actors", d["actors"]))
        if d.get("release_date"):
            print(_section("Release", d["release_date"]))
        if d.get("trailer_url"):
            print(
                _section(
                    "Trailer",
                    _hyperlink(d["trailer_url"], f"{_UNDERLINE}YouTube{_RESET}"),
                )
            )
        if d.get("full_description") and d["full_description"] != movie.get(
            "description", ""
        ):
            wrapped = _wrap_text(d["full_description"])
            print(_section("Synopsis", wrapped))

    # --- Schedule table ---
    if movie["schedule"]:
        print(f"  {_DIM}Schedule:{_RESET}")
        for entry in movie["schedule"]:
            day_str = entry["date_label"]
            for screening in entry["screenings"]:
                time_str = screening["time"]
                loc = screening["location"]
                zaal = screening["zaal"]
                ticket = screening["ticket_url"]

                # Time: bold + bright
                time_display = f"{_BOLD}{time_str}{_RESET}"

                # Location info: dimmed
                loc_parts = []
                if zaal:
                    loc_parts.append(zaal)
                if loc:
                    loc_parts.append(loc)
                loc_info = (
                    f"  {_DIM}{' - '.join(loc_parts)}{_RESET}" if loc_parts else ""
                )

                # Ticket link
                ticket_link = (
                    f"  {_hyperlink(ticket, f'{_FG_YELLOW}Tickets{_RESET}')}"
                    if ticket
                    else ""
                )

                print(
                    f"    {_FG_BRIGHT_BLACK}{day_str:<12}{_RESET}"
                    f"{time_display}{loc_info}{ticket_link}"
                )

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
