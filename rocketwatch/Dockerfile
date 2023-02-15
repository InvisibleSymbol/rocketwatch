# syntax=docker/dockerfile:1
FROM python:3.11.2

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . ./app
ENV PYTHONUNBUFFERED=1
WORKDIR ./app
CMD [ "python", "." ]
