"""Publish every VIGIL org's ledger head as a dated release of this repository, then tell VIGIL where it is.

    python anchor_heads.py [--date YYYY-MM-DD] [--repo OWNER/NAME] [--out-dir anchors] [--dry-run]

Environment: VIGIL_URL (the API, for example https://vigil.supertruth.ai), VIGIL_ANCHOR_KEY (a VIGIL key with
the platform scope; it reads GET /anchors/heads and posts POST /anchors/record, nothing else), GH_TOKEN (the
workflow's own github.token: it creates releases in this repository and nowhere else), GITHUB_REPOSITORY (set
by Actions; --repo overrides it).

Standalone on purpose: the standard library only, no VIGIL code. The manifest recipe is the one VIGIL checks:
{"schema": "vigil-ledger-anchor/1", "date": "YYYY-MM-DD", "orgs": {"<org id>": {"head_hash", "length"}},
"manifest_sha256": sha256 of the canonical JSON (sorted keys, no spaces) of the manifest without that field}.
Hashes only, never content.

One release per UTC date (tag anchor-<date>, the manifest as the asset, the same values in the note). When the
date's release already exists, its manifest is read back and reported to VIGIL again; VIGIL stores only the
rows still missing and never changes a stored one. Nothing is ever clobbered or deleted here. --dry-run writes
the manifest and stops: nothing published, nothing reported.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "vigil-ledger-anchor/1"
KIND = "github_release"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def manifest_sha256(manifest: dict) -> str:
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def build_manifest(heads: list[dict], date: str) -> dict:
    body = {
        "schema": SCHEMA,
        "date": date,
        "orgs": {str(h["org_id"]): {"head_hash": h["head_hash"], "length": int(h["length"])} for h in sorted(heads, key=lambda h: int(h["org_id"]))},
    }
    return {**body, "manifest_sha256": manifest_sha256(body)}


def manifest_text(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def parse_manifest(text: str) -> dict:
    manifest = json.loads(text)
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"manifest schema is {manifest.get('schema')!r}, expected {SCHEMA!r}")
    if manifest_sha256(manifest) != manifest.get("manifest_sha256"):
        raise ValueError("manifest_sha256 does not recompute over the manifest body")
    return manifest


def api(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list]:
    """One call to VIGIL with the anchor key; (status, decoded body). Tests replace this."""
    url = os.environ["VIGIL_URL"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {os.environ['VIGIL_ANCHOR_KEY']}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "vigil-anchor/1",   # an explicit client name: an edge bot ruleset may refuse the bare urllib one
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"detail": raw.decode(errors="replace")[:500]}


def gh(args: list[str]) -> tuple[int, str, str]:
    """Run the gh CLI; (exit code, stdout, stderr). Tests replace this."""
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def release_tag(date: str) -> str:
    return f"anchor-{date}"


def existing_release(repo: str, tag: str) -> dict | None:
    code, out, _ = gh(["release", "view", tag, "--repo", repo, "--json", "url,publishedAt,tagName"])
    return json.loads(out) if code == 0 else None


def release_notes(manifest: dict) -> str:
    lines = [
        f"VIGIL ledger anchor for {manifest['date']} (UTC).",
        "",
        "Each line is one org's hash-chain head when this release was published: the org id, the chain length "
        "and the head hash (sha256 of the previous head and the last record's content hash). The asset "
        f"`{manifest['date']}.json` carries the same values. Its `manifest_sha256` is `{manifest['manifest_sha256']}`: "
        "sha256 of the canonical JSON (sorted keys, no spaces) of the manifest without that field.",
        "",
        "| org | length | head |",
        "|---|---|---|",
    ]
    for org_key, entry in manifest["orgs"].items():
        lines.append(f"| {org_key} | {entry['length']} | `{entry['head_hash']}` |")
    lines += [
        "",
        "Hashes only: no record content is published here. To check a chain against this anchor, export the "
        "org's ledger (`GET /audit/export`) and confirm the record at this length carries this chain hash; "
        "`GET /anchors/latest` on the VIGIL API reports whether the current chain still contains it.",
    ]
    return "\n".join(lines) + "\n"


def publish(repo: str, tag: str, path: Path, manifest: dict) -> dict:
    code, _, err = gh([
        "release", "create", tag, str(path), "--repo", repo,
        "--title", f"VIGIL ledger anchor {manifest['date']}", "--notes", release_notes(manifest),
    ])
    if code != 0:
        raise SystemExit(f"anchor: gh release create {tag} on {repo} failed: {err.strip()}")
    release = existing_release(repo, tag)
    if release is None:
        raise SystemExit(f"anchor: release {tag} was created on {repo} but cannot be read back")
    return release


def published_manifest(repo: str, tag: str, date: str) -> dict:
    code, out, err = gh(["release", "download", tag, "--repo", repo, "--pattern", f"{date}.json", "--output", "-"])
    if code != 0:
        raise SystemExit(f"anchor: release {tag} exists on {repo} but its manifest could not be downloaded: {err.strip()}")
    try:
        return parse_manifest(out)
    except ValueError as e:
        raise SystemExit(f"anchor: the manifest published for {tag} on {repo} is not valid ({e}); nothing reported")


def write_manifest(out_dir: Path, manifest: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{manifest['date']}.json"
    path.write_text(manifest_text(manifest))
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"), help="UTC date of the anchor (default today)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or "", help="OWNER/NAME of this repository (default GITHUB_REPOSITORY)")
    ap.add_argument("--out-dir", default="anchors", help="where the manifest file is written")
    ap.add_argument("--dry-run", action="store_true", help="write the manifest only; publish nothing, report nothing")
    a = ap.parse_args(argv)

    for name in ("VIGIL_URL", "VIGIL_ANCHOR_KEY"):
        if not os.environ.get(name):
            print(f"anchor: {name} is not set", file=sys.stderr)
            return 2
    if not a.repo:
        print("anchor: --repo or GITHUB_REPOSITORY is required", file=sys.stderr)
        return 2

    status, heads = api("GET", "/anchors/heads")
    if status != 200:
        print(f"anchor: GET /anchors/heads returned {status}: {heads}", file=sys.stderr)
        return 1
    if not heads:
        print("anchor: no ledger heads; nothing to anchor")
        return 0

    tag = release_tag(a.date)
    release = None if a.dry_run else existing_release(a.repo, tag)
    if release is not None:
        manifest = published_manifest(a.repo, tag, a.date)
        print(f"anchor: release {tag} already exists at {release['url']}; reporting its manifest again (VIGIL stores only what is missing)")
    else:
        manifest = build_manifest(heads, a.date)
        path = write_manifest(Path(a.out_dir), manifest)
        print(f"anchor: manifest {path} over {len(heads)} orgs, sha256 {manifest['manifest_sha256']}")
        if a.dry_run:
            print("anchor: dry run; nothing published, nothing reported")
            return 0
        release = publish(a.repo, tag, path, manifest)
        print(f"anchor: published {release['url']}")

    body = {
        "date": manifest["date"], "anchor_url": release["url"], "manifest_sha256": manifest["manifest_sha256"], "kind": KIND,
        "published_at": release.get("publishedAt"),
        "orgs": [{"org_id": int(k), "head_hash": v["head_hash"], "length": int(v["length"])} for k, v in manifest["orgs"].items()],
    }
    status, result = api("POST", "/anchors/record", body)
    if status != 200:
        print(f"anchor: POST /anchors/record returned {status}: {result}", file=sys.stderr)
        return 1
    for org_id in result["written"]:
        e = manifest["orgs"][str(org_id)]
        print(f"anchor: org {org_id} length {e['length']} head {e['head_hash']} recorded {release['url']}")
    if result["skipped"]:
        print(f"anchor: already recorded for {manifest['date']}: org {', '.join(map(str, result['skipped']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
