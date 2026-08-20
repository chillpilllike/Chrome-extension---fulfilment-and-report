from pathlib import Path
import unittest


class TrackerThumbnailRefreshGuardTests(unittest.TestCase):
    def test_cached_thumbnail_completion_clears_loading_state(self):
        source = (Path(__file__).parents[1] / "frontend" / "src" / "App.tsx").read_text()
        component = source.split("function DispatchProductThumb(", 1)[1].split(
            "function DispatchProductThumbs(", 1
        )[0]

        self.assertIn("element?.complete", component)
        self.assertIn("element.naturalWidth > 0", component)
        self.assertIn("setLoaded(true)", component)
        self.assertIn("ref={imageElement}", component)


if __name__ == "__main__":
    unittest.main()
