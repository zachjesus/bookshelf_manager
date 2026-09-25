from django.conf import settings
from django.db import models


class Change(models.Model):
    ADD, REMOVE = 'add', 'remove'
    PENDING, ACCEPTED, PROCESSED = 'pending', 'accepted', 'processed'

    kind = models.CharField(max_length=8)
    shelf_pk = models.IntegerField()
    book_pk = models.IntegerField()
    status = models.CharField(max_length=16, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']


def is_reviewer(user):
    emails = [email.lower() for email in settings.BSM_REVIEWERS if email]
    return bool(user.is_authenticated and user.email and user.email.lower() in emails)
