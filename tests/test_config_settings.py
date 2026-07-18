import os
import tempfile
import unittest
from unittest.mock import patch

import config
from src.settings_router import Settings, get_settings, save_settings


class ConfigDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.original_cache = config._config_cache.copy()
        self.original_persisted = config._persisted_config.copy()
        self.original_path = config._CONFIG_JSON_PATH

    def tearDown(self):
        config._config_cache = self.original_cache
        config._persisted_config = self.original_persisted
        config._CONFIG_JSON_PATH = self.original_path

    def test_secure_china_defaults(self):
        self.assertEqual(config._DEFAULT_CONFIG["CODEBUDDY_SITE"], "china")
        self.assertEqual(config._DEFAULT_CONFIG["CODEBUDDY_BARK_URL"], "")
        self.assertNotIn("CODEBUDDY_SSL_VERIFY", config._DEFAULT_CONFIG)

    def test_removed_persisted_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.json")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    '{"CODEBUDDY_SITE":"china",'
                    '"CODEBUDDY_SSL_VERIFY":"false",'
                    '"REMOVED_SETTING":"value"}'
                )

            config._CONFIG_JSON_PATH = config_path
            with patch.dict(os.environ, {}, clear=True):
                current_directory = os.getcwd()
                try:
                    os.chdir(directory)
                    config.load_config()
                finally:
                    os.chdir(current_directory)

            active = config.get_active_config()
            self.assertEqual(active["CODEBUDDY_SITE"], "china")
            self.assertNotIn("CODEBUDDY_SSL_VERIFY", active)
            self.assertNotIn("REMOVED_SETTING", config._config_cache)


class SettingsRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_cache = config._config_cache.copy()
        self.original_persisted = config._persisted_config.copy()
        self.original_path = config._CONFIG_JSON_PATH
        self.temporary_directory = tempfile.TemporaryDirectory()
        config._CONFIG_JSON_PATH = os.path.join(self.temporary_directory.name, "config.json")
        config._config_cache = config._DEFAULT_CONFIG.copy()
        config._persisted_config = {}

    def tearDown(self):
        config._config_cache = self.original_cache
        config._persisted_config = self.original_persisted
        config._CONFIG_JSON_PATH = self.original_path
        self.temporary_directory.cleanup()

    async def test_settings_metadata_marks_environment_fields_readonly(self):
        response = await get_settings(_token="test")

        self.assertIn("CODEBUDDY_CREDS_DIR", response["readonly_keys"])
        self.assertIn("WORKBUDDY_CREDS_DIR", response["readonly_keys"])
        self.assertIn("CODEBUDDY_LOG_LEVEL", response["readonly_keys"])
        self.assertIn("CODEBUDDY_HOST", response["readonly_keys"])
        self.assertIn("CODEBUDDY_PORT", response["readonly_keys"])

    async def test_save_ignores_environment_only_settings(self):
        response = await save_settings(
            Settings(settings={
                "CODEBUDDY_HOST": "0.0.0.0",
                "CODEBUDDY_SITE": "international",
                "CODEBUDDY_CREDS_DIR": "/tmp/should-not-apply",
            }),
            _token="test",
        )

        self.assertEqual(response["message"], "设置已保存并成功热加载！")
        self.assertEqual(config.get_server_host(), "127.0.0.1")
        self.assertEqual(config.get_codebuddy_site(), "international")
        self.assertEqual(config.get_codebuddy_creds_dir(), ".codebuddy_creds")
        self.assertEqual(config._persisted_config, {"CODEBUDDY_SITE": "international"})


if __name__ == "__main__":
    unittest.main()
