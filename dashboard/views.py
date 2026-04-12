import io
import json

from asgiref.sync import sync_to_async
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponseForbidden, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from orchestrator.genetic import (
    ARCHETYPES,
    MODERATOR,
    MIN_AGENTS,
    MAX_AGENTS,
    get_archetype_choices,
)
from orchestrator.safety import run_sanity_check, set_abort_event
from orchestrator.engine import run_debate_engine
from orchestrator.exporter import generate_pdf_report
from .models import ApiKeyStorage, Session, SessionAgent


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
        delete_provider = request.POST.get('delete_provider')
        if delete_provider:
            ApiKeyStorage.objects.filter(provider=delete_provider).delete()
            messages.success(request, f"✓ Provider {delete_provider.upper()} supprimé.")
            return redirect('setup')

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


class AbortSessionAPIView(View):
    """API view to securely abort a debate session."""
    
    def post(self, request, session_id, *args, **kwargs):
        try:
            data = json.loads(request.body)
            justification = data.get('justification')
            
            if not justification or not justification.strip():
                return JsonResponse({"success": False, "error": "L'explication de l'arrêt est obligatoire."}, status=400)
                
            session = get_object_or_404(Session, pk=session_id)
            
            # Allow aborting even if already aborting to be idempotent
            if session.status in ['ABORTED', 'SUCCESS']:
                return JsonResponse({"success": False, "error": f"Session is already {session.status}."}, status=400)
                
            session.status = 'ABORTED'
            session.abort_justification = justification.strip()
            session.save(update_fields=['status', 'abort_justification', 'updated_at'])
            
            # Immediately set the abort event in safety to break the async generator
            set_abort_event(session.id)
            
            return JsonResponse({"success": True}, status=200)
            
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)}, status=500)


class RoundtableView(TemplateView):
    """Roundtable agent configuration page."""
    template_name = 'dashboard/roundtable.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session_id = self.kwargs.get('session_id')
        session = get_object_or_404(Session, pk=session_id)
        context['session'] = session
        context['archetypes'] = ARCHETYPES
        context['moderator'] = MODERATOR
        context['min_agents'] = MIN_AGENTS
        context['max_agents'] = MAX_AGENTS
        # Available providers = configured API keys
        context['providers'] = list(
            ApiKeyStorage.objects.values_list('provider', flat=True)
        )
        # Existing agents for this session
        context['existing_agents'] = list(
            session.agents.values('slot_number', 'provider', 'archetype')
        )
        return context


class RoundtableConfigAPIView(View):
    """API endpoint to configure agents for a session's roundtable.

    Expects JSON payload::

        {
            "session_id": 1,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
                ...
            ]
        }

    Constraints enforced:
    - Between 2 and 4 agents (inclusive).
    - Each archetype may appear at most once per session.
    - Each provider must have a registered API key.
    - The Moderator Architect is always implicitly included (not stored
      as a SessionAgent — it is a protocol-level constant).
    """

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse(
                {"success": False, "error": "Invalid JSON."},
                status=400,
            )

        session_id = data.get("session_id")
        agents_data = data.get("agents", [])

        # ── Basic validation ──────────────────────────────
        if not session_id:
            return JsonResponse(
                {"success": False, "error": "Missing session_id."},
                status=400,
            )

        if not isinstance(agents_data, list):
            return JsonResponse(
                {"success": False, "error": "'agents' must be an array."},
                status=400,
            )

        agent_count = len(agents_data)
        if agent_count < MIN_AGENTS or agent_count > MAX_AGENTS:
            return JsonResponse(
                {
                    "success": False,
                    "error": (
                        f"You must configure between {MIN_AGENTS} and "
                        f"{MAX_AGENTS} agents (received {agent_count})."
                    ),
                },
                status=400,
            )

        # ── Session lookup ────────────────────────────────
        try:
            session = Session.objects.get(pk=session_id)
        except Session.DoesNotExist:
            return JsonResponse(
                {"success": False, "error": f"Session {session_id} not found."},
                status=404,
            )

        # ── Per-agent validation ──────────────────────────
        valid_archetype_keys = set(ARCHETYPES.keys())
        configured_providers = set(
            ApiKeyStorage.objects.values_list("provider", flat=True)
        )
        seen_archetypes: set[str] = set()
        validated_agents: list[dict] = []

        for idx, agent in enumerate(agents_data, start=1):
            provider = agent.get("provider", "").strip().lower()
            archetype = agent.get("archetype", "").strip().lower()

            if not provider or not archetype:
                return JsonResponse(
                    {
                        "success": False,
                        "error": f"Agent #{idx}: provider and archetype are required.",
                    },
                    status=400,
                )

            if archetype not in valid_archetype_keys:
                return JsonResponse(
                    {
                        "success": False,
                        "error": (
                            f"Agent #{idx}: unknown archetype '{archetype}'. "
                            f"Valid: {', '.join(sorted(valid_archetype_keys))}."
                        ),
                    },
                    status=400,
                )

            if archetype in seen_archetypes:
                return JsonResponse(
                    {
                        "success": False,
                        "error": (
                            f"Agent #{idx}: archetype '{archetype}' is already "
                            f"assigned to another agent."
                        ),
                    },
                    status=400,
                )

            if provider not in configured_providers:
                return JsonResponse(
                    {
                        "success": False,
                        "error": (
                            f"Agent #{idx}: no API key configured for "
                            f"provider '{provider}'."
                        ),
                    },
                    status=400,
                )

            seen_archetypes.add(archetype)
            validated_agents.append(
                {
                    "provider": provider,
                    "archetype": archetype,
                    "slot_number": idx,
                }
            )

        # ── Persist ───────────────────────────────────────
        # Clear previous configuration for this session
        session.agents.all().delete()

        created_agents = SessionAgent.objects.bulk_create(
            [
                SessionAgent(session=session, **agent_data)
                for agent_data in validated_agents
            ]
        )

        # Update session status
        session.status = "CONFIGURING"
        session.save(update_fields=["status", "updated_at"])

        # ── Response ──────────────────────────────────────
        return JsonResponse(
            {
                "success": True,
                "session_id": session.id,
                "agents_configured": len(created_agents),
                "moderator_included": True,
                "moderator_label": MODERATOR["label"],
                "agents": [
                    {
                        "slot": a.slot_number,
                        "provider": a.provider,
                        "archetype": a.archetype,
                        "archetype_label": ARCHETYPES[a.archetype]["label"],
                    }
                    for a in created_agents
                ],
            },
            status=201,
        )


