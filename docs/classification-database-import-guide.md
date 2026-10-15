# 分類結果を派生 SQLite に保存する

`scripts/import_classification_database.py` は、停止済み run の分類結果を、元の書誌 DB の SQLite backup に追加するローカル処理です。LLM・Web・Codex CLI の探索、版照会、起動は行いません。元 DB、入力、run、prepared execution context、migration manifest、terminal proof は読み取り専用です。既存出力や入力ディレクトリ配下への出力は拒否します。

初回の実装検証は合成fixtureでした。現在は固定runtimeとworld用採用経路を通じて実36作品へ適用済みです（[実施記録](phase8-quality-evaluation-v1.md)）。以下の汎用importerのschema 3契約と、world用next DB 3.2の採用経路は区別します。元 DB は schema 3、checkpoint 済みである必要があり、非空 WAL があれば拒否します。

## 終了証拠

呼出し側は実際の process/session handle の終了を観測した**後**、次の共通 proof を保存します。manifest が存在することだけでは終了証拠になりません。

```json
{
  "schema_version": "classification-terminal-proof-v1",
  "observed_at": "2026-10-12T01:02:03+00:00",
  "session_id": "actual-observed-session",
  "exit_code": 0,
  "process_exit_observed": true,
  "run_id": "actual-manifest-run-id",
  "manifest_sha256": "SHA256 of exact run/manifest.json bytes",
  "results_sha256": "SHA256 of exact run/results.jsonl bytes",
  "ordered_inputs_sha256": "SHA256 of exact selected input bytes",
  "execution_context_manifest_sha256": "SHA256 of fixed prepared manifest bytes",
  "migration_manifest_sha256": "SHA256 of offline migration manifest bytes",
  "cli_profile_sha256_before": "same fixed prepared manifest SHA256",
  "cli_profile_sha256_after": "same fixed prepared manifest SHA256",
  "artifact_sha256": {"database": "SHA256 of checkpointed original SQLite bytes"}
}
```

`observed_at` は UTC ISO8601、`exit_code` は bool を除く整数です。非ゼロの終了値もそのまま保存しますが、取込には `pending=0`・`stopped=false`、全 selected job が completed/held/invalid_cached_result、件数の一致が必要です。`artifact_sha256` の診断用追加キーは許容します。`--cli-profile` はユーザー設定・認証情報ではなく、固定 prepared manifest のパスであり、before/after/context の三つの SHA と一致させます。proof は呼出し側の観測記録を検査するもので、importer 自身が process 終了を実測するものではありません。

## 実行

```powershell
uv run --frozen --offline python scripts/import_classification_database.py `
  --source-db <checkpointed-source.sqlite> `
  --ordered-inputs <fixed-selected-inputs.jsonl> `
  --run-dir <terminal-run-directory> `
  --execution-context-manifest <prepared-directory/manifest.json> `
  --cli-profile <prepared-directory/manifest.json> `
  --migration-manifest <migration-directory/manifest.json> `
  --terminal-proof <observed-terminal-proof.json> `
  --output-dir <new-derived-directory>
```

入力には sibling `manifest.json` が必要です。宣言された ordered/work inputs SHA は正確な bytes の SHA と一致する必要があります。prepared contextの入力、元sibling manifest、ローカルspecから導くartifact集合（従来baseline10件、provenance-v4は13件）のraw copyと現コードを照合し、`build_run_config` が同じ config を生成することも確認します。CLI 版文字列は保存された値を比較し、実測したと扱いません。

全選択 WorkInput を元 DB の immutable/query-only 接続から既存 `work_inputs()` で再構成し、入力 hash 全体、immutable source version refs、根拠と権利、work/expression scope を照合します。`DatasetDB.__init__` は呼ばず、migration・WAL pragma は実行しません。版 source の work content link と expression の所属も確認します。completed は current validator、model/CLI、job key、durable job、durable result、JSONL index の値と順序まで一致させます。duplicate JSON property、非有限値、float overflow、bool の件数、duplicate work/expression を拒否します。

runner の `result_path` は Windows の native backslash と slash の両方を受理し、解決したパスが `results/<job_key>.json` と一致することを検証します。絶対パス、drive 指定、rooted/UNC パス、`..`、namespace 外への symlink、別 job の結果ファイルを拒否します。文字列上の区切り文字の一致だけでは判定しません。

## 出力と保持範囲

出力は `dataset.sqlite` と `manifest.json` です。UUID stage で backup し、単一 writer の `BEGIN IMMEDIATE` transaction で次の追加テーブルだけを書きます。

| テーブル | 保存内容 |
| --- | --- |
| `classification_runs` | run config、execution context、migration、terminal proof、元 artifact SHA、件数、人手未確認 |
| `classification_inputs` | job key、選択順、work/expression、input hash、入力全体、元 job state、completed result または held 理由 |
| `classification_source_refs` | immutable version、work/edition scope、payload/raw SHA、元 work node・edition ID |
| `classification_input_evidence` | 入力根拠全体、元 DB evidence key への lineage |
| `classification_labels` | decision、scope、relevance、raw confidence、calibrated null、元 label JSON |
| `classification_label_evidence` | label から入力根拠への参照、locator、quote、summary、directness |

同一 work の原文 input と translation input はそれぞれの job key で保持します。held/invalid_cached_result は元 status と理由を保存し、result と label を生成しません。件数も held と invalid_cached_result を分け、その合計を failed として保存します。元 model の数値や null を変更せず、calibration は uncalibrated、human_verified は false です。人手 gold、意味的正解、独立資料による裏付けは追加しません。

全元テーブルについて型と重複を含む行 multiset の SHA を backup 前後で比較し、foreign key/integrity check を行います。run 全ファイルの SHA と inventory、元 DB・入力・context・migration・proof・現コードを開始時と公開直前に照合し、WAL 出現や変化があれば stage を撤去して公開しません。成功時は新ディレクトリへの rename で公開します。

派生 DB は保存済み分類の監査用 snapshot として扱います。`DatasetDB.ingest/resolve/undo` で後から再構築すると元 work graph と分類との結合契約を失うため、この派生 DB には適用しません。書込み禁止を OS permission で強制する実装ではありません。次の入力・run は元書誌 DB から別の新 namespace を作成してください。
