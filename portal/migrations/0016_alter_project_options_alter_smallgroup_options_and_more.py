# Generated by Django 4.1.4 on 2023-01-25 15:20

import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0015_remove_semester_is_accepting_mentors_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="project",
            options={
                "get_latest_by": "created_at",
                "ordering": [django.db.models.functions.text.Lower("name")],
            },
        ),
        migrations.AlterModelOptions(
            name="smallgroup",
            options={
                "ordering": [
                    "semester",
                    django.db.models.functions.text.Lower("name"),
                    "location",
                ]
            },
        ),
        migrations.AlterModelOptions(
            name="user",
            options={
                "ordering": [
                    django.db.models.functions.text.Lower("first_name"),
                    django.db.models.functions.text.Lower("last_name"),
                ]
            },
        ),
        migrations.AddField(
            model_name="enrollment",
            name="is_mentor",
            field=models.BooleanField(default=False, verbose_name="mentor?"),
        ),
    ]
