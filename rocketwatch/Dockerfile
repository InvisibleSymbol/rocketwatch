# syntax=docker/dockerfile:1
FROM python:3.10.1-slim

RUN apt-get update
RUN apt-get install gcc git -y

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

ENV PYTHONUNBUFFERED=1
WORKDIR ./app
CMD [ "python", "." ]
