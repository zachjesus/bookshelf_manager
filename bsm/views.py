import time
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from . import changes as ch
from . import gutenberg as g
from .models import DraftShelf, Report, can_view_reports, is_reviewer


def reports_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_view_reports(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return login_required(wrapped)


def _page(request, key='page'):
    try:
        return max(1, int(request.GET.get(key, 1)))
    except (TypeError, ValueError):
        return 1


def _pager(total, page):
    pages = max(1, -(-total // settings.PAGE_SIZE))
    page = min(max(1, page), pages)
    return {'page': page, 'pages': pages, 'total': total, 'previous': page - 1,
            'next': page + 1, 'has_previous': page > 1, 'has_next': page < pages}


def _paged(fetch, page):
    rows, total = fetch(page)
    pager = _pager(total, page)
    if pager['page'] != page:
        rows, total = fetch(pager['page'])
        pager = _pager(total, pager['page'])
    return rows, pager


def _sort(request, choices, default, key='sort'):
    sort = request.GET.get(key, default)
    return sort if sort in choices else default


def _back(request, fallback):
    target = request.POST.get('next', '')
    return target if target and url_has_allowed_host_and_scheme(
        target, {request.get_host()}, request.is_secure()) else fallback


def _stale(request):
    return time.time() - request.session.get('reauth_at', 0) >= settings.REAUTH_SECONDS


def _api(view):
    @csrf_exempt
    @ratelimit(key='ip', rate=settings.RATE_API, block=True)
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        given = request.headers.get('Authorization', '')
        given = given[7:] if given.startswith('Bearer ') else given
        if not settings.BSM_API_KEY or not constant_time_compare(given, settings.BSM_API_KEY):
            return JsonResponse({'error': 'unauthorized'}, status=401)
        hosts = settings.BSM_API_HOSTS
        if hosts and request.get_host().split(':')[0] not in hosts:
            return JsonResponse({'error': 'forbidden host'}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def _load(pk, draft):
    if draft:
        return get_object_or_404(DraftShelf, pk=pk, catalog_pk=None)
    shelf = g.shelf(pk)
    if shelf is None:
        raise Http404('No such bookshelf.')
    return shelf


def _shelf_page(request, shelf, is_draft=False):
    ch.reconcile_overlay()
    pk = shelf['pk']
    names = ('new_add_book', 'new_remove_book', 'new_shelf', 'new_rename') if is_draft \
        else ('add_book', 'remove_book', 'shelf', 'rename_shelf')
    add_url, remove_url, shelf_url, rename_url = (reverse(n, args=[pk]) for n in names)
    query, page = request.GET.get('q', '').strip(), _page(request)
    sort = _sort(request, g.BOOK_SORTS, 'title')
    adds, rems = ch.sets(ch.key(pk, is_draft))
    books, pager = _paged(
        lambda p: g.books_by_ids(query, sort, p, adds, rems, None if is_draft else pk), page)
    add_query = request.GET.get('aq', '').strip()
    asort = _sort(request, g.BOOK_SORTS, 'downloads', 'asort')
    apage = _page(request, 'apage')
    if add_query:
        candidates, apager = _paged(lambda p: g.search_books(add_query, asort, p), apage)
    else:
        candidates, apager = [], _pager(0, 1)
    on_prod = set() if is_draft else {book for book, _ in g.membership([pk])}
    for row in books:
        row['pending'] = (not is_draft) and row['pk'] in adds and row['pk'] not in on_prod
        row['removing'] = (not is_draft) and row['pk'] in rems
    for row in candidates:
        row['on_shelf'] = (row['pk'] in on_prod or row['pk'] in adds) and row['pk'] not in rems
    shelf = dict(shelf)
    shelf['books'] = len(adds) if is_draft else shelf['books'] + len(adds)
    return render(request, 'shelf.html', {
        'shelf': shelf, 'books': books, 'q': query, 'sort': sort, 'is_draft': is_draft,
        'sorts': g.BOOK_SORTS, 'pager': pager, 'aq': add_query,
        'asort': asort, 'apage': apager['page'], 'candidates': candidates,
        'apager': apager, 'reauth_needed': _stale(request),
        'add_url': add_url, 'remove_url': remove_url, 'shelf_url': shelf_url,
        'rename_url': rename_url,
    })


@login_required
@ratelimit(key='user', rate=settings.RATE_BROWSE, block=True)
def shelf_list(request):
    ch.reconcile_overlay()
    query, page = request.GET.get('q', '').strip(), _page(request)
    sort = _sort(request, g.SHELF_SORTS, 'name')
    rows, pager = _paged(lambda p: g.shelves(query, sort, p), page)
    names = ch.overlay_names()
    for row in rows:
        row['bookshelf'] = names.get(row['pk'], row['bookshelf'])
    drafts = [{'pk': draft.pk, 'bookshelf': draft.name,
               'books': len(ch.sets(ch.key(draft.pk, True))[0])}
              for draft in DraftShelf.objects.filter(catalog_pk=None).order_by('name')]
    return render(request, 'shelves.html', {
        'shelves': rows, 'drafts': drafts, 'q': query, 'sort': sort,
        'sorts': g.SHELF_SORTS, 'pager': pager,
    })


@login_required
@require_POST
@ratelimit(key='user', rate=settings.RATE_WRITE, block=True)
def create_shelf(request):
    name = request.POST.get('name', '').strip()
    if not name:
        messages.error(request, 'A bookshelf needs a name.')
    elif ch.name_busy(name):
        messages.error(request, 'A bookshelf with that name already exists.')
    else:
        draft = DraftShelf.objects.create(name=name)
        ch.sync_current()
        messages.success(request, 'Created “%s”.' % name)
        return redirect('new_shelf', pk=draft.pk)
    return redirect('shelves')


@login_required
@ratelimit(key='user', rate=settings.RATE_BROWSE, block=True)
def shelf_detail(request, pk):
    shelf = dict(_load(pk, False))
    queued = ch.overlay_names().get(pk)
    if queued:
        shelf['queued_from'], shelf['bookshelf'] = shelf['bookshelf'], queued
    return _shelf_page(request, shelf)


@login_required
@ratelimit(key='user', rate=settings.RATE_BROWSE, block=True)
def new_shelf_detail(request, pk):
    draft = _load(pk, True)
    return _shelf_page(
        request, {'pk': draft.pk, 'bookshelf': draft.name, 'downloads': 0, 'books': 0}, True)


@sensitive_post_parameters('password')
@login_required
@require_POST
@ratelimit(key='user', rate=settings.RATE_WRITE, block=True)
def modify(request, pk, action, draft=False):
    fallback = reverse('new_shelf' if draft else 'shelf', args=[pk])
    _load(pk, draft)
    try:
        book = int(request.POST.get('book', ''))
    except ValueError:
        messages.error(request, 'That is not a valid ebook number.')
        return redirect(_back(request, fallback))
    title = g.book_title(book)
    if title is None:
        messages.error(request, 'No ebook #%s exists in the catalog.' % book)
        return redirect(_back(request, fallback))
    on_prod = False if draft else g.catalog_has(pk, book)
    want = action == 'add'
    if not want and _stale(request):
        password = request.POST.get('password', '')
        if not password or not request.user.check_password(password):
            messages.error(request, "Password didn't match.")
            return redirect(_back(request, fallback))
        request.session['reauth_at'] = time.time()
    if ch.set_want(ch.key(pk, draft), book, want, on_prod):
        ch.sync_current()
        messages.success(request, '%s “%s” (#%s).' % (
            'Added' if want else 'Removed', title, book))
    else:
        messages.info(request, '“%s” (#%s) is %s on this shelf.' % (
            title, book, 'already' if want else 'not'))
    return redirect(_back(request, fallback))


@login_required
@require_POST
@ratelimit(key='user', rate=settings.RATE_WRITE, block=True)
def rename_shelf(request, pk, draft=False):
    fallback = reverse('new_shelf' if draft else 'shelf', args=[pk])
    obj = _load(pk, draft)
    name = request.POST.get('name', '').strip()
    shown = obj.name if draft else (ch.overlay_names().get(pk) or obj['bookshelf'])
    if not name:
        messages.error(request, 'A bookshelf needs a name.')
    elif name == shown:
        messages.info(request, 'That is already the name of this bookshelf.')
    elif ch.name_busy(name, None if draft else pk, pk if draft else None):
        messages.error(request, 'A bookshelf with that name already exists.')
    elif draft:
        DraftShelf.objects.filter(pk=pk).update(name=name)
        ch.sync_current()
        messages.success(request, 'Name is now “%s”.' % name)
    elif ch.set_rename(pk, name, obj['bookshelf']):
        ch.sync_current()
        messages.success(request, 'Renamed to “%s”.' % name)
    else:
        ch.sync_current()
        messages.success(request, 'Name is back to the catalog name.')
    return redirect(fallback)


@reports_required
def review_list(request):
    ch.sync_current()
    rows = [{'report': r, 'votes': ch.vote_state(r), 'span': ch.week_span(r.week),
             'counts': ch.counts(r.payload)} for r in Report.objects.all()]
    return render(request, 'reviews.html', {'rows': rows})


@reports_required
def review_detail(request, week):
    if week == ch.current_week():
        ch.sync_current()
    report = get_object_or_404(Report, week=week)
    can_vote = is_reviewer(request.user)
    if request.method == 'POST' and report.status != Report.APPLIED:
        if not can_vote:
            messages.error(request, 'Only reviewers can vote.')
            return redirect('review', week=week)
        item_id = request.POST.get('item', '')
        raw = request.POST.get('vote', '')
        if raw == 'clear':
            accept = None
        else:
            accept = raw == 'accept'
        ch.cast_vote(report, request.user, item_id, accept)
        messages.success(request, 'Cleared your vote.' if accept is None else 'Saved your vote.')
        return redirect('review', week=week)
    state = ch.vote_state(report)
    start, end = ch.week_span(report.week)
    rows = ch.review_items(report, request.user)
    kinds = (
        ('New bookshelves', 'create'),
        ('Renames', 'rename'),
        ('Add to shelves', 'add'),
        ('Remove from shelves', 'remove'),
    )

    def buckets(status):
        return [(title, [row for row in rows if row['kind'] == kind and row['status'] == status])
                for title, kind in kinds if any(row['kind'] == kind and row['status'] == status
                                                for row in rows)]

    return render(request, 'review.html', {
        'report': report, 'votes': state, 'start': start, 'end': end,
        'counts': ch.counts(report.payload), 'waiting': buckets('pending'),
        'accepted': buckets('accepted'), 'denied': buckets('denied'),
        'can_vote': can_vote,
    })


@_api
@require_GET
def api_reports(request):
    rows = []
    for report in Report.objects.all():
        state = ch.vote_state(report)
        rows.append({'week': report.week, 'status': report.status,
                     'created_at': report.created_at, 'applied_at': report.applied_at,
                     'accepted': state['accepted'], 'denied': state['denied'],
                     'pending': state['pending'], 'required': state['required']})
    return JsonResponse({'reports': rows})


@_api
@require_GET
def api_report(request, week):
    return JsonResponse(ch.report_json(get_object_or_404(Report, week=week)))


@_api
@require_GET
def api_vetted(request):
    report = Report.objects.filter(status=Report.VETTED).first()
    if report is None:
        return JsonResponse({'error': 'none'}, status=404)
    return JsonResponse(ch.report_json(report))


@_api
@require_POST
def api_applied(request, week):
    report = get_object_or_404(Report, week=week)
    if report.status != Report.VETTED:
        return JsonResponse({'error': 'not vetted'}, status=409)
    return JsonResponse(ch.report_json(ch.mark_applied(report)))
