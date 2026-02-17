FROM python:3.11
RUN apt-get update --fix-missing && \
    pip3 install Flask
COPY service /service
RUN chmod +x /service/app.py