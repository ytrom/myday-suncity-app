from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_entry_confirmation"),
    ]

    operations = [
        migrations.CreateModel(
            name="PushSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_id", models.IntegerField(db_index=True)),
                ("endpoint", models.URLField(max_length=1000, unique=True)),
                ("p256dh", models.CharField(max_length=200)),
                ("auth", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
