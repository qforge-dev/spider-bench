# Data sources

Use official APIs / bulk exports; preserve source provenance + source IDs. Raw
dumps go to `poland/raw-metadata/<source>/<snapshot>/` (immutable, never overwritten).

- **Polish membership**: national checklist / biodiversity DB / Spiders of Europe /
  GBIF institutional checklists. Decide one primary authority (Milestone 0); store
  version, citation, retrieval date.
- **World Spider Catalog**: pinned snapshot for accepted names/synonyms/IDs.
  Confirm reuse terms; keep the snapshot with the release.
- **iNaturalist**: Open Data bulk preferred; REST API only for discovery/smoke
  tests. Filters: Araneae, Poland, photo present, accepted license, wild,
  research-grade as candidate. Keep observation/photo/observer IDs, taxon,
  timestamps, safe geography, URLs, creator, license, snapshot date.
- **GBIF**: supplementary occurrences/media, reproducible downloads (key + DOI).
  Dedup against iNaturalist via occurrence IDs, references, URLs, image hashes —
  cross-publication is not independent evidence.
- **Wikimedia Commons** (optional): MediaWiki API; capture file revision, creator,
  license name+URL, page, media URL. Ambiguous/multi-licensed files -> manual review.
- **Medical evidence** (plan §4.6): peer-reviewed case reports/reviews,
  poison-center/public-health publications, clinical/toxicology references, expert
  statements with authorship + review date. Record DOI/PMID/ISBN/URL + the
  supported claim; citation alone is not an assessment.
