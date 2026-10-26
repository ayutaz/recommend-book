# Documentation index

更新日: 2026-10-26。これはリポジトリ内のMarkdownを、再利用する現行ガイド、最新状態を反映した更新済み記録、記録日を基準に読む履歴、固定保存するピン留め原本に分ける台帳です。本文の件数・実行結果・計画は無断で現在値へ置き換えず、現在のプロジェクト状態は [current-status.md](current-status.md) を参照してください。

## 監査時点の状態

- 実行中のデータ取得・分類ジョブはありません。
- 検証済み実装の基準は `feat/japanese-light-novels-v1` の `8ea2eb6`（統合先PR #31。マージ状態はPR参照）です。後続の文書コミットでブランチ先頭が変わっても、このSHAは実装確認の基準として扱います。世界側20,000分類のPR #30は `main` へ反映済みです。
- 最新ライトノベル台帳の観測値は、story series 2,218、content series 584、content work 1、memberships 5,163、references 10,094、entries 100,884です。混在する対象種別を含むスナップショットであり、独立した作品数・品質合格数・利用許諾済み件数ではありません。
- API・ソースの初期調査は候補整理の履歴です。利用開始時は提供元の一次資料、項目、用途、規約、料金、認証、レート制限を再確認してください。初期調査は現在の採用・利用許諾を示しません。

監査分類は「現行ガイド・仕様」「更新済み現行記録」「履歴・結果・計画・スナップショット」「ピン留め原本」の4区分です。初期API・ソース調査は履歴区分の「利用確認待ち」サブセット、ライトノベルの最新資料は更新済み現行記録のサブセットとして数え、全tracked文書をいずれかへ割り当てています。

## 現行ガイド・仕様

再利用を想定する手順、入出力契約、評価・権利・分類の仕様です。実装やデータの現在値は `current-status.md` と本文の前提を確認してください。

- [autonomous-database-guide.md](autonomous-database-guide.md)
- [autonomous-delivery-guide.md](autonomous-delivery-guide.md)
- [ci-validation-guide.md](ci-validation-guide.md)
- [classification-cache-migration-guide.md](classification-cache-migration-guide.md)
- [classification-database-import-guide.md](classification-database-import-guide.md)
- [classification-execution-freeze-guide.md](classification-execution-freeze-guide.md)
- [classification-observation-guide.md](classification-observation-guide.md)
- [classification-stability-guide.md](classification-stability-guide.md)
- [combined-poc-guide.md](combined-poc-guide.md)
- [combined-production-inputs-guide.md](combined-production-inputs-guide.md)
- [content-update-verification-v1.md](content-update-verification-v1.md)
- [dataset-audit-guide.md](dataset-audit-guide.md)
- [dataset-update-verification-v1.md](dataset-update-verification-v1.md)
- [documentation-index.md](documentation-index.md)
- [evaluation-guide.md](evaluation-guide.md)
- [evaluation-numeric-boundaries-v2-guide.md](evaluation-numeric-boundaries-v2-guide.md)
- [guarded-classification-v3.md](guarded-classification-v3.md)
- [human-taxonomy-v1-guide.md](human-taxonomy-v1-guide.md)
- [implementation-contracts.md](implementation-contracts.md)
- [narou-reference-adapter.md](narou-reference-adapter.md)
- [native-snapshot-runtime-v1.md](native-snapshot-runtime-v1.md)
- [production-classification-guide.md](production-classification-guide.md)
- [prose-integration-guide.md](prose-integration-guide.md)
- [quality-evaluation-protocol-v1.md](quality-evaluation-protocol-v1.md)
- [recommendation-engine.md](recommendation-engine.md)
- [recommendation-overlay-guide.md](recommendation-overlay-guide.md)
- [recommendation-poc-guide.md](recommendation-poc-guide.md)
- [relevance-observation-v2-guide.md](relevance-observation-v2-guide.md)
- [relevance-v2-validation-guide.md](relevance-v2-validation-guide.md)
- [resolution-review-guide.md](resolution-review-guide.md)
- [source-rights.md](source-rights.md)
- [usage-guard-v3.md](usage-guard-v3.md)
- [world-recommendation-adapter.md](world-recommendation-adapter.md)

## 履歴・結果・計画・スナップショット

日付、バージョン、実行、監査、結果を記録した資料です。本文の「現在」「最新」「実行中」は、本文に記された記録日時の状態として読み、現状の判断には `current-status.md` を優先します。

