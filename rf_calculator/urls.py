"""rf_calculator URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    # Include URLs from the circle_detector app
    path('', include('circle_detector.urls')),

    # Serve user media (annotated images, CSVs). This is a low-traffic internal
    # tool, so serving media through Django is acceptable; a reverse proxy could
    # take this over in a higher-traffic deployment.
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
