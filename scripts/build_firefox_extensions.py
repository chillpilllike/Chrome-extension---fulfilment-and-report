from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "firefox-extensions"
DIST_DIR = OUT_DIR / "dist"

FIREFOX_BUILDS = [
    {
        "source": "chrome-extension",
        "target": "fulfilment",
        "name_suffix": "Firefox",
        "gecko_id": "fulfilment@nutricity.local",
    },
    {
        "source": "tracking-extension",
        "target": "tracking",
        "name_suffix": "Firefox",
        "gecko_id": "tracking@nutricity.local",
    },
    {
        "source": "manual-order-match-extension",
        "target": "manual-order-match",
        "name_suffix": "Firefox",
        "gecko_id": "manual-order-match@nutricity.local",
    },
    {
        "source": "amazon-invoice-extension",
        "target": "amazon-invoice",
        "name_suffix": "Firefox",
        "gecko_id": "amazon-invoice@nutricity.local",
    },
    {
        "source": "epost-extension",
        "target": "epost",
        "name_suffix": "Firefox",
        "gecko_id": "epost@nutricity.local",
    },
]

FIREFOX_API_SHIM = """/* Firefox build: prefer Firefox's Promise-based browser namespace. */
const chrome = globalThis.browser || globalThis.chrome;

"""


def ignore_generated(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in {".DS_Store", "__MACOSX"}}


def copy_source(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=ignore_generated)


def patch_manifest(manifest_path: Path, build: dict[str, str]) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    name_suffix = build["name_suffix"]
    if name_suffix not in manifest["name"]:
        manifest["name"] = f"{manifest['name']} ({name_suffix})"
    manifest["description"] = manifest.get("description", "").replace("Chrome-based", "Firefox-compatible browser-based")
    manifest["background"] = {"scripts": ["background.js"]}
    manifest["browser_specific_settings"] = {
        "gecko": {
            "id": build["gecko_id"],
            "strict_min_version": "128.0",
            "data_collection_permissions": {"required": ["none"]},
        }
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def patch_javascript(file_path: Path) -> None:
    text = file_path.read_text(encoding="utf-8")
    if "const chrome = globalThis.browser || globalThis.chrome;" in text:
        return
    if "chrome." not in text:
        return
    file_path.write_text(FIREFOX_API_SHIM + text, encoding="utf-8")


def patch_extension_readme(readme_path: Path) -> None:
    if not readme_path.exists():
        return
    text = readme_path.read_text(encoding="utf-8")
    text = text.replace("Chrome Extension", "Firefox Extension")
    text = text.replace("Chrome extension", "Firefox extension")
    text = text.replace("Chrome-based", "Firefox-compatible browser-based")
    text = text.replace("Open Chrome and go to `chrome://extensions`.", "Open Firefox and go to `about:debugging#/runtime/this-firefox`.")
    text = text.replace("Enable **Developer mode**.\n4. Click **Load unpacked**.", "Click **Load Temporary Add-on...**.")
    text = text.replace("Log in to Amazon in Chrome.", "Log in to Amazon in Firefox.")
    text = text.replace("same Chrome profile", "same Firefox profile")
    readme_path.write_text(text, encoding="utf-8")


def patch_firefox_wording(file_path: Path) -> None:
    if file_path.suffix.lower() not in {".js", ".html", ".css", ".md"}:
        return
    text = file_path.read_text(encoding="utf-8")
    patched = text.replace("Chrome", "Firefox")
    if patched != text:
        file_path.write_text(patched, encoding="utf-8")


def write_readme() -> None:
    readme = """# Nutricity Firefox Extensions

These folders are generated from the Chrome extension sources by:

```sh
python3 scripts/build_firefox_extensions.py
```

Load a Firefox extension manually:

1. Open Firefox.
2. Go to `about:debugging#/runtime/this-firefox`.
3. Click **Load Temporary Add-on...**.
4. Select the `manifest.json` inside one of these folders:
   - `firefox-extensions/fulfilment`
   - `firefox-extensions/tracking`
   - `firefox-extensions/manual-order-match`
   - `firefox-extensions/amazon-invoice`
   - `firefox-extensions/epost`

Packaged ZIP files are in `firefox-extensions/dist` for easier sharing or signing.
"""
    (OUT_DIR / "README.md").write_text(readme, encoding="utf-8")


def zip_extension(target: Path) -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / f"{target.name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(target.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(target))


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    for build in FIREFOX_BUILDS:
        source = ROOT / build["source"]
        target = OUT_DIR / build["target"]
        copy_source(source, target)
        patch_manifest(target / "manifest.json", build)
        patch_extension_readme(target / "README.md")
        for text_file in target.rglob("*"):
            if text_file.is_file():
                patch_firefox_wording(text_file)
        for js_file in target.rglob("*.js"):
            patch_javascript(js_file)
        zip_extension(target)
    write_readme()
    print(f"Built {len(FIREFOX_BUILDS)} Firefox extensions in {OUT_DIR}")


if __name__ == "__main__":
    main()
