"""Bounded single-public-work pilot; source acquisition never includes owner input."""
from pathlib import Path
import argparse
import hashlib
import json
from datetime import datetime, timezone

import httpx

from recommend_book.personal_content_pilot_v1 import assess_candidate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/autonomous-next-v3/evaluation/phase6-personal-content-pilot-v1"


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def acquire():
    """One exact QID request; no retry except a bounded network-error fallback."""
    output = OUTPUT / "acquisition"
    if output.exists():
        raise ValueError("Acquisition namespace exists; no duplicate network requests")
    output.mkdir(parents=True)
    urls = [
        "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q24873892&props=labels%7Caliases%7Cdescriptions%7Cclaims%7Csitelinks&languages=ja%7Cen&format=json",
        "https://www.wikidata.org/wiki/Special:EntityData/Q24873892.json",
    ]
    attempts = []
    for number, url in enumerate(urls, 1):
        receipt = {"attempt": number, "url": url, "started_at": datetime.now(timezone.utc).isoformat(),
                   "purpose": "single_public_Q24873892_structured_work_identity_and_sitelink", "private_inputs": 0}
        try:
            response = httpx.get(url, timeout=45, follow_redirects=True,
                                 headers={"User-Agent": "recommend-book/0.1 bounded public identity pilot"})
            raw = output / f"qid-Q24873892-attempt-{number}.json"
            raw.write_bytes(response.content)
            receipt.update(status="success" if response.is_success else "http_error", status_code=response.status_code,
                           final_url=str(response.url), path=str(raw.resolve()),
                           sha256=hashlib.sha256(response.content).hexdigest(), size=len(response.content))
            attempts.append(receipt)
            write_json(output / f"attempt-{number}-receipt.json", receipt)
            # HTTP errors are observed results, not network errors permitting fallback.
            break
        except httpx.TransportError as error:
            receipt.update(status="network_error", error_type=type(error).__name__, error=str(error))
            attempts.append(receipt)
            write_json(output / f"attempt-{number}-receipt.json", receipt)
    write_json(output / "manifest.json", {"version": "single-public-entity-acquisition-v1", "attempts": attempts,
        "network_requests": len(attempts), "maximum_requests": 2, "private_history_externalized": False})
    return {"attempts": len(attempts), "status": attempts[-1]["status"]}


def assess():
    """Finish a held pilot from preserved bytes, without any further acquisition."""
    acquisition = json.loads((OUTPUT / "acquisition/manifest.json").read_bytes())
    receipts = [row for row in acquisition["attempts"] if row["status"] == "success"]
    if len(receipts) != 1:
        raise ValueError("One successful exact public entity response required")
    receipt = receipts[0]
    proposal_path = ROOT / "data/autonomous-next-v3/evaluation/phase3-serialization-feature-feasibility-v1/runtime-input-proposals.jsonl"
    proposals = [json.loads(line) for line in proposal_path.read_bytes().splitlines() if line.strip()]
    selected = [row for row in proposals if row["work_id"] == "public:wd:Q24873892"]
    if len(selected) != 1:
        raise ValueError("One fixed conditional proposal required")
    proposal = selected[0]
    article_path = Path(proposal["source_raw_path"])
    protected = [
        ROOT / "data/autonomous-next-v3/evaluation/phase3-personal-profile-v1/runtime-v2/private-profile.json",
        ROOT / "src/recommend_book/recommendation/serialization_profile.py",
        ROOT / "src/recommend_book/recommendation/serialization_profile_v3.py",
        ROOT / "src/recommend_book/recommendation/history_binding.py",
        ROOT / "src/recommend_book/wikipedia_paragraphs.py", ROOT / "src/recommend_book/usage_guard.py",
        ROOT / "config/taxonomy.json", ROOT / "config/sources.json",
        ROOT / "data/autonomous-next-v3/database/phase3-personal-poc-v1/comparison.json",
    ]
    digest = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    before = {str(path.resolve()): digest(path) for path in protected}
    status_path = ROOT / "data/autonomous-next-v3/evaluation/phase5-personal-recommendation-status-v1/report.json"
    status = json.loads(status_path.read_bytes())
    if status["version"] != "phase5-personal-recommendation-status-v1":
        raise ValueError("Expected prior aggregate recommendation status")
    report = assess_candidate(Path(receipt["path"]).read_bytes(), receipt["sha256"], article_path.read_bytes(), proposal)
    report.update(network_requests=acquisition["network_requests"], bounded_candidate_n=1,
        acquisition_receipt=receipt,
        source_pins={str(proposal_path.resolve()): digest(proposal_path),
            str(article_path.resolve()): digest(article_path), str(Path(receipt["path"]).resolve()): receipt["sha256"]},
        protected_before_sha256=before,
        protected_after_sha256={str(path.resolve()): digest(path) for path in protected},
        preserved_owner_observation_n=status["owned_observation_n"],
        preserved_profile_bound_reference_n=status["content_connected_n"],
        prior_aggregate_status_source={"path": str(status_path.resolve()), "sha256": digest(status_path)},
        prior_comparison_spec_sha256=status["comparison"]["spec_sha256"],
        prior_comparison_input_sha256=status["comparison"]["input_sha256"],
        prior_comparison_split_sha256=status["comparison"]["split_sha256"],
        prior_comparison_fold_n=status["comparison"]["fold_n"],
        prior_counts_scope="Reused pinned aggregate status; owner HTML/individual ratings not replayed by this assessment.",
        private_profile_file_hashed_n=1, individual_private_rows_parsed_n=0,
        tdd_red="1 collection error: assessment module absent before implementation")
    if report["protected_before_sha256"] != report["protected_after_sha256"]:
        raise ValueError("Protected source/profile changed during assessment")
    report["protected_sources_and_profile_unchanged"] = True
    report["implementation_sha256"] = {str(path.resolve()): digest(path) for path in [Path(__file__),
        ROOT / "src/recommend_book/personal_content_pilot_v1.py", ROOT / "tests/test_personal_content_pilot_v1.py"]}
    write_json(OUTPUT / "report.json", report)
    return {key: report[key] for key in ("source_scope_status", "network_requests", "model_calls", "comparison_reruns")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["acquire", "assess"])
    arguments = parser.parse_args()
    print(json.dumps(acquire() if arguments.command == "acquire" else assess(), ensure_ascii=False))
