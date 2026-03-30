from django.views.generic import TemplateView
from django.shortcuts import redirect
from django.contrib import messages
from .models import ApiKeyStorage

class HomeView(TemplateView):
    """Design system verification page — The Obsidian Observatory."""
    template_name = 'dashboard/home.html'


class SetupView(TemplateView):
    """API Keys setup page."""
    template_name = 'dashboard/setup.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_keys = ApiKeyStorage.objects.values_list('provider', flat=True)
        main_providers = ['openai', 'gemini', 'anthropic']
        context['keys'] = {k: True for k in all_keys if k in main_providers}
        context['custom_keys'] = [k for k in all_keys if k not in main_providers]
        return context

    def post(self, request, *args, **kwargs):
        main_providers = ['openai', 'gemini', 'anthropic']
        updated_count = 0
        
        # Process Main Providers
        for provider in main_providers:
            value = request.POST.get(provider)
            if value and value.strip():
                ApiKeyStorage.objects.update_or_create(
                    provider=provider,
                    defaults={'api_key': value.strip()}
                )
                updated_count += 1
                
        # Process Custom Provider
        custom_provider = request.POST.get('custom_provider')
        custom_api_key = request.POST.get('custom_api_key')
        
        if custom_provider and custom_provider.strip() and custom_api_key and custom_api_key.strip():
            provider_name = custom_provider.strip().lower().replace(" ", "-")
            ApiKeyStorage.objects.update_or_create(
                provider=provider_name,
                defaults={'api_key': custom_api_key.strip()}
            )
            updated_count += 1
                
        if updated_count > 0:
            messages.success(request, f"{updated_count} clé(s) API sécurisée(s) avec succès.")
        else:
            messages.warning(request, "Aucune nouvelle clé n'a été soumise.")
            
        return redirect('dashboard:setup')

import json
from django.http import JsonResponse
from django.views import View
from orchestrator.safety import run_sanity_check
from .models import Session

class SessionCreateAPIView(View):
    """API view to create a new debate session."""
    
    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            title = data.get('title')
            topic = data.get('topic')
            token_budget = data.get('token_budget')
            
            if not all([title, topic, token_budget]):
                return JsonResponse({"success": False, "error": "Missing required fields."}, status=400)
                
            # Run pre-flight sanity check
            is_valid = run_sanity_check(topic)
            
            if not is_valid:
                return JsonResponse({"success": False, "error": "Sanity check failed: Invalid topic or connectivity issue."}, status=400)
                
            # Create session
            session = Session.objects.create(
                title=title,
                topic=topic,
                token_budget=token_budget,
                status='READY'
            )
            
            return JsonResponse({"success": True, "session_id": session.id}, status=201)
            
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)}, status=500)

