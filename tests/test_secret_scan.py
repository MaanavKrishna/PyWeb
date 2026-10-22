"""pyweb check finds credentials written into source files."""

import subprocess
import sys

import pytest

from pyweb.secret_scan import entropy, scan_app, scan_text

# Built at run time so no key-shaped literal sits in this file.
FAKE = {
    "AWS access key": "AKIA" + "QX7Z" * 4,
    "GitHub token": "ghp_" + "aB3dE6gH9k" * 4,
    "Anthropic API key": "sk-ant-" + "api03-" + "Zq8wX2" * 6,
    "OpenAI API key": "sk-proj-" + "Lm4Nq7Rt0V" * 4,
    "Stripe key": "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc",
    "Slack token": "xox" + "b-1234567890-" + "abcdefghij",
    "Google API key": "AIza" + "SyD3k9" * 5 + "abcde",
    "private key": "-----BEGIN " + "RSA PRIVATE KEY-----",
}


@pytest.mark.parametrize("label", sorted(FAKE))
def test_known_formats_are_found_and_masked(label):
    value = FAKE[label]
    (hit,) = scan_text(f'KEY = "{value}"\n', "app.pyweb")
    assert hit["kind"] == "hardcoded-secret" and hit["line"] == 1 and label in hit["message"]
    assert value not in hit["message"]


def test_random_values_in_secret_names_are_warnings():
    token = "q8Zr2Lw9Xc4Vb7Nm1Kj6Hg3F"
    (hit,) = scan_text(f'stripe_api_key = "{token}"\n')
    assert hit["kind"] == "possible-secret" and token not in hit["message"]
    for fine in ('api_key = os.environ["API_KEY"]', 'password = ""', 'token_url = "https://example.com/token"',
                 'api_key = "your-api-key-goes-here"', 'title = "q8Zr2Lw9Xc4Vb7Nm1Kj6Hg3F"',
                 'secret = "aaaaaaaaaaaaaaaaaaaa"', f'API_KEY = "{token}"  # pyweb: allow-secret'):
        assert scan_text(fine) == [], fine
    assert entropy("aaaa") == 0 and entropy("abcd") == 2


def test_pyweb_check_fails_on_a_key_in_a_file_next_to_the_app(tmp_path):
    app = tmp_path / "app.pyweb"
    app.write_text('from pyweb import App\napp = App()\n@app.page("/")\ndef H():\n    <p>hi</p>\n')
    (tmp_path / "settings.py").write_text(f'AWS = "{FAKE["AWS access key"]}"\n')
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text(f'k = "{FAKE["GitHub token"]}"')
    found = scan_app(str(app))
    assert [f["file"].endswith("settings.py") for f in found] == [True]
    done = subprocess.run([sys.executable, "-m", "pyweb.cli", "check", str(app)], capture_output=True, text=True,
                          cwd=tmp_path)
    assert done.returncode == 1 and "hardcoded-secret settings.py:1: AWS access key" in done.stdout
