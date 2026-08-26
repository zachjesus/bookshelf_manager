from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bsm', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftshelf',
            name='catalog_pk',
            field=models.IntegerField(blank=True, null=True, unique=True),
        ),
    ]
