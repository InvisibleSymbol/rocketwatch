# syntax=docker/dockerfile:1
FROM python:3.10.3

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

ENV PYTHONUNBUFFERED=1
WORKDIR ./app
CMD [ "python", "." ]
