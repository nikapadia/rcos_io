# Generated by Django 4.1.4 on 2023-01-10 03:30

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0002_alter_user_discord_user_id_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="projectpitch",
            name="url",
            field=models.URLField(
                help_text="Direct link to the pitch presentation (usually a Google Slides link)",
                verbose_name="Presentation URL",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="projectpitch",
            unique_together={("semester", "project")},
        ),
    ]
