from django.test import TestCase, Client
from django.conf import settings


class InfrastructureTests(TestCase):
    """AC 1, 2, 3, 8 — Infrastructure setup verification."""

    def test_dashboard_app_installed(self):
        """Dashboard app is registered in INSTALLED_APPS."""
        self.assertIn('dashboard', settings.INSTALLED_APPS)

    def test_tailwind_app_configured(self):
        """Tailwind app name is set to 'theme'."""
        self.assertEqual(settings.TAILWIND_APP_NAME, 'theme')

    def test_theme_app_installed(self):
        """Theme app is registered in INSTALLED_APPS."""
        self.assertIn('theme', settings.INSTALLED_APPS)

    def test_asgi_application_importable(self):
        """ASGI application can be imported for async support."""
        from discussion_ia.asgi import application
        self.assertIsNotNone(application)


class HomePageTests(TestCase):
    """AC 7 — Verification workspace page."""

    def setUp(self):
        self.client = Client()

    def test_home_page_status_200(self):
        """GET / returns HTTP 200."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_home_page_uses_correct_template(self):
        """Home page renders dashboard/home.html."""
        response = self.client.get('/')
        self.assertTemplateUsed(response, 'dashboard/home.html')

    def test_home_page_uses_base_template(self):
        """Home page extends dashboard/base.html."""
        response = self.client.get('/')
        self.assertTemplateUsed(response, 'dashboard/base.html')

    def test_home_page_contains_project_title(self):
        """Home page displays MAS-D title."""
        response = self.client.get('/')
        self.assertContains(response, 'MAS-D')

    def test_home_page_contains_design_tokens(self):
        """Home page showcases color palette section."""
        response = self.client.get('/')
        self.assertContains(response, 'Color Palette')
        self.assertContains(response, 'Typography')
        self.assertContains(response, '#0b0e14')


class ApiKeyStorageTests(TestCase):
    """AC 3, 5 - Database Model & Encryption"""

    def test_create_and_retrieve_key(self):
        from dashboard.models import ApiKeyStorage
        ApiKeyStorage.objects.create(provider="openai", api_key="sk-1234")
        key = ApiKeyStorage.get_key("openai")
        self.assertEqual(key, "sk-1234")

    def test_get_key_returns_none_if_not_found(self):
        from dashboard.models import ApiKeyStorage
        self.assertIsNone(ApiKeyStorage.get_key("gemini"))

    def test_api_key_is_encrypted_in_db(self):
        from dashboard.models import ApiKeyStorage
        ApiKeyStorage.objects.create(provider="anthropic", api_key="sk-anth-123")
        # Check raw database value
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT api_key FROM dashboard_apikeystorage WHERE provider='anthropic'")
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertNotEqual(row[0], "sk-anth-123")
            self.assertNotIn("sk-anth-123", str(row[0]))


class SetupViewTests(TestCase):
    """AC 1, 2, 4 - Setup View UI and Integration"""

    def setUp(self):
        self.client = Client()

    def test_setup_page_status_200(self):
        response = self.client.get('/setup/')
        self.assertEqual(response.status_code, 200)

    def test_setup_page_uses_correct_template(self):
        response = self.client.get('/setup/')
        self.assertTemplateUsed(response, 'dashboard/setup.html')
        self.assertTemplateUsed(response, 'dashboard/base.html')

    def test_setup_page_contains_form(self):
        response = self.client.get('/setup/')
        self.assertContains(response, '<form')
        self.assertContains(response, 'openai')
        self.assertContains(response, 'gemini')
        self.assertContains(response, 'anthropic')

    def test_setup_page_post_saves_keys(self):
        from dashboard.models import ApiKeyStorage
        response = self.client.post('/setup/', {
            'openai': 'sk-openai-123',
            'gemini': 'sk-gemini-123',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ApiKeyStorage.objects.count(), 2)
        self.assertEqual(ApiKeyStorage.get_key('openai'), 'sk-openai-123')
        # Empty field should not be saved or overwrite anything
        self.assertIsNone(ApiKeyStorage.get_key('anthropic'))

        # A subsequent GET should show they are saved but not expose the value
        response = self.client.get('/setup/')
        self.assertContains(response, 'Clé enregistrée')

class SessionModelTests(TestCase):
    """AC 6 - Database Session Model"""

    def test_create_session(self):
        from dashboard.models import Session
        session = Session.objects.create(
            title="Test Debate",
            topic="Is AI dangerous?",
            token_budget=1000,
            status="READY",
        )
        self.assertEqual(session.title, "Test Debate")
        self.assertEqual(session.topic, "Is AI dangerous?")
        self.assertEqual(session.token_budget, 1000)
        self.assertEqual(session.status, "READY")

from unittest.mock import patch

class SessionCreateAPITests(TestCase):
    """AC 4, 6 - Session Creation API integration with Sanity Check"""

    def setUp(self):
        self.client = Client()

    @patch('dashboard.views.run_sanity_check')
    def test_create_session_success(self, mock_sanity_check):
        mock_sanity_check.return_value = True
        
        response = self.client.post('/api/sessions/', {
            'title': 'Test Debate',
            'topic': 'Is AI dangerous?',
            'token_budget': 1000
        }, content_type='application/json')
        
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['success'], True)
        
        from dashboard.models import Session
        session = Session.objects.first()
        self.assertIsNotNone(session)
        self.assertEqual(session.status, 'READY')

    @patch('dashboard.views.run_sanity_check')
    def test_create_session_failure(self, mock_sanity_check):
        mock_sanity_check.return_value = False
        
        response = self.client.post('/api/sessions/', {
            'title': 'Test Debate',
            'topic': 'Controversial unsafe topic',
            'token_budget': 1000
        }, content_type='application/json')
        
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['success'], False)
        
        from dashboard.models import Session
        self.assertEqual(Session.objects.count(), 0)


# ════════════════════════════════════════════════════════════
#  Story 2.1 — The Genetic Matrix & Agent Selection
# ════════════════════════════════════════════════════════════

import json
from django.core.exceptions import ValidationError
from dashboard.models import ApiKeyStorage, Session, SessionAgent
from orchestrator.genetic import (
    ARCHETYPES,
    MODERATOR,
    MIN_AGENTS,
    MAX_AGENTS,
    get_archetype,
    get_archetype_choices,
    get_system_prompt,
    get_moderator,
    list_archetype_keys,
)


class GeneticMatrixTests(TestCase):
    """Test the orchestrator.genetic module — pure domain logic."""

    def test_five_archetypes_defined(self):
        """Exactly 5 archetypes exist in the V1 Genetic Matrix."""
        self.assertEqual(len(ARCHETYPES), 5)

    def test_archetype_keys(self):
        """All expected archetype keys are present."""
        expected = {"skeptic", "optimist", "pragmatist", "conservative", "innovator"}
        self.assertEqual(set(ARCHETYPES.keys()), expected)

    def test_each_archetype_has_required_fields(self):
        """Every archetype has label, color, icon, system_prompt."""
        for key, arch in ARCHETYPES.items():
            for field in ("label", "color", "icon", "system_prompt"):
                self.assertIn(field, arch, f"{key} missing '{field}'")
            self.assertTrue(len(arch["system_prompt"]) > 50,
                            f"{key} system_prompt too short")

    def test_moderator_defined(self):
        """The Moderator Architect is defined with all required fields."""
        for field in ("key", "label", "color", "icon", "system_prompt"):
            self.assertIn(field, MODERATOR)
        self.assertEqual(MODERATOR["key"], "moderator")

    def test_get_archetype_returns_dict(self):
        result = get_archetype("skeptic")
        self.assertIsNotNone(result)
        self.assertEqual(result["label"], "Le Sceptique")

    def test_get_archetype_returns_none_for_unknown(self):
        self.assertIsNone(get_archetype("unknown"))

    def test_get_archetype_choices(self):
        choices = get_archetype_choices()
        self.assertEqual(len(choices), 5)
        keys = [c[0] for c in choices]
        self.assertIn("skeptic", keys)

    def test_get_system_prompt(self):
        prompt = get_system_prompt("optimist")
        self.assertIn("Optimist", prompt)

    def test_get_system_prompt_raises_for_unknown(self):
        with self.assertRaises(KeyError):
            get_system_prompt("nonexistent")

    def test_get_moderator(self):
        mod = get_moderator()
        self.assertEqual(mod["key"], "moderator")

    def test_list_archetype_keys(self):
        keys = list_archetype_keys()
        self.assertEqual(len(keys), 5)

    def test_min_max_constraints(self):
        self.assertEqual(MIN_AGENTS, 2)
        self.assertEqual(MAX_AGENTS, 4)

    def test_no_django_imports_in_genetic(self):
        """genetic.py must NOT import from dashboard or django HTTP layer."""
        import inspect
        import orchestrator.genetic as mod
        source = inspect.getsource(mod)
        self.assertNotIn("from dashboard", source)
        self.assertNotIn("from django.http", source)
        self.assertNotIn("from django.views", source)


class SessionAgentModelTests(TestCase):
    """Test the SessionAgent model constraints and validation."""

    def setUp(self):
        self.session = Session.objects.create(
            title="Agent Test", topic="Test topic", token_budget=5000
        )

    def test_create_agent(self):
        agent = SessionAgent.objects.create(
            session=self.session,
            provider="openai",
            archetype="skeptic",
            slot_number=1,
        )
        self.assertEqual(agent.provider, "openai")
        self.assertEqual(agent.archetype, "skeptic")
        self.assertEqual(agent.slot_number, 1)

    def test_agent_str(self):
        agent = SessionAgent.objects.create(
            session=self.session,
            provider="openai",
            archetype="skeptic",
            slot_number=1,
        )
        self.assertIn("Le Sceptique", str(agent))
        self.assertIn("[1]", str(agent))

    def test_unique_archetype_per_session(self):
        """Cannot assign the same archetype twice in one session."""
        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=1,
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            SessionAgent.objects.create(
                session=self.session, provider="gemini",
                archetype="skeptic", slot_number=2,
            )

    def test_unique_slot_per_session(self):
        """Cannot assign two agents to the same slot."""
        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=1,
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            SessionAgent.objects.create(
                session=self.session, provider="gemini",
                archetype="optimist", slot_number=1,
            )

    def test_clean_rejects_invalid_archetype(self):
        agent = SessionAgent(
            session=self.session, provider="openai",
            archetype="invalid", slot_number=1,
        )
        with self.assertRaises(ValidationError):
            agent.clean()

    def test_clean_rejects_invalid_slot(self):
        agent = SessionAgent(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=5,
        )
        with self.assertRaises(ValidationError):
            agent.clean()

    def test_session_agent_count(self):
        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=1,
        )
        SessionAgent.objects.create(
            session=self.session, provider="gemini",
            archetype="optimist", slot_number=2,
        )
        self.assertEqual(self.session.agent_count(), 2)

    def test_session_validate_agent_count_too_few(self):
        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=1,
        )
        with self.assertRaises(ValidationError):
            self.session.validate_agent_count()

    def test_session_validate_agent_count_ok(self):
        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=1,
        )
        SessionAgent.objects.create(
            session=self.session, provider="gemini",
            archetype="optimist", slot_number=2,
        )
        # Should not raise
        self.session.validate_agent_count()

    def test_ordering_by_slot(self):
        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="innovator", slot_number=3,
        )
        SessionAgent.objects.create(
            session=self.session, provider="gemini",
            archetype="skeptic", slot_number=1,
        )
        SessionAgent.objects.create(
            session=self.session, provider="anthropic",
            archetype="optimist", slot_number=2,
        )
        agents = list(self.session.agents.all())
        self.assertEqual(agents[0].slot_number, 1)
        self.assertEqual(agents[1].slot_number, 2)
        self.assertEqual(agents[2].slot_number, 3)


class RoundtableConfigAPITests(TestCase):
    """Test the /api/roundtable/ endpoint."""

    def setUp(self):
        self.client = Client()
        self.session = Session.objects.create(
            title="RT Test", topic="AI ethics", token_budget=5000
        )
        # Register API keys for providers
        ApiKeyStorage.objects.create(provider="openai", api_key="sk-test-1")
        ApiKeyStorage.objects.create(provider="gemini", api_key="sk-test-2")
        ApiKeyStorage.objects.create(provider="anthropic", api_key="sk-test-3")

    def _post(self, payload):
        return self.client.post(
            '/api/roundtable/',
            json.dumps(payload),
            content_type='application/json',
        )

    # ── Success cases ─────────────────────────────────────

    def test_configure_2_agents_success(self):
        """Minimum configuration: 2 agents."""
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["agents_configured"], 2)
        self.assertTrue(data["moderator_included"])
        self.assertEqual(SessionAgent.objects.filter(session=self.session).count(), 2)

    def test_configure_4_agents_success(self):
        """Maximum configuration: 4 agents."""
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
                {"provider": "anthropic", "archetype": "pragmatist"},
                {"provider": "openai", "archetype": "conservative"},
            ]
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["agents_configured"], 4)

    def test_session_status_updated_to_configuring(self):
        self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "CONFIGURING")

    def test_response_includes_moderator_label(self):
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        data = resp.json()
        self.assertEqual(data["moderator_label"], "Modérateur Architecte")

    def test_reconfiguration_replaces_previous_agents(self):
        """Re-posting clears previous agents and stores new ones."""
        self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        self.assertEqual(SessionAgent.objects.filter(session=self.session).count(), 2)

        self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "anthropic", "archetype": "pragmatist"},
                {"provider": "openai", "archetype": "conservative"},
                {"provider": "gemini", "archetype": "innovator"},
            ]
        })
        agents = SessionAgent.objects.filter(session=self.session)
        self.assertEqual(agents.count(), 3)
        self.assertEqual(set(agents.values_list('archetype', flat=True)),
                         {"pragmatist", "conservative", "innovator"})

    # ── Validation failures ───────────────────────────────

    def test_reject_less_than_min_agents(self):
        """Only 1 agent should fail."""
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("2", resp.json()["error"])

    def test_reject_more_than_max_agents(self):
        """5 agents should fail."""
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
                {"provider": "anthropic", "archetype": "pragmatist"},
                {"provider": "openai", "archetype": "conservative"},
                {"provider": "gemini", "archetype": "innovator"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("4", resp.json()["error"])

    def test_reject_duplicate_archetype(self):
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "skeptic"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already assigned", resp.json()["error"])

    def test_reject_unknown_archetype(self):
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai", "archetype": "unknown"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("unknown archetype", resp.json()["error"])

    def test_reject_unconfigured_provider(self):
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "deepseek", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no API key", resp.json()["error"])

    def test_reject_missing_session_id(self):
        resp = self._post({
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("session_id", resp.json()["error"])

    def test_reject_nonexistent_session(self):
        resp = self._post({
            "session_id": 99999,
            "agents": [
                {"provider": "openai", "archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        self.assertEqual(resp.status_code, 404)

    def test_reject_invalid_json(self):
        resp = self.client.post(
            '/api/roundtable/',
            'not json',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_reject_empty_agents_list(self):
        resp = self._post({
            "session_id": self.session.id,
            "agents": []
        })
        self.assertEqual(resp.status_code, 400)

    def test_reject_missing_provider(self):
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"archetype": "skeptic"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("required", resp.json()["error"])

    def test_reject_missing_archetype(self):
        resp = self._post({
            "session_id": self.session.id,
            "agents": [
                {"provider": "openai"},
                {"provider": "gemini", "archetype": "optimist"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("required", resp.json()["error"])


class RoundtableViewTests(TestCase):
    """Test the roundtable UI page."""

    def setUp(self):
        self.client = Client()
        self.session = Session.objects.create(
            title="View Test", topic="Test", token_budget=5000
        )
        ApiKeyStorage.objects.create(provider="openai", api_key="sk-test")

    def test_roundtable_page_status_200(self):
        resp = self.client.get(f'/session/{self.session.id}/roundtable/')
        self.assertEqual(resp.status_code, 200)

    def test_roundtable_page_template(self):
        resp = self.client.get(f'/session/{self.session.id}/roundtable/')
        self.assertTemplateUsed(resp, 'dashboard/roundtable.html')

    def test_roundtable_page_contains_session_info(self):
        resp = self.client.get(f'/session/{self.session.id}/roundtable/')
        self.assertContains(resp, 'The Roundtable')
        self.assertContains(resp, 'View Test')

    def test_roundtable_page_contains_moderator_info(self):
        resp = self.client.get(f'/session/{self.session.id}/roundtable/')
        self.assertContains(resp, 'Modérateur Architecte')

    def test_roundtable_page_contains_archetypes(self):
        resp = self.client.get(f'/session/{self.session.id}/roundtable/')
        self.assertContains(resp, 'Le Sceptique')
        self.assertContains(resp, 'L&#x27;Optimiste')

    def test_roundtable_page_404_for_invalid_session(self):
        resp = self.client.get('/session/99999/roundtable/')
        self.assertEqual(resp.status_code, 404)


# ════════════════════════════════════════════════════════════
#  Story 2.2 — The 3-Phase State Machine (Protocol)
# ════════════════════════════════════════════════════════════

import asyncio
import inspect


def _run_protocol_sync(agents, topic, confrontation_rounds=2):
    """Helper: collect all events from run_protocol into a list (sync wrapper)."""
    from orchestrator.protocol import run_protocol

    async def _collect():
        events = []
        async for event in run_protocol(agents, topic, confrontation_rounds):
            events.append(event)
        return events

    return asyncio.run(_collect())


class ProtocolStateMachineTests(TestCase):
    """Story 2.2 — validate the 3-phase debate protocol state machine."""

    # ── Shared fixtures ──────────────────────────────────────

    AGENTS_2 = [
        {"provider": "openai", "archetype": "skeptic", "slot_number": 1},
        {"provider": "gemini", "archetype": "optimist", "slot_number": 2},
    ]
    AGENTS_3 = [
        {"provider": "openai", "archetype": "skeptic", "slot_number": 1},
        {"provider": "gemini", "archetype": "optimist", "slot_number": 2},
        {"provider": "anthropic", "archetype": "pragmatist", "slot_number": 3},
    ]
    TOPIC = "Is AI beneficial to humanity?"

    # ── Tests ────────────────────────────────────────────────

    def test_phase_order_exposition_first(self):
        """First system event must announce EXPOSITION."""
        events = _run_protocol_sync(self.AGENTS_2, self.TOPIC)
        system_events = [e for e in events if e["type"] == "system"]
        self.assertTrue(len(system_events) >= 1)
        self.assertIn("EXPOSITION", system_events[0]["content"])

    def test_all_agents_speak_in_exposition(self):
        """Each agent must produce a 'speech' event during EXPOSITION (before CONFRONTATION)."""
        events = _run_protocol_sync(self.AGENTS_3, self.TOPIC)
        # Exposition speeches appear before any CONFRONTATION system event
        exposition_speeches = []
        confrontation_started = False
        for e in events:
            if e["type"] == "system" and "CONFRONTATION" in e["content"]:
                confrontation_started = True
            if not confrontation_started and e["type"] == "speech":
                exposition_speeches.append(e)
        self.assertEqual(len(exposition_speeches), 3)

    def test_confrontation_rounds_respected(self):
        """Number of CONFRONTATION system events must equal confrontation_rounds."""
        events = _run_protocol_sync(self.AGENTS_2, self.TOPIC, confrontation_rounds=3)
        confrontation_events = [
            e for e in events
            if e["type"] == "system" and "CONFRONTATION" in e["content"]
        ]
        self.assertEqual(len(confrontation_events), 3)

    def test_resolution_phase_reached(self):
        """A RESOLUTION system event must be emitted, followed by speech events."""
        events = _run_protocol_sync(self.AGENTS_2, self.TOPIC)
        resolution_idx = next(
            (i for i, e in enumerate(events)
             if e["type"] == "system" and "RESOLUTION" in e["content"]),
            None,
        )
        self.assertIsNotNone(resolution_idx, "RESOLUTION phase not found in events")
        # At least one speech event must follow RESOLUTION
        post_resolution = events[resolution_idx + 1:]
        speech_events = [e for e in post_resolution if e["type"] == "speech"]
        self.assertGreater(len(speech_events), 0)

    def test_done_event_is_last(self):
        """The very last event must be of type 'done'."""
        events = _run_protocol_sync(self.AGENTS_2, self.TOPIC)
        self.assertTrue(len(events) > 0)
        self.assertEqual(events[-1]["type"], "done")

    def test_agent_id_matches_archetype(self):
        """Speech events from the skeptic agent must carry agent_id == 'skeptic'."""
        events = _run_protocol_sync(self.AGENTS_2, self.TOPIC)
        skeptic_speeches = [
            e for e in events
            if e["type"] == "speech" and e["agent_id"] == "skeptic"
        ]
        self.assertGreater(len(skeptic_speeches), 0,
                           "No speech events found for 'skeptic' agent")
        for e in skeptic_speeches:
            self.assertEqual(e["agent_id"], "skeptic")

    def test_minimum_total_event_count(self):
        """With 2 agents and 2 confrontation rounds the event count must be >= 10.

        Minimum accounting:
          1 system  (EXPOSITION)
          2 thought + 2 speech = 4  (EXPOSITION)
          2 system  (CONFRONTATION rounds)
          2×2 thought + 2×2 speech = 8  (CONFRONTATION)
          1 system  (RESOLUTION)
          2 thought + 2 speech = 4  (RESOLUTION)
          1 done
          Total = 21, well above the 10-event floor.
        """
        events = _run_protocol_sync(self.AGENTS_2, self.TOPIC, confrontation_rounds=2)
        self.assertGreaterEqual(len(events), 10)

    def test_no_django_imports_in_protocol(self):
        """protocol.py must NOT import from django or dashboard."""
        import orchestrator.protocol as mod
        source = inspect.getsource(mod)
        self.assertNotIn("from django", source,
                         "orchestrator.protocol must not import from django")
        self.assertNotIn("import django", source,
                         "orchestrator.protocol must not import django")
        self.assertNotIn("from dashboard", source,
                         "orchestrator.protocol must not import from dashboard")

    def test_asyncio_sleep_not_time_sleep(self):
        """protocol.py must use asyncio.sleep, never time.sleep."""
        import orchestrator.protocol as mod
        source = inspect.getsource(mod)
        self.assertNotIn("time.sleep", source,
                         "time.sleep is forbidden — use asyncio.sleep(0)")
        self.assertIn("asyncio.sleep", source,
                      "asyncio.sleep(0) must be present to yield the ASGI event loop")


# ════════════════════════════════════════════════════════════
#  Story 2.3 — Moderator Architect & Rational Adherence
# ════════════════════════════════════════════════════════════


def _run_engine_sync(agents, topic, confrontation_rounds=2):
    """Helper: collect all events from run_debate_engine into a list (sync wrapper)."""
    from orchestrator.engine import run_debate_engine

    async def _collect():
        events = []
        async for event in run_debate_engine(agents, topic, confrontation_rounds):
            events.append(event)
        return events

    return asyncio.run(_collect())


class ModeratorEngineTests(TestCase):
    """Story 2.3 — validate ConcessionDetector, ModeratorPromptBuilder,
    prioritise_speaking_order, and the run_debate_engine façade."""

    # ── Shared fixtures ──────────────────────────────────────

    AGENTS_2 = [
        {"provider": "openai", "archetype": "skeptic", "slot_number": 1},
        {"provider": "gemini", "archetype": "optimist", "slot_number": 2},
    ]
    TOPIC = "Is AI beneficial to humanity?"

    # ── ConcessionDetector tests ─────────────────────────────

    def test_concession_detector_detects_english_phrase(self):
        """Detector identifies an English concession phrase."""
        from orchestrator.engine import ConcessionDetector
        detector = ConcessionDetector()
        self.assertTrue(detector.detect("You've made a point, I must admit."))

    def test_concession_detector_detects_french_phrase(self):
        """Detector identifies a French concession phrase."""
        from orchestrator.engine import ConcessionDetector
        detector = ConcessionDetector()
        self.assertTrue(detector.detect("tu as marqué un point sur ce sujet."))

    def test_concession_detector_rejects_non_concession(self):
        """Detector returns False for a non-concession statement."""
        from orchestrator.engine import ConcessionDetector
        detector = ConcessionDetector()
        self.assertFalse(detector.detect("I strongly disagree with your argument."))

    def test_concession_detector_case_insensitive(self):
        """Detector is case-insensitive."""
        from orchestrator.engine import ConcessionDetector
        detector = ConcessionDetector()
        self.assertTrue(detector.detect("YOU ARE RIGHT about that specific claim."))

    # ── ModeratorPromptBuilder tests ─────────────────────────

    def test_moderator_prompt_includes_archetype_system_prompt(self):
        """Built prompt contains the archetype's system_prompt content and the preamble."""
        from orchestrator.engine import ModeratorPromptBuilder
        from orchestrator.protocol import DebatePhase
        builder = ModeratorPromptBuilder()
        prompt = builder.build("skeptic", [], None, DebatePhase.CONFRONTATION)
        # genetic.py skeptic system_prompt contains "Skeptic"
        self.assertIn("Skeptic", prompt)
        self.assertIn("RATIONAL ADHERENCE", prompt)

    def test_moderator_prompt_includes_history_summary(self):
        """Built prompt references recent history entries."""
        from orchestrator.engine import ModeratorPromptBuilder
        from orchestrator.protocol import DebatePhase
        history = [
            {
                "agent_id": "optimist",
                "role": "agent",
                "content": "AI is the future.",
                "phase": "CONFRONTATION",
                "round_num": 1,
            }
        ]
        builder = ModeratorPromptBuilder()
        prompt = builder.build("skeptic", history, "optimist", DebatePhase.CONFRONTATION)
        # Either the agent_id or the content must appear in the prompt
        self.assertTrue(
            "optimist" in prompt or "AI is the future" in prompt[:500],
            "History not referenced in built prompt",
        )

    def test_moderator_prompt_includes_previous_speaker(self):
        """Built prompt mentions the previous speaker."""
        from orchestrator.engine import ModeratorPromptBuilder
        from orchestrator.protocol import DebatePhase
        builder = ModeratorPromptBuilder()
        prompt = builder.build("skeptic", [], "optimist", DebatePhase.CONFRONTATION)
        self.assertTrue(
            "optimist" in prompt.lower() or "L'Optimiste" in prompt,
            "Previous speaker not mentioned in built prompt",
        )

    # ── prioritise_speaking_order tests ─────────────────────

    def test_speaking_order_exposition_uses_slot_order(self):
        """EXPOSITION phase returns agents sorted by slot_number ascending."""
        from orchestrator.engine import prioritise_speaking_order
        from orchestrator.protocol import DebatePhase
        agents = [
            {"provider": "openai", "archetype": "optimist", "slot_number": 2},
            {"provider": "gemini", "archetype": "skeptic", "slot_number": 1},
            {"provider": "anthropic", "archetype": "pragmatist", "slot_number": 3},
        ]
        result = prioritise_speaking_order(agents, [], DebatePhase.EXPOSITION)
        self.assertEqual(result[0]["slot_number"], 1)
        self.assertEqual(result[1]["slot_number"], 2)

    def test_speaking_order_resolution_is_reversed(self):
        """RESOLUTION phase returns agents in reverse slot_number order."""
        from orchestrator.engine import prioritise_speaking_order
        from orchestrator.protocol import DebatePhase
        agents = [
            {"provider": "openai", "archetype": "skeptic", "slot_number": 1},
            {"provider": "gemini", "archetype": "optimist", "slot_number": 2},
            {"provider": "anthropic", "archetype": "pragmatist", "slot_number": 3},
        ]
        result = prioritise_speaking_order(agents, [], DebatePhase.RESOLUTION)
        self.assertEqual(result[0]["slot_number"], 3)

    def test_speaking_order_confrontation_empty_history_falls_back(self):
        """CONFRONTATION with empty history falls back to slot_number ascending."""
        from orchestrator.engine import prioritise_speaking_order
        from orchestrator.protocol import DebatePhase
        result = prioritise_speaking_order(
            self.AGENTS_2, [], DebatePhase.CONFRONTATION
        )
        self.assertEqual(result[0]["slot_number"], 1)

    # ── run_debate_engine tests ──────────────────────────────

    def test_engine_yields_done_last(self):
        """The very last event from run_debate_engine must be of type 'done'."""
        events = _run_engine_sync(self.AGENTS_2, self.TOPIC)
        self.assertTrue(len(events) > 0)
        self.assertEqual(events[-1]["type"], "done")

    def test_engine_no_django_imports(self):
        """engine.py must NOT import from django or dashboard."""
        import orchestrator.engine as mod
        source = inspect.getsource(mod)
        self.assertNotIn("from django", source,
                         "orchestrator.engine must not import from django")
        self.assertNotIn("import django", source,
                         "orchestrator.engine must not import django")

    def test_engine_uses_asyncio_sleep_not_time_sleep(self):
        """engine.py must use asyncio.sleep, never time.sleep."""
        import orchestrator.engine as mod
        source = inspect.getsource(mod)
        self.assertNotIn("time.sleep", source,
                         "time.sleep is forbidden — use asyncio.sleep(0)")
        self.assertIn("asyncio.sleep", source,
                      "asyncio.sleep(0) must be present to yield the ASGI event loop")

