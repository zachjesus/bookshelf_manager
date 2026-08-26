import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from . import gutenberg as g


def _page(request, key='page'):
    try:
        return max(1, int(request.GET.get(key, 1)))
    except (TypeError, ValueError):
        return 1


def _pager(total, page):
    pages = max(1, -(-total // settings.PAGE_SIZE))
    return {'page': page, 'pages': pages, 'total': total, 'previous': page - 1,
            'next': page + 1, 'has_previous': page > 1, 'has_next': page < pages}


def _sort(request, choices, default, key='sort'):
    sort = request.GET.get(key, default)
    return sort if sort in choices else default


def _back(request, fallback):
    target = request.POST.get('next', '')
    return target if target and url_has_allowed_host_and_scheme(
        target, {request.get_host()}, request.is_secure()) else fallback


def _stale(request):
    return time.time() - request.session.get('reauth_at', 0) >= settings.REAUTH_SECONDS


def _reauthenticated(request, password):
    if not _stale(request):
        return True
    if password and request.user.check_password(password):
        request.session['reauth_at'] = time.time()
        return True
    return False


@login_required
@ratelimit(key='user', rate=settings.RATE_BROWSE, block=True)
def shelf_list(request):
    query, page = request.GET.get('q', '').strip(), _page(request)
    sort = _sort(request, g.SHELF_SORTS, 'name')
    rows, total = g.shelves(query, sort, page)
    return render(request, 'shelves.html', {
        'shelves': rows, 'q': query, 'sort': sort, 'sorts': g.SHELF_SORTS,
        'pager': _pager(total, page)})


@login_required
@ratelimit(key='user', rate=settings.RATE_BROWSE, block=True)
def shelf_detail(request, pk):
    shelf = g.shelf(pk)
    if shelf is None:
        raise Http404('No such bookshelf.')
    query, page = request.GET.get('q', '').strip(), _page(request)
    sort = _sort(request, g.BOOK_SORTS, 'title')
    books, total = g.shelf_books(pk, query, sort, page)
    add_query = request.GET.get('aq', '').strip()
    asort = _sort(request, g.BOOK_SORTS, 'downloads', 'asort')
    apage = _page(request, 'apage')
    candidates, atotal = g.search_books(pk, add_query, asort, apage) if add_query else ([], 0)
    return render(request, 'shelf.html', {
        'shelf': shelf, 'books': books, 'q': query, 'sort': sort,
        'sorts': g.BOOK_SORTS, 'pager': _pager(total, page), 'aq': add_query,
        'asort': asort, 'apage': apage, 'candidates': candidates,
        'apager': _pager(atotal, apage), 'reauth_needed': _stale(request)})


@login_required
@require_POST
@ratelimit(key='user', rate=settings.RATE_WRITE, block=True)
def modify(request, pk, action):
    fallback = reverse('shelf', args=[pk])
    if g.shelf(pk) is None:
        raise Http404('No such bookshelf.')
    try:
        book = int(request.POST.get('book', ''))
    except ValueError:
        messages.error(request, 'That is not a valid ebook number.')
        return redirect(_back(request, fallback))
    title = g.book_title(book)
    if title is None:
        messages.error(request, 'No ebook #%s exists in the catalog.' % book)
    elif action == 'add':
        if g.add_book(pk, book):
            messages.success(request, 'Added “%s” (#%s).' % (title, book))
        else:
            messages.info(request, '“%s” (#%s) is already on this shelf.' % (title, book))
    elif not _reauthenticated(request, request.POST.get('password', '')):
        messages.error(request, 'Password incorrect — nothing was removed.')
    elif g.remove_book(pk, book):
        messages.success(request, 'Removed “%s” (#%s).' % (title, book))
    else:
        messages.info(request, '“%s” (#%s) was not on this shelf.' % (title, book))
    return redirect(_back(request, fallback))
