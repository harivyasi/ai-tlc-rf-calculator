# Use the official Python image from the Docker Hub
FROM python:3.9

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory
WORKDIR /code

# Install openGL library
RUN apt-get update && apt-get install -y libgl1-mesa-glx libglib2.0-0

# Install dependencies
COPY requirements.txt /code/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy the project
COPY . /code/

# Collect static files
RUN python manage.py collectstatic --noinput

# Expose the port the app runs on
EXPOSE 8050

# Run the application
CMD ["gunicorn", "rf_calculator.wsgi:application", "--bind", "0.0.0.0:8050"]