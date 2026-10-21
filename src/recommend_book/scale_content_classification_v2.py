"""Output-rights-reviewed public projection over unchanged native v1 evidence.

This automatic output review is separate from semantic review. It checks the
actual published closed-taxonomy records and exact quotations, retains source
credits, and publishes this projection's contribution under CC BY-SA 4.0.
It is not human review, independent legal clearance, or a novel's license.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

from .models import WorkInput
from .usage_guard import model_hash

VERSION = 'scale-content-native-classification-export-v2'
LICENSE = 'CC-BY-SA-4.0'
LICENSE_URL = 'https://creativecommons.org/licenses/by-sa/4.0/'
OBLIGATIONS = {'attribution', 'share_alike', 'mark_changes', 'third_party_text_screened'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write('\n')


def _wp_url(value, *, revision=None, history=False):
    if not isinstance(value, str):
        raise ValueError('Missing public Wikipedia attribution URL')
    url = urlparse(value)
    if url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.wikipedia.org') or url.username or url.password:
        raise ValueError('Unsafe or non-Wikipedia attribution URL')
    query = parse_qs(url.query)
    if revision is not None and query.get('oldid') != [str(revision)]:
        raise ValueError('Article permalink does not identify exact revision')
    if history and (query.get('action') != ['history'] or not query.get('title')):
        raise ValueError('Contributor history URL absent')
    return value


def review_public_rows(rows, works, taxonomy):
    """Review actual displayed record fields; never trust v1 display flags."""
    label_axes = {label['label_id']: label['axis'] for label in taxonomy['labels']}
    by_id = {work.work_id: work for work in works}
    if len(by_id) != len(works):
        raise ValueError('Duplicate actual input identity')
    output, audits = [], []
    for row in rows:
        work = by_id[row['input_work_id']]
        if model_hash(work) != row['input_hash']:
            raise ValueError('Output-rights input hash differs from native admission')
        evidence = {item.evidence_id: item for item in work.evidence}
        content_ids = set(row['content_evidence_ids'])
        if not content_ids <= evidence.keys():
            raise ValueError('Actual retained content evidence absent')
        attrs = []
        quoted_ids = {citation['evidence_id'] for label in row['admitted_labels']
                      for citation in label.get('evidence', [])
                      if isinstance(citation.get('quote'), str) and citation['quote'].strip()}
        for evidence_id in sorted(content_ids):
            item = evidence[evidence_id]
            if (item.rights_policy_id != 'wikipedia-screened-prose' or not item.llm_input_allowed
                    or item.kind not in ('synopsis', 'excerpt', 'note')
                    or item.license != LICENSE or item.license_url != LICENSE_URL):
                raise ValueError('Public content output lacks supported source license/policy')
            attribution = item.attribution
            revision = attribution.get('revision_id')
            if (type(revision) is not int or revision <= 0 or not item.locator
                    or not isinstance(attribution.get('credit'), str) or not attribution['credit'].strip()
                    or not isinstance(attribution.get('changes'), str) or not attribution['changes'].strip()
                    or not OBLIGATIONS <= set(attribution.get('fulfilled_obligations', []))
                    or not OBLIGATIONS <= set(item.inherited_restrictions)
                    or f'revision:{revision};' not in item.locator):
                raise ValueError('Public content attribution/change/screening obligations incomplete')
            permalink = _wp_url(attribution.get('permalink'), revision=revision)
            source_url = _wp_url(item.source_url, revision=revision)
            history = _wp_url(attribution.get('history_url'), history=True)
            attrs.append({'evidence_ids': [evidence_id], 'source_url': source_url,
                'license': LICENSE, 'license_url': LICENSE_URL,
                'attribution': {'credit': attribution['credit'], 'permalink': permalink,
                                'history_url': history, 'changes': attribution['changes']},
                'locator': item.locator, 'revision': revision,
                'display_allowed': True, 'quote_allowed': evidence_id in quoted_ids,
                'changed_from_source': attribution['changes'] + '; closed-taxonomy AI classification added; original native record preserved',
                'output_license': LICENSE, 'output_license_url': LICENSE_URL})
        citation_count, quote_count, summary_count = 0, 0, 0
        for label in row['admitted_labels']:
            if (label.get('label_id') not in label_axes or label.get('axis') != label_axes[label['label_id']]
                    or label.get('scope') != 'work' or label.get('decision') != 'present'):
                raise ValueError('Output is not an admitted closed-taxonomy work label')
            if not label.get('evidence'):
                raise ValueError('Published label has no actual citation')
            for citation in label['evidence']:
                item = evidence[citation['evidence_id']]
                quote = citation.get('quote')
                summary = citation.get('summary')
                if not item.llm_input_allowed:
                    raise ValueError('Public citation is not permitted retained source evidence')
                if quote not in (None, ''):
                    if not isinstance(quote, str) or not quote.strip() or quote not in item.text:
                        raise ValueError('Public output quotation is not exact retained source text')
                    quote_count += 1
                else:
                    if not isinstance(summary, str) or not summary.strip():
                        raise ValueError('Public reference needs exact quote or nonempty generated summary')
                    # An actual semantic-reviewed summary is generated adapted
                    # expression under this record's CC-BY-SA output license.
                    # It is never attributed as a verbatim source quotation.
                    summary_count += 1
                if citation.get('locator') != item.locator:
                    raise ValueError('Public citation locator differs from retained source')
                if item.rights_policy_id != 'wikidata-cc0' and item.evidence_id not in content_ids:
                    raise ValueError('Output quote is outside exact reviewed licensed content')
                citation_count += 1
        output.append({**row, 'source_attributions': attrs})
        audits.append({'qid': row['qid'], 'input_hash': row['input_hash'],
            'reviewed_label_count': len(row['admitted_labels']), 'exact_citation_count': citation_count,
            'exact_quote_count': quote_count, 'generated_summary_adaptation_count': summary_count,
            'reviewed_content_evidence_ids': sorted(content_ids),
            'public_source_attributions_sha256': model_hash(attrs), 'output_license': LICENSE})
    return output, {'version': 'scale-content-output-rights-review-v1',
        'review_kind': 'automatic_actual_output_rights_review', 'human_reviewed': False,
        'human_verified': False, 'commercial_cleared': False,
        'legal_clearance_claimed': False, 'output_license': LICENSE, 'output_license_url': LICENSE_URL,
        'license_scope': 'This publication record contribution and retained quoted adaptations; source novel rights unchanged',
        'checks': ['closed_taxonomy', 'actual_input_hash', 'exact_retained_quotation',
                   'summary_only_adapted_output_license_without_quote_permission',
                   'exact_content_evidence_ids', 'source_license', 'credit_revision_history_locator',
                   'screening_proof_in_native_display_guard', 'share_alike_output_license', 'mark_changes'],
        'rows': audits}


def _review_parent(parent_path, expected_sha):
    from .scale_content_classification_v1 import verify_catalogue_classification
    parent_path = Path(parent_path).resolve(strict=True)
    parent_raw = parent_path.read_bytes()
    if hashlib.sha256(parent_raw).hexdigest() != expected_sha:
        raise ValueError('Native parent export changed')
    parent = json.loads(parent_raw)
    # Full genuine native/usage/display replay, independent of saved flags.
    authenticated = verify_catalogue_classification(parent_path, expected_sha)
    rows = parent['verified_projection']['rows']
    if {r['qid'] for r in authenticated} != {r['qid'] for r in rows}:
        raise ValueError('Authenticated parent row set differs')
    source_path = Path(parent['source_preparation']) / 'manifest.json'
    if sha(source_path) != parent['source_preparation_sha256']:
        raise ValueError('Source preparation changed before output review')
    source = json.loads(source_path.read_bytes())
    inputs_path = Path(source['ordered_inputs_path'])
    if sha(inputs_path) != source['ordered_inputs_sha256']:
        raise ValueError('Actual licensed output-review inputs changed')
    works = [WorkInput.model_validate_json(line) for line in inputs_path.read_bytes().splitlines() if line.strip()]
    taxonomy_path = Path(parent['runtime_preparation']) / 'runtime/config/taxonomy.json'
    ledger_path = Path(parent['usage_ledger'])
    ledger = json.loads(ledger_path.read_bytes())
    registry_path = Path(ledger['registry_path'])
    if sha(registry_path) != ledger['registry_file_sha256']:
        raise ValueError('Actual output rights policy file changed')
    reviewed_rows, review = review_public_rows(rows, works, json.loads(taxonomy_path.read_bytes()))
    review.update(parent_manifest_path=str(parent_path), parent_manifest_sha256=expected_sha,
                  ordered_inputs_path=str(inputs_path), ordered_inputs_sha256=sha(inputs_path),
                  taxonomy_path=str(taxonomy_path), taxonomy_sha256=sha(taxonomy_path),
                  registry_path=str(registry_path), registry_sha256=sha(registry_path),
                  official_conditions=ledger['official_conditions'],
                  public_projection_sha256=model_hash(reviewed_rows),
                  original_native_projection_sha256=model_hash(parent['verified_projection']),
                  verifier_sha256=sha(__file__))
    return parent, reviewed_rows, review


def create_reviewed_export(parent_path, expected_sha, output_dir):
    """Observe a separate actual output-review process, preserving v1 bytes."""
    parent_path = Path(parent_path).resolve(strict=True)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / 'frozen-output-verifier.py'
    frozen.write_bytes(Path(__file__).read_bytes())
    code = ('import json,sys; from recommend_book.scale_content_classification_v2 import _review_parent; '
            'p,r,a=_review_parent(sys.argv[1],sys.argv[2]); '
            'print(json.dumps({"rows":r,"review":a},ensure_ascii=True,allow_nan=False))')
    argv = [sys.executable, '-c', code, str(parent_path), expected_sha]
    save(output / 'request.json', {'argv': argv, 'shell': False, 'verifier_path': str(Path(__file__).resolve()),
                                  'verifier_sha256': sha(__file__), 'parent_manifest_sha256': expected_sha})
    started = time.monotonic()
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=Path(__file__).resolve().parents[2], shell=False) as process:
        save(output / 'process-start.json', {'pid': process.pid, 'started_at': datetime.now(timezone.utc).isoformat()})
        stdout, stderr = process.communicate()
        observed = {'pid': process.pid, 'actual_exit_code': process.returncode, 'process_exit_observed': True,
                    'elapsed_seconds': time.monotonic() - started}
    (output / 'stdout.json').write_bytes(stdout)
    (output / 'stderr.txt').write_bytes(stderr)
    save(output / 'process-exit.json', observed)
    if process.returncode:
        raise ValueError('Actual separate output-rights review failed: ' + stderr.decode('utf-8', 'replace')[-2000:])
    result = json.loads(stdout)
    save(output / 'output-rights-review.json', result['review'])
    parent = json.loads(parent_path.read_bytes())
    projection = {**parent['verified_projection'], 'rows': result['rows']}
    pins = {**parent['artifact_sha256'], str(parent_path): expected_sha,
            str(Path(__file__).resolve()): sha(__file__)}
    pins.update({str(path): sha(path) for path in output.iterdir() if path.is_file()})
    document = {**parent, 'version': VERSION, 'verified_projection': projection, 'artifact_sha256': pins,
        'native_parent_manifest': str(parent_path), 'native_parent_manifest_sha256': expected_sha,
        'output_rights_review': str(output / 'output-rights-review.json'),
        'output_rights_review_process': str(output / 'process-exit.json'),
        'frozen_output_verifier': str(frozen), 'output_verifier_sha256': sha(__file__),
        'output_license': LICENSE, 'output_license_url': LICENSE_URL}
    manifest = output / 'manifest.json'
    save(manifest, document)
    return {'manifest': str(manifest), 'sha256': sha(manifest), 'actual_output_review_process': observed}


def verify_catalogue_classification(manifest_path, expected_sha256):
    path = Path(manifest_path).resolve(strict=True)
    raw = path.read_bytes()
    if not expected_sha256 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Rights-reviewed export SHA differs')
    document = json.loads(raw)
    if document.get('version') != VERSION:
        raise ValueError('Output-rights-reviewed content export v2 required')
    if (document['output_verifier_sha256'] != sha(__file__)
            or Path(document['frozen_output_verifier']).read_bytes() != Path(__file__).read_bytes()):
        raise ValueError('Output verifier differs from frozen code')
    review_dir = Path(document['output_rights_review_process']).parent
    required = {str(Path(document[key]).resolve(strict=True)) for key in (
        'native_parent_manifest', 'output_rights_review', 'output_rights_review_process', 'frozen_output_verifier')}
    required.update(str((review_dir / name).resolve(strict=True)) for name in (
        'request.json', 'process-start.json', 'stdout.json', 'stderr.txt'))
    required.add(str(Path(__file__).resolve()))
    if not required <= document['artifact_sha256'].keys():
        raise ValueError('Output review lacks mandatory actual process/code pins')
    for name, digest in document['artifact_sha256'].items():
        if not Path(name).is_absolute() or sha(name) != digest:
            raise ValueError('Rights-reviewed output artifact changed: ' + name)
    parent, rows, review = _review_parent(document['native_parent_manifest'], document['native_parent_manifest_sha256'])
    if (review != json.loads(Path(document['output_rights_review']).read_bytes())
            or document['verified_projection'] != {**parent['verified_projection'], 'rows': rows}):
        raise ValueError('Output rights review or public projection differs on independent replay')
    process = json.loads(Path(document['output_rights_review_process']).read_bytes())
    request = json.loads(Path(document['output_rights_review_process']).with_name('request.json').read_bytes())
    start = json.loads(Path(document['output_rights_review_process']).with_name('process-start.json').read_bytes())
    stdout = json.loads((review_dir / 'stdout.json').read_bytes())
    if (process.get('actual_exit_code') != 0 or process.get('process_exit_observed') is not True
            or type(process.get('pid')) is not int or process['pid'] <= 0 or process['pid'] != start['pid']
            or request['verifier_sha256'] != sha(__file__)
            or request.get('shell') is not False or request.get('verifier_path') != str(Path(__file__).resolve())
            or request['parent_manifest_sha256'] != document['native_parent_manifest_sha256']
            or stdout != {'rows': rows, 'review': review}
            or document.get('output_license') != LICENSE or document.get('output_license_url') != LICENSE_URL):
        raise ValueError('Actual separate output-rights review process/license differs')
    if path.read_bytes() != raw or any(sha(name) != digest for name, digest in document['artifact_sha256'].items()):
        raise ValueError('Rights-reviewed export changed during verification')
    return [{'qid': row['qid'], 'work_id': row['work_id'], 'input_work_id': row['input_work_id'],
        'labels': row['admitted_labels'], 'provenance': {
            'export_manifest_path': str(path), 'export_manifest_sha256': expected_sha256,
            **{key: row[key] for key in ('run_id', 'native_result', 'label_admission', 'native_provenance',
                'input_hash', 'source_record_ids', 'content_evidence_ids', 'source_attributions')},
            'output_license': LICENSE, 'output_license_url': LICENSE_URL,
            'artifacts': [{'path': str(path), 'sha256': expected_sha256},
                          *[{'path': name, 'sha256': digest} for name, digest in document['artifact_sha256'].items()]]}}
        for row in rows]
