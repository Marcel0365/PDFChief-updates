#!/usr/bin/env python3
"""Offline regression checks with synthetic app bundles and temporary test keys."""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.dont_write_bytecode = True
specification = importlib.util.spec_from_file_location("release_validator", Path(__file__).with_name("validate-release.py"))
validator = importlib.util.module_from_spec(specification)
specification.loader.exec_module(validator)


class ReleaseValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="PDFChief-release-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.incoming = self.root / "incoming"
        self.incoming.mkdir()
        self.private_key = self.root / "test-key.pem"
        self.command("openssl", "genpkey", "-algorithm", "ED25519", "-out", str(self.private_key))
        public_der = self.command("openssl", "pkey", "-in", str(self.private_key), "-pubout", "-outform", "DER")
        self.public_key = base64.b64encode(public_der[-32:]).decode("ascii")
        self.published = self.root / "published.xml"
        self.published.write_text('<rss version="2.0"><channel/></rss>')
        self.fixture()

    def command(self, *arguments):
        return subprocess.run(arguments, check=True, capture_output=True).stdout

    def sign(self, path):
        signature = self.command("openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(self.private_key), "-in", str(path))
        return base64.b64encode(signature).decode("ascii")

    def fixture(self, bundle_version="0.2.0", extra_path=None, symlink=None, minimum="14.0", feed_minimum="14.0",
                hardware="arm64", cpu_type=0x0100000C, executable=None):
        archive = self.incoming / "PDFChief-v0.2.0.zip"
        info = {
            "CFBundleIdentifier": "com.marcelschmidt.pdfchief",
            "NSHumanReadableCopyright": "© 2026 Marcel Schmidt",
            "CFBundleExecutable": "PDFChief",
            "CFBundleVersion": bundle_version,
            "CFBundleShortVersionString": bundle_version,
            "SUPublicEDKey": self.public_key,
            "SUFeedURL": validator.FEED_URL,
            "LSMinimumSystemVersion": minimum,
        }
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("PDFChief.app/Contents/Info.plist", plistlib.dumps(info))
            package.writestr("PDFChief.app/Contents/Resources/Credits.rtf", b"{\\rtf1\\ansi Entwickelt von Marcel Schmidt.}")
            if executable is None:
                executable = struct.pack("<IiiIIIII", 0xFEEDFACF, cpu_type, 0, 2, 0, 0, 0, 0)
            package.writestr("PDFChief.app/Contents/MacOS/PDFChief", executable)
            if extra_path:
                package.writestr(extra_path, b"unexpected entry")
            if symlink:
                entry = zipfile.ZipInfo("PDFChief.app/Contents/Frameworks/escape")
                entry.create_system = 3
                entry.external_attr = 0o120777 << 16
                package.writestr(entry, symlink)
        archive_signature = self.sign(archive)
        root = ET.Element("rss", version="2.0")
        item = ET.SubElement(ET.SubElement(root, "channel"), "item")
        for name, text in (("version", "0.2.0"), ("shortVersionString", "0.2.0"), ("minimumSystemVersion", feed_minimum)):
            ET.SubElement(item, f"{{{validator.SPARKLE}}}{name}").text = text
        if hardware is not None:
            ET.SubElement(item, f"{{{validator.SPARKLE}}}hardwareRequirements").text = hardware
        ET.SubElement(item, "enclosure", {
            "url": f"https://github.com/{validator.REPOSITORY}/releases/download/v0.2.0/{archive.name}",
            "length": str(archive.stat().st_size),
            f"{{{validator.SPARKLE}}}edSignature": archive_signature,
            "type": "application/octet-stream",
        })
        feed = self.incoming / "appcast.xml"
        ET.ElementTree(root).write(feed, encoding="utf-8", xml_declaration=True)
        metadata = {
            "schema": 1, "tag": "v0.2.0", "version": "0.2.0", "archive": archive.name,
            "length": archive.stat().st_size, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "edSignature": archive_signature, "appcastEdSignature": self.sign(feed), "publicKey": self.public_key,
        }
        (self.incoming / "release.json").write_text(json.dumps(metadata))

    def validate(self, branch="delivery/v0.2.0"):
        return validator.validate(self.incoming, branch, self.public_key, self.published)

    def test_valid_delivery(self):
        self.assertEqual(self.validate()["version"], "0.2.0")

    def test_modified_archive_with_updated_checksum(self):
        archive = self.incoming / "PDFChief-v0.2.0.zip"
        archive.write_bytes(archive.read_bytes() + b"tampered")
        metadata_file = self.incoming / "release.json"
        metadata = json.loads(metadata_file.read_text())
        metadata["length"] = archive.stat().st_size
        metadata["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        metadata_file.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            self.validate()

    def test_modified_feed(self):
        feed = self.incoming / "appcast.xml"
        feed.write_bytes(feed.read_bytes() + b"<!--tampered-->")
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            self.validate()

    def test_wrong_branch_tag(self):
        with self.assertRaises(ValueError):
            self.validate("delivery/v0.3.0")

    def test_shell_characters_in_branch(self):
        with self.assertRaises(ValueError):
            self.validate("delivery/v0.2.0;echo unsafe")

    def test_signed_wrong_bundle_version(self):
        self.fixture(bundle_version="0.1.0")
        with self.assertRaisesRegex(ValueError, "application versions"):
            self.validate()

    def test_signed_app_requires_newer_macos(self):
        self.fixture(minimum="26.0")
        with self.assertRaisesRegex(ValueError, "support macOS Sonoma"):
            self.validate()

    def test_signed_feed_excludes_sonoma(self):
        self.fixture(feed_minimum="26.0")
        with self.assertRaisesRegex(ValueError, "minimum macOS"):
            self.validate()

    def test_signed_feed_without_arm64_requirement(self):
        self.fixture(hardware=None)
        with self.assertRaisesRegex(ValueError, "exclude Intel"):
            self.validate()

    def test_signed_feed_with_wrong_hardware_requirement(self):
        self.fixture(hardware="x86_64")
        with self.assertRaisesRegex(ValueError, "exclude Intel"):
            self.validate()

    def test_signed_intel_executable(self):
        self.fixture(cpu_type=0x01000007)
        with self.assertRaisesRegex(ValueError, "only Apple Silicon"):
            self.validate()

    def test_signed_fat_executable(self):
        self.fixture(executable=b"\xca\xfe\xba\xbe" + bytes(28))
        with self.assertRaisesRegex(ValueError, "only Apple Silicon"):
            self.validate()

    def test_signed_invalid_executable(self):
        self.fixture(executable=b"not a Mach-O executable")
        with self.assertRaisesRegex(ValueError, "Mach-O header"):
            self.validate()

    def test_signed_path_traversal(self):
        self.fixture(extra_path="PDFChief.app/../../escape")
        with self.assertRaisesRegex(ValueError, "Unsafe archive path"):
            self.validate()

    def test_signed_absolute_symlink(self):
        self.fixture(symlink="/tmp/outside")
        with self.assertRaisesRegex(ValueError, "Symlink escapes"):
            self.validate()

    def test_signed_relative_escaping_symlink(self):
        self.fixture(symlink="../../../outside")
        with self.assertRaisesRegex(ValueError, "Symlink escapes"):
            self.validate()

    def test_rollback(self):
        root = ET.Element("rss", version="2.0")
        item = ET.SubElement(ET.SubElement(root, "channel"), "item")
        ET.SubElement(item, f"{{{validator.SPARKLE}}}version").text = "0.3.0"
        ET.ElementTree(root).write(self.published)
        with self.assertRaisesRegex(ValueError, "older update"):
            self.validate()

    def test_byte_identical_published_retry(self):
        self.published.write_bytes((self.incoming / "appcast.xml").read_bytes())
        self.assertEqual(self.validate()["version"], "0.2.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
