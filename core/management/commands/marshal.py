"""Give or take away the Lane Marshal screen (report a lane to the mechanic) for one person.

    python manage.py marshal                          # who has it
    python manage.py marshal someone@example.com on
    python manage.py marshal someone@example.com off
"""
from .mechanic import Command as MechanicCommand


class Command(MechanicCommand):
    help = "Switch the Lane Marshal screen on or off per person."
    tool = "marshal"
    title = "Lane Marshal"
