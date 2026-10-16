import copy
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grow_static

class ContractParityTests(unittest.TestCase):
    def setUp(self):
        self.article = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/sample-a.json').read_text(encoding='utf-8'))

    def test_duplicate_claim_references_rejected(self):
        for section in ('news', 'technology'):
            with self.subTest(section=section):
                article = copy.deepcopy(self.article)
                article['sections'][section]['claim_ids'] *= 2
                with self.assertRaises(ValueError):
                    grow_static.validate_edition(article)

    def test_repository_url_must_match_name(self):
        for url in ('https://example.org/android/nowinandroid', 'https://github.com/android/wrong', 'https://github.com/android/nowinandroid?download=1'):
            with self.subTest(url=url):
                article = copy.deepcopy(self.article)
                article['sections']['github']['url'] = url
                with self.assertRaises(ValueError):
                    grow_static.validate_edition(article)

    def test_official_source_anchor_is_valid(self):
        self.article['sources'][0]['url'] += '#separation-of-concerns'
        grow_static.validate_edition(self.article)

if __name__ == '__main__':
    unittest.main()
