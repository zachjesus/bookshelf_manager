from collections import defaultdict
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from . import gutenberg as g
from .models import DraftShelf, Intent, Report, Vote, is_reviewer, reviewer_emails


def key(pk, draft=False):
    return '%s:%s' % ('d' if draft else 'c', pk)


def current_week(day=None):
    year, week, _ = (day or date.today()).isocalendar()
    return '%s-W%02d' % (year, week)


def week_span(week):
    year, num = week.split('-W')
    start = date.fromisocalendar(int(year), int(num), 1)
    return start, start + timedelta(days=6)


def sets(shelf_key):
    adds, rems = set(), set()
    for book_id, want in Intent.objects.filter(shelf_key=shelf_key).values_list('book_id', 'want'):
        (adds if want else rems).add(book_id)
    return adds, rems


def overlay_names():
    return dict(DraftShelf.objects.exclude(catalog_pk=None).values_list('catalog_pk', 'name'))


def name_busy(name, exclude_catalog=None, exclude_draft=None):
    if g.name_taken(name, exclude_pk=exclude_catalog):
        return True
    rows = DraftShelf.objects.filter(name=name)
    if exclude_draft:
        rows = rows.exclude(pk=exclude_draft)
    if exclude_catalog:
        rows = rows.exclude(catalog_pk=exclude_catalog)
    return rows.exists()


def set_rename(pk, name, current):
    if name == current:
        DraftShelf.objects.filter(catalog_pk=pk).delete()
        return False
    DraftShelf.objects.update_or_create(catalog_pk=pk, defaults={'name': name})
    return True


def set_want(shelf_key, book_id, want, on_prod):
    if want == on_prod:
        Intent.objects.filter(shelf_key=shelf_key, book_id=book_id).delete()
        return False
    Intent.objects.update_or_create(shelf_key=shelf_key, book_id=book_id, defaults={'want': want})
    return True


def items():
    catalog, draft_books = defaultdict(list), defaultdict(list)
    for intent in Intent.objects.all():
        kind, _, pk = intent.shelf_key.partition(':')
        if kind == 'c':
            catalog[int(pk)].append(intent)
        elif kind == 'd' and intent.want:
            draft_books[int(pk)].append(intent.book_id)
    present = g.membership(list(catalog))
    out = []
    for draft in DraftShelf.objects.filter(catalog_pk=None):
        out.append({'id': 'create:d:%s' % draft.pk, 'kind': 'create',
                    'draft': draft.pk, 'bookshelf': draft.name})
        for book in sorted(draft_books[draft.pk]):
            out.append({'id': 'add:d:%s:%s' % (draft.pk, book), 'kind': 'add',
                        'draft': draft.pk, 'fk_books': book, 'bookshelf': draft.name})
    queued = list(DraftShelf.objects.exclude(catalog_pk=None).values_list('catalog_pk', 'name'))
    current = g.shelf_names(pk for pk, _ in queued)
    for pk, name in queued:
        if current.get(pk) and current[pk] != name:
            out.append({'id': 'rename:c:%s' % pk, 'kind': 'rename',
                        'pk': pk, 'bookshelf': name})
    for pk, intents in catalog.items():
        for intent in intents:
            pair = (intent.book_id, pk)
            if intent.want and pair not in present:
                out.append({'id': 'add:c:%s:%s' % (pk, intent.book_id), 'kind': 'add',
                            'fk_books': intent.book_id, 'fk_bookshelves': pk})
            elif not intent.want and pair in present:
                out.append({'id': 'remove:c:%s:%s' % (pk, intent.book_id), 'kind': 'remove',
                            'fk_books': intent.book_id, 'fk_bookshelves': pk})
    return out