# ════════════════════════════════════════════════════════════
#  Story 3.1 — Core SSE Engine & Live Streaming
# ════════════════════════════════════════════════════════════

class CockpitViewTests(TestCase):
    """Story 3.1 — Verify cockpit view rendering and components."""

    def setUp(self):
        self.client = Client()
        self.session = Session.objects.create(
            title="Cockpit Test", topic="Live Stream Topic", token_budget=6000
        )
        # Register API keys for providers
        ApiKeyStorage.objects.create(provider="openai", api_key="sk-test-1")
        ApiKeyStorage.objects.create(provider="gemini", api_key="sk-test-2")

        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=1,
        )
        SessionAgent.objects.create(
            session=self.session, provider="gemini",
            archetype="optimist", slot_number=2,
        )

    def test_cockpit_page_status_200(self):
        resp = self.client.get(f'/session/{self.session.id}/cockpit/')
        self.assertEqual(resp.status_code, 200)

    def test_cockpit_uses_correct_template(self):
        resp = self.client.get(f'/session/{self.session.id}/cockpit/')
        self.assertTemplateUsed(resp, 'dashboard/cockpit.html')

    def test_cockpit_contains_session_info(self):
        resp = self.client.get(f'/session/{self.session.id}/cockpit/')
        self.assertContains(resp, 'Cockpit Test')
        self.assertContains(resp, 'Live Stream Topic')

    def test_cockpit_contains_stream_url_data_attr(self):
        resp = self.client.get(f'/session/{self.session.id}/cockpit/')
        self.assertContains(resp, 'data-stream-url=')

    def test_cockpit_contains_event_stream_log_div(self):
        resp = self.client.get(f'/session/{self.session.id}/cockpit/')
        self.assertContains(resp, 'id="event-stream-log"')

    def test_cockpit_contains_kill_switch_button(self):
        resp = self.client.get(f'/session/{self.session.id}/cockpit/')
        self.assertContains(resp, 'id="kill-switch-btn"')

    def test_cockpit_404_for_invalid_session(self):
        resp = self.client.get('/session/99999/cockpit/')
        self.assertEqual(resp.status_code, 404)


