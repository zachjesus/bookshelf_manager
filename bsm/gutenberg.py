import re
from contextlib import contextmanager

from django.conf import settings
from sqlalchemy import bindparam, text

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
    stmt = text(sql)
    expanding = [key for key, value in params.items()
                 if isinstance(value, (list, tuple, set)) and (':%s' % key) in sql]
    if expanding:
        stmt = stmt.bindparams(*[bindparam(key, expanding=True) for key in expanding])
        params = {key: (list(value) if key in expanding else value) for key, value in params.items()}
    with _session() as session:
        return [dict(row) for row in session.execute(stmt, params).mappings()]


def _args(query, page):
    return {'ts': ' & '.join(w + ':*' for w in re.findall(r'\w+', query)) or None,
            'like': '%%%s%%' % query, 'pk': int(query) if query.isdigit() else -1,
            'limit': settings.PAGE_SIZE, 'offset': (page - 1) * settings.PAGE_SIZE}


def _titles(rows):
    for row in rows:
        lines = [part.strip() for part in (row.get('title') or '').split('\n') if part.strip()]
        row['title'] = lines[0] if lines else 'Untitled'
        row['subtitle'] = '; '.join(lines[1:])
    return rows


def _search(query):
    if not query:
        return ''
    return ("AND (b.pk = :pk OR b.title ILIKE :like"
            " OR b.tsvec @@ to_tsquery('english', :ts))")


def _books(where, sort, params):
    rows = _run("""
        SELECT b.pk, b.title, b.downloads, filing(b.title, b.nonfiling) AS filing,
               count(*) OVER () AS total, %s
          FROM books b WHERE %s
         ORDER BY %s LIMIT :limit OFFSET :offset
    """ % (AUTHORS, where, BOOK_SORTS[sort][1]), **params)
    return _titles(rows), rows[0]['total'] if rows else 0


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


def shelf_names(pks):
    pks = list(dict.fromkeys(pks))
    if not pks:
        return {}
    rows = _run('SELECT pk, bookshelf FROM bookshelves WHERE pk IN :pks', pks=pks)
    return {row['pk']: row['bookshelf'] for row in rows}


def name_taken(name, exclude_pk=None):
    sql = 'SELECT 1 FROM bookshelves WHERE bookshelf = :name'
    params = {'name': name}
    if exclude_pk is not None:
        sql += ' AND pk <> :pk'
        params['pk'] = exclude_pk
    return bool(_run(sql + ' LIMIT 1', **params))


def shelf_pk_by_name(name):
    rows = _run('SELECT pk FROM bookshelves WHERE bookshelf = :name LIMIT 1', name=name)
    return rows[0]['pk'] if rows else None


def catalog_has(shelf_pk, book_pk):
    return bool(_run("""SELECT 1 FROM mn_books_bookshelves
                         WHERE fk_books = :book AND fk_bookshelves = :shelf LIMIT 1""",
                     book=book_pk, shelf=shelf_pk))


def membership(shelf_pks):
    if not shelf_pks:
        return set()
    rows = _run("""SELECT fk_books, fk_bookshelves FROM mn_books_bookshelves
                    WHERE fk_bookshelves IN :shelves""", shelves=list(shelf_pks))
    return {(row['fk_books'], row['fk_bookshelves']) for row in rows}


def book_titles(pks):
    pks = list(dict.fromkeys(pk for pk in pks if pk is not None))
    if not pks:
        return {}
    rows = _titles(_run('SELECT b.pk, b.title FROM books b WHERE b.pk IN :pks', pks=pks))
    return {row['pk']: row['title'] for row in rows}


def book_title(pk):
    return book_titles([pk]).get(pk)


def books_by_ids(query='', sort='title', page=1, adds=None, rems=None, catalog_pk=None):
    adds = list(adds or [])
    if catalog_pk is None and not adds:
        return [], 0
    params = dict(_args(query, page), adds=adds or [0])
    if catalog_pk is None:
        where = 'b.pk IN :adds'
    else:
        params['shelf'] = catalog_pk
        params['rems'] = list(rems or [0])
        where = ("EXISTS (SELECT 1 FROM mn_books_bookshelves mn"
                 " WHERE mn.fk_books = b.pk AND mn.fk_bookshelves = :shelf)"
                 " OR b.pk IN :adds OR b.pk IN :rems")
    return _books('%s %s' % (where, _search(query)), sort, params)


def search_books(query, sort='downloads', page=1):
    return _books("b.pk = :pk OR b.tsvec @@ to_tsquery('english', :ts)",
                  sort, _args(query, page))
