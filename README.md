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

Edits update this week’s report right away. Waiting items roll into the next
ISO week (America/New_York); accepted and denied stay on the old report. Denied
items are dropped from the overlay. A change is accepted only if every reviewer
votes accept.

Cron writes Gutenberg first, then `POST .../applied/`. Overlay for accepted
items is dropped only after the catalog already has them.

## Commands

```bash
.venv/bin/python manage.py sync
.venv/bin/python manage.py digest
.venv/bin/python manage.py digest --send
.venv/bin/python manage.py digest --refreeze
.venv/bin/python manage.py crontab add
.venv/bin/python manage.py crontab show
```

`sync` (Monday morning) opens the new ISO week and moves waiting items.
Shelf edits update this week’s report themselves. Overlay cleanup also runs
after apply and when you browse shelves. `digest --send` emails reviewers.
`--refreeze` remakes the current week only (votes for that week are dropped).

After install, run `crontab add` once. That installs:

* Monday 12:05am Eastern `sync` (week rollover)
* Sunday 6pm Eastern `digest --send`

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
