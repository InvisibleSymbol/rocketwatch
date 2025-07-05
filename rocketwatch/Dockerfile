# syntax=docker/dockerfile:1
FROM python:3.13.5

COPY requirements.txt requirements.txt
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . /app
ENV PYTHONUNBUFFERED=1
ENV MULTICALL_PROCESSES=11
WORKDIR /app
CMD [ "python", "." ]
