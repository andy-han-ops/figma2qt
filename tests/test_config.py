import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

import config  # noqa: E402


def make_qt(root: Path) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "rcc.exe").write_bytes(b"")
    return root


class ProfileValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.qt = make_qt(self.root / "qt")
        self.fonts = self.root / "fonts"
        self.fonts.mkdir()
        (self.fonts / "Sample-Regular.ttf").write_bytes(b"")
        (self.fonts / "Sample-Bold.ttf").write_bytes(b"")
        self.good = {
            "qt": {"prefix": str(self.qt)},
            "font": {"family": "Sample", "dir": str(self.fonts)},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_profile(self):
        self.assertEqual(config.validate_profile(self.good), [])

    def test_each_required_field_is_reported(self):
        for section, key in (("qt", "prefix"), ("font", "family"), ("font", "dir")):
            profile = {k: dict(v) for k, v in self.good.items()}
            del profile[section][key]
            problems = config.validate_profile(profile)
            self.assertTrue(
                any(f"{section}.{key}" in problem for problem in problems),
                f"{section}.{key}: {problems}",
            )

    def test_blank_and_placeholder_count_as_missing(self):
        profile = {
            "qt": {"prefix": ""},
            "font": {"family": "REPLACE_ME", "dir": "  "},
        }
        problems = config.validate_profile(profile)
        self.assertEqual(len(problems), 3)

    def test_non_mapping_profile(self):
        self.assertEqual(len(config.validate_profile(["x"])), 1)

    def test_qt_prefix_without_rcc(self):
        empty = self.root / "empty_qt"
        empty.mkdir()
        self.good["qt"]["prefix"] = str(empty)
        problems = config.validate_profile(self.good)
        self.assertTrue(any("rcc.exe" in problem for problem in problems))

    def test_missing_directories(self):
        self.good["qt"]["prefix"] = str(self.root / "nope")
        self.good["font"]["dir"] = str(self.root / "nofonts")
        problems = config.validate_profile(self.good)
        self.assertEqual(len(problems), 2)

    def test_font_file_globs(self):
        self.good["font"]["files"] = ["Sample-*.ttf"]
        self.assertEqual(config.validate_profile(self.good), [])
        self.good["font"]["files"] = ["Other-*.ttf"]
        problems = config.validate_profile(self.good)
        self.assertTrue(any("font.files" in problem for problem in problems))

    def test_canvas(self):
        self.good["canvas"] = [1280, 720]
        self.assertEqual(config.validate_profile(self.good), [])
        for bad in ([0, 10], [1], "1920x1080", [1.5, 2]):
            self.good["canvas"] = bad
            self.assertTrue(config.validate_profile(self.good), bad)

    def test_weights_keys_must_be_numeric(self):
        self.good["font"]["weights"] = {400: "Regular", "bold": "Bold"}
        problems = config.validate_profile(self.good)
        self.assertTrue(any("font.weights" in problem for problem in problems))


class SettingsAndTemplates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "proj"
        self.project.mkdir()
        qt = make_qt(self.root / "qt")
        fonts = self.root / "fonts"
        fonts.mkdir()
        (fonts / "Sample-Regular.ttf").write_bytes(b"")
        self.profile = {
            "qt": {"prefix": str(qt)},
            "font": {
                "family": "Sample",
                "dir": str(fonts),
                "weights": {400: "Regular", 700: "Bold"},
            },
            "canvas": [800, 600],
            "assets": {"search_dirs": ["res"]},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write_profile(self):
        path = config.default_profile_path(self.project)
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(self.profile), encoding="utf-8")
        return path

    def test_build_settings(self):
        path = self.write_profile()
        settings = config.build_settings(self.project)
        self.assertEqual(settings.profile_path, path.resolve())
        self.assertEqual(settings.font_family, "Sample")
        self.assertEqual(settings.font_files, [])  # no globs -> qt_preview scans the dir
        self.assertEqual(settings.rcc_exe.name, "rcc.exe")
        self.assertEqual(settings.work_dir, (self.root / "figma2qt-work").resolve())

    def test_work_dir_from_profile(self):
        self.profile["work_dir"] = "out/work"
        self.write_profile()
        settings = config.build_settings(self.project)
        self.assertEqual(settings.work_dir, (self.project / "out" / "work").resolve())

    def test_profile_defaults_maps_canvas_and_hides_qt(self):
        self.write_profile()
        defaults = config.profile_defaults(config.build_settings(self.project).profile)
        self.assertEqual(defaults["target_size"], [800, 600])
        self.assertNotIn("qt", defaults)
        self.assertEqual(defaults["font"]["maps"], [])
        self.assertEqual(defaults["assets"]["search_dirs"], ["res"])

    def test_font_weights_text(self):
        text = config.font_weights_text({"weights": {700: "Bold", 400: "Regular"}})
        self.assertLess(text.index("400"), text.index("700"))
        self.assertIn("no mapping configured", config.font_weights_text({}))

    def test_missing_profile_raises(self):
        with self.assertRaises(config.ProfileError):
            config.build_settings(self.project)

    def test_init_templates_must_be_filled_in(self):
        results = config.write_templates(self.project)
        self.assertEqual({item["status"] for item in results}, {"written"})
        with self.assertRaises(config.ProfileError) as caught:
            config.build_settings(self.project)
        joined = " ".join(caught.exception.problems)
        for field in ("qt.prefix", "font.family", "font.dir"):
            self.assertIn(field, joined)

    def test_init_keeps_existing_files(self):
        config.write_templates(self.project)
        again = config.write_templates(self.project)
        self.assertEqual({item["status"] for item in again}, {"exists (kept)"})

    def test_find_project_root(self):
        self.write_profile()
        nested = self.project / "a" / "b"
        nested.mkdir(parents=True)
        self.assertEqual(config.find_project_root(nested), self.project.resolve())


if __name__ == "__main__":
    unittest.main()
