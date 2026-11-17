# VIGIL ledger anchors

Each release of this repository is one day's anchor: every VIGIL org's hash-chain head (the org id, the chain
length and the head hash) as it stood when the release was published. GitHub dates the release and, with
release immutability on, signs an attestation over its tag, commit and asset digest. A reader who does not
trust VIGIL's database can see here that a hash existed on a date; the chain is append-only, so everything up
to that length was fixed by then.

Hashes only. No record content, no agent names, no org names: org ids are numbers.

## What a release holds

- Tag `anchor-YYYY-MM-DD`, one per UTC date.
- Asset `YYYY-MM-DD.json`: `{"schema": "vigil-ledger-anchor/1", "date", "orgs": {"<org id>": {"head_hash", "length"}}, "manifest_sha256"}`.
  `manifest_sha256` is the sha256 of the canonical JSON (sorted keys, no spaces) of the manifest without that
  field. Recompute it: remove the field, `json.dumps(body, sort_keys=True, separators=(",", ":"))`, sha256.
- The note repeats the table and the sha256. The asset and the attestation are the record; a note stays editable.

## How to check a chain against an anchor

1. Export the org's ledger from VIGIL (`GET /audit/export`, a key for that org) and verify it offline with
   VIGIL's `tools/vigil_verify.py`.
2. The record at position `length` must carry `chain_hash` equal to `head_hash`. If it does, every record up
   to it existed on the release date.
3. `GET /anchors/latest` on the VIGIL API answers the same question for the current chain (`in_chain`).
4. `gh release verify anchor-YYYY-MM-DD --repo <this repository>` checks GitHub's attestation;
   `gh release verify-asset anchor-YYYY-MM-DD YYYY-MM-DD.json --repo <this repository>` checks a downloaded manifest against it.

## How it runs

`.github/workflows/anchor.yml` runs `anchor_heads.py` daily at 06:30 UTC and on dispatch. The script reads the
heads from VIGIL (`GET /anchors/heads`), writes the manifest, creates the release with this repository's own
`github.token`, and reports the release to VIGIL (`POST /anchors/record`), which stores one row per org and
never a second for the same date. A rerun on the same date reads the published manifest back and reports it
again; nothing is clobbered or deleted. The standard library only; no VIGIL code.

## Settings this repository needs

- Visibility: public.
- Release immutability: on, before the first release (Settings, the Releases section, "Enable release
  immutability"; it applies only to releases created afterwards).
- Actions: enabled. The workflow asks for `contents: write` on its own token and nothing else.
- Secrets (Settings > Secrets and variables > Actions):
  - `VIGIL_URL`: the API base, `https://vigil.supertruth.ai`.
  - `VIGIL_ANCHOR_KEY`: a VIGIL key with the `anchors` scope, minted by the VIGIL operator
    (`POST /keys/ {"name": "anchors", "scopes": ["anchors"]}` with the env-admin key). That scope reads
    every org's chain head and records anchors, and nothing else: no events, scores, ledger rows, orgs or
    plans. It lives in this repository's secrets and nowhere else.
- A commit a day: the workflow commits each manifest under `anchors/` as well as publishing it, because
  GitHub disables a public repository's scheduled workflows after 60 days without repository activity.
