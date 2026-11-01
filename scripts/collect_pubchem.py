#!/usr/bin/env python3
"""Collect a small PubChem 3D-text pilot dataset by CID.

Outputs:
  metadata.csv
  descriptions/<CID>.txt
  sdf/<CID>.sdf

This script deliberately performs RAW collection only. It does not decide
whether a description is truly 3D-sensitive; that is a separate review stage.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

import requests

PUG_REST = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PUG_VIEW = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view"
TIMEOUT = 30

PROPERTY_NAMES = [
    "Title",
    "CanonicalSMILES",
    "IsomericSMILES",
    "InChI",
    "InChIKey",
    "MolecularFormula",
    "MolecularWeight",
    "XLogP",
    "TPSA",
    "Complexity",
    "HBondDonorCount",
    "HBondAcceptorCount",
    "RotatableBondCount",
]


def request(session: requests.Session, url: str, *, retries: int = 4) -> requests.Response:
    """GET with small retry/backoff for transient PubChem/network errors."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=TIMEOUT)
            if response.status_code == 200:
                return response
            if response.status_code in {400, 404}:
                response.raise_for_status()
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError(f"Request failed after {retries} attempts: {url}")


def read_cids(path: Path, limit: int | None = None) -> list[int]:
    cids: list[int] = []
    seen: set[int] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cid = int(line)
        if cid not in seen:
            seen.add(cid)
            cids.append(cid)
        if limit is not None and len(cids) >= limit:
            break
    return cids


def fetch_properties(session: requests.Session, cid: int) -> dict[str, Any]:
    props = ",".join(PROPERTY_NAMES)
    url = f"{PUG_REST}/compound/cid/{cid}/property/{props}/JSON"
    payload = request(session, url).json()
    return payload["PropertyTable"]["Properties"][0]


def fetch_3d_sdf(session: requests.Session, cid: int) -> str:
    # record_type=3d asks PubChem for its 3D conformer record when available.
    url = f"{PUG_REST}/compound/cid/{cid}/SDF?record_type=3d"
    return request(session, url).text


def extract_strings(node: Any) -> list[str]:
    """Recursively collect human-readable strings from PUG View StringWithMarkup nodes."""
    found: list[str] = []
    if isinstance(node, dict):
        swm = node.get("StringWithMarkup")
        if isinstance(swm, list):
            for item in swm:
                if isinstance(item, dict):
                    value = item.get("String")
                    if isinstance(value, str) and value.strip():
                        found.append(value.strip())
        for value in node.values():
            found.extend(extract_strings(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(extract_strings(item))
    return found


def find_description_sections(node: Any) -> list[dict[str, Any]]:
    """Find PUG View sections whose heading is Description."""
    sections: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if str(node.get("TOCHeading", "")).strip().lower() == "description":
            sections.append(node)
        for value in node.values():
            sections.extend(find_description_sections(value))
    elif isinstance(node, list):
        for item in node:
            sections.extend(find_description_sections(item))
    return sections


def fetch_description(session: requests.Session, cid: int) -> tuple[str, dict[str, Any]]:
    # Keep the raw JSON so Stage 2 can revisit provenance/section structure.
    url = f"{PUG_VIEW}/data/compound/{cid}/JSON"
    raw = request(session, url).json()
    sections = find_description_sections(raw)
    strings: list[str] = []
    for section in sections:
        strings.extend(extract_strings(section))

    # Preserve order while removing exact duplicates.
    unique = list(dict.fromkeys(s for s in strings if s.strip()))
    return "\n\n".join(unique), raw


def collect_one(session: requests.Session, cid: int, out_dir: Path) -> dict[str, Any]:
    row: dict[str, Any] = {
        "CID": cid,
        "has_properties": False,
        "has_3d_sdf": False,
        "has_description": False,
        "status": "ok",
        "error": "",
    }

    try:
        props = fetch_properties(session, cid)
        row.update(props)
        row["has_properties"] = True
    except Exception as exc:  # keep partial samples for diagnosis
        row["status"] = "partial"
        row["error"] += f"properties: {exc}; "

    try:
        sdf = fetch_3d_sdf(session, cid)
        (out_dir / "sdf" / f"{cid}.sdf").write_text(sdf, encoding="utf-8")
        row["has_3d_sdf"] = True
    except Exception as exc:
        row["status"] = "partial"
        row["error"] += f"3d_sdf: {exc}; "

    try:
        description, raw_json = fetch_description(session, cid)
        (out_dir / "descriptions" / f"{cid}.txt").write_text(description, encoding="utf-8")
        (out_dir / "raw_pug_view" / f"{cid}.json").write_text(
            json.dumps(raw_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        row["description"] = description.replace("\n", " ").strip()
        row["has_description"] = bool(description.strip())
    except Exception as exc:
        row["status"] = "partial"
        row["error"] += f"description: {exc}; "

    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cids", type=Path, default=Path("data/cids.txt"))
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.25, help="Delay between CIDs in seconds")
    args = parser.parse_args()

    out_dir: Path = args.out
    for subdir in ("sdf", "descriptions", "raw_pug_view"):
        (out_dir / subdir).mkdir(parents=True, exist_ok=True)

    cids = read_cids(args.cids, args.limit)
    if not cids:
        raise SystemExit("No CIDs found.")

    session = requests.Session()
    session.headers.update({"User-Agent": "3D-Text-Dataset-Pilot/0.1"})

    rows: list[dict[str, Any]] = []
    for index, cid in enumerate(cids, start=1):
        print(f"[{index}/{len(cids)}] CID {cid}")
        rows.append(collect_one(session, cid, out_dir))
        time.sleep(max(args.delay, 0.0))

    preferred = [
        "CID", "Title", "CanonicalSMILES", "IsomericSMILES", "InChI", "InChIKey",
        "MolecularFormula", "MolecularWeight", "XLogP", "TPSA", "Complexity",
        "HBondDonorCount", "HBondAcceptorCount", "RotatableBondCount",
        "has_properties", "has_3d_sdf", "has_description", "status", "error", "description",
    ]
    extra = sorted({key for row in rows for key in row} - set(preferred))
    fieldnames = preferred + extra

    metadata_path = out_dir / "metadata.csv"
    with metadata_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    complete = sum(bool(r.get("has_3d_sdf")) and bool(r.get("has_description")) for r in rows)
    print(f"Done. {complete}/{len(rows)} CIDs have both 3D SDF and description.")
    print(f"Metadata: {metadata_path}")


if __name__ == "__main__":
    main()
