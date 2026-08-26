from django.conf import settings
from django.contrib.auth.models import User
from django.db import models


class DraftShelf(models.Model):
    name = models.TextField(unique=True)
    catalog_pk = models.IntegerField(null=True, blank=True, unique=True)


class Intent(models.Model):
    shelf_key = models.CharField(max_length=32)
    book_id = models.IntegerField()
    want = models.BooleanField()

    class Meta:
        unique_together = ('shelf_key', 'book_id')


class Report(models.Model):
    OPEN, VETTED, APPLIED = 'open', 'vetted', 'applied'
    week = models.CharField(max_length=8, unique=True)
    payload = models.JSONField()
    status = models.CharField(max_length=16, default=OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-week']


class Vote(models.Model):
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    item_id = models.CharField(max_length=64)
    accept = models.BooleanField()
    agreed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('report', 'user', 'item_id')


def reviewer_emails():
    return [email.lower() for email in settings.BSM_REVIEWERS if email]


def is_reviewer(user):
    return bool(user.is_authenticated and user.email and user.email.lower() in reviewer_emails())


def can_view_reports(user):
    return is_reviewer(user) or bool(user.is_authenticated and user.is_superuser)


def reviewer_nav(request):
    return {'is_reviewer': is_reviewer(request.user),
            'can_view_reports': can_view_reports(request.user)}
