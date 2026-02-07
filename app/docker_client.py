"""Singleton do Docker client."""

import docker

_client: docker.DockerClient | None = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def close_client():
    global _client
    if _client:
        _client.close()
        _client = None
