"""Timeout policy whose answer requires reading constants.py too."""

from .constants import BASE_TIMEOUT_SECONDS, ENV_MULTIPLIERS


def timeout_for(environment: str) -> int:
    return BASE_TIMEOUT_SECONDS * ENV_MULTIPLIERS[environment]
