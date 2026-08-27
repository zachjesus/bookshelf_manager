from django.core.management.base import BaseCommand

from bsm import changes as ch


class Command(BaseCommand):
    help = 'Open this ISO week if needed and move waiting items from older weeks.'

    def handle(self, *args, **opts):
        ch.reconcile_overlay()
        report, action = ch.roll_week()
        self.stdout.write('%s %s (%s)' % (report.week, report.status, action))
