#!/usr/bin/env python3
"""Run repeatable retrieval-quality checks against a live NoteFlow API."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_CASES = Path(__file__).parents[1] / "resources" / "search-quality-cases.json"


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def evaluate_case(base_url: str, case: dict) -> dict:
    payload = {
        "query": case["query"],
        "mode": case.get("mode", "MIXED"),
        "topK": case.get("topK", 5),
    }
    if "pdfDocumentIds" in case:
        payload["pdfDocumentIds"] = case["pdfDocumentIds"]
    if "aiNoteDocumentIds" in case:
        payload["aiNoteDocumentIds"] = case["aiNoteDocumentIds"]
    response = post_json(f"{base_url.rstrip('/')}/search", payload)
    results = response.get("results", [])
    expected_ids = set(case.get("expectedDocumentIds", []))
    allowed_document_ids = set(case.get("allowedDocumentIds", []))
    allowed_domains = set(case.get("allowedDomains", []))
    expected_terms = [term.lower() for term in case.get("expectedTerms", [])]

    first_relevant_rank = next(
        (
            index
            for index, result in enumerate(results, start=1)
            if result.get("documentId") in expected_ids
        ),
        None,
    )
    combined_relevant_text = " ".join(
        f"{result.get('title', '')} {result.get('snippet', '')}".lower()
        for result in results
        if result.get("documentId") in expected_ids
    )
    missing_terms = [term for term in expected_terms if term not in combined_relevant_text]
    invalid_domains = sorted(
        {
            result.get("sourceDomain")
            for result in results
            if allowed_domains and result.get("sourceDomain") not in allowed_domains
        }
    )
    invalid_document_ids = sorted(
        {
            result.get("documentId")
            for result in results
            if allowed_document_ids and result.get("documentId") not in allowed_document_ids
        }
    )
    expected_document_share = (
        sum(result.get("documentId") in expected_ids for result in results) / len(results)
        if results
        else 0
    )

    return {
        "name": case["name"],
        "resultCount": len(results),
        "firstRelevantRank": first_relevant_rank,
        "recallAtK": 1 if first_relevant_rank is not None else 0,
        "reciprocalRank": 0 if first_relevant_rank is None else 1 / first_relevant_rank,
        "termsMatched": not missing_terms,
        "missingTerms": missing_terms,
        "domainsValid": not invalid_domains,
        "invalidDomains": invalid_domains,
        "scopeValid": not invalid_document_ids,
        "invalidDocumentIds": invalid_document_ids,
        "expectedDocumentShareAtK": expected_document_share,
        "topResult": (
            {
                "documentId": results[0].get("documentId"),
                "sourceDomain": results[0].get("sourceDomain"),
                "title": results[0].get("title"),
                "score": results[0].get("score"),
            }
            if results
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--min-mrr",
        type=float,
        default=0.75,
        help="Fail when mean reciprocal rank is below this value.",
    )
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    reports = []
    try:
        for case in cases:
            report = evaluate_case(args.base_url, case)
            reports.append(report)
            rank = report["firstRelevantRank"] or "-"
            status = (
                "PASS"
                if report["recallAtK"]
                and report["termsMatched"]
                and report["domainsValid"]
                and report["scopeValid"]
                else "FAIL"
            )
            print(
                f"{status:4} {report['name']:<32} "
                f"rank={rank} target_share={report['expectedDocumentShareAtK']:.2f} "
                f"terms={report['termsMatched']} scope={report['scopeValid']}"
            )
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"Search API request failed: {error}", file=sys.stderr)
        return 2

    recall = sum(report["recallAtK"] for report in reports) / len(reports)
    mrr = sum(report["reciprocalRank"] for report in reports) / len(reports)
    contract_pass_rate = sum(
        report["termsMatched"] and report["domainsValid"] and report["scopeValid"]
        for report in reports
    ) / len(reports)
    mean_target_share = sum(report["expectedDocumentShareAtK"] for report in reports) / len(reports)
    print(
        f"\nRecall@K={recall:.3f} MRR={mrr:.3f} "
        f"contract_pass_rate={contract_pass_rate:.3f} "
        f"mean_target_share@K={mean_target_share:.3f}"
    )

    failed = [
        report
        for report in reports
        if not report["recallAtK"]
        or not report["termsMatched"]
        or not report["domainsValid"]
        or not report["scopeValid"]
    ]
    if failed or mrr < args.min_mrr:
        print("\nDetailed failures:")
        print(json.dumps(failed, indent=2, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
