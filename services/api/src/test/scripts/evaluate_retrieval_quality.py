#!/usr/bin/env python3
"""Evaluate the live vector retrieval and context-building pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_CASES = Path(__file__).parents[1] / "resources" / "search-quality-cases.json"
BLANK_MARKERS = (
    "completely blank",
    "no visible text",
    "no discernible content",
    "image region is blank",
)


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def evaluate_case(base_url: str, case: dict, context_budget: int) -> dict:
    payload = {
        "query": case["query"],
        "mode": case.get("mode", "MIXED"),
        "topK": case.get("topK", 5),
        "maxContextTokens": context_budget,
    }
    for key in ("pdfDocumentIds", "aiNoteDocumentIds"):
        if key in case:
            payload[key] = case[key]

    response = post_json(f"{base_url.rstrip('/')}/retrieval", payload)
    items = response.get("items", [])
    expected_ids = set(case.get("expectedDocumentIds", []))
    allowed_ids = set(case.get("allowedDocumentIds", []))
    allowed_domains = set(case.get("allowedDomains", []))
    expected_terms = [term.lower() for term in case.get("expectedTerms", [])]
    required_channels = set(case.get("requiredChannels", []))

    first_relevant_rank = next(
        (
            index
            for index, item in enumerate(items, start=1)
            if item.get("documentId") in expected_ids
        ),
        None,
    )
    relevant_text = " ".join(
        f"{item.get('title', '')} {item.get('content', '')}".lower()
        for item in items
        if item.get("documentId") in expected_ids
    )
    missing_terms = [term for term in expected_terms if term not in relevant_text]
    invalid_ids = sorted(
        {
            item.get("documentId")
            for item in items
            if allowed_ids and item.get("documentId") not in allowed_ids
        }
    )
    invalid_domains = sorted(
        {
            item.get("sourceDomain")
            for item in items
            if allowed_domains and item.get("sourceDomain") not in allowed_domains
        }
    )
    citations = [item.get("citationId") for item in items]
    expected_citations = [f"S{index}" for index in range(1, len(items) + 1)]
    blank_items = [
        item.get("citationId")
        for item in items
        if any(marker in item.get("content", "").lower() for marker in BLANK_MARKERS)
    ]
    matched_channels = {
        channel
        for item in items
        if item.get("documentId") in expected_ids
        for channel in item.get("matchedChannels", [])
    }
    missing_channels = sorted(required_channels - matched_channels)
    expected_hyde_triggered = case.get("expectedHydeTriggered")
    hyde_triggered = response.get("diagnostics", {}).get("hydeTriggered", False)

    return {
        "name": case["name"],
        "firstRelevantRank": first_relevant_rank,
        "termsMatched": not missing_terms,
        "missingTerms": missing_terms,
        "scopeValid": not invalid_ids,
        "invalidDocumentIds": invalid_ids,
        "domainsValid": not invalid_domains,
        "invalidDomains": invalid_domains,
        "citationsValid": citations == expected_citations,
        "budgetValid": response.get("contextTokenCount", 0) <= context_budget,
        "blankContentFiltered": not blank_items,
        "blankItems": blank_items,
        "requiredChannelsMatched": not missing_channels,
        "missingChannels": missing_channels,
        "hydeExpectationMatched": (
            expected_hyde_triggered is None or hyde_triggered == expected_hyde_triggered
        ),
        "hydeTriggered": hyde_triggered,
        "evidenceStatus": response.get("evidenceStatus"),
        "contextTokenCount": response.get("contextTokenCount", 0),
        "itemCount": len(items),
    }


def passed(report: dict) -> bool:
    return all(
        (
            report["firstRelevantRank"] is not None,
            report["termsMatched"],
            report["scopeValid"],
            report["domainsValid"],
            report["citationsValid"],
            report["budgetValid"],
            report["blankContentFiltered"],
            report["requiredChannelsMatched"],
            report["hydeExpectationMatched"],
            report["evidenceStatus"] in {"SUFFICIENT", "WEAK"},
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--context-budget", type=int, default=4000)
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    reports = []
    try:
        for case in cases:
            report = evaluate_case(args.base_url, case, args.context_budget)
            reports.append(report)
            status = "PASS" if passed(report) else "FAIL"
            rank = report["firstRelevantRank"] or "-"
            print(
                f"{status:4} {report['name']:<32} rank={rank} "
                f"evidence={report['evidenceStatus']:<10} "
                f"items={report['itemCount']} tokens={report['contextTokenCount']}"
            )
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"Retrieval API request failed: {error}", file=sys.stderr)
        return 2

    pass_rate = sum(passed(report) for report in reports) / len(reports)
    mean_reciprocal_rank = sum(
        0 if report["firstRelevantRank"] is None else 1 / report["firstRelevantRank"]
        for report in reports
    ) / len(reports)
    print(f"\npass_rate={pass_rate:.3f} MRR={mean_reciprocal_rank:.3f}")

    failures = [report for report in reports if not passed(report)]
    if failures:
        print(json.dumps(failures, indent=2, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
