# Bookshelf Manager

Edit Project Gutenberg bookshelves without writing the catalog. Volunteers
change shelves in the UI. A weekly freeze turns those changes into a report.
Reviewers vote. Only unanimous accepts are applied.

The Gutenberg Postgres database is read-only. Auth, queued edits, and reports
live in SQLite.

## Installation

Needs a local Gutenberg catalog (Postgres, via
[libgutenberg](https://github.com/gutenbergtools/libgutenberg)). Python 3.12.

1. Clone the repo

   ```bash
   git clone https://github.com/zachjesus/bookshelf_manager.git
   cd bookshelf_manager
   ```

2. Create the virtualenv and install packages

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set at least:

   * `PG*` for the Gutenberg database
   * `BSM_REVIEWERS` (comma-separated emails; these users can vote)
   * `BSM_API_KEY` (for the apply API)
   * `BSM_SITE_URL` (used in digest emails)

   Load it:

   ```bash
   set -a && source .env && set +a
   ```

4. Apply the SQLite schema

   ```bash
   .venv/bin/python manage.py migrate
   ```

5. Create a user (reviewers need the same email as in `BSM_REVIEWERS`)

   ```bash
   .venv/bin/python manage.py createsuperuser
   ```

6. Start the development server

   ```bash
   .venv/bin/python manage.py runserver
   ```

   * http://127.0.0.1:8000/
   * http://127.0.0.1:8000/review/
   * http://127.0.0.1:8000/admin/

## How it works

Adds, removes, new shelves, and renames are stored locally and show in the UI
right away. They are not written to Gutenberg.

`digest` snapshots the current overlay into that ISO week's report. Changes made
after freeze wait for the next week. Denied items are dropped from the overlay
and do not come back. A change is accepted only if every reviewer votes accept.

Cron (or a reviewer) applies the accepted payload to Gutenberg, then tells this
app it was applied.

## Commands

```bash
.venv/bin/python manage.py digest
.venv/bin/python manage.py digest --send
.venv/bin/python manage.py digest --week 2026-W35
.venv/bin/python manage.py digest --refreeze
.venv/bin/python manage.py digest --refreeze --send
```

`--send` emails `BSM_REVIEWERS`. `--refreeze` remakes the current week's report
only (votes for that week are dropped). Already-applied weeks cannot be replaced.

Cron `digest --send` at the end of the ISO week (Sunday is safest). Freeze is
one-shot unless you `--refreeze`.

## API

`Authorization: Bearer <BSM_API_KEY>`

```
GET  /api/reports/                      list all weeks
GET  /api/reports/vetted/               the one ready to apply (404 if none)
GET  /api/reports/2026-W35/             that week (open, vetted, or applied)
POST /api/reports/2026-W35/applied/     mark that week applied
```

`GET vetted` when you do not know the week. `GET /api/reports/<week>/` when you do.
Both return the accepted payload. `POST .../applied/` after Gutenberg is updated.

```json
{
  "bookshelves": {
    "insert": [{"bookshelf": "Foo", "fk_books": [99, 74]}],
    "update": [{"pk": 82, "bookshelf": "Adventure Fixed"}]
  },
  "mn_books_bookshelves": {
    "insert": [{"fk_books": 74, "fk_bookshelves": 82}],
    "delete": [{"fk_books": 10551, "fk_bookshelves": 82}]
  }
}
```

New shelves have no `pk`. Insert the shelf, then its books. Renames use the existing `pk`.
