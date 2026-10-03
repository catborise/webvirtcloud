from rest_framework import serializers


class StatusSerializer(serializers.Serializer):
    """Response body of API actions that only report a status message."""

    status = serializers.CharField()