class StreamingSSETests(TestCase):
    """Story 3.1 — Verify SSE event stream correctly wraps engine execution."""

    def setUp(self):
        self.client = Client()
        self.session = Session.objects.create(
            title="Stream Test", topic="Streaming Event Test Topic", token_budget=7000,
            status="CONFIGURING"
        )
        ApiKeyStorage.objects.create(provider="openai", api_key="sk-test-1")
        ApiKeyStorage.objects.create(provider="gemini", api_key="sk-test-2")

        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=1,
        )
        SessionAgent.objects.create(
            session=self.session, provider="gemini",
            archetype="optimist", slot_number=2,
        )

    def _collect_sse(self, session_id):
        """Collect all SSE events from the stream endpoint as a list of dicts."""
        import json
        import asyncio
        response = self.client.get(f"/session/{session_id}/stream/", HTTP_ACCEPT="text/event-stream")
        
        async def _consume():
            chunks = []
            if hasattr(response.streaming_content, "__aiter__"):
                async for chunk in response.streaming_content:
                    chunks.append(chunk)
            else:
                for chunk in response.streaming_content:
                    chunks.append(chunk)
            return chunks

        raw_chunks = asyncio.run(_consume())
        events = []
        for chunk in raw_chunks:
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            for line in chunk.split("\n"):
                line = line.strip()
                if line.startswith("data: "):
                    payload = line[len("data: "):]
                    try:
                        events.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
        return events

    def test_stream_returns_200(self):
        resp = self.client.get(f"/session/{self.session.id}/stream/", HTTP_ACCEPT="text/event-stream")
        self.assertEqual(resp.status_code, 200)

    def test_stream_content_type_is_event_stream(self):
        resp = self.client.get(f"/session/{self.session.id}/stream/", HTTP_ACCEPT="text/event-stream")
        self.assertTrue(resp["Content-Type"].startswith("text/event-stream"))

    def test_stream_yields_events(self):
        events = self._collect_sse(self.session.id)
        self.assertGreater(len(events), 0)

    def test_stream_events_have_required_fields(self):
        events = self._collect_sse(self.session.id)
        for event in events:
            self.assertIn("type", event)
            self.assertIn("agent_id", event)
            self.assertIn("content", event)

    def test_stream_last_event_is_done(self):
        events = self._collect_sse(self.session.id)
        self.assertEqual(events[-1]["type"], "done")

    def test_stream_contains_exposition_system_event(self):
        events = self._collect_sse(self.session.id)
        system_events = [e for e in events if e["type"] == "system"]
        self.assertTrue(any("EXPOSITION" in e["content"] for e in system_events))

    def test_stream_updates_session_status_to_running(self):
        self._collect_sse(self.session.id)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "RUNNING")

    def test_stream_404_for_invalid_session(self):
        events = self._collect_sse(99999)
        self.assertTrue(len(events) > 0)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("not found", events[0]["content"].lower())

    def test_stream_no_agents_returns_error_event(self):
        empty_session = Session.objects.create(
            title="Empty", topic="Empty Topic", token_budget=1000
        )
        events = self._collect_sse(empty_session.id)
        self.assertTrue(len(events) > 0)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("no agents", events[0]["content"].lower())

