"""Read website evidence from registration text without using email domains as sites."""
import re
import urllib.parse

PREFIXED = re.compile(r'(?<![@\w.-])(?:https?://|www\.)[^\s<>\[\]{}|]+', re.I)
BARE = re.compile(r'(?<![@\w.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}(?:/[^\s<>\[\]{}|]*)?', re.I)


def normalize_registered_site(value):
    value = value.strip().rstrip('.,;)')
    if '://' not in value:
        value = 'https://' + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in ('http', 'https') or parsed.username or parsed.password:
        return None
    if not parsed.hostname or not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', parsed.hostname, re.I):
        return None
    try:
        if parsed.port not in (None, 80, 443):
            return None
    except ValueError:
        return None
    if parsed.hostname.endswith(('.gov', '.edu')):
        return None
    # The supplied domain is retained. Transport still enforces public DNS and
    # valid HTTPS before any source capture; this does not verify campaign purpose.
    return urllib.parse.urlunsplit(('https', parsed.hostname.lower(), parsed.path or '/', parsed.query, ''))


def registration_websites(text):
    """Use bare domains only within the labeled Form 1 website field."""
    values = PREFIXED.findall(text)
    label = re.search(r'WEB\s*(?:PAGE|SITE)?\s*ADDRESS\s*(?:\(URL\))?', text, re.I)
    if label:
        block = text[label.end():label.end() + 600]
        end = re.search(r'\n\s*(?:3\.\s*FEC|4\.\s*IS\s+THIS|[23]\.\s*DATE)', block, re.I)
        if end:
            block = block[:end.start()]
        values.extend(BARE.findall(block))
    return sorted({site for value in values if (site := normalize_registered_site(value))})
