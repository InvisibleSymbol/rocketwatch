# syntax=docker/dockerfile:1
FROM python:3.10.8

# install stupid rust for safetensors
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y 
RUN pip install setuptools-rust

COPY requirements.txt requirements.txt
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . ./app
ENV PYTHONUNBUFFERED=1
WORKDIR ./app
CMD [ "python", "." ]
