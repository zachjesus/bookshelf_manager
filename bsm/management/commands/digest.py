from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from bsm import changes as ch


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true')
        parser.add_argument('--week')
        parser.add_argument('--refreeze', action='store_true')

    def handle(self, *args, **opts):
        report, action = ch.freeze_week(opts.get('week'), refreeze=opts['refreeze'])
        if action == 'wrong_week':
            raise CommandError('You can only refreeze the current week (%s).' % ch.current_week())
        if action == 'applied':
            raise CommandError('Week %s was already applied. Not replaced.' % (opts.get('week') or ch.current_week()))
        if action == 'missing':
            raise CommandError('No report for that week.')
        start, end = ch.week_span(report.week)
        url = urljoin(settings.BSM_SITE_URL.rstrip('/') + '/', reverse('review', args=[report.week]))
        listing = ch.present_text(report.payload)
        body = (
            'Week %s (%s to %s)\n\n%s\n\nPlease take a look:\n%s\n'
            % (report.week, start.isoformat(), end.isoformat(), listing, url)
        )
        self.stdout.write(body)
        if action == 'replaced':
            self.stdout.write('Replaced this week’s report. Earlier votes are gone.')
        if not opts['send']:
            return
        to = settings.BSM_REVIEWERS
        if not to:
            self.stderr.write('No reviewers are set.')
            return
        send_mail('Bookshelf report %s' % report.week, body, settings.DEFAULT_FROM_EMAIL, to)
        self.stdout.write('Sent to %s' % ', '.join(to))
