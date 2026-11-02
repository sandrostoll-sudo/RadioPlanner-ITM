"""Verified HTTPS using OS trust plus the Mozilla CA bundle (certifi)."""
import ssl
from urllib.request import urlopen


def verified_context():
    """Retain OS/enterprise CAs, augment with certifi when installed."""
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return context
    context.load_verify_locations(cafile=certifi.where())
    return context


def secure_urlopen(request, timeout=30):
    """Never bypass TLS certificate verification."""
    return urlopen(request, timeout=timeout, context=verified_context())
