import os
import cv2
import numpy as np
import matplotlib.pyplot as plt
from sklearn.cluster import DBSCAN
from django.shortcuts import render
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
import json
import csv
from django.core.files.storage import default_storage
import requests
import logging
import tempfile
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt

# View for handling image upload
def upload_image(request):
    """
    Handle image upload and rendering.

    This view handles both GET and POST requests. When an image is uploaded via
    a POST request, it saves the image to the server's file system and redirects
    the user to the annotation page. For GET requests, it renders the image upload page.

    Args:
        request (HttpRequest): The HTTP request object containing image file data (if POST).

    Returns:
        HttpResponse: A rendered HTML template for either image upload or annotation,
                      depending on the request method.
    """
    ## Check if the request method is POST and an image file is provided
    #if request.method == 'POST' and request.FILES.get('image'):
    #    image = request.FILES['image']  # Get the uploaded image file
    #    fs = FileSystemStorage()  # Initialize file system storage handler
    #    filename = fs.save(image.name, image)  # Save the uploaded image to the server
    #    uploaded_file_url = fs.url(filename)  # Get the URL of the saved image
    #
    #    # Render the annotation page with the URL of the uploaded image
    #    return render(request, 'circle_detector/annotate.html', {
    #        'uploaded_file_url': uploaded_file_url,
    #    })

    # If the request method is GET, render the upload page template
    return render(request, 'circle_detector/annotate.html')



