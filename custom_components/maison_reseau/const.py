"""Constants for Maison Réseau."""

from datetime import timedelta

DOMAIN = "maison_reseau"
CONF_HOST = "host"
CONF_PASSWORD = "password"
DEFAULT_HOST = "192.168.1.1"

STATUS_INTERVAL = timedelta(minutes=1)
SECURITY_INTERVAL = timedelta(minutes=15)
