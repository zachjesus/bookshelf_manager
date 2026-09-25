# Bookshelf Manager

Queue adds and removes on existing Project Gutenberg bookshelves. The catalog
database is only read from here. A reviewer accepts a change. The processor
writes it, then marks it processed.

## Run locally

Python 3.12. Postgres catalog via `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`
(same as the other Gutenberg tools).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
BSM_REVIEWERS=you@example.org BSM_API_KEY=dev .venv/bin/python manage.py runserver
```

The reviewer account email must match `BSM_REVIEWERS`.

* http://127.0.0.1:8000/
* http://127.0.0.1:8000/review/

## Changes API

`Authorization: Bearer <BSM_API_KEY>`

A change moves `pending` → `accepted` → `processed`.

```
GET  /api/accepted/
POST /api/processed/     {"ids": [1, 2]}
```

`GET` lists accepted changes:

```json
{
  "mn_books_bookshelves": {
    "insert": [{"id": 1, "fk_books": 74, "fk_bookshelves": 82}],
    "delete": [{"id": 2, "fk_books": 10551, "fk_bookshelves": 82}]
  }
}
```

Write those rows into `mn_books_bookshelves`, then `POST` the ids. A change is
marked processed only when the catalog already matches.