# Configure logging for the module
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@csrf_exempt
def calculate_rf(request):
    """
    Handles the calculation of RF values from the spots and lines in the request body.

    This function processes image data and performs RF (Retention Factor) calculations
    based on the position of lines (min, max, vertical, horizontal) and spots. The annotated
    image and the RF values are returned as URLs.

    Args:
        request (HttpRequest): The HTTP request containing JSON data for lines, spots, and imageSrc.

    Returns:
        JsonResponse: JSON response with RF values, annotated image URL, and CSV URL,
                      or an error message if the request is invalid.
    """
    if request.method == 'POST':
        try:
            # Parse the incoming JSON data
            data = json.loads(request.body)

            # Extract lines, spots, and imageSrc from the request
            lines = data.get('lines')
            spots = data.get('spots')
            image_src = data.get('imageSrc')

            # Check for missing data and return error responses accordingly
            if not lines:
                logger.error('Lines data is missing')
                return JsonResponse({'error': 'Missing "lines" data in request'}, status=400)
            if not spots:
                logger.error('Spots data is missing')
                return JsonResponse({'error': 'Missing "spots" data in request'}, status=400)
            if not image_src:
                logger.error('Image source is missing')
                return JsonResponse({'error': 'Missing "imageSrc" data in request'}, status=400)

            # Check for the presence of both min and max lines
            min_line = next((line for line in lines if line['type'] == 'min'), None)
            max_line = next((line for line in lines if line['type'] == 'max'), None)

            if not min_line or not max_line:
                logger.error('Min or Max line is missing')
                return JsonResponse({'error': 'Please add "min" and "max" lines.'}, status=400)

            # Construct the image path and ensure the image exists
            #image_src = image_src.strip('/')  # Remove leading slashes
            image_path = os.path.join(settings.MEDIA_ROOT, image_src)  # Cross-platform path
            if not os.path.exists(image_path):
                logger.error(f'Image not found at path: {image_path}')
                return JsonResponse({'error': f'Image not found: {image_path}'}, status=400)

            # Load the image using OpenCV
            image = cv2.imread(image_path)
            if image is None:
                logger.error(f'Failed to load image from path: {image_path}')
                return JsonResponse({'error': 'Failed to read the image file.'}, status=400)

            # Get the original image dimensions
            original_height, original_width = image.shape[:2]

            # Calculate RF values based on spot positions
            min_y = min_line['y']
            max_y = max_line['y']

            rf_values_for_csv = []
            for spot in spots:
                x = spot['x']
                y = spot['y']

                # Calculate the RF value for the spot
                rf_value = (y - min_y) / (max_y - min_y)
                rf_value = round(rf_value, 2)  # Round to two decimal places

                # Skip invalid RF values (outside the range 0.0 to 1.0)
                if rf_value <= 0.0 or rf_value >= 1.0:
                    continue

                # Find closest vertical and horizontal lines for annotation
                closest_vertical_line = min(
                    (line for line in lines if line['type'] == 'vertical'),
                    key=lambda line: abs(line.get('x', 0) - x),
                    default=None
                )
                closest_horizontal_line = min(
                    (line for line in lines if line['type'] == 'horizontal'),
                    key=lambda line: abs(line.get('y', 0) - y),
                    default=None
                )

                solvent = closest_vertical_line['text'] if closest_vertical_line else 'Unknown'
                compound = closest_horizontal_line['text'] if closest_horizontal_line else 'Unknown'

                # Add the RF value, solvent, and compound to the CSV list
                rf_values_for_csv.append({
                    "compound": compound,
                    "solvent": solvent,
                    "rf": rf_value
                })

                # Annotate the image with the calculated RF value
                cv2.circle(image, (int(x), int(y)), 10, (0, 165, 255), -1)  # Draw circle at spot location
                cv2.putText(image, f'RF={rf_value}', (int(x)+5, int(y)-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                cv2.putText(image, f'{compound}', (int(x)+5, int(y)+10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)
                cv2.putText(image, f'{solvent}', (int(x)+5, int(y)+25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

            # Save the annotated image
            annotated_image_name = f'annotated_{os.path.basename(image_src)}'
            annotated_image_path = os.path.join(settings.MEDIA_ROOT, annotated_image_name)
            cv2.imwrite(annotated_image_path, image)

            # Save RF values to a CSV file
            csv_name = f'rf_values_{os.path.splitext(os.path.basename(image_src))[0]}.csv'
            csv_path = os.path.join(settings.MEDIA_ROOT, csv_name)
            with open(csv_path, 'w', newline='') as csvfile:
                fieldnames = ['Solvent (X value)', 'Compound (Y value)', 'RF value']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for value in rf_values_for_csv:
                    writer.writerow({
                        'Solvent (X value)': value['solvent'],
                        'Compound (Y value)': value['compound'],
                        'RF value': value['rf']
                    })

            # Return URLs for the annotated image and CSV
            annotated_image_url = settings.MEDIA_URL + annotated_image_name
            csv_url = settings.MEDIA_URL + csv_name

            return JsonResponse({
                'rf_values': rf_values_for_csv,
                'annotated_image_url': annotated_image_url,
                'csv_url': csv_url
            })

        except json.JSONDecodeError:
            logger.error('Invalid JSON format received')
            return JsonResponse({'error': 'Invalid JSON format'}, status=400)

    return JsonResponse({'error': 'Invalid request'}, status=400)

@csrf_exempt
def fetch_and_save_image(request):
    """
    Fetch and save an image from a remote URL.

    This view fetches an image from the URL provided as a GET parameter,
    temporarily saves it to the server, and returns the file path.

    Args:
        request (HttpRequest): The HTTP request object containing the 'url' parameter.

    Returns:
        JsonResponse: A JSON response containing the local path of the saved image
                      or an error message if the fetch fails.
    """
    # Extract the image URL from the request
    image_url = request.GET.get('url')
    
    # Attempt to fetch the image from the remote URL
    response = requests.get(image_url)
    
    if response.status_code == 200:
        # If the request is successful, save the image to a temporary file
        temp_dir = tempfile.gettempdir()  # Get the system's temporary directory
        temp_file_path = os.path.join(settings.MEDIA_ROOT, 'downloaded_image.png')  # Set file name and path
        
        # Write the image content to the temp file
        with open(temp_file_path, 'wb') as temp_file:
            temp_file.write(response.content)
        
        # Return the file path of the saved image
        return JsonResponse({'imagePath': temp_file_path})
    else:
        # Return an error message if the image fetch fails
        return JsonResponse({'error': 'Failed to fetch image'}, status=400)


def serve_image(request):
    """
    Serve an image file to the client.

    This view reads an image from the local file system, given its path in the
    'file_path' GET parameter, and serves it as a PNG image.

    Args:
        request (HttpRequest): The HTTP request object containing the 'file_path' parameter.

    Returns:
        HttpResponse: A response containing the image file's binary content and correct
                      content type or an error message if the file is not found.
    """
    # Get the file path from the request
    file_path = request.GET.get('file_path')
    
    # Check if the file exists at the specified path
    if os.path.exists(file_path):
        # Serve the image if found
        with open(file_path, 'rb') as f:
            return HttpResponse(f.read(), content_type="image/png")
    else:
        # Return an error if the file does not exist
        return JsonResponse({'error': 'File not found'}, status=404)

