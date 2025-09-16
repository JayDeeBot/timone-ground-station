#!/usr/bin/env python3
"""
Fetch front-end vendor assets into static/vendor for offline use.

Usage:
  python tools/fetch_vendors.py

What it does:
- Creates static/vendor/* subfolders
- Downloads specific versions of Bootstrap, Bootstrap Icons, Leaflet (incl. marker images), and Chart.js
- Normalizes Bootstrap Icons CSS font URLs so they resolve correctly
"""

from pathlib import Path
from urllib.request import urlopen
import shutil
import os

# -------- Config: change if your static dir is elsewhere --------
PROJECT_ROOT = Path(__file__).resolve().parents[1]          # TimoneGUI/
STATIC_DIR    = PROJECT_ROOT / "src" / "static"             # <-- correct folder
VENDOR_DIR    = STATIC_DIR / "vendor"
VENDOR_DIR.mkdir(parents=True, exist_ok=True)

# Pin versions (match your HTML/CDN versions)
VERS = {
    "bootstrap": "5.3.0",
    "bootstrap_icons": "1.7.2",
    "leaflet": "1.7.1",
    "chartjs_major": "4",  # major line from jsDelivr
}

# Files to fetch: { local_path (relative to VENDOR_DIR) : url }
ASSETS = {
    # Bootstrap 5.3.0
    f"bootstrap-{VERS['bootstrap']}/css/bootstrap.min.css":
        f"https://cdn.jsdelivr.net/npm/bootstrap@{VERS['bootstrap']}/dist/css/bootstrap.min.css",
    f"bootstrap-{VERS['bootstrap']}/js/bootstrap.bundle.min.js":
        f"https://cdn.jsdelivr.net/npm/bootstrap@{VERS['bootstrap']}/dist/js/bootstrap.bundle.min.js",

    # Bootstrap Icons 1.7.2 – CSS and fonts
    # We will normalize CSS URL paths after download.
    f"bootstrap-icons-{VERS['bootstrap_icons']}/bootstrap-icons.css":
        f"https://cdn.jsdelivr.net/npm/bootstrap-icons@{VERS['bootstrap_icons']}/font/bootstrap-icons.css",
    f"bootstrap-icons-{VERS['bootstrap_icons']}/fonts/bootstrap-icons.woff2":
        f"https://cdn.jsdelivr.net/npm/bootstrap-icons@{VERS['bootstrap_icons']}/font/fonts/bootstrap-icons.woff2",
    f"bootstrap-icons-{VERS['bootstrap_icons']}/fonts/bootstrap-icons.woff":
        f"https://cdn.jsdelivr.net/npm/bootstrap-icons@{VERS['bootstrap_icons']}/font/fonts/bootstrap-icons.woff",

    # Leaflet 1.7.1 – CSS/JS + required marker images
    f"leaflet-{VERS['leaflet']}/leaflet.css":
        f"https://cdn.jsdelivr.net/npm/leaflet@{VERS['leaflet']}/dist/leaflet.css",
    f"leaflet-{VERS['leaflet']}/leaflet.js":
        f"https://cdn.jsdelivr.net/npm/leaflet@{VERS['leaflet']}/dist/leaflet.js",
    f"leaflet-{VERS['leaflet']}/images/marker-icon.png":
        f"https://cdn.jsdelivr.net/npm/leaflet@{VERS['leaflet']}/dist/images/marker-icon.png",
    f"leaflet-{VERS['leaflet']}/images/marker-icon-2x.png":
        f"https://cdn.jsdelivr.net/npm/leaflet@{VERS['leaflet']}/dist/images/marker-icon-2x.png",
    f"leaflet-{VERS['leaflet']}/images/marker-shadow.png":
        f"https://cdn.jsdelivr.net/npm/leaflet@{VERS['leaflet']}/dist/images/marker-shadow.png",

    # Chart.js 4 – UMD bundle
    f"chart.js-{VERS['chartjs_major']}/chart.umd.min.js":
        f"https://cdn.jsdelivr.net/npm/chart.js@{VERS['chartjs_major']}/dist/chart.umd.min.js",
}

def download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)

def normalize_bootstrap_icons_css(css_path: Path):
    """
    Ensure font URLs inside bootstrap-icons.css point to 'fonts/...'
    relative to the CSS file location. Handles './fonts' and '../fonts' cases.
    """
    try:
        text = css_path.read_text(encoding="utf-8")
        # Replace common patterns with 'fonts/...'
        text = text.replace("url(./fonts/", "url(fonts/")
        text = text.replace("url('../fonts/", "url(fonts/")
        text = text.replace('url("../fonts/', 'url(fonts/')
        css_path.write_text(text, encoding="utf-8")
    except Exception as e:
        print(f"[warn] Could not normalize Bootstrap Icons CSS: {e}")

def main():
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Static dir  : {STATIC_DIR}")
    print(f"Vendor dir  : {VENDOR_DIR}\n")

    for rel_path, url in ASSETS.items():
        dest = VENDOR_DIR / rel_path
        print(f"→ {rel_path}\n  {url}")
        try:
            download(url, dest)
        except Exception as e:
            print(f"[error] Failed: {url} -> {dest}\n        {e}")
            continue

    # Post-process Bootstrap Icons CSS so font paths resolve
    bi_css = VENDOR_DIR / f"bootstrap-icons-{VERS['bootstrap_icons']}" / "bootstrap-icons.css"
    if bi_css.exists():
        normalize_bootstrap_icons_css(bi_css)

    print("\nDone. You can now reference files like:")
    print("  {{ url_for('static', filename='vendor/bootstrap-5.3.0/css/bootstrap.min.css') }}")
    print("  {{ url_for('static', filename='vendor/bootstrap-5.3.0/js/bootstrap.bundle.min.js') }}")
    print("  {{ url_for('static', filename='vendor/bootstrap-icons-1.7.2/bootstrap-icons.css') }}")
    print("  {{ url_for('static', filename='vendor/leaflet-1.7.1/leaflet.css') }}")
    print("  {{ url_for('static', filename='vendor/leaflet-1.7.1/leaflet.js') }}")
    print("  {{ url_for('static', filename='vendor/chart.js-4/chart.umd.min.js') }}")

if __name__ == "__main__":
    main()
