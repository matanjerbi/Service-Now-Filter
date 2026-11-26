import tempfile
import unittest
from pathlib import Path

from ticket_radar.config import DEFAULT_TABLES, AuthType, ConfigError, ScopeMode, load_dotenv, load_settings


class LoadSettingsTests(unittest.TestCase):
    def test_defaults(self):
        s = load_settings({}, base_dir=Path("/data"))
        self.assertFalse(s.servicenow.configured)
        self.assertEqual(s.servicenow.tables, DEFAULT_TABLES)
        self.assertIs(s.servicenow.auth.type, AuthType.BASIC)
        self.assertIs(s.servicenow.scope.mode, ScopeMode.ALL)
        self.assertEqual(s.server.port, 8765)
        self.assertEqual(s.server.db_path, Path("/data/tickets.db"))

    def test_parses_all_types(self):
        s = load_settings({
            "SN_INSTANCE": "corp.service-now.com/",
            "SN_AUTH_TYPE": "API_KEY",
            "SN_API_KEY": "k",
            "SN_TABLES": "incident | problem",
            "SN_SCOPE": "groups",
            "SN_SCOPE_GROUPS": "DBA - Redis, Network",
            "SN_VERIFY_SSL": "false",
            "SN_PAGE_SIZE": "200",
            "TR_DB_PATH": "/abs/x.db",
        })
        sn = s.servicenow
        self.assertEqual(sn.base_url, "https://corp.service-now.com")
        self.assertIs(sn.auth.type, AuthType.API_KEY)
        self.assertEqual(sn.tables, ("incident", "problem"))
        self.assertEqual(sn.scope.groups, ("DBA - Redis", "Network"))
        self.assertFalse(sn.verify_ssl)
        self.assertEqual(sn.page_size, 200)
        self.assertEqual(s.server.db_path, Path("/abs/x.db"))

    def test_empty_values_fall_back_to_defaults(self):
        s = load_settings({"SN_TABLES": "", "TR_PORT": "  "})
        self.assertEqual(s.servicenow.tables, DEFAULT_TABLES)
        self.assertEqual(s.server.port, 8765)

    def test_invalid_values_raise(self):
        for env in ({"SN_AUTH_TYPE": "kerberos"}, {"TR_PORT": "abc"}, {"SN_VERIFY_SSL": "maybe"},
                    {"SN_PAGE_SIZE": "0"}, {"SN_SCOPE": "groups"}):
            with self.subTest(env=env), self.assertRaises(ConfigError):
                load_settings(env)

    def test_secrets_are_masked_in_describe_and_repr(self):
        s = load_settings({"SN_INSTANCE": "x", "SN_PASSWORD": "hunter2"})
        self.assertNotIn("hunter2", s.describe())
        self.assertNotIn("hunter2", repr(s))
        self.assertIn("password", s.describe())


class DotenvTests(unittest.TestCase):
    def test_parsing_and_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# comment\n\nSN_INSTANCE=https://a\nexport SN_USERNAME='matan'\n"
                'SN_PASSWORD="p #1"\nSN_EXTRA_QUERY=priority<=2  # trailing comment\nBROKEN LINE\n',
                encoding="utf-8")
            env = {"SN_INSTANCE": "https://from-real-env"}
            loaded = load_dotenv(path, env)
        self.assertNotIn("SN_INSTANCE", loaded)
        self.assertEqual(env["SN_INSTANCE"], "https://from-real-env")
        self.assertEqual(env["SN_USERNAME"], "matan")
        self.assertEqual(env["SN_PASSWORD"], "p #1")
        self.assertEqual(env["SN_EXTRA_QUERY"], "priority<=2")

    def test_missing_file_is_fine(self):
        self.assertEqual(load_dotenv(Path("/nonexistent/.env"), {}), [])


if __name__ == "__main__":
    unittest.main()