- [autonomous-commercial-scale-preparation.md](autonomous-commercial-scale-preparation.md)
- [autonomous-completion-plan-v2.md](autonomous-completion-plan-v2.md)
- [autonomous-completion-progress.md](autonomous-completion-progress.md)
- [autonomous-enrichment-run.md](autonomous-enrichment-run.md)
- [autonomous-next-delivery-report-v3.md](autonomous-next-delivery-report-v3.md)
- [autonomous-next-phase-plan-v3.md](autonomous-next-phase-plan-v3.md)
- [autonomous-next-phase-plan-v4.md](autonomous-next-phase-plan-v4.md)
- [autonomous-next-phase-plan-v5.md](autonomous-next-phase-plan-v5.md)
- [autonomous-next-phase-plan-v6.md](autonomous-next-phase-plan-v6.md)
- [autonomous-next-phase-plan-v7.md](autonomous-next-phase-plan-v7.md)
- [autonomous-next-phase-plan-v8.md](autonomous-next-phase-plan-v8.md)
- [autonomous-next-phase-plan-v9.md](autonomous-next-phase-plan-v9.md)
- [autonomous-next-phase-plan-v10.md](autonomous-next-phase-plan-v10.md)
- [autonomous-next-phase-plan-v11.md](autonomous-next-phase-plan-v11.md)
- [autonomous-next-phase-plan-v12.md](autonomous-next-phase-plan-v12.md)
- [autonomous-next-phase-plan-v13.md](autonomous-next-phase-plan-v13.md)
- [autonomous-next-phase-plan-v14.md](autonomous-next-phase-plan-v14.md)
- [autonomous-next-phase-plan-v15.md](autonomous-next-phase-plan-v15.md)
- [autonomous-next-phase-plan-v16.md](autonomous-next-phase-plan-v16.md)
- [autonomous-next-phase-plan-v17.md](autonomous-next-phase-plan-v17.md)
- [autonomous-next-phase-plan-v18.md](autonomous-next-phase-plan-v18.md)
- [autonomous-next-phase-plan-v21.md](autonomous-next-phase-plan-v21.md)
- [autonomous-next-phase-plan-v22.md](autonomous-next-phase-plan-v22.md)
- [autonomous-next-phase-plan-v23.md](autonomous-next-phase-plan-v23.md)
- [autonomous-next-phase-plan-v24.md](autonomous-next-phase-plan-v24.md)
- [autonomous-poc-final-report.md](autonomous-poc-final-report.md)
- [autonomous-recommendation-evaluation.md](autonomous-recommendation-evaluation.md)
- [autonomous-reference-run.md](autonomous-reference-run.md)
- [autonomous-resolution-run.md](autonomous-resolution-run.md)
- [autonomous-silver-run.md](autonomous-silver-run.md)
- [bounded-scope-repair-v1.md](bounded-scope-repair-v1.md)
- [calibration-candidate-diagnostic.md](calibration-candidate-diagnostic.md)
- [calibration-feasibility-v1.md](calibration-feasibility-v1.md)
- [codex-diagnostic-preparation-v1.md](codex-diagnostic-preparation-v1.md)
- [conssa-main-crosswalk-v1.md](conssa-main-crosswalk-v1.md)
- [enrichment-classification-preparation.md](enrichment-classification-preparation.md)
- [enrichment-classification-run-record.md](enrichment-classification-run-record.md)
- [evaluation-balanced-v1.md](evaluation-balanced-v1.md)
- [evaluation-poc-frozen-v2.md](evaluation-poc-frozen-v2.md)
- [evaluation-ui-smoke.md](evaluation-ui-smoke.md)
- [external-genre-reference-v3.md](external-genre-reference-v3.md)
- [next-database-v3.md](next-database-v3.md)
- [next-recommendation-v3.md](next-recommendation-v3.md)
- [next-reference-v3.md](next-reference-v3.md)
- [next-resolution-v3.md](next-resolution-v3.md)
- [ndl-resolution-coverage.md](ndl-resolution-coverage.md)
- [light-novel-publisher-source-group-proposal-v1.md](light-novel-publisher-source-group-proposal-v1.md)
- [one-hour-continuation-2026-10-25.md](one-hour-continuation-2026-10-25.md)
- [openlibrary-enrichment-validation.md](openlibrary-enrichment-validation.md)
- [personal-content-pilot-v1.md](personal-content-pilot-v1.md)
- [personal-recommendation-current-assessment-v1.md](personal-recommendation-current-assessment-v1.md)
- [phase8-quality-evaluation-v1.md](phase8-quality-evaluation-v1.md)
- [phase9-evidence-and-scaling-v1.md](phase9-evidence-and-scaling-v1.md)
- [phase10-recommendation-and-batch-v1.md](phase10-recommendation-and-batch-v1.md)
- [phase11-evidence-and-evaluation-v1.md](phase11-evidence-and-evaluation-v1.md)
- [phase12-targeted-evidence-and-delivery-v1.md](phase12-targeted-evidence-and-delivery-v1.md)
- [phase13-balanced-poc-and-delivery-v1.md](phase13-balanced-poc-and-delivery-v1.md)
- [phase14-identity-and-label-admission-v1.md](phase14-identity-and-label-admission-v1.md)
- [phase15-bounded-poc-and-delivery-v1.md](phase15-bounded-poc-and-delivery-v1.md)
- [phase16-flow-measurement-and-delivery-v1.md](phase16-flow-measurement-and-delivery-v1.md)
- [phase17-bounded-improvements-v1.md](phase17-bounded-improvements-v1.md)
- [pilot-candidate-selection-v1.md](pilot-candidate-selection-v1.md)
- [poc-completion-audit.md](poc-completion-audit.md)
- [poc-execution-report.md](poc-execution-report.md)
- [poc-quality-gates-v1.md](poc-quality-gates-v1.md)
- [production-v2-run-record.md](production-v2-run-record.md)
- [prose-evidence-plan.md](prose-evidence-plan.md)
- [public-rating-evaluation-feasibility-v1.md](public-rating-evaluation-feasibility-v1.md)
- [recommendation-error-analysis.md](recommendation-error-analysis.md)
- [recommendation-evaluation.md](recommendation-evaluation.md)
- [recommendation-poc-run-record.md](recommendation-poc-run-record.md)
- [reference-evaluation-sources.md](reference-evaluation-sources.md)
- [relevance-contract-review.md](relevance-contract-review.md)
- [relevance-v2-development-verification.md](relevance-v2-development-verification.md)
- [scale-catalogue-v1.md](scale-catalogue-v1.md)
- [scale-catalogue-v2.md](scale-catalogue-v2.md)
- [scale-catalogue-v3.md](scale-catalogue-v3.md)
- [scale-catalogue-v4.md](scale-catalogue-v4.md)
- [scale-classification-v4.md](scale-classification-v4.md)
- [scaling-readiness-v1.md](scaling-readiness-v1.md)
- [source-mapping-resolution.md](source-mapping-resolution.md)
- [toc-candidate-v1.md](toc-candidate-v1.md)
- [wikidata-original-assertion-review.md](wikidata-original-assertion-review.md)
- [world-pilot-review-delivery-v1.md](world-pilot-review-delivery-v1.md)
- [world-novel-dataset-plan.md](world-novel-dataset-plan.md)

