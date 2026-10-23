from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("unlock", views.unlock, name="unlock"),
    path("lock", views.lock, name="lock"),
    path("myday", views.myday, name="myday"),
    path("roster", views.roster, name="roster"),
    path("rdo", views.rdo, name="rdo"),
    path("messages", views.messages_tab, name="messages"),
    path("tabs", views.tabs, name="tabs"),
    path("shift-card", views.shift_card, name="shift_card"),
    path("clock/<str:action>/start", views.clock_start, name="clock_start"),
    path("clock/<str:action>", views.clock_submit, name="clock_submit"),
    path("clock/<str:action>/offsite", views.clock_offsite, name="clock_offsite"),
    path("tasks/<int:task_id>/toggle", views.task_toggle, name="task_toggle"),
    path("roster/<int:assignment_id>/confirm", views.shift_confirm, name="shift_confirm"),
    path("messages/<int:note_id>/ack", views.message_ack, name="message_ack"),
    path("healthz", views.healthz, name="healthz"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="sw"),
]
