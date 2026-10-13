"""Long-running operations: the queue, the history and the resources jobs
hold (spec 2026-10-13, sections 1-2)."""

import uuid

from django.conf import settings
from django.db import models


class Operation(models.Model):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"
    ACTIVE = (QUEUED, RUNNING, CANCELLING)
    TERMINAL = (SUCCEEDED, FAILED, CANCELLED, UNCERTAIN)
    STATES = [(s, s) for s in ACTIVE + TERMINAL]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=64, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="operations")
    username = models.CharField(max_length=150)  # kept when the user is deleted
    submission_key = models.CharField(max_length=64)
    resource = models.CharField(max_length=255, db_index=True)  # the page that shows the job
    resource_label = models.CharField(max_length=255)
    state = models.CharField(max_length=16, choices=STATES, default=QUEUED, db_index=True)
    params = models.JSONField(default=dict)
    intent = models.JSONField(default=dict)  # plan facts, then what run is about to change
    # set with the intent committed right before the first libvirt effect:
    # recovery fails a job without it, it never reached libvirt
    effects_started = models.BooleanField(default=False)
    phase = models.CharField(max_length=255, blank=True)
    progress_done = models.BigIntegerField(null=True)
    progress_total = models.BigIntegerField(null=True)
    result = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    heartbeat_at = models.DateTimeField(null=True)
    worker = models.CharField(max_length=128, blank=True)  # identity of the worker that owns it
    time_limit = models.PositiveIntegerField(null=True)  # seconds, fixed at admission
    cancel_requested = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    resolved_at = models.DateTimeField(null=True)
    resolution = models.CharField(max_length=16, blank=True)
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "submission_key"], name="unique_submission")]

    def __str__(self):
        return f"{self.kind} {self.id}"


class Claim(models.Model):
    SHARED = "shared"
    EXCLUSIVE = "exclusive"

    operation = models.ForeignKey(Operation, null=True, on_delete=models.CASCADE, related_name="claims")
    resource = models.CharField(max_length=255, db_index=True)
    mode = models.CharField(max_length=9, choices=[(SHARED, SHARED), (EXCLUSIVE, EXCLUSIVE)])
    holder = models.CharField(max_length=128, blank=True)  # transient: host:pid:thread
    expires_at = models.DateTimeField(null=True)  # transient only
    created_at = models.DateTimeField(auto_now_add=True)


class WorkerLease(models.Model):
    name = models.CharField(primary_key=True, max_length=16, default="worker")
    holder = models.CharField(max_length=128)
    host = models.CharField(max_length=255)
    heartbeat_at = models.DateTimeField()
