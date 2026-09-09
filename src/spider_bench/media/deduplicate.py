"""Deduplication: exact sha256 + source-link identity + perceptual-hash grouping.

Pure grouping functions (no network/S3). SQLite persistence uses a
standalone `duplicate_groups` table created on demand — db.py is untouched.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from spider_bench.media.hash import hamming


@dataclass
class MediaRecord:
    key: str  # local id / sqlite media id / sha — whatever identifies the row
    sha256: str | None = None
    source: str | None = None
    source_media_id: str | None = None
    source_url: str | None = None
    ahash: int | None = None
    dhash: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class DuplicateGroup:
    members: list[str] = field(default_factory=list)
    method: str = ""
    score: float = 0.0
    canonical_key: str = ""


def _source_identity(rec: MediaRecord) -> str | None:
    if rec.source and rec.source_media_id:
        return f"{rec.source}:{rec.source_media_id}"
    if rec.source_url:
        return rec.source_url.strip().rstrip("/")
    return None


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def pick_canonical(members: list[MediaRecord]) -> str:
    """Deterministic canonical: largest area, then smallest key."""

    def area(r: MediaRecord) -> int:
        if r.width and r.height:
            return r.width * r.height
        return 0

    best = sorted(members, key=lambda r: (-area(r), r.key))[0]
    return best.key


def find_duplicate_groups(
    records: list[MediaRecord],
    phash_threshold: int = 6,
    use_source_identity: bool = True,
) -> list[DuplicateGroup]:
    """Group records. Methods: sha256 | source_link | perceptual.

    Exact (sha256, source-link) groups merge transitively with perceptual
    neighbours via union-find; the reported method is the strongest link
    that formed the group (sha256 > source_link > perceptual).
    """
    uf = _UnionFind()
    for r in records:
        uf.find(r.key)
    edge_method: dict[tuple[str, str], str] = {}

    def link(a: str, b: str, method: str) -> None:
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            return
        uf.union(a, b)
        edge_method[(a, b)] = method

    by_sha: dict[str, list[MediaRecord]] = {}
    for r in records:
        if r.sha256:
            by_sha.setdefault(r.sha256.lower(), []).append(r)
    for same in by_sha.values():
        for i in range(1, len(same)):
            link(same[0].key, same[i].key, "sha256")

    if use_source_identity:
        by_src: dict[str, list[MediaRecord]] = {}
        for r in records:
            ident = _source_identity(r)
            if ident:
                by_src.setdefault(ident, []).append(r)
        for same in by_src.values():
            for i in range(1, len(same)):
                link(same[0].key, same[i].key, "source_link")

    with_hash = [r for r in records if r.ahash is not None and r.dhash is not None]
    for i in range(len(with_hash)):
        for j in range(i + 1, len(with_hash)):
            a, b = with_hash[i], with_hash[j]
            da = hamming(a.ahash or 0, b.ahash or 0)
            dd = hamming(a.dhash or 0, b.dhash or 0)
            if da <= phash_threshold and dd <= phash_threshold:
                link(a.key, b.key, "perceptual")

    clusters: dict[str, list[MediaRecord]] = {}
    for r in records:
        clusters.setdefault(uf.find(r.key), []).append(r)

    groups: list[DuplicateGroup] = []
    strength = {"perceptual": 0, "source_link": 1, "sha256": 2}
    for members in clusters.values():
        if len(members) < 2:
            continue
        keys = {m.key for m in members}
        best_method = "perceptual"
        best_score = 0.0
        # Re-derive strongest direct evidence inside the cluster.
        for x in members:
            for y in members:
                if x.key >= y.key:
                    continue
                direct = None
                if x.sha256 and y.sha256 and x.sha256.lower() == y.sha256.lower():
                    direct = "sha256"
                elif (
                    use_source_identity
                    and _source_identity(x)
                    and _source_identity(x) == _source_identity(y)
                ):
                    direct = "source_link"
                elif (
                    x.ahash is not None
                    and y.ahash is not None
                    and x.dhash is not None
                    and y.dhash is not None
                    and hamming(x.ahash, y.ahash) <= phash_threshold
                    and hamming(x.dhash, y.dhash) <= phash_threshold
                ):
                    direct = "perceptual"
                if direct and strength[direct] >= strength[best_method]:
                    best_method = direct
                    if direct == "sha256":
                        best_score = 1.0
                    elif direct == "source_link":
                        best_score = 0.9
                    else:
                        dd = max(
                            hamming(x.ahash or 0, y.ahash or 0),
                            hamming(x.dhash or 0, y.dhash or 0),
                        )
                        best_score = max(best_score, 1.0 - dd / 64.0)
        # Edge case: cluster formed only via transitive perceptual chain;
        # keep method=perceptual with a mid score.
        if best_score == 0.0:
            best_method, best_score = "perceptual", 0.5
        groups.append(
            DuplicateGroup(
                members=sorted(keys),
                method=best_method,
                score=best_score,
                canonical_key=pick_canonical(members),
            )
        )
    groups.sort(key=lambda g: g.members[0])
    return groups


def ensure_duplicate_groups_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS duplicate_groups (
          id INTEGER PRIMARY KEY,
          group_key TEXT NOT NULL,
          member_key TEXT NOT NULL,
          method TEXT NOT NULL,
          score REAL NOT NULL,
          canonical_key TEXT NOT NULL,
          review_state TEXT NOT NULL DEFAULT 'pending',
          UNIQUE(group_key, member_key)
        )
        """
    )
    conn.commit()


def write_duplicate_groups(conn: sqlite3.Connection, groups: list[DuplicateGroup]) -> int:
    """Insert groups; returns number of member rows written."""
    ensure_duplicate_groups_table(conn)
    n = 0
    with conn:
        for i, g in enumerate(groups):
            group_key = f"g{i:06d}-{g.method}-{g.canonical_key}"
            for m in g.members:
                conn.execute(
                    "INSERT OR IGNORE INTO duplicate_groups"
                    "(group_key, member_key, method, score, canonical_key) VALUES (?,?,?,?,?)",
                    (group_key, m, g.method, g.score, g.canonical_key),
                )
                n += 1
    return n


def duplicate_report(groups: list[DuplicateGroup]) -> dict[str, Any]:
    by_method: dict[str, int] = {}
    dup_members = 0
    for g in groups:
        by_method[g.method] = by_method.get(g.method, 0) + 1
        dup_members += len(g.members)
    return {
        "groups": len(groups),
        "duplicate_members": dup_members,
        "by_method": by_method,
    }
