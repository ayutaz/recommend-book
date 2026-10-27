# データ処理の実装契約

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

2026年10月26日案内: 以下は2026年10月12日に定義した基礎APIと初期担当分担の契約です。現行の差分分類は[世界小説の分類・取り込み手順](world-classification-expansion-guide.md)、到達件数と未完了事項は[current-status.md](current-status.md)を参照してください。基礎APIの既定並列度と、現行バッチの最大8並列・1作品単位の設定は別です。

初期担当分担の「commit/pushは担当外」は子エージェントの分担です。統合担当Codexは必要な検証、文書更新、commit・pushを実施します。利用者の追加採点、外注、購入、有料APIを必須工程にしません。

共通モデルは `src/recommend_book/models.py` のPydanticモデルを使う。各担当は共通モデルの変更を統合担当へ提案し、担当範囲のモジュール・テストを編集する。Python 3.14、uv、pytest、httpx、jsonschema、pydanticを使う。依存管理は統合担当が行う。

1. 収集担当は `ingestion/` と `rights.py` を担当する。各アダプタは原データを保存して `SourceRecord` を返す。正規化レコードをJSONLに保存する。Wikidata、Open Libraryダンプ、NDLの少量取得・OAI-PMHに対応。説明文とあらすじを混同せず、Evidenceは用途別許諾確認を通す。
2. DB担当は `database.py`、`normalization.py`、`resolution.py` を担当する。`DatasetDB(path)` は `ingest(records: Iterable[SourceRecord]) -> dict`、`work_inputs() -> list[WorkInput]`、`stats() -> dict` を提供する。作品・表現・版・収録関係・出典・フィールド来歴・同定履歴を保存し、タイトルやISBNのみで作品を統合しない。取り消しAPIと難例テストを作る。
3. 分類担当は `classification/`、`jobs.py`、`schemas/classification.schema.json`、`config/taxonomy.json` を担当する。`run_classification(inputs: list[WorkInput], output_dir: Path, *, concurrency: int=2, model: str='gpt-6.1-sol', executable: str|None=None, limit: int|None=None) -> dict` と、検証・読み取りAPIを用意する。結果はJSONLや個別JSONとrun manifestに保存。SQLiteはrootが管理し、ワーカーは個別ファイルに出力。子プロセスによるshell拡張を避ける。
4. 評価担当は `evaluation/` を担当する。`evaluate_results(results, gold, taxonomy) -> dict`、`write_report(report, output_dir)`、`create_review_pack(inputs, results, output_dir)` を提供する。goldは人手/外部参照/LLMによるものを区別する。unknownの判定範囲、保留による未検出、校正と最終評価分離を実装。レビュー用HTMLと入力CSV/JSONをローカル生成し、人手判定なしに人手検証済みと表示しない。

最初に重要な失敗テストを実行してから実装する。各担当は失敗確認・成功確認のコマンドと結果を報告する。gitのcommit/push、共有設定の変更、既存の収集スクリプトの改変は担当外。データの一括取得は統合担当がAPI制約と保存容量を確認したうえで実行する。
