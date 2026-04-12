from django.core.exceptions import ValidationError
from django.db import models
from django_cryptography.fields import encrypt

from orchestrator.genetic import (
    ARCHETYPES,
    MIN_AGENTS,
    MAX_AGENTS,
    get_archetype_choices,
)


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
        ('CONFIGURING', 'Configuring'),
        ('RUNNING', 'Running'),
        ('ABORTED', 'Aborted'),
        ('SUCCESS', 'Success'),
    ]
    title = models.CharField(max_length=255)
    topic = models.TextField()
    token_budget = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='READY')
    abort_justification = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Session"
        verbose_name_plural = "Sessions"

    def __str__(self):
        return f"{self.title} ({self.status})"

    # ── Agent helpers ──────────────────────────────────────
    def agent_count(self) -> int:
        """Return the number of participant agents (excludes moderator)."""
        return self.agents.count()

    def validate_agent_count(self) -> None:
        """Raise ``ValidationError`` if the agent count is out of bounds."""
        count = self.agent_count()
        if count < MIN_AGENTS:
            raise ValidationError(
                f"A session requires at least {MIN_AGENTS} agents "
                f"(currently {count})."
            )
        if count > MAX_AGENTS:
            raise ValidationError(
                f"A session allows at most {MAX_AGENTS} agents "
                f"(currently {count})."
            )


class SessionAgent(models.Model):
    """Maps an AI provider to a Genetic Matrix archetype for a session."""

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="agents",
    )
    provider = models.CharField(
        max_length=50,
        help_text="AI provider key, e.g. 'openai', 'gemini', 'anthropic'",
    )
    archetype = models.CharField(
        max_length=30,
        choices=get_archetype_choices(),
        help_text="Genetic Matrix archetype key",
    )
    slot_number = models.PositiveSmallIntegerField(
        help_text="Position 1-4 in the roundtable",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Session Agent"
        verbose_name_plural = "Session Agents"
        ordering = ["slot_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "slot_number"],
                name="unique_slot_per_session",
            ),
            models.UniqueConstraint(
                fields=["session", "archetype"],
                name="unique_archetype_per_session",
            ),
        ]

    def clean(self):
        super().clean()
        # Validate archetype key exists
        if self.archetype not in ARCHETYPES:
            raise ValidationError(
                {"archetype": f"Unknown archetype '{self.archetype}'."}
            )
        # Validate slot range
        if self.slot_number is not None and not (1 <= self.slot_number <= MAX_AGENTS):
            raise ValidationError(
                {"slot_number": f"Slot must be between 1 and {MAX_AGENTS}."}
            )

    def __str__(self):
        label = ARCHETYPES.get(self.archetype, {}).get("label", self.archetype)
        return f"[{self.slot_number}] {self.provider} → {label}"
