from contextlib import contextmanager

from django.conf import settings
from sqlalchemy import and_, func, or_, select

# Same access as the other Gutenberg tools: libgutenberg session, models,
# and the catalog view the site already serves (v_appserver_books_4).

_base = None
_lib = None


def _models():
    global _base, _lib
    if _lib is None:
        from libgutenberg import Models
        from libgutenberg.GutenbergDatabase import Objectbase
        _base = Objectbase(False)
        _lib = Models
    return _lib


@contextmanager
def _session():
    _models()
    session = _base.get_session()
    try:
        yield session
    finally:
        session.close()


def _page(stmt, session, page):
    page = max(1, int(page or 1))
    size = settings.PAGE_SIZE
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = session.execute(stmt.limit(size).offset((page - 1) * size)).mappings().all()
    return [dict(row) for row in rows], total


def _titles(rows):
    for row in rows:
        lines = [part.strip() for part in (row.get('title') or '').split('\n') if part.strip()]
        row['title'] = lines[0] if lines else 'Untitled'
        row['subtitle'] = '; '.join(lines[1:])
        author = row.get('author') or []
        row['authors'] = '; '.join(author) if isinstance(author, (list, tuple)) else author
    return rows


def _book_query(query):
    cat = _models().t_v_appserver_books_4
    if str(query).isdigit():
        return cat, cat.c.pk == int(query)
    match = cat.c.tsvec.op('@@')(func.websearch_to_tsquery('english', query))
    return cat, match


def shelves(query='', page=1):
    m = _models()
    shelf, mem = m.Bookshelf, m.t_mn_books_bookshelves
    stmt = (
        select(shelf.id.label('pk'), shelf.bookshelf, shelf.downloads,
               func.count(mem.c.fk_books).label('books'))
        .outerjoin(mem, mem.c.fk_bookshelves == shelf.id)
        .group_by(shelf.id, shelf.bookshelf, shelf.downloads)
        .order_by(shelf.bookshelf))
    if query:
        if query.isdigit():
            cond = shelf.id == int(query)
        else:
            cond = shelf.tsvec.op('@@')(func.websearch_to_tsquery('english', query))
        stmt = stmt.where(cond)
    with _session() as session:
        return _page(stmt, session, page)


def shelf(pk):
    m = _models()
    shelf, mem = m.Bookshelf, m.t_mn_books_bookshelves
    stmt = (
        select(shelf.id.label('pk'), shelf.bookshelf, shelf.downloads,
               func.count(mem.c.fk_books).label('books'))
        .outerjoin(mem, mem.c.fk_bookshelves == shelf.id)
        .where(shelf.id == pk)
        .group_by(shelf.id, shelf.bookshelf, shelf.downloads))
    with _session() as session:
        row = session.execute(stmt).mappings().first()
    return dict(row) if row else None


def shelf_names(pks):
    pks = list(dict.fromkeys(pk for pk in pks if pk))
    if not pks:
        return {}
    shelf = _models().Bookshelf
    with _session() as session:
        rows = session.execute(select(shelf.id, shelf.bookshelf).where(shelf.id.in_(pks))).all()
    return {pk: name for pk, name in rows}


def members(shelf_pk, book_pks):
    book_pks = list(book_pks)
    if not book_pks:
        return set()
    mem = _models().t_mn_books_bookshelves
    with _session() as session:
        rows = session.execute(select(mem.c.fk_books).where(
            mem.c.fk_bookshelves == shelf_pk, mem.c.fk_books.in_(book_pks))).all()
    return {row[0] for row in rows}


def on_shelf(shelf_pk, book_pk):
    return book_pk in members(shelf_pk, [book_pk])


def titles(pks):
    pks = list(dict.fromkeys(pk for pk in pks if pk))
    if not pks:
        return {}
    cat = _models().t_v_appserver_books_4
    with _session() as session:
        rows = _titles([dict(row) for row in session.execute(
            select(cat.c.pk, cat.c.title).where(cat.c.pk.in_(pks))).mappings()])
    return {row['pk']: row['title'] for row in rows}


def book_title(pk):
    return titles([pk]).get(pk)


def shelf_books(shelf_pk, query='', page=1, also=()):
    cat, mem = _models().t_v_appserver_books_4, _models().t_mn_books_bookshelves
    cond = cat.c.pk.in_(select(mem.c.fk_books).where(mem.c.fk_bookshelves == shelf_pk))
    extra = [pk for pk in also if pk]
    if extra:
        cond = or_(cond, cat.c.pk.in_(extra))
    if query:
        _cat, match = _book_query(query)
        cond = and_(cond, match)
    stmt = (select(cat.c.pk, cat.c.title, cat.c.author, cat.c.downloads)
            .where(cond).order_by(cat.c.filing))
    with _session() as session:
        rows, total = _page(stmt, session, page)
    return _titles(rows), total


def search_books(query, page=1):
    if not query:
        return [], 0
    cat, match = _book_query(query)
    stmt = (select(cat.c.pk, cat.c.title, cat.c.author, cat.c.downloads)
            .where(match).order_by(cat.c.downloads.desc()))
    with _session() as session:
        rows, total = _page(stmt, session, page)
    return _titles(rows), total
