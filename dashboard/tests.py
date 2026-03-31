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

