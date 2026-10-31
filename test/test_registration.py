import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from campaign_research.registration import normalize_registered_site, registration_websites


class RegistrationTests(unittest.TestCase):
    def test_uppercase_and_bare_website_fields_are_read_without_email_domains(self):
        text = """COMMITTEE'S E-MAIL ADDRESS
TEAM@EXAMPLE.ORG
COMMITTEE'S WEB PAGE ADDRESS (URL)
VOTEFORJANE.COM
3. FEC IDENTIFICATION NUMBER
C00000000
"""
        self.assertEqual(registration_websites(text), ['https://voteforjane.com/'])
        self.assertEqual(normalize_registered_site('HTTP://WWW.VOTEFORJANE.COM/Issues'), 'https://www.voteforjane.com/Issues')

    def test_explicit_uris_and_bare_domains_are_deduplicated(self):
        text = "COMMITTEE'S WEB PAGE ADDRESS (URL)\nHTTPS://WWW.VOTEFORJANE.COM/\n3. FEC IDENTIFICATION NUMBER\n"
        self.assertEqual(registration_websites(text), ['https://www.voteforjane.com/'])

    def test_no_website_is_inferred_from_email_or_unlabeled_bare_domain(self):
        self.assertEqual(registration_websites('TEAM@CAMPAIGN.EXAMPLE\nMAIL@WWW.OTHER.ORG\nexample.net'), [])
        self.assertIsNone(normalize_registered_site('https://user:password@example.org/'))
        self.assertIsNone(normalize_registered_site('https://www.fec.gov/'))