def tables_from_items(item_list, accepted=None):
    allowed = None if accepted is None else set(accepted)
    kept = [item for item in item_list if allowed is None or item['id'] in allowed]
    created = {item['draft'] for item in kept if item['kind'] == 'create'}
    inserts, deletes, updates, shelves = [], [], [], {}
    for item in kept:
        kind = item['kind']
        if kind == 'create':
            shelves[item['draft']] = {'bookshelf': item['bookshelf'], 'fk_books': []}
        elif kind == 'add' and item.get('draft'):
            if item['draft'] not in created:
                continue
            shelves.setdefault(item['draft'], {'bookshelf': item['bookshelf'], 'fk_books': []})
            shelves[item['draft']]['fk_books'].append(item['fk_books'])
        elif kind == 'rename':
            updates.append({'pk': item['pk'], 'bookshelf': item['bookshelf']})
        elif kind == 'add':
            inserts.append({'fk_books': item['fk_books'], 'fk_bookshelves': item['fk_bookshelves']})
        elif kind == 'remove':
            deletes.append({'fk_books': item['fk_books'], 'fk_bookshelves': item['fk_bookshelves']})
    return {
        'bookshelves': {
            'insert': [{'bookshelf': row['bookshelf'], 'fk_books': sorted(row['fk_books'])}
                       for row in shelves.values()],
            'update': updates,
        },
        'mn_books_bookshelves': {'insert': inserts, 'delete': deletes},
    }


def payload():
    return tables_from_items(items())


@transaction.atomic
def freeze_week(week=None, refreeze=False):
    week = week or current_week()
    if refreeze and week != current_week():
        return None, 'wrong_week'
    existing = Report.objects.filter(week=week).first()
    if existing and not refreeze:
        return existing, 'kept'
    if existing and existing.status == Report.APPLIED:
        return existing, 'applied'
    if existing:
        existing.delete()
        action = 'replaced'
    else:
        action = 'created'
    item_list = items()
    data = tables_from_items(item_list)
    data['items'] = item_list
    report = Report.objects.create(week=week, payload=data, status=Report.OPEN)
    if vote_state(report)['complete']:
        report.status = Report.VETTED
        report.save(update_fields=['status'])
    return report, action


def _votes_by_item(report):
    grouped = defaultdict(dict)
    for vote in report.votes.select_related('user'):
        if vote.user.email:
            grouped[vote.item_id][vote.user.email.lower()] = vote.accept
    return grouped


def item_status(report, item_id, grouped=None):
    required = reviewer_emails()
    votes = (grouped or _votes_by_item(report)).get(item_id, {})
    people = [{'email': email, 'accept': votes.get(email)} for email in required]
    if any(person['accept'] is False for person in people):
        status = 'denied'
    elif required and all(person['accept'] is True for person in people):
        status = 'accepted'
    else:
        status = 'pending'
    return {'status': status, 'people': people,
            'accepted': status == 'accepted', 'denied': status == 'denied'}


def accepted_ids(report):
    grouped = _votes_by_item(report)
    return [item['id'] for item in report.payload.get('items') or []
            if item_status(report, item['id'], grouped)['accepted']]


def accepted_payload(report):
    item_list = report.payload.get('items')
    if item_list is None:
        data = {key: report.payload[key] for key in ('bookshelves', 'mn_books_bookshelves')
                if key in report.payload}
        return data
    return tables_from_items(item_list, accepted_ids(report))


def vote_state(report):
    required = reviewer_emails()
    item_list = report.payload.get('items') or []
    grouped = _votes_by_item(report)
    tallies = {'accepted': 0, 'denied': 0, 'pending': 0}
    for item in item_list:
        tallies[item_status(report, item['id'], grouped)['status']] += 1
    if not required:
        complete = False
    elif not item_list:
        complete = True
    else:
        complete = all(email in grouped.get(item['id'], {})
                       for item in item_list for email in required)
    return {'required': len(required), 'items': len(item_list), 'complete': complete, **tallies}


