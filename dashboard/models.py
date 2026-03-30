from django.db import models
from django_cryptography.fields import encrypt

class ApiKeyStorage(models.Model):
    provider = models.CharField(max_length=50, unique=True, help_text="e.g., openai, anthropic, gemini")
    api_key = encrypt(models.CharField(max_length=255))

    class Meta:
        verbose_name = "API Key Storage"
        verbose_name_plural = "API Key Storages"

    @classmethod
    def get_key(cls, provider: str) -> str | None:
        try:
            record = cls.objects.get(provider=provider)
            return record.api_key
        except cls.DoesNotExist:
            return None

    def __str__(self):
        return f"API Key for {self.provider}"

class Session(models.Model):
    STATUS_CHOICES = [
        ('READY', 'Ready'),
        ('ABORTED', 'Aborted'),
        ('SUCCESS', 'Success'),
    ]
    title = models.CharField(max_length=255)
    topic = models.TextField()
    token_budget = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='READY')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Session"
        verbose_name_plural = "Sessions"

    def __str__(self):
        return f"{self.title} ({self.status})"
