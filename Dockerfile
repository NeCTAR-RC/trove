FROM ubuntu:20.04

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

RUN groupadd --gid 1000 trove && useradd -l -M --shell /usr/sbin/nologin --uid 1000 --gid 1000 trove

# Install pip requirements
COPY requirements.txt .

RUN apt update && apt install -y gcc git curl python3-pip && apt clean

RUN pip install -c https://opendev.org/openstack/requirements/raw/tag/ussuri-eol/upper-constraints.txt -r requirements.txt

WORKDIR /app
COPY . /app

RUN pip install -c https://opendev.org/openstack/requirements/raw/tag/ussuri-eol/upper-constraints.txt -e /app

USER trove
