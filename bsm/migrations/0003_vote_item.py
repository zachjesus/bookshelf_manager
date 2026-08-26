from django.conf import settings
from django.db import migrations, models


def wipe_votes(apps, schema_editor):
    apps.get_model('bsm', 'Vote').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('bsm', '0002_rename'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(wipe_votes, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='vote',
            unique_together=set(),
        ),
        migrations.AddField(
            model_name='vote',
            name='accept',
            field=models.BooleanField(default=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='vote',
            name='item_id',
            field=models.CharField(default='', max_length=64),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='vote',
            name='agreed_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterUniqueTogether(
            name='vote',
            unique_together={('report', 'user', 'item_id')},
        ),
    ]
