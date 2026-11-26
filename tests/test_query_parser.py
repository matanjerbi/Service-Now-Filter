import unittest

from ticket_radar.search import hebrew, query_parser


class HebrewTests(unittest.TestCase):
    def test_document_forms_strip_prefix_and_suffix(self):
        forms = hebrew.document_forms("פתיחת פורט לשרתי Redis").split()
        self.assertIn("שרתי", forms)
        self.assertIn("שרת", forms)
        self.assertNotIn("Redis", forms)

    def test_double_prefix(self):
        self.assertIn("בית", hebrew.document_forms("מהבית").split())

    def test_query_variants_keep_short_roots(self):
        # "בקשה" starts with a prefix letter, but stripping it would give "קשה" (a different word).
        self.assertNotIn("קשה", hebrew.query_variants("בקשה"))
        self.assertIn("בקש", hebrew.query_variants("בקשה"))
        self.assertIn("שרתים", hebrew.query_variants("לשרתים"))

    def test_non_hebrew_untouched(self):
        self.assertEqual(hebrew.query_variants("redis"), ["redis"])


class QueryParserTests(unittest.TestCase):
    def test_words_become_prefix_terms(self):
        q = query_parser.parse("redis memory")
        self.assertEqual(q.match_all(), '("redis"* AND "memory"*)')
        self.assertEqual(q.match_any(), '("redis"* OR "memory"*)')

    def test_phrase_and_exclusion(self):
        q = query_parser.parse('"too many connections" -role')
        self.assertEqual(q.match_all(), '("too many connections") NOT "role"*')
        self.assertTrue(q.single_term)

    def test_punctuation_only_tokens_are_ignored(self):
        self.assertTrue(query_parser.parse('- " ** ').empty)

    def test_quotes_inside_words_cannot_break_fts_syntax(self):
        q = query_parser.parse('ab"c')
        self.assertEqual(q.include, ('"ab c"*',))

    def test_ticket_numbers(self):
        self.assertTrue(query_parser.is_ticket_number("INC0012345"))
        self.assertTrue(query_parser.is_ticket_number(" ritm001 "))
        self.assertFalse(query_parser.is_ticket_number("redis 6379"))


if __name__ == "__main__":
    unittest.main()
