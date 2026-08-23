import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()


class TrackingBrowserlessImageTests(unittest.TestCase):
    def test_production_image_installs_playwright_chromium(self) -> None:
        self.assertIn("python -m playwright install --with-deps chromium", DOCKERFILE)


if __name__ == "__main__":
    unittest.main()
