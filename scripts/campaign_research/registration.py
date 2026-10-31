"""Read website evidence from registration text without using email domains as sites."""
import re
import urllib.parse
from html.parser import HTMLParser

PREFIXED = re.compile(r'(?<![@\w.-])(?:https?://|www\.)[^\s<>\[\]{}|]+', re.I)
BARE = re.compile(r'(?<![@\w.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}(?:/[^\s<>\[\]{}|]*)?', re.I)


def authorizing_candidate_ids(body):
    """Read explicitly labeled FEC authorizing-candidate links, without fetching them.

    Callers must require the unique expected ID from their independently matched
    candidate/committee record before using a registration website as evidence.
    Unlabeled navigation links do not establish that relationship.
    """
    class AuthorizingLinks(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.href = None
            self.label = []
            self.ids = set()

        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                self.href = dict(attrs).get('href')
                self.label = []

        def handle_data(self, data):
            if self.href is not None:
                self.label.append(data)

        def handle_endtag(self, tag):
            if tag != 'a':
                return
            if self.href is not None and re.search(r'\bauthorizing\s+candidates?\b', ' '.join(self.label), re.I):
                url = urllib.parse.urlsplit(urllib.parse.urljoin('https://www.fec.gov/', self.href))
                try:
                    allowed = (url.scheme == 'https' and url.hostname in ('fec.gov', 'www.fec.gov')
                               and not url.username and not url.password and url.port in (None, 443))
                except ValueError:
                    allowed = False
                match = re.fullmatch(r'/data/candidate/([HS][0-9A-Z]{8})/?', url.path)
                if allowed and match:
                    self.ids.add(match.group(1))
            self.href = None
            self.label = []

    parser = AuthorizingLinks()
    parser.feed(body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body)
    parser.close()
    return sorted(parser.ids)


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