# ════════════════════════════════════════════════════════════
#  Story 3.3 — The Justified Kill-Switch
# ════════════════════════════════════════════════════════════

class KillSwitchTests(TestCase):
    """Story 3.3 — Verify the kill switch API and abort propagation."""

    def setUp(self):
        self.client = Client()
        self.session = Session.objects.create(
            title="Kill Switch Test", topic="A controversial topic", token_budget=5000,
            status="RUNNING"
        )
        ApiKeyStorage.objects.create(provider="openai", api_key="sk-test-1")
        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=1,
        )

    def tearDown(self):
        from orchestrator.safety import clear_abort_event
        clear_abort_event(self.session.id)
        super().tearDown()

    def test_abort_session_requires_justification(self):
        resp = self.client.post(
            f"/api/session/{self.session.id}/abort/",
            json.dumps({"justification": ""}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("obligatoire", resp.json()["error"])
        
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "RUNNING")

    def test_abort_session_success(self):
        resp = self.client.post(
            f"/api/session/{self.session.id}/abort/",
            json.dumps({"justification": "Infinite loop detected."}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "ABORTED")
        self.assertEqual(self.session.abort_justification, "Infinite loop detected.")

    def test_abort_triggers_engine_error_yield(self):
        from orchestrator.safety import get_abort_event
        resp = self.client.post(
            f"/api/session/{self.session.id}/abort/",
            json.dumps({"justification": "Testing event set"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        
        # Verify the event dictionary in safety.py holds the flag
        ev = get_abort_event(self.session.id)
        self.assertTrue(ev.is_set())


# ════════════════════════════════════════════════════════════
#  Story 4.1 — Automated PDF Forensic Report
# ════════════════════════════════════════════════════════════

import io


class PdfExporterUnitTests(TestCase):
    """Story 4.1 — Unit tests for orchestrator.exporter module."""

    def _make_session(self, status="SUCCESS", justification=None):
        session = Session.objects.create(
            title="Rapport Forensique",
            topic="L'IA est-elle dangereuse ?",
            token_budget=2000,
            status=status,
            abort_justification=justification,
        )
        ApiKeyStorage.objects.create(provider="openai", api_key="sk-test-1")
        ApiKeyStorage.objects.create(provider="gemini", api_key="sk-test-2")
        SessionAgent.objects.create(
            session=session, provider="openai",
            archetype="skeptic", slot_number=1,
        )
        SessionAgent.objects.create(
            session=session, provider="gemini",
            archetype="optimist", slot_number=2,
        )
        return session

    # ── Module-level guards ──────────────────────────────────

    def test_exporter_module_importable(self):
        """orchestrator.exporter can be imported without error."""
        import orchestrator.exporter as mod
        self.assertIsNotNone(mod)

    def test_exporter_no_django_http_imports(self):
        """exporter.py must NOT import from django.http or django.views
        (domain boundary — only ORM + stdlib + reportlab allowed)."""
        import inspect
        import orchestrator.exporter as mod
        source = inspect.getsource(mod)
        self.assertNotIn("from django.http", source)
        self.assertNotIn("from django.views", source)
        self.assertNotIn("from dashboard.views", source)

    def test_generate_pdf_report_function_exists(self):
        """generate_pdf_report(session) public function must be present."""
        from orchestrator.exporter import generate_pdf_report
        self.assertTrue(callable(generate_pdf_report))

    # ── Output validation ────────────────────────────────────

    def test_generate_pdf_returns_bytes(self):
        """generate_pdf_report must return bytes (a valid binary blob)."""
        from orchestrator.exporter import generate_pdf_report
        session = self._make_session()
        result = generate_pdf_report(session)
        self.assertIsInstance(result, bytes)

    def test_generate_pdf_starts_with_pdf_magic_bytes(self):
        """The returned bytes must be a valid PDF (starts with %PDF-)."""
        from orchestrator.exporter import generate_pdf_report
        session = self._make_session()
        result = generate_pdf_report(session)
        self.assertTrue(result.startswith(b"%PDF-"),
                        "Output does not start with %PDF- magic bytes")

    def test_generate_pdf_non_empty(self):
        """PDF output must be non-trivial (> 1 KB)."""
        from orchestrator.exporter import generate_pdf_report
        session = self._make_session()
        result = generate_pdf_report(session)
        self.assertGreater(len(result), 1024,
                           "PDF output is suspiciously small")

    # ── Content correctness ──────────────────────────────────

    def test_pdf_contains_session_title(self):
        """PDF text content must include the session title."""
        from orchestrator.exporter import generate_pdf_report
        session = self._make_session()
        result = generate_pdf_report(session)
        # ReportLab uses WinAnsiEncoding — try both utf-8 and latin-1
        title = session.title
        self.assertTrue(
            title.encode("utf-8") in result or title.encode("latin-1", errors="replace") in result,
            f"Session title '{title}' not found in PDF bytes",
        )

    def test_pdf_contains_session_topic(self):
        """PDF must include the session topic."""
        from orchestrator.exporter import generate_pdf_report
        session = self._make_session()
        result = generate_pdf_report(session)
        # ReportLab uses WinAnsiEncoding — try both utf-8 and latin-1
        topic = session.topic
        self.assertTrue(
            topic.encode("utf-8") in result or topic.encode("latin-1", errors="replace") in result,
            "Session topic not found in PDF bytes",
        )

    def test_pdf_aborted_session_includes_justification_block(self):
        """For ABORTED sessions, the justification text must be present in the PDF."""
        from orchestrator.exporter import generate_pdf_report
        justification = "Boucle infinie detectee par le chercheur."
        session = self._make_session(status="ABORTED", justification=justification)
        result = generate_pdf_report(session)
        # ReportLab uses WinAnsiEncoding (latin-1 family) for Type1 fonts.
        # Try multiple encodings for resilience.
        encoded_utf8   = justification.encode("utf-8")
        encoded_latin1 = justification.encode("latin-1", errors="replace")
        self.assertTrue(
            encoded_utf8 in result or encoded_latin1 in result,
            "Abort justification not found in ABORTED session PDF",
        )

    def test_pdf_success_session_no_abort_block(self):
        """For SUCCESS sessions without abort, the abort banner must NOT appear."""
        from orchestrator.exporter import generate_pdf_report
        session = self._make_session(status="SUCCESS")
        result = generate_pdf_report(session)
        # The abort header keyword should be absent
        self.assertNotIn(b"INTERROMPU", result)


# ── View-level tests ─────────────────────────────────────────

class DownloadPdfViewTests(TestCase):
    """Story 4.1 — HTTP-level tests for download_pdf_report view."""

    def setUp(self):
        self.client = Client()
        self.session = Session.objects.create(
            title="Debate Alpha",
            topic="Is nuclear power safe?",
            token_budget=3000,
            status="SUCCESS",
        )
        ApiKeyStorage.objects.create(provider="openai", api_key="sk-dl-1")
        ApiKeyStorage.objects.create(provider="gemini", api_key="sk-dl-2")
        SessionAgent.objects.create(
            session=self.session, provider="openai",
            archetype="skeptic", slot_number=1,
        )
        SessionAgent.objects.create(
            session=self.session, provider="gemini",
            archetype="optimist", slot_number=2,
        )

    def _url(self, session_id=None):
        sid = session_id if session_id is not None else self.session.id
        return f"/session/{sid}/report/pdf/"

    def test_pdf_download_url_exists_and_returns_200(self):
        """GET /session/<id>/report/pdf/ must return HTTP 200 for SUCCESS session."""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)

    def test_pdf_download_content_type_is_pdf(self):
        """Response Content-Type must be application/pdf."""
        resp = self.client.get(self._url())
        self.assertTrue(
            resp["Content-Type"].startswith("application/pdf"),
            f"Expected application/pdf but got: {resp['Content-Type']}",
        )

    def test_pdf_download_content_disposition_is_attachment(self):
        """Response must use Content-Disposition: attachment for file download."""
        resp = self.client.get(self._url())
        disposition = resp.get("Content-Disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn(".pdf", disposition)

    def test_pdf_download_response_is_non_empty(self):
        """Downloaded PDF content must be non-empty."""
        resp = self.client.get(self._url())
        content = b"".join(resp.streaming_content) if hasattr(resp, "streaming_content") else resp.content
        self.assertGreater(len(content), 0)

    def test_pdf_download_404_for_invalid_session(self):
        """Non-existent session must return 404."""
        resp = self.client.get(self._url(session_id=99999))
        self.assertEqual(resp.status_code, 404)

    def test_pdf_download_403_for_session_not_terminal(self):
        """RUNNING session must return 403 (not yet downloadable)."""
        running_session = Session.objects.create(
            title="Running",
            topic="Still running",
            token_budget=1000,
            status="RUNNING",
        )
        resp = self.client.get(self._url(session_id=running_session.id))
        self.assertEqual(resp.status_code, 403)

    def test_pdf_download_works_for_aborted_session(self):
        """ABORTED sessions must also produce a downloadable PDF."""
        aborted = Session.objects.create(
            title="Aborted Debate",
            topic="Aborted topic",
            token_budget=1000,
            status="ABORTED",
            abort_justification="Arrêt forcé par le chercheur.",
        )
        resp = self.client.get(self._url(session_id=aborted.id))
        self.assertEqual(resp.status_code, 200)


# ── Frontend integration tests ────────────────────────────────

class CockpitPdfButtonTests(TestCase):
    """Story 4.1 — Verify the Download PDF button is present in cockpit template."""

    def setUp(self):
        self.client = Client()
        self.session_success = Session.objects.create(
            title="Finished Debate",
            topic="Test topic",
            token_budget=2000,
            status="SUCCESS",
        )
        self.session_running = Session.objects.create(
            title="Live Debate",
            topic="Live topic",
            token_budget=2000,
            status="RUNNING",
        )
        ApiKeyStorage.objects.create(provider="openai", api_key="sk-btn-1")
        for sess in (self.session_success, self.session_running):
            SessionAgent.objects.create(
                session=sess, provider="openai",
                archetype="skeptic", slot_number=1,
            )

    def test_cockpit_has_pdf_download_button(self):
        """Cockpit page must include the PDF download button element."""
        resp = self.client.get(f"/session/{self.session_success.id}/cockpit/")
        self.assertContains(resp, "download-pdf-btn")

    def test_pdf_download_button_links_to_pdf_url(self):
        """The download button/link must reference the /report/pdf/ URL."""
        resp = self.client.get(f"/session/{self.session_success.id}/cockpit/")
        self.assertContains(resp, "report/pdf")
