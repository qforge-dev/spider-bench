# Licensing

Profiles: `configs/license-profiles.yaml` (versioned; version stored in release.json).

- `conservative`: CC0-1.0, CC-BY-4.0, CC-BY-3.0.
- `research` (Poland default): + CC-BY-NC-4.0/3.0; CC-BY-SA-*/CC-BY-NC-SA-*
  need a documented decision (`license_review_ok`) before inclusion (ShareAlike).
- Reject: unknown, all-rights-reserved, transformation-incompatible.

Every accepted image needs: relative S3 media key + SHA-256, source record + URLs,
creator, license id + URL, attribution text, source snapshot id. Decisions are
frozen at release time (don't rely on live source state).

Helpers (`licensing.py`): `is_accepted`, `review_required`, `classify_license`
-> `accepted|review_required|rejected`, `build_attribution(creator, license_id, ...)`.

CLI: `spider-bench audit licenses [--input candidates.json]`,
`spider-bench media select --input ...` (caps + license filter).
