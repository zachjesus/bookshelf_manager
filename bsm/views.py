import json
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
from django.views.decorators.http import require_GET, require_POST

from . import changes as ch
from . import gutenberg as g
from .models import Change, is_reviewer


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


def _back(request, fallback):
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(
            target, {request.get_host()}, request.is_secure()):
        return target
    return fallback


def _api(view):
    @csrf_exempt
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        given = request.headers.get('Authorization', '')
        given = given[7:] if given.startswith('Bearer ') else given
        if not settings.BSM_API_KEY or not constant_time_compare(given, settings.BSM_API_KEY):
            return JsonResponse({'error': 'unauthorized'}, status=401)
        return view(request, *args, **kwargs)
    return wrapped


def _shelf(pk):
    shelf = g.shelf(pk)
    if shelf is None:
        raise Http404('No such bookshelf.')
    return shelf


@login_required
def shelf_list(request):
    query = request.GET.get('q', '').strip()
    rows, pager = _paged(lambda p: g.shelves(query, p), _page(request))
    return render(request, 'shelves.html', {'shelves': rows, 'q': query, 'pager': pager})


@login_required
def shelf_detail(request, pk):
    shelf = _shelf(pk)
    adds, rems, accepted = ch.sets(pk)
    query = request.GET.get('q', '').strip()
    books, pager = _paged(lambda p: g.shelf_books(pk, query, p, adds | rems), _page(request))
    live = g.members(pk, [book['pk'] for book in books])
    for book in books:
        queued = book['pk'] in accepted
        if book['pk'] in adds and book['pk'] not in live:
            book['state'] = 'accepted' if queued else 'pending'
        elif book['pk'] in rems and book['pk'] in live:
            book['removing'] = True
            book['state'] = 'accepted' if queued else 'removing'
    aq = request.GET.get('aq', '').strip()
    if aq:
        candidates, apager = _paged(lambda p: g.search_books(aq, p), _page(request, 'apage'))
        live_c = g.members(pk, [book['pk'] for book in candidates])
        for book in candidates:
            book['on_shelf'] = (book['pk'] in live_c or book['pk'] in adds) and book['pk'] not in rems
    else:
        candidates, apager = [], _pager(0, 1)
    return render(request, 'shelf.html', {
        'shelf': shelf, 'books': books, 'q': query, 'pager': pager, 'aq': aq,
        'apage': apager['page'], 'candidates': candidates, 'apager': apager,
        'add_url': reverse('add_book', args=[pk]),
        'remove_url': reverse('remove_book', args=[pk]),
    })


@login_required
@require_POST
def modify(request, pk, action):
    _shelf(pk)
    fallback = reverse('shelf', args=[pk])
    try:
        book = int(request.POST.get('book', ''))
    except ValueError:
        messages.error(request, 'That is not a valid ebook number.')
        return redirect(_back(request, fallback))
    title = g.book_title(book)
    if title is None:
        messages.error(request, 'No ebook #%s exists in the catalog.' % book)
        return redirect(_back(request, fallback))
    want = action == 'add'
    result = ch.queue(pk, book, want)
    if result == 'queued':
        messages.success(request, '%s “%s” (#%s).' % (
            'Added' if want else 'Removed', title, book))
    elif result == 'cleared':
        messages.success(request, 'Taken off the queue.')
    else:
        messages.info(request, '“%s” (#%s) is %s on this shelf.' % (
            title, book, 'already' if want else 'not'))
    return redirect(_back(request, fallback))


@login_required
def review(request):
    if request.method == 'POST':
        if not is_reviewer(request.user):
            raise PermissionDenied
        change = get_object_or_404(
            Change, pk=request.POST.get('id'), status__in=(Change.PENDING, Change.ACCEPTED))
        ch.decide(change, request.user, request.POST.get('vote'))
        return redirect('review')
    return render(request, 'review.html', {
        'rows': ch.rows(), 'can_review': is_reviewer(request.user)})


@_api
@require_GET
def api_accepted(request):
    return JsonResponse(ch.accepted())


@_api
@require_POST
def api_processed(request):
    try:
        body = json.loads(request.body.decode() or '{}')
        ids = [int(item) for item in body.get('ids') or []]
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({'error': 'bad request'}, status=400)
    return JsonResponse({'processed': ch.mark_processed(ids)})
