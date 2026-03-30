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
