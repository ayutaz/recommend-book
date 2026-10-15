# データ処理の実装契約

2026年10月12日。ステージ1〜4を並列実装するための共通契約。

共通モデルは `src/recommend_book/models.py` のPydanticモデルを使う。各担当は共通モデルの変更を統合担当へ提案し、担当範囲のモジュール・テストを編集する。Python 3.12以上、uv、pytest、httpx、jsonschema、pydanticを使う。依存管理は統合担当が行う。

1. 収集担当は `ingestion/` と `rights.py` を担当する。各アダプタは原データを保存して `SourceRecord` を返す。正規化レコードをJSONLに保存する。Wikidata、Open Libraryダンプ、NDLの少量取得・OAI-PMHに対応。説明文とあらすじを混同せず、Evidenceは用途別許諾確認を通す。
2. DB担当は `database.py`、`normalization.py`、`resolution.py` を担当する。`DatasetDB(path)` は `ingest(records: Iterable[SourceRecord]) -> dict`、`work_inputs() -> list[WorkInput]`、`stats() -> dict` を提供する。作品・表現・版・収録関係・出典・フィールド来歴・同定履歴を保存し、タイトルやISBNのみで作品を統合しない。取り消しAPIと難例テストを作る。
3. 分類担当は `classification/`、`jobs.py`、`schemas/classification.schema.json`、`config/taxonomy.json` を担当する。`run_classification(inputs: list[WorkInput], output_dir: Path, *, concurrency: int=2, model: str='gpt-6.1-sol', executable: str|None=None, limit: int|None=None) -> dict` と、検証・読み取りAPIを用意する。結果はJSONLや個別JSONとrun manifestに保存。SQLiteはrootが管理し、ワーカーは個別ファイルに出力。子プロセスによるshell拡張を避ける。
4. 評価担当は `evaluation/` を担当する。`evaluate_results(results, gold, taxonomy) -> dict`、`write_report(report, output_dir)`、`create_review_pack(inputs, results, output_dir)` を提供する。goldは人手/外部参照/LLMによるものを区別する。unknownの判定範囲、保留による未検出、校正と最終評価分離を実装。レビュー用HTMLと入力CSV/JSONをローカル生成し、人手判定なしに人手検証済みと表示しない。

最初に重要な失敗テストを実行してから実装する。各担当は失敗確認・成功確認のコマンドと結果を報告する。gitのcommit/push、共有設定の変更、既存の収集スクリプトの改変は担当外。データの一括取得は統合担当がAPI制約と保存容量を確認したうえで実行する。