## 初期API・ソース調査（利用確認待ち）

以下は候補と当時の応答・仕様を保存する初期調査です。利用開始前に一次資料を目的別に確認し、採用・利用許諾・料金・認証を別途確定します。

- [anime-manga-apis.md](anime-manga-apis.md)
- [api-survey.md](api-survey.md)
- [api-test-results.md](api-test-results.md)
- [google-books-api.md](google-books-api.md)
- [narou-api.md](narou-api.md)
- [ndl-search-api.md](ndl-search-api.md)
- [openbd-api.md](openbd-api.md)
- [rakuten-books-api.md](rakuten-books-api.md)
- [web-novel-platforms.md](web-novel-platforms.md)

## ピン留め原本

以下は過去の計画・権利審査の固定原本です。本文のバイト列を変更せず、現在の案内はこの台帳と `current-status.md` から行います。

- [autonomous-next-phase-plan-v19.md](autonomous-next-phase-plan-v19.md)
- [autonomous-next-phase-plan-v20.md](autonomous-next-phase-plan-v20.md)
- [source-rights-review-scope-v3.md](source-rights-review-scope-v3.md)

## 更新済み現行記録

2026-10-26時点の最新作業状態を示す資料です。検証済み実装の基準は冒頭の `8ea2eb6` とし、ライトノベルの最新数値・残作業は以下の資料と [current-status.md](current-status.md) を参照します。

- [autonomous-next-phase-plan-v25.md](autonomous-next-phase-plan-v25.md)
- [light-novel-expansion-five-stages-2026-10-26.md](light-novel-expansion-five-stages-2026-10-26.md)
- [light-novel-delivery-2026-10-26.md](light-novel-delivery-2026-10-26.md)
- [light-novels-progress-v1.md](light-novels-progress-v1.md)
- [current-status.md](current-status.md)

## ルート文書

README/CLAUDEは現行ガイド・開発規約の入口として分類します。

- [README.md](../README.md)：利用者向け概要と確認済みの入口。
- [CLAUDE.md](../CLAUDE.md)：開発時の作業規約と現在の制約。

監査対象は既存の tracked Markdown 134件（README/CLAUDEを含む）に、この index と `current-status.md`、今回の実行記録を加えた137件です。初期API・ソース調査は履歴の「利用確認待ち」、v25・ライトノベル進捗・5段階記録・current-statusは「更新済み現行記録」として扱い、全資料を現行ガイド・更新済み現行記録・履歴・ピン留めのいずれかへ分類しています。
