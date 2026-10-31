import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from campaign_research.registration import authorizing_candidate_ids, normalize_registered_site, registration_websites


class RegistrationTests(unittest.TestCase):
    def test_authorizing_link_binds_the_explicit_official_candidate(self):
        body = b'<a href="/data/candidate/H6CA01001/?cycle=2026"><h5>SMITH, JANE</h5><span>Authorizing candidate</span></a>'
        self.assertEqual(authorizing_candidate_ids(body), ['H6CA01001'])

    def test_unlabeled_links_cannot_establish_authorizing_relationship(self):
        self.assertEqual(authorizing_candidate_ids('<a href="/data/candidate/H6CA01001/">Other candidate</a>'), [])

    def test_multiple_authorizing_ids_are_exposed_for_caller_to_hold(self):
        body = ''.join(f'<a href="https://www.fec.gov/data/candidate/{cid}/">Authorizing candidate</a>'
                       for cid in ('H6CA01001', 'H6CA01002', 'H6CA01001'))
        self.assertEqual(authorizing_candidate_ids(body), ['H6CA01001', 'H6CA01002'])

    def test_untrusted_authorizing_link_destinations_are_rejected(self):
        for url in ('https://example.org/data/candidate/H6CA01001/',
                    'https://user@www.fec.gov/data/candidate/H6CA01001/',
                    'http://www.fec.gov/data/candidate/H6CA01001/',
                    'https://www.fec.gov:8443/data/candidate/H6CA01001/'):
            self.assertEqual(authorizing_candidate_ids(f'<a href="{url}">Authorizing candidate</a>'), [])

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
