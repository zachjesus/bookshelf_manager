#!/usr/bin/env python3
"""Apply accepted bookshelf changes once. Run this from cron."""

import json
import logging
import os
import sys
import time
import urllib.request

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
API = os.environ.get('BSM_API_URL', 'http://127.0.0.1:8000').rstrip('/')
KEY = os.environ.get('BSM_API_KEY', '')
LOG = os.environ.get('BSM_LOG', os.path.join(HERE, 'apply.log'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.FileHandler(LOG), logging.StreamHandler()],
)
log = logging.getLogger('apply')


def api(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            'Authorization': 'Bearer ' + KEY,
            'Content-Type': 'application/json',
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def on_shelf(conn, book, shelf):
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT 1 FROM mn_books_bookshelves
               WHERE fk_books = %s AND fk_bookshelves = %s""",
            (book, shelf),
        )
        return cursor.fetchone() is not None


def write_change(conn, kind, book, shelf):
    if kind == 'insert':
        sql = """INSERT INTO mn_books_bookshelves (fk_books, fk_bookshelves)
                 VALUES (%s, %s) ON CONFLICT DO NOTHING"""
    else:
        sql = """DELETE FROM mn_books_bookshelves
                 WHERE fk_books = %s AND fk_bookshelves = %s"""
    with conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (book, shelf))
    if kind == 'insert':
        return on_shelf(conn, book, shelf)
    return not on_shelf(conn, book, shelf)


def mark(ids):
    for attempt in range(3):
        try:
            result = api('POST', '/api/processed/', {'ids': ids})
            return set(result.get('processed') or [])
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1)


def main():
    if not KEY:
        log.error('Set BSM_API_KEY before running this script.')
        return 1
    try:
        member = api('GET', '/api/accepted/').get('mn_books_bookshelves') or {}
    except Exception:
        log.exception('Could not read accepted changes from the API.')
        return 1

    rows = [('insert', row) for row in member.get('insert') or []]
    rows += [('delete', row) for row in member.get('delete') or []]
    if not rows:
        log.info('No accepted changes are waiting.')
        return 0

    ready = []
    failed = False
    try:
        conn = psycopg2.connect(
            host=os.environ.get('PGHOST', '127.0.0.1'),
            port=os.environ.get('PGPORT', '5432'),
            dbname=os.environ.get('PGDATABASE', 'gutenberg'),
            user=os.environ.get('PGUSER', 'gutenberg'),
        )
    except Exception:
        log.exception('Could not connect to the catalog database.')
        return 1

    try:
        for kind, row in rows:
            change_id = str(row['id'])
            book, shelf = row['fk_books'], row['fk_bookshelves']
            verb = 'add' if kind == 'insert' else 'remove'
            prep = 'to' if kind == 'insert' else 'from'
            detail = (verb, book, prep, shelf, change_id)
            try:
                ok = write_change(conn, kind, book, shelf)
            except Exception:
                failed = True
                log.exception(
                    'Failed to %s book %s %s shelf %s, change %s.',
                    *detail,
                )
                continue
            if not ok:
                failed = True
                log.error(
                    'Failed to %s book %s %s shelf %s, change %s.',
                    *detail,
                )
                continue
            ready.append(int(change_id))
            done = 'Added' if kind == 'insert' else 'Removed'
            log.info(
                '%s book %s %s shelf %s, change %s.',
                done, book, prep, shelf, change_id,
            )
    finally:
        conn.close()

    if ready:
        try:
            marked = mark(ready)
        except Exception:
            log.exception('Could not mark the written changes processed.')
            return 1
        missed = [change_id for change_id in ready if change_id not in marked]
        if missed:
            log.error('These changes are still accepted. %s', missed)
            return 1
        log.info('Marked these changes processed. %s', sorted(marked))
    if failed:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