def _revert(item):
    if item['kind'] == 'create':
        Intent.objects.filter(shelf_key=key(item['draft'], True)).delete()
        DraftShelf.objects.filter(pk=item['draft'], catalog_pk=None).delete()
    elif item['kind'] == 'rename':
        DraftShelf.objects.filter(catalog_pk=item['pk']).delete()
    elif item['kind'] == 'add' and item.get('draft'):
        Intent.objects.filter(shelf_key=key(item['draft'], True), book_id=item['fk_books']).delete()
    elif item['kind'] in ('add', 'remove'):
        Intent.objects.filter(shelf_key=key(item['fk_bookshelves']),
                              book_id=item['fk_books']).delete()


def _restore(item):
    if item['kind'] == 'create':
        DraftShelf.objects.update_or_create(
            pk=item['draft'], defaults={'name': item['bookshelf'], 'catalog_pk': None})
    elif item['kind'] == 'rename':
        DraftShelf.objects.update_or_create(catalog_pk=item['pk'], defaults={'name': item['bookshelf']})
    elif item['kind'] == 'add' and item.get('draft'):
        Intent.objects.update_or_create(
            shelf_key=key(item['draft'], True), book_id=item['fk_books'], defaults={'want': True})
    elif item['kind'] == 'add':
        Intent.objects.update_or_create(
            shelf_key=key(item['fk_bookshelves']), book_id=item['fk_books'], defaults={'want': True})
    elif item['kind'] == 'remove':
        Intent.objects.update_or_create(
            shelf_key=key(item['fk_bookshelves']), book_id=item['fk_books'], defaults={'want': False})


def sync_overlay(report):
    item_list = report.payload.get('items') or []
    grouped = _votes_by_item(report)
    denied_creates = {item['draft'] for item in item_list
                      if item['kind'] == 'create'
                      and item_status(report, item['id'], grouped)['denied']}
    rank = {'create': 0, 'rename': 1, 'add': 2, 'remove': 2}
    for item in sorted(item_list, key=lambda row: rank[row['kind']]):
        denied = item_status(report, item['id'], grouped)['denied']
        if item.get('draft') and item['kind'] != 'create' and item['draft'] in denied_creates:
            _revert(item)
        elif denied:
            _revert(item)
        else:
            _restore(item)


@transaction.atomic
def cast_vote(report, user, item_id, accept):
    if report.status != Report.OPEN or not is_reviewer(user):
        return report
    ids = {item['id'] for item in report.payload.get('items') or []}
    if item_id not in ids:
        return report
    Vote.objects.update_or_create(
        report=report, user=user, item_id=item_id, defaults={'accept': accept})
    sync_overlay(report)
    if vote_state(report)['complete']:
        report.status = Report.VETTED
        report.save(update_fields=['status'])
        tables = accepted_payload(report)
        payload = dict(report.payload)
        payload.update(tables)
        report.payload = payload
        report.save(update_fields=['payload'])
        sync_overlay(report)
    return report


def mark_applied(report):
    report.status = Report.APPLIED
    report.applied_at = timezone.now()
    report.save(update_fields=['status', 'applied_at'])
    return report


def report_json(report):
    data = accepted_payload(report)
    data.update(week=report.week, status=report.status,
                created_at=report.created_at, applied_at=report.applied_at,
                votes=vote_state(report), items=report.payload.get('items') or [])
    return data


def counts(payload_data):
    item_list = payload_data.get('items')
    if item_list is not None:
        return {
            'creates': sum(item['kind'] == 'create' for item in item_list),
            'renames': sum(item['kind'] == 'rename' for item in item_list),
            'adds': sum(item['kind'] == 'add' for item in item_list),
            'removes': sum(item['kind'] == 'remove' for item in item_list),
        }
    shelves, member = payload_data.get('bookshelves', {}), payload_data.get('mn_books_bookshelves', {})
    return {'creates': len(shelves.get('insert', [])), 'renames': len(shelves.get('update', [])),
            'adds': len(member.get('insert', [])), 'removes': len(member.get('delete', []))}


