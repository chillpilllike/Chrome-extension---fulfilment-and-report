import unittest

from app.db.session import _translate_placeholders


class PostgresPlaceholderTranslationTests(unittest.TestCase):
    def test_apostrophe_in_line_comment_does_not_hide_later_placeholder(self) -> None:
        sql = """
        SELECT CASE WHEN worker_id = ? THEN 1 ELSE 0 END
        -- A worker's own claim should be shown first.
        LIMIT ?
        """

        translated = _translate_placeholders(sql)

        self.assertEqual(translated.count("%s"), 2)
        self.assertIn("worker_id = %s", translated)
        self.assertIn("LIMIT %s", translated)

    def test_question_marks_inside_sql_comments_are_not_placeholders(self) -> None:
        sql = "SELECT value FROM settings WHERE key = ? -- missing? no\nLIMIT ?"

        translated = _translate_placeholders(sql)

        self.assertEqual(translated.count("%s"), 2)
        self.assertIn("missing? no", translated)


if __name__ == "__main__":
    unittest.main()
