#!/usr/bin/env python3
"""Validate a prebuilt delivery using only the channel's pinned public key."""

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import posixpath
import re
import stat
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

REPOSITORY = "Marcel0365/PDFChief-updates"
FEED_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/main/appcast.xml"
SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
NAMESPACE = {"sparkle": SPARKLE}
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def signature(value, length):
    require(isinstance(value, str), "Expected a base64 signature or key.")
    decoded = base64.b64decode(value, validate=True)
    require(len(decoded) == length, "Invalid signature or public key length.")
    return decoded


def version_tuple(value):
    require(isinstance(value, str) and len(value) <= 63 and VERSION.fullmatch(value), "Invalid stable version.")
    return tuple(map(int, value.split(".")))


def read_feed(path):
    content = path.read_bytes()
    require(len(content) <= 1_048_576, "Appcast is too large.")
    require(b"<!DOCTYPE" not in content and b"<!ENTITY" not in content, "XML declarations are not allowed.")
    root = ET.fromstring(content)
    require(root.tag == "rss" and len(root.findall("channel")) == 1, "Expected a single RSS channel.")
    return root.findall("./channel/item")


def verify_ed25519(path, signed, public_key):
    with tempfile.TemporaryDirectory(prefix="pdfchief-signature-") as directory:
        directory = Path(directory)
        # RFC 8410 SubjectPublicKeyInfo for an Ed25519 public key.
        key = directory / "public.der"
        key.write_bytes(bytes.fromhex("302a300506032b6570032100") + public_key)
        sig = directory / "signature.bin"
        sig.write_bytes(signature(signed, 64))
        result = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-pubin", "-keyform", "DER", "-inkey", str(key),
             "-rawin", "-in", str(path), "-sigfile", str(sig)],
            capture_output=True,
            check=False,
        )
        require(result.returncode == 0, "Ed25519 signature verification failed.")


