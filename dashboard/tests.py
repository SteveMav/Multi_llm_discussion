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
