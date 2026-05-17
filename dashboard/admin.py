from django.contrib import admin

from .models import ApiKeyStorage, Session, SessionAgent


@admin.register(ApiKeyStorage)
class ApiKeyStorageAdmin(admin.ModelAdmin):
    list_display = ("provider",)


class SessionAgentInline(admin.TabularInline):
    model = SessionAgent
    extra = 0
    fields = ("slot_number", "provider", "model_name", "archetype")


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "moderator_provider", "moderator_model", "created_at")
    list_filter = ("status", "moderator_provider")
    search_fields = ("title", "topic", "discussion_axes")
    inlines = [SessionAgentInline]