def validate(incoming, branch, public_key_string, published_feed):
    public_key_string = public_key_string.strip()
    require(branch.startswith("delivery/v"), "Expected a delivery/vMAJOR.MINOR.PATCH branch.")
    tag = branch.removeprefix("delivery/")
    version = tag.removeprefix("v")
    current_version = version_tuple(version)
    public_key = signature(public_key_string, 32)
    archive_name = f"PDFChief-{tag}.zip"
    expected_files = {archive_name, "appcast.xml", "release.json"}
    require(incoming.is_dir() and not incoming.is_symlink(), "Missing delivery directory.")
    require({item.name for item in incoming.iterdir()} == expected_files, "Unexpected delivery files.")
    require(all(item.is_file() and not item.is_symlink() for item in incoming.iterdir()), "Delivery entries must be regular files.")
    require((incoming / "release.json").stat().st_size <= 16_384, "Release metadata is too large.")
    metadata = json.loads((incoming / "release.json").read_text())
    require(isinstance(metadata, dict) and set(metadata) == {
        "schema", "tag", "version", "archive", "length", "sha256", "edSignature", "appcastEdSignature", "publicKey",
    }, "Unexpected release metadata fields.")
    require(metadata["schema"] == 1 and metadata["tag"] == tag and metadata["version"] == version,
            "Release metadata does not match its branch.")
    require(metadata["archive"] == archive_name and metadata["publicKey"] == public_key_string,
            "Unexpected archive name or signing key.")
    archive = incoming / archive_name
    length = archive.stat().st_size
    require(0 < length < 95 * 1024 * 1024, "Archive exceeds the delivery Git file limit.")
    require(type(metadata["length"]) is int and metadata["length"] == length, "Archive length mismatch.")
    with archive.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    require(metadata["sha256"] == digest, "Archive checksum mismatch.")
    verify_ed25519(archive, metadata["edSignature"], public_key)
    verify_ed25519(incoming / "appcast.xml", metadata["appcastEdSignature"], public_key)

    items = read_feed(incoming / "appcast.xml")
    require(len(items) == 1, "Expected exactly one current appcast release.")
    versions = set()
    for item in items:
        item_version = item.findtext("sparkle:version", namespaces=NAMESPACE)
        require(version_tuple(item_version) <= current_version, "Appcast contains an unexpected future release.")
        require(item_version not in versions, "Duplicate appcast version.")
        versions.add(item_version)
        enclosures = item.findall("enclosure")
        require(len(enclosures) == 1, "Expected one enclosure per release.")
        enclosure = enclosures[0]
        item_archive = f"PDFChief-v{item_version}.zip"
        require(enclosure.get("url") == f"https://github.com/{REPOSITORY}/releases/download/v{item_version}/{item_archive}",
                "Unexpected archive download URL.")
        require(enclosure.get("type") == "application/octet-stream", "Unexpected enclosure type.")
        signature(enclosure.get(f"{{{SPARKLE}}}edSignature"), 64)
        if item_version == version:
            require(enclosure.get(f"{{{SPARKLE}}}edSignature") == metadata["edSignature"]
                    and enclosure.get("length") == str(length), "Appcast and archive metadata disagree.")
            minimum = item.findtext("sparkle:minimumSystemVersion", namespaces=NAMESPACE)
            require(minimum in {"14", "14.0", "14.0.0"}, "Unexpected minimum macOS version.")
            hardware = item.findall("sparkle:hardwareRequirements", namespaces=NAMESPACE)
            require(len(hardware) == 1 and hardware[0].text == "arm64",
                    "The appcast must require arm64 hardware to exclude Intel Macs.")
    require(version in versions, "This release is missing from the appcast.")

    with zipfile.ZipFile(archive) as package:
        require(sum(item.file_size for item in package.infolist()) < 500 * 1024 * 1024, "Unpacked archive is too large.")
        for item in package.infolist():
            path = PurePosixPath(item.filename)
            require(not path.is_absolute() and ".." not in path.parts and "\\" not in item.filename, "Unsafe archive path.")
            require(item.filename.startswith(("PDFChief.app/", "__MACOSX/PDFChief.app/"))
                    or item.filename in {"__MACOSX/", "__MACOSX/._PDFChief.app"},
                    "The archive may only contain PDFChief.app and its resource metadata.")
            if stat.S_ISLNK(item.external_attr >> 16):
                require(item.file_size <= 1024, "Invalid framework symlink.")
                target = package.read(item).decode("utf-8")
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(item.filename), target))
                require(not target.startswith("/") and resolved.startswith("PDFChief.app/"), "Symlink escapes the app bundle.")
        info_entry = package.getinfo("PDFChief.app/Contents/Info.plist")
        require(info_entry.file_size <= 1_048_576, "Application Info.plist is too large.")
        info = plistlib.loads(package.read(info_entry))
        require(info.get("CFBundleIdentifier") == "com.marcelschmidt.pdfchief", "Unexpected bundle identifier.")
        require(info.get("NSHumanReadableCopyright") == "© 2026 Marcel Schmidt",
                "The archived app must credit Marcel Schmidt.")
        credits_entry = package.getinfo("PDFChief.app/Contents/Resources/Credits.rtf")
        require(credits_entry.file_size <= 65_536 and b"Marcel Schmidt" in package.read(credits_entry),
                "The archived app must include its Marcel Schmidt author credit.")
        require(info.get("CFBundleVersion") == version and info.get("CFBundleShortVersionString") == version,
                "Archived application versions do not match the release.")
        require(info.get("LSMinimumSystemVersion") in {"14", "14.0", "14.0.0"},
                "The archived app must support macOS Sonoma 14.")
        require(info.get("CFBundleExecutable") == "PDFChief", "Unexpected application executable name.")
        with package.open("PDFChief.app/Contents/MacOS/PDFChief") as executable:
            header = executable.read(32)
        require(len(header) == 32, "The application executable needs a Mach-O header.")
        magic, cpu_type, cpu_subtype, file_type = struct.unpack("<IiiI", header[:16])
        # A thin little-endian 64-bit ARM executable, rather than Intel or a fat binary.
        require(magic == 0xFEEDFACF and cpu_type == 0x0100000C and cpu_subtype & 0x00FFFFFF == 0
                and file_type == 2, "The application executable must contain only Apple Silicon arm64 code.")
        require(info.get("SUPublicEDKey") == public_key_string and info.get("SUFeedURL") == FEED_URL,
                "Archived updater configuration does not match the channel.")

    published = read_feed(published_feed)
    published_versions = [version_tuple(item.findtext("sparkle:version", namespaces=NAMESPACE)) for item in published]
    if published_versions:
        require(current_version >= max(published_versions), "Refusing to publish an older update.")
        if current_version == max(published_versions):
            require((incoming / "appcast.xml").read_bytes() == published_feed.read_bytes(),
                    "An already-published version cannot be replaced.")
    return metadata


def main():
    require(len(sys.argv) == 5, "Usage: validate-release.py incoming delivery/vX.Y.Z public-key published-appcast.xml")
    metadata = validate(Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4]))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as file:
            for key in ("tag", "version", "archive", "sha256"):
                file.write(f"{key}={metadata[key]}\n")
    print(f"Verified signed PDFChief {metadata['tag']} delivery.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Delivery rejected: {error}") from None
