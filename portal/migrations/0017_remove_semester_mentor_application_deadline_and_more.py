# Generated by Django 4.1.4 on 2023-01-25 15:44

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0016_alter_project_options_alter_smallgroup_options_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="semester",
            name="mentor_application_deadline",
        ),
        migrations.AddField(
            model_name="semester",
            name="mentor_application_deadlines",
            field=models.DateTimeField(
                blank=True,
                help_text="The last date students can apply to be Mentors for this semester",
                null=True,
            ),
        ),
    ]
