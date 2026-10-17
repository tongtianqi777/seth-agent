import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BRAND = REPO_ROOT / "brand"
DASHBOARD = REPO_ROOT / "src" / "seth_agent" / "dashboard"


def test_every_logo_in_the_manifest_exists():
    manifest = json.loads((BRAND / "brand.json").read_text())
    assert manifest["logos"]
    for name, logo in manifest["logos"].items():
        assert (BRAND / logo["file"]).is_file(), f"{name}: {logo['file']} is missing"
        assert logo["use"], f"{name} has no 'use' line"


def test_dashboard_logos_match_the_brand_kit():
    pairs = {
        "logos/logo-dark.png": "seth-logo-dark.png",
        "logos/crest.png": "seth-crest.png",
    }
    for brand_file, dashboard_file in pairs.items():
        assert (BRAND / brand_file).read_bytes() == (DASHBOARD / dashboard_file).read_bytes(), (
            f"{dashboard_file} has drifted from brand/{brand_file}"
        )


def test_the_flyer_layout_is_where_the_manifest_says():
    manifest = json.loads((BRAND / "brand.json").read_text())
    assert (BRAND / manifest["templates_dir"] / "event.html").is_file()


def test_the_slogan_and_address_are_exactly_the_schools():
    manifest = json.loads((BRAND / "brand.json").read_text())
    slogan = manifest["slogan"]
    assert slogan["text"] == "Christ-centered education • Unity among family, school, & church • Building Christian character"
    assert " • ".join(slogan["parts"]) == slogan["text"]
    assert manifest["address"]["text"] == "1395 Hollenbeck Ave, Sunnyvale, CA 94087"
    assert slogan["use"] and manifest["address"]["use"]
