"""Audit bounded retrieval frames without presenting them as all Japanese novels.

Label rows are query scopes (often publishing imprints), never story series.
Query evidence schema is deliberately explicit: label_id, term,
partitioning_complete, partitions[{id, expected_total, terminal,
pages[{start, record_ids, raw_sha256}]}].  A producer must derive this evidence
from retained API receipts; this module checks accounting, not remote truth.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def adapt_ndl_index(index: dict) -> list[dict]:
    """Replay current collector raw SRU pages and verify complete year splitting.

    This adapter is read-only and never sends network requests. A failed receipt,
    damaged raw file, edited parser ID list or missing facet partition remains
    incomplete, even when a producer has written complete=true.
    """
    out = []
    ns = {'sru': 'http://www.loc.gov/zing/srw/', 'dcndl': 'http://ndl.go.jp/dcndl/terms/'}
    rdf_about = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about'
    def replay(part):
        receipt = part.get('receipt', {})
        raw = Path(receipt['raw_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt.get('raw_sha256'):
            raise ValueError('raw_hash_mismatch')
        xml = ET.fromstring(raw)
        total = int(xml.findtext('sru:numberOfRecords', namespaces=ns))
        raw_ids = [node.get(rdf_about, '').rsplit('/', 1)[-1]
                   for node in xml.findall('.//dcndl:BibAdminResource', ns)]
        if raw_ids != part.get('returned_record_ids', []) or total != part.get('expected_total'):
            raise ValueError('raw_result_mismatch')
        return xml
    for label_id, label in index.get('labels', {}).items():
        for term, state in label.get('terms', {}).items():
            issues = []
            root = state.get('root', {})
            parts = state.get('partitions') or [root]
            partitioning = bool(state.get('complete'))
            if root.get('expected_total', 0) > 500:
                facets = root.get('year_facets', {})
                # Every leaf is independently replayed below; the parent must
                # also account for every year, not only returned child totals.
                years = {str(part.get('partition'))[:4] for part in parts}
                partitioning = (partitioning and bool(facets) and sum(facets.values()) == root['expected_total']
                                and years == set(facets))
                annual_parents = {str(parent.get('partition')):parent for parent in state.get('year_parent_pages',[])}
                for year, expected in facets.items():
                    leaves = [part for part in parts if str(part.get('partition'))[:4] == year]
                    if len(leaves) == 1 and str(leaves[0].get('partition')) == year:
                        partitioning = partitioning and leaves[0].get('expected_total') == expected
                        continue
                    # A large annual query is replaced only by a complete,
                    # disjoint calendar of twelve individually replayed months.
                    month_names = {str(part.get('partition')) for part in leaves}
                    calendar = {f'{year}-{month:02}' for month in range(1,13)}
                    parent = annual_parents.get(year)
                    partitioning = (partitioning and len(leaves) == 12 and month_names == calendar
                                    and sum(part.get('expected_total', -1) for part in leaves) == expected
                                    and parent is not None and parent.get('expected_total') == expected)
                    if parent is not None:
                        try:
                            replay(parent)
                        except (OSError, KeyError, TypeError, ValueError, ET.ParseError) as exc:
                            partitioning = False
                            issues.append(f'annual_raw_replay_failed:{type(exc).__name__}')
                try:
                    parent_xml = replay(root)
                    year_node = parent_xml.find('.//sru:lst[@name="ISSUED_DATE"]', ns)
                    raw_facets = {node.get('name'): int(node.text) for node in year_node} if year_node is not None else {}
                    if facets != raw_facets:
                        raise ValueError('parent_facets_changed')
                except (OSError, KeyError, TypeError, ValueError, ET.ParseError) as exc:
                    partitioning = False
                    issues.append(f'parent_raw_replay_failed:{type(exc).__name__}')
            partitions = []
            for number, part in enumerate(parts):
                receipt = part.get('receipt', {})
                raw_sha = receipt.get('raw_sha256', '')
                ids = part.get('returned_record_ids', [])
                terminal = bool(part.get('complete'))
                try:
                    replay(part)
                except (OSError, KeyError, TypeError, ValueError, ET.ParseError) as exc:
                    terminal = False
                    issues.append(f'raw_replay_failed:{type(exc).__name__}')
                partitions.append({'id': str(part.get('partition') or f'all-years-{number}'),
                                   'expected_total': part.get('expected_total'), 'terminal': terminal,
                                   'pages': [{'start': part.get('requested_position', 1), 'record_ids': ids,
                                              'raw_sha256': raw_sha}]})
            out.append({'label_id': label_id, 'term': term, 'partitioning_complete': partitioning,
                        'partitions': partitions, 'adapter_issues': sorted(set(issues))})
    return out


def check_partition(partition: dict) -> tuple[set[str], list[str]]:
    issues = []
    expected = partition.get('expected_total')
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        return set(), ['invalid_expected_total']
    if not partition.get('terminal'):
        issues.append('partition_not_terminal')
    ids = set()
    next_start = 1
    pages = partition.get('pages', [])
    if not pages:
        issues.append('missing_page_evidence')
    for page in sorted(pages, key=lambda p: p.get('start', 0)):
        if page.get('start') != next_start:
            issues.append('page_gap_or_overlap')
        records = page.get('record_ids', [])
        if not all(isinstance(value, str) and value for value in records):
            issues.append('invalid_record_ids')
            records = [value for value in records if isinstance(value, str) and value]
        ids.update(records)
        next_start = page.get('start', 0) + len(records)
        if not re.fullmatch('[a-f0-9]{64}', page.get('raw_sha256', '')):
            issues.append('missing_raw_hash')
    if len(ids) != expected:
        issues.append('unique_record_count_mismatch')
    if next_start != expected + 1:
        issues.append('returned_record_count_mismatch')
    return ids, sorted(set(issues))


def audit(labels: list[dict], queries: list[dict], reference_series: list[dict] | None = None,
          identity_mappings: list[dict] | None = None) -> dict:
    label_ids = [row['id'] for row in labels]
    if len(set(label_ids)) != len(label_ids):
        raise ValueError('duplicate label ids in denominator')
    by_label = defaultdict(list)
    for query in queries:
        if query.get('label_id') not in label_ids:
            raise ValueError('query references label outside denominator')
        by_label[query['label_id']].append(query)
    results = []
    all_ids = set()
    scopes = defaultdict(Counter)
    for label in labels:
        these_queries = by_label.get(label['id'], [])
        issues = []
        label_records = set()
        if not these_queries:
            status = 'unqueried'
        else:
            required_terms = set(label.get('query_terms', [label['label']]))
            queried_terms = {row.get('term') for row in these_queries}
            if required_terms - queried_terms:
                issues.append('unqueried_alias_term')
            for query in these_queries:
                issues.extend(query.get('adapter_issues', []))
                if not query.get('partitioning_complete'):
                    issues.append('partitioning_incomplete')
                partitions = query.get('partitions', [])
                if not partitions:
                    issues.append('missing_partitions')
                partition_ids = [part.get('id') for part in partitions]
                if len(partition_ids) != len(set(partition_ids)):
                    issues.append('duplicate_partition_ids')
                query_records = set()
                for partition in partitions:
                    ids, found = check_partition(partition)
                    if query_records & ids:
                        issues.append('overlapping_partitions')
                    query_records.update(ids)
                    label_records.update(ids)
                    issues.extend(found)
            status = 'complete' if not issues else 'incomplete'
        all_ids.update(label_records)
        scopes[label.get('scope', 'unknown')][status] += 1
        results.append({'label_id': label['id'], 'label': label['label'],
                        'publisher': label.get('publisher'), 'scope': label.get('scope', 'unknown'),
                        'primary_verification': label.get('verification_status', 'unknown'),
                        'retrieval_status': status, 'retrieved_unique_edition_ids': len(label_records),
                        'issues': sorted(set(issues))})
    coverage = None
    if reference_series is not None:
        reference_ids = {row['reference_id'] for row in reference_series if row.get('kind') == 'series' and row.get('source_url')}
        if len(reference_ids) != len(reference_series):
            raise ValueError('reference series require distinct IDs, kind=series and source URLs')
        matched = {row['reference_id'] for row in (identity_mappings or [])
                   if row.get('kind') == 'series' and row.get('verified') is True
                   and row.get('catalogue_id') and row.get('relation_evidence')}
        coverage = {'denominator': 'supplied independent source roster only',
                    'reference_series': len(reference_ids), 'matched_series': len(reference_ids & matched),
                    'unmatched_reference_ids': sorted(reference_ids - matched)}
    return {'schema_version': 'light-novel-coverage-audit-v1',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'population': 'explicit label query frame; not every Japanese light novel',
            'labels_in_frame': len(labels),
            'retrieval_complete_labels': sum(row['retrieval_status'] == 'complete' for row in results),
            'unqueried_labels': [row['label_id'] for row in results if row['retrieval_status'] == 'unqueried'],
            'retrieved_unique_edition_ids': len(all_ids), 'scope_counts': dict(scopes),
            'series_coverage': coverage, 'japanese_national_coverage_proved': False,
            'limitations': ['Label presence and edition retrieval do not establish story-series identities.',
                           'A publisher page may omit out-of-print books; a national bibliography may omit undeposited and electronic-only items.',
                           'No national coverage percentage is inferred from a chosen label roster or a 1000-series milestone.',
                           'Retrieval accounting depends on producer evidence; raw receipts must be independently hash-checked.'],
            'labels': results}


def read_rows(path: Path | None) -> list[dict]:
    if path is None:
        return []
    text = path.read_text(encoding='utf-8')
    if path.suffix == '.jsonl':
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    if not isinstance(value, list):
        raise ValueError(f'{path.name} must contain an array')
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--queries', type=Path)
    parser.add_argument('--ndl-index', type=Path, help='Read collector index and independently replay retained raw SRU pages')
    parser.add_argument('--reference-series', type=Path)
    parser.add_argument('--identity-mappings', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    queries = read_rows(args.queries)
    if args.ndl_index:
        queries.extend(adapt_ndl_index(json.loads(args.ndl_index.read_text(encoding='utf-8'))))
    report = audit(read_rows(args.labels), queries,
                   read_rows(args.reference_series) if args.reference_series else None,
                   read_rows(args.identity_mappings))
    report['inputs'] = [{'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                        for path in (args.labels, args.queries, args.ndl_index,
                                     args.reference_series, args.identity_mappings) if path]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('labels_in_frame', 'retrieval_complete_labels', 'retrieved_unique_edition_ids', 'japanese_national_coverage_proved')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
