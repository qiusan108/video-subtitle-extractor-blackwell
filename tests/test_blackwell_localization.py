import configparser
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class BlackwellLocalizationTests(unittest.TestCase):
    def test_fresh_install_uses_existing_chinese_translation(self):
        source = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            re.compile(r'interface\s*=\s*OptionsConfigItem\([^\n]*"ch"'),
        )
        self.assertTrue((ROOT / "backend" / "interface" / "ch.ini").is_file())

    def test_all_interface_languages_have_language_switch_messages(self):
        for language in ("ch", "chinese_cht", "en", "japan", "ko", "vi", "es", "tr"):
            with self.subTest(language=language):
                parser = configparser.ConfigParser()
                parser.read(
                    ROOT / "backend" / "interface" / f"{language}.ini",
                    encoding="utf-8",
                )
                section = parser["LanguageModeGUI"]
                self.assertTrue(section["InterfaceLanguage"])
                self.assertTrue(section["InterfaceLanguageDesc"])
                self.assertTrue(section["RestartUpdatedTitle"])
                self.assertTrue(section["RestartRequiredDesc"])

    def test_readmes_link_to_each_other(self):
        chinese = (ROOT / "README.md").read_text(encoding="utf-8")
        english = (ROOT / "README_en.md").read_text(encoding="utf-8")
        self.assertIn("README_en.md", chinese)
        self.assertIn("README.md", english)


if __name__ == "__main__":
    unittest.main()
