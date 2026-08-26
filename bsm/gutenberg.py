import re
from contextlib import contextmanager

from django.conf import settings
from sqlalchemy import text

SHELF_SORTS = {
    'name': ('Name', 'b.bookshelf'),
    'id': ('ID', 'b.pk'),
    'books': ('Most books', 'books DESC, b.bookshelf'),
    'downloads': ('Most downloaded', 'b.downloads DESC, b.bookshelf'),
}

BOOK_SORTS = {
    'title': ('Title', 'filing'),
    'id': ('ID', 'b.pk'),
    'downloads': ('Most downloaded', 'b.downloads DESC'),
    'newest': ('Newest', 'b.release_date DESC, b.pk DESC'),
}

AUTHORS = """(SELECT string_agg(a.author, '; ' ORDER BY a.author)
       FROM authors a JOIN mn_books_authors mba ON mba.fk_authors = a.pk
      WHERE mba.fk_books = b.pk AND mba.fk_roles IN ('aut', 'cre')) AS authors"""

_objectbase = None


def _ob():
    global _objectbase
    if _objectbase is None:
        from libgutenberg.GutenbergDatabase import Objectbase
        _objectbase = Objectbase(False)
    return _objectbase


@contextmanager
def _session():
    session = _ob().get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _run(sql, **params):
    with _session() as session:
        return [dict(row) for row in session.execute(text(sql), params).mappings()]


def _write(sql, shelf_pk, book_pk):
    with _session() as session:
        return session.execute(text(sql), {'book': book_pk, 'shelf': shelf_pk}).rowcount


def _args(query, page):
    return {'ts': ' & '.join(w + ':*' for w in re.findall(r'\w+', query)) or None,
            'like': '%%%s%%' % query, 'pk': int(query) if query.isdigit() else -1,
            'limit': settings.PAGE_SIZE, 'offset': (page - 1) * settings.PAGE_SIZE}


def _titles(rows):
    for row in rows:
        lines = [part.strip() for part in (row.get('title') or '').split('\n') if part.strip()]
        row['title'] = lines[0] if lines else 'Untitled'
        row['subtitle'] = ' — '.join(lines[1:])
    return rows


def shelves(query='', sort='name', page=1):
    where = ("WHERE (b.bookshelf ILIKE :like OR b.pk = :pk"
             " OR b.tsvec @@ to_tsquery('english', :ts))") if query else ''
    rows = _run("""
        SELECT b.pk, b.bookshelf, b.downloads, count(mn.fk_books) AS books,
               count(*) OVER () AS total
          FROM bookshelves b
          LEFT JOIN mn_books_bookshelves mn ON mn.fk_bookshelves = b.pk
        %s GROUP BY b.pk, b.bookshelf, b.downloads
         ORDER BY %s LIMIT :limit OFFSET :offset
    """ % (where, SHELF_SORTS[sort][1]), **_args(query, page))
    return rows, rows[0]['total'] if rows else 0


def shelf(pk):
    rows = _run("""
        SELECT b.pk, b.bookshelf, b.downloads,
               (SELECT count(*) FROM mn_books_bookshelves mn
                 WHERE mn.fk_bookshelves = b.pk) AS books
          FROM bookshelves b WHERE b.pk = :pk""", pk=pk)
    return rows[0] if rows else None


def shelf_books(shelf_pk, query='', sort='title', page=1):
    where = ("AND (b.pk = :pk OR b.title ILIKE :like"
             " OR b.tsvec @@ to_tsquery('english', :ts))") if query else ''
    rows = _run("""
        SELECT b.pk, b.title, b.downloads, filing(b.title, b.nonfiling) AS filing,
               count(*) OVER () AS total, %s
          FROM books b JOIN mn_books_bookshelves mn ON mn.fk_books = b.pk
         WHERE mn.fk_bookshelves = :shelf %s
         ORDER BY %s LIMIT :limit OFFSET :offset
    """ % (AUTHORS, where, BOOK_SORTS[sort][1]), shelf=shelf_pk, **_args(query, page))
    return _titles(rows), rows[0]['total'] if rows else 0


def search_books(shelf_pk, query, sort='downloads', page=1):
    rows = _run("""
        SELECT b.pk, b.title, b.downloads, filing(b.title, b.nonfiling) AS filing,
               count(*) OVER () AS total, %s,
               EXISTS (SELECT 1 FROM mn_books_bookshelves mn
                        WHERE mn.fk_books = b.pk
                          AND mn.fk_bookshelves = :shelf) AS on_shelf
          FROM books b
         WHERE (b.pk = :pk OR b.tsvec @@ to_tsquery('english', :ts))
         ORDER BY %s LIMIT :limit OFFSET :offset
    """ % (AUTHORS, BOOK_SORTS[sort][1]), shelf=shelf_pk, **_args(query, page))
    return _titles(rows), rows[0]['total'] if rows else 0


def book_title(pk):
    rows = _titles(_run('SELECT b.pk, b.title FROM books b WHERE b.pk = :pk', pk=pk))
    return rows[0]['title'] if rows else None


def add_book(shelf_pk, book_pk):
    return _write("""INSERT INTO mn_books_bookshelves (fk_books, fk_bookshelves)
                     VALUES (:book, :shelf) ON CONFLICT DO NOTHING""", shelf_pk, book_pk)


def remove_book(shelf_pk, book_pk):
    return _write("""DELETE FROM mn_books_bookshelves
                      WHERE fk_books = :book AND fk_bookshelves = :shelf""", shelf_pk, book_pk)
