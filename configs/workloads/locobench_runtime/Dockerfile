FROM python:3.10.16-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash git patch ripgrep build-essential libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/miniconda3/envs/testbed

COPY requirements.txt /tmp/locobench-requirements.txt
RUN /opt/miniconda3/envs/testbed/bin/python -m pip install --no-cache-dir \
    -r /tmp/locobench-requirements.txt \
    && /opt/miniconda3/envs/testbed/bin/python -m pip check \
    && rm /tmp/locobench-requirements.txt

ENV KIVY_NO_ARGS=1 \
    KIVY_NO_CONSOLELOG=1 \
    MPLBACKEND=Agg
