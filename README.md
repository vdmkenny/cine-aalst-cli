# CineAalstCLI

CineAalstCLI is a command-line interface tool that allows users to view movie schedules and details for Cine Aalst cinema in Aalst.

It provides a simple way to check the movie schedules for today, tomorrow, or a specific date, search for movies by title, and get detailed information about a specific movie.

You can get a direct link to book tickets or get more information.

## Disclaimer

This tool is not affiliated with Cine Aalst cinema in any way. The tool scrapes data from the cinema's website in a respectful manner and does not introduce any harm to the website.

The tool is intended for personal use only, and the author does not take any responsibility for any misuse of the tool.

## Features

- View movie schedules for today, tomorrow, or a specific date.
- Search for movies by title.
- Get detailed information when searching by title (director, actors, release date, trailer link).
- Clickable terminal hyperlinks for tickets, posters and trailers.
- Color-coded output with language tags (OV/NV), labels (Nieuw, Verwacht, Laatste kans) and formatted schedules.

## Requirements

- Python 3.11 or newer

## Installation

```bash
pip install -r requirements.txt
```

## Usage

Options:

- `-d, --date [DATE]`: Specify the date for which to get movie schedules (format: YYYY-MM-DD, 'today', or 'tomorrow').
- `-m, --movie [TITLE]`: Search for a movie by title. When used, extra details (director, actors, trailer) are fetched.

These options can be combined.

## Examples

```bash
# Show all movies and schedules
python cine_aalst.py

# Show movie schedules for today
python cine_aalst.py -d today

# Show movie schedules for tomorrow
python cine_aalst.py -d tomorrow

# Show movie schedules for a specific date
python cine_aalst.py -d 2026-03-21

# Search for a movie by title (partial matches supported)
python cine_aalst.py -m "Mario"

# Search for a specific movie on a specific date
python cine_aalst.py -d today -m "Mario"
```