async def cockpit_view(request, session_id):
    """
    Renders the cockpit shell template for session_id.
    GET /session/<session_id>/cockpit/
    """
    try:
        session = await sync_to_async(Session.objects.get)(id=session_id)
    except Session.DoesNotExist:
        raise Http404(f"Session {session_id} not found.")

    agents_qs = await sync_to_async(list)(session.agents.all())
    
    stream_url = reverse("dashboard:stream-debate", args=[session_id])
    
    context = {
        "session": session,
        "agents": agents_qs,
        "stream_url": stream_url,
        "ARCHETYPES": ARCHETYPES,
    }
    return render(request, "dashboard/cockpit.html", context)


async def stream_debate(request, session_id):
    """
    Async SSE streaming view.
    GET /session/<session_id>/stream/
    """
    try:
        session = await sync_to_async(Session.objects.get)(id=session_id)
    except Session.DoesNotExist:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'agent_id': 'system', 'content': 'Session not found.'})}\n\n"
        return StreamingHttpResponse(error_stream(), content_type="text/event-stream")

    agents_qs = await sync_to_async(list)(session.agents.all())
    if not agents_qs:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'agent_id': 'system', 'content': 'No agents configured for this session.'})}\n\n"
        return StreamingHttpResponse(error_stream(), content_type="text/event-stream")

    agents = [
        {
            "provider": a.provider,
            "archetype": a.archetype,
            "slot_number": a.slot_number,
        }
        for a in agents_qs
    ]

    session.status = "RUNNING"
    await sync_to_async(session.save)(update_fields=["status", "updated_at"])

    async def event_stream():
        try:
            async for event in run_debate_engine(agents, session.topic, confrontation_rounds=2, session_id=session.id):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'agent_id': 'system', 'content': f'Engine exception: {str(e)}'})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


async def download_pdf_report(request, session_id):
    """
    Generate and serve a forensic PDF report for a completed debate session.

    GET /session/<session_id>/report/pdf/

    Access Rules
    ------------
    - HTTP 404 if the session does not exist.
    - HTTP 403 if the session has not yet reached a terminal state
      (only SUCCESS and ABORTED sessions are reportable).
    - HTTP 200 with application/pdf and Content-Disposition: attachment
      otherwise.

    Architecture
    ------------
    ``generate_pdf_report`` is CPU/I/O bound (ReportLab rendering).
    It is wrapped in ``sync_to_async`` to keep the ASGI event loop free.
    """
    try:
        session = await sync_to_async(Session.objects.get)(id=session_id)
    except Session.DoesNotExist:
        raise Http404(f"Session {session_id} not found.")

    # Only terminal sessions can be exported
    if session.status not in ("SUCCESS", "ABORTED"):
        return HttpResponseForbidden(
            "Ce rapport n'est disponible que pour les sessions terminées "
            "(statut SUCCESS ou ABORTED)."
        )

    # Generate PDF asynchronously (sync_to_async isolates blocking I/O)
    _generate_async = sync_to_async(generate_pdf_report)
    pdf_bytes = await _generate_async(session)

    # Build a safe filename
    safe_title = "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in session.title
    )[:50]
    filename = f"MAS-D_rapport_{session.id}_{safe_title}.pdf"

    response = FileResponse(
        io.BytesIO(pdf_bytes),
        content_type="application/pdf",
        as_attachment=True,
        filename=filename,
    )
    return response
