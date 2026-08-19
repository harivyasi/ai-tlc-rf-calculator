"""rf_calculator URL configuration."""

from django.conf import settings
from django.urls import include, path, re_path

from circle_detector.media_views import serve_media

urlpatterns = [
    # Media is routed regardless of DEBUG: the annotated image and CSV that
    # calculate_rf returns are the app's output, and django.conf.urls.static
    # silently does nothing when DEBUG is off. Static files are handled by
    # WhiteNoise in both modes, so they need no route here.
    re_path(r'^media/(?P<path>.*)$', serve_media, name='serve_media'),

    # URLs from the circle_detector app.
    path('', include('circle_detector.urls')),
]

# There are no models and nothing registered, so the admin is only a login
# surface. Off unless a deployment asks for it.
if settings.ENABLE_ADMIN:
    from django.contrib import admin

    urlpatterns.insert(0, path('admin/', admin.site.urls))