def _labels(item_list):
    titles = g.book_titles(item['fk_books'] for item in item_list if 'fk_books' in item)
    names = g.shelf_names(
        [item['fk_bookshelves'] for item in item_list if 'fk_bookshelves' in item]
        + [item['pk'] for item in item_list if item['kind'] == 'rename'])
    shown = dict(names)
    shown.update((item['pk'], item['bookshelf']) for item in item_list if item['kind'] == 'rename')

    def title(pk):
        return titles.get(pk) or 'Untitled'

    labels = {}
    for item in item_list:
        kind = item['kind']
        if kind == 'create':
            labels[item['id']] = item['bookshelf']
        elif kind == 'rename':
            labels[item['id']] = '#%s “%s” → “%s”' % (
                item['pk'], names.get(item['pk'], '#%s' % item['pk']), item['bookshelf'])
        elif kind == 'add' and item.get('draft'):
            labels[item['id']] = '#%s %s → %s' % (
                item['fk_books'], title(item['fk_books']), item.get('bookshelf') or 'new shelf')
        else:
            shelf = shown.get(item.get('fk_bookshelves'), '#%s' % item.get('fk_bookshelves'))
            labels[item['id']] = '#%s %s → %s' % (item['fk_books'], title(item['fk_books']), shelf)
    return labels


def present(payload_data):
    item_list = payload_data.get('items')
    if item_list is None:
        item_list = []
        for row in payload_data.get('bookshelves', {}).get('insert', []):
            item_list.append({'id': 'create:%s' % row['bookshelf'], 'kind': 'create',
                              'bookshelf': row['bookshelf']})
            for book in row.get('fk_books') or []:
                item_list.append({'id': 'add:%s:%s' % (row['bookshelf'], book), 'kind': 'add',
                                  'fk_books': book, 'bookshelf': row['bookshelf'], 'draft': True})
        for row in payload_data.get('bookshelves', {}).get('update', []):
            item_list.append({'id': 'rename:c:%s' % row['pk'], 'kind': 'rename', **row})
        for row in payload_data.get('mn_books_bookshelves', {}).get('insert', []):
            item_list.append({'id': 'add:c:%s:%s' % (row['fk_bookshelves'], row['fk_books']),
                              'kind': 'add', **row})
        for row in payload_data.get('mn_books_bookshelves', {}).get('delete', []):
            item_list.append({'id': 'remove:c:%s:%s' % (row['fk_bookshelves'], row['fk_books']),
                              'kind': 'remove', **row})
    labels = _labels(item_list)
    groups = {'creates': [], 'renames': [], 'adds': [], 'removes': []}
    for item in item_list:
        groups[{'create': 'creates', 'rename': 'renames',
                'add': 'adds', 'remove': 'removes'}[item['kind']]].append(labels[item['id']])
    return groups


def present_text(payload_data):
    groups = present(payload_data)
    blocks = []
    for key, title in (('creates', 'New bookshelves'), ('renames', 'Renames'),
                       ('adds', 'Add to shelves'), ('removes', 'Remove from shelves')):
        if not groups[key]:
            continue
        blocks.append('%s:\n%s' % (title, '\n'.join('  - %s' % row for row in groups[key])))
    return '\n\n'.join(blocks) or 'No bookshelf changes this week.'


def review_items(report, user=None):
    item_list = report.payload.get('items') or []
    labels = _labels(item_list)
    grouped = _votes_by_item(report)
    email = (user.email or '').lower() if user and user.email else ''
    rows = []
    for item in item_list:
        state = item_status(report, item['id'], grouped)
        mine = None
        for person in state['people']:
            if person['email'] == email:
                mine = person['accept']
        rows.append({
            **item, 'label': labels[item['id']], 'status': state['status'],
            'people': state['people'], 'mine': mine,
        })
    return rows
