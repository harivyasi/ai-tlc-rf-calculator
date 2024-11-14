from django.urls import path
from . import views

# Define URL patterns for the application
urlpatterns = [
    # URL for the image upload page
    path('', views.upload_image, name='upload_image'),

    # URL for calculating RF values based on image data and user annotations
    path('calculate_rf/', views.calculate_rf, name='calculate_rf'),

    # URL for fetching and saving an image from a remote URL
    path('fetch-image/', views.fetch_and_save_image, name='fetch_image'),

    # URL for serving an image file to the client
    path('serve-image/', views.serve_image, name='serve_image'),
]

