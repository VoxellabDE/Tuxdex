# SPDX-License-Identifier: GPL-3.0-or-later
"""Signaturprüfung der Release-Pakete – mit einem Wegwerf-Schlüssel, der nur für den Test erzeugt wird."""
import json
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("gpg"), reason="gpg nicht installiert")


def _make_key(home, name):
    home.mkdir(mode=0o700)
    base = ["gpg", "--homedir", str(home), "--batch", "--no-tty", "--quiet", "--pinentry-mode", "loopback",
            "--passphrase", ""]
    subprocess.run(base + ["--quick-generate-key", name, "ed25519", "sign", "never"], check=True, capture_output=True)
    listing = subprocess.run(base + ["--with-colons", "--fingerprint"], check=True, capture_output=True, text=True).stdout
    fpr = next(l.split(":")[9] for l in listing.splitlines() if l.startswith("fpr:"))
    pub = subprocess.run(base + ["--armor", "--export", fpr], check=True, capture_output=True, text=True).stdout
    return base, fpr, pub


@pytest.fixture(scope="module")
def signed(tmp_path_factory):
    d = tmp_path_factory.mktemp("sig")
    base, fpr, pub = _make_key(d / "gpg", "Tuxdex Test <test@example.invalid>")
    pkg = d / "tuxdex-1.6.2-1-any.pkg.tar.zst"
    pkg.write_bytes(b"paketinhalt")
    subprocess.run(base + ["--detach-sign", "--no-armor", str(pkg)], check=True, capture_output=True)
    _other_base, other_fpr, other_pub = _make_key(d / "gpg2", "Fremd <fremd@example.invalid>")
    return {"dir": d, "pkg": str(pkg), "sig": str(pkg) + ".sig", "fpr": fpr, "pub": pub,
            "other_fpr": other_fpr, "other_pub": other_pub}


def test_valid_signature_is_accepted(tx, signed):
    assert tx.verify_signature(signed["pkg"], signed["sig"], signed["pub"], signed["fpr"]) == (True, "")
    assert tx.verify_signature(signed["pkg"], signed["sig"], signed["pub"], signed["fpr"].lower())[0]


def test_changed_file_is_rejected(tx, signed):
    bad = signed["dir"] / "manipuliert.pkg.tar.zst"
    bad.write_bytes(b"paketinhalt + schadcode")
    ok, why = tx.verify_signature(str(bad), signed["sig"], signed["pub"], signed["fpr"])
    assert not ok and "ungültig" in why


def test_signature_from_other_key_is_rejected(tx, signed):
    # richtige Signatur, aber hinterlegt ist ein anderer Schlüssel
    ok, _why = tx.verify_signature(signed["pkg"], signed["sig"], signed["other_pub"], signed["other_fpr"])
    assert not ok


def test_key_must_match_pinned_fingerprint(tx, signed):
    # jemand tauscht den hinterlegten Schlüssel aus, der Fingerabdruck bleibt der alte
    ok, why = tx.verify_signature(signed["pkg"], signed["sig"], signed["pub"], signed["other_fpr"])
    assert not ok and "Fingerabdruck" in why


def test_nothing_is_trusted_without_key(tx, signed):
    assert not tx.verify_signature(signed["pkg"], signed["sig"], "", "")[0]
    assert not tx.signing_enabled() or tx.SIGNING_KEY_FPR        # ohne Fingerabdruck ist die Prüfung aus


def _release(monkeypatch, tx, names):
    body = {"assets": [{"name": n, "browser_download_url": "https://example.invalid/" + n} for n in names]}
    monkeypatch.setattr(tx, "_http_get", lambda url, timeout=15: json.dumps(body).encode())


def test_release_package_picks_matching_version(tx, monkeypatch):
    _release(monkeypatch, tx, ["tuxdex.db", "tuxdex-1.1.6.2-1-any.pkg.tar.zst", "tuxdex-1.1.6.2-1-any.pkg.tar.zst.sig"])
    url, sig, name = tx.release_package("VoxellabDE/Tuxdex", "1.6.2")
    assert name == "tuxdex-1.1.6.2-1-any.pkg.tar.zst" and sig == url + ".sig"


def test_release_package_refuses_unsigned_or_old(tx, monkeypatch):
    _release(monkeypatch, tx, ["tuxdex-1.1.6.2-1-any.pkg.tar.zst"])
    with pytest.raises(LookupError, match="keine Signatur"):
        tx.release_package("VoxellabDE/Tuxdex", "1.6.2")
    _release(monkeypatch, tx, ["tuxdex-1.1.6.1-1-any.pkg.tar.zst", "tuxdex-1.1.6.1-1-any.pkg.tar.zst.sig"])
    with pytest.raises(LookupError, match="noch nicht veröffentlicht"):
        tx.release_package("VoxellabDE/Tuxdex", "1.6.2")
