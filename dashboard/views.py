from django.views.generic import TemplateView


class HomeView(TemplateView):
    """Design system verification page — The Obsidian Observatory."""

    template_name = 'dashboard/home.html'
