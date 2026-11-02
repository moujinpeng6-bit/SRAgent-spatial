"""Install project-local NCBI binaries or probe upstream public resource URLs."""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SRA_URL = "https://ftp-trace.ncbi.nlm.nih.gov/sra/sdk/current/sratoolkit.current-win64.zip"
ONTOLOGY_URLS = {
    "tissue_ontology": "https://storage.googleapis.com/arc-scbasecount/2025-02-25/tissue_ontology/uberon-full_chroma.tar.gz",
    "disease_ontology": "https://storage.googleapis.com/arc-scbasecount/2025-02-25/disease_ontology/mondo_chroma.tar.gz",
}


def extract_zip(archive, target):
    root = target.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            dest = (root / item.filename).resolve()
            if not dest.is_relative_to(root) or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Unsafe archive member")
        bundle.extractall(root)


def install_sra():
    from sragent_spatial.dependencies import activate_toolkit, toolkit_bin
    if os.name != "nt":
        raise RuntimeError("This installer is for Windows; use the official NCBI package for your OS")
    path = toolkit_bin()
    digest = None
    if path is None:
        directory = ROOT / "outputs" / "tooling"
        directory.mkdir(parents=True, exist_ok=True)
        partial = directory / "sratoolkit.current-win64.zip.part"
        digest = hashlib.sha256()
        total = 0
        with urllib.request.urlopen(SRA_URL, timeout=60) as response, partial.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 256 * 1024 * 1024:
                    raise ValueError("Toolkit package exceeded 256 MiB limit")
                digest.update(chunk)
                stream.write(chunk)
        archive = partial.with_suffix("")
        partial.replace(archive)
        destination = directory / ("sratoolkit-" + uuid.uuid4().hex[:8])
        extract_zip(archive, destination)
        path = toolkit_bin()
        if path is None:
            raise RuntimeError("Package has no bin directory containing both required tools")
        digest = digest.hexdigest()
    activate_toolkit()
    versions = {}
    for name in ("fastq-dump", "sra-stat"):
        result = subprocess.run([str(path / (name + ".exe")), "--version"],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError("Toolkit version command failed: " + name)
        versions[name] = result.stdout.strip()
    return {"source": SRA_URL, "download_sha256": digest, "versions": versions,
            "scope": "installed_and_version_checked_no_sequence_download"}


def probe_resources():
    rows = []
    for key, url in ONTOLOGY_URLS.items():
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20) as response:
                row = {"module": key, "url": url, "http_status": response.status,
                       "content_length": response.headers.get("Content-Length")}
        except urllib.error.HTTPError as exc:
            row = {"module": key, "url": url, "http_status": exc.code}
        except Exception as exc:
            row = {"module": key, "url": url, "error_type": type(exc).__name__}
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-sra", action="store_true")
    parser.add_argument("--probe-resources", action="store_true")
    args = parser.parse_args()
    if args.install_sra or args.probe_resources:
        import truststore
        truststore.inject_into_ssl()
    from sragent_spatial.dependencies import dependency_report
    report = {}
    if args.install_sra:
        report["sra_install"] = install_sra()
    if args.probe_resources:
        report["ontology_downloads"] = probe_resources()
    report["dependencies"] = dependency_report()
    target = ROOT / "outputs" / "module_validation" / "dependencies.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
