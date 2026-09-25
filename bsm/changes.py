from django.db import transaction
from django.utils import timezone

from . import gutenberg as g
from .models import Change, is_reviewer


def _open(shelf_pk=None):
    rows = Change.objects.filter(status__in=(Change.PENDING, Change.ACCEPTED))
    if shelf_pk is not None:
        rows = rows.filter(shelf_pk=shelf_pk)
    return rows


def sets(shelf_pk):
    adds, rems, accepted = set(), set(), set()
    for kind, book_pk, status in _open(shelf_pk).values_list('kind', 'book_pk', 'status'):
        (adds if kind == Change.ADD else rems).add(book_pk)
        if status == Change.ACCEPTED:
            accepted.add(book_pk)
    return adds, rems, accepted


def queue(shelf_pk, book_pk, want):
    """Queue an add or a remove. Returns queued, cleared, or same."""
    row = _open(shelf_pk).filter(book_pk=book_pk).first()
    kind = Change.ADD if want else Change.REMOVE
    if row and row.kind != kind:
        row.delete()
        return 'cleared'
    if row or want == g.on_shelf(shelf_pk, book_pk):
        return 'same'
    Change.objects.create(kind=kind, shelf_pk=shelf_pk, book_pk=book_pk)
    return 'queued'


def decide(change, user, vote):
    if not is_reviewer(user) or change.status == Change.PROCESSED:
        return
    if vote == 'approve' and change.status == Change.PENDING:
        change.status = Change.ACCEPTED
        change.save(update_fields=['status'])
    elif vote == 'unapprove' and change.status == Change.ACCEPTED:
        change.status = Change.PENDING
        change.save(update_fields=['status'])
    elif vote == 'drop':
        change.delete()


def rows():
    items = list(Change.objects.exclude(status=Change.PROCESSED))
    titles = g.titles(item.book_pk for item in items)
    names = g.shelf_names(item.shelf_pk for item in items)
    for item in items:
        verb = 'Add' if item.kind == Change.ADD else 'Remove'
        prep = 'to' if item.kind == Change.ADD else 'from'
        item.label = '%s #%s %s %s “%s”' % (
            verb, item.book_pk, titles.get(item.book_pk) or 'Untitled', prep,
            names.get(item.shelf_pk) or '#%s' % item.shelf_pk)
    return items


def accepted():
    inserts, deletes = [], []
    for row in Change.objects.filter(status=Change.ACCEPTED):
        item = {'id': row.id, 'fk_books': row.book_pk, 'fk_bookshelves': row.shelf_pk}
        (inserts if row.kind == Change.ADD else deletes).append(item)
    return {'mn_books_bookshelves': {'insert': inserts, 'delete': deletes}}


@transaction.atomic
def mark_processed(ids):
    """Mark accepted changes processed only once the catalog already has them."""
    done = []
    for row in Change.objects.filter(id__in=ids, status=Change.ACCEPTED):
        on = g.on_shelf(row.shelf_pk, row.book_pk)
        if (row.kind == Change.ADD) == bool(on):
            row.status = Change.PROCESSED
            row.processed_at = timezone.now()
            row.save(update_fields=['status', 'processed_at'])
            done.append(row.id)
    return done
