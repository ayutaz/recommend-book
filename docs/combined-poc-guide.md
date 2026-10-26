# 固定 2000 と原文 20 の単一 DB snapshot

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

2026-10-12、計画②のローカル単一 DB として `data/poc-combined-v1/dataset.sqlite` を生成した。固定 2000 作品に独立原文 PoC 20 候補を追加し、**resolved canonical work は期待値どおり 2020**。元/main/frozen/独立原文 DB、元入力、旧分類結果は変更していない。追加 LLM・通信・共通 core の変更はない。

## 実行と再現

実行コマンドと handle は次のとおり。session_id `78316` の最終結果で exit_code `0` を確認した。

```powershell
uv run python scripts/combine_poc_snapshots.py --output data/poc-combined-v1
```

既定入力は `data/poc-frozen-v2`、`data/prose-poc`、`data/prose-poc/database-integrated-v2`。出力は新規ディレクトリに限定し、既存スナップショットや入力 tree 内への出力を拒否する。再実行時は別名の `--output` を指定する。main/prose 件数の自動調整や部分成功の manifest 発行は行わない。

元 DB を immutable read-only / query-only 接続で開き、SQLite `Connection.backup()` により新 DB を作成した。ファイルコピーで WAL の状態を取り落とさないよう、immutable source に非空 WAL があれば処理を拒否する。今回は元 frozen WAL は空で、`database-snapshot.json` が指定する DB SHA と一致した。

新 DB 上で既存 `DatasetDB.ingest` を次の順に実行した。

| 順番 | 入力 | inserted / updated / unchanged |
| --- | --- | --- |
| 1 | immutable 元 Wikisource 20 SourceRecords | 20 / 0 / 0 |
| 2 | v2 activated work authorities 8 | 8 / 0 / 0 |
| 3 | v2 enriched Wikisource source versions 8 | 0 / 8 / 0 |

途中の canonical 数は 2020 → 2028 → 2020。原作 authority を先に投入した段階は本文新版の `work_refs` が未投入なので一時的に別 node になる。最終 snapshot の期待値は固定 2020 であり、最後に同定できなければ件数を調整せず停止する。

原作 WD 8 ID の frozen work-scope `external_ids` との重複、および既存 source / source_id の衝突を実行前に検査する。原作 ID がすでに存在すれば新しい 20 件として数えず、処理を拒否する。

## 元 2000 入力と既存 DB の保持

ingest 前の backup DB から WorkInput を再構成し、固定 `work-inputs.jsonl` の 2000 件と ID、`input_hash`、`source_record_ids` を比較した。コードや元データの変化で再構成が一致しない場合は、元入力を再正規化せず停止する。ingest 後にも同じ比較を行った。

実測は **2000 / 2000 件で work_id・input_hash・source-version refs が不変**。`main-input-comparison.json` に旧/新 hash と refs を作品ごとに保存する。既存 2639 immutable source rows は全列の signature が一致し、全 source heads も保持された。既存 NDL 26 editions は edition 行だけでなく、edition_contents の work / expression / position も前後一致した。

SQLite backup は既存の source records・同定 ledger・assertions を含む DB を複製する。derived views の rebuild に伴う audit ledger の追加は新 DB 内だけで起こる。元 main `data/poc-v1/dataset.sqlite` や frozen DB に rebuild を実行していない。

## 原文 20 の scope と provenance

独立 v2 の 36 source versions（immutable 元本文 20、新本文版 8、原作 authority 8）を、新 DB の同一 version ID・全列 signature・parent-version 関係として保持した。v2 の出力 manifest SHA を照合し、JSONL の内容が immutable v2 DB と一致することも検証する。manifest の checksum だけを書き換えた別 provenance を受け入れない。

原文候補は引き続き **8 接続 / 12 保留**。Alice の `en` / `en-GB` と Frankenstein の statement rank / scope 差は解除していない。残る uncertain 10 件も unchanged。保存された本文・言語・年・権利を正規化して成功件数を増やしていない。

新 WorkInput の原文 40 evidence は、元 input と ID、本文、Unicode `chars` locator、出典 URL、language、license、attribution、inherited_restrictions を含めて一致する。active 本文 source version が WorkInput の refs に含まれ、work-level scope / original English として保持される。DB の8版は raw-proof原作 authority の canonical ID に接続し、original English expression / 版 scope WD ID を維持する。20 件すべての input hash は独立 DB v2 の work-level inputs とも一致した。

JP / US 個別原典条件と現代寄与の conditional CC-BY-SA は維持し、metadata CC0 を本文へ移していない。`RightsRegistry.default()` と個別 fulfilled obligations で40抜粋を再確認した。この DB はローカル audit snapshot であり、全 records の再配布や世界一律 public domain の許諾ではない。原文の権利確認・本文代表性・同定の人手精度を新たに承認した成果物でもない。

## 分類 snapshot と crosswalk

`work-inputs.jsonl` は新 DB の work-level 2020 件、`prose-work-inputs.jsonl` はそのうち原文20件。`prose-id-crosswalk.json` は旧 `ws:en:*` ID、immutable 元 source version、active version、独立 v2 canonical ID、新 canonical ID、旧/新 input hash を保持する。全件 `old_classification_valid_for_new_input=false` を維持し、旧分類を新IDへ書き換えたり DB に有効分類として移植したりしない。

次の legacy copies は元ファイルと byte-identical であり、各々の元 snapshot に対する資料として扱う。

| ファイル | 内容 |
| --- | --- |
| `legacy-main-inputs.jsonl` | frozen v2 固定 2000 入力 |
| `legacy-prose-style-inputs.jsonl` | 旧 `ws:en:*` 原文20入力 |
| `legacy-prose-style-results.jsonl` | 旧原文20に対する実 Sol 結果、旧ID・hashのまま |

進行中の main 1000 分類の結果は元 frozen snapshot と `data/runs` に残る。本処理はその増加中ファイルの SHA を固定・コピー・更新していない。main WorkInput hash が同じでも、今回の DB 統合作業がその分類を再検証・評価したという意味にはしない。

`classification_ready_works=2020` は許可された入力 evidence がある件数であり、分類済み作品数や人手品質の成功件数ではない。DB expression nodes は2060あるが、許可された expression-specific 分類入力は0件で、`expression-inputs.jsonl` は空。26 unresolved canonical nodes は既存 NDL の版だけの候補として保持され、2020 resolved works に加算していない。

## read-only 監査と SHA

`audit.json` は `mode=ro` / `query_only=ON`、別の最終 `verification.json` は `mode=ro&immutable=1` / `query_only=ON` の接続で検査した。実測は次のとおり。

| 検査 | 実測 |
| --- | ---: |
| resolved canonical works / expected | 2020 / 2020 |
| unexpected canonical works | 0 |
| work nodes / active source records | 2663 / 2667 |
| immutable source versions | 2675 = 2639 + 36 |
| editions / edition_contents | 34 / 34 = NDL26 + prose8 |
| expressions / unresolved works | 2060 / 26 |
| 元 main入力 / hash・source refs 不変 | 2000 / 2000 |
| prose input hash 独立v2との一致 | 20 / 20 |
| 原文版接続 / 許諾fulfilled抜粋 | 8 / 40 |
| FK / invalid evidence scope / edition-target violations | 0 / 0 / 0 |
| integrity check | ok |
| 新しい人手確認 / 独立同定確認 / license変更 | 0 / 0 / false |
| 保護ファイルSHA前後一致 | 314 / 314 |
| classification rebound / 追加LLM / 追加Web | false / 0 / 0 |

`protected-inputs-sha.json` は main DB、frozen tree、prose tree（原 raw・v1・v2を含む）と旧style results の絶対パス/SHA一覧。元 DB・入力・結果を含む314ファイルが実行直前・実行後・最終検査で一致した。外部rootレーンの別ファイル監査を再実行したと主張しない。

その後、rootレーンでも2026-10-12T02:01Zに保護314ファイルのSHAを独立に照合し、旧2,000入力と新DB入力のinput hash/source refs全件一致、古典20IDの非重複、合計2,020の集合一致を確認した。直前のroot監査では永続出力13件のSHA、SQLite immutable read-only接続のintegrity=ok/FK0も照合した。分類の意味精度や人手同定はこの監査の対象ではない。

| 最終成果物 | SHA-256 |
| --- | --- |
| SQLite | `1e50c2eabf4dd7afbb1b19a05f9b253439ae9523cf39ce84810bd90f45f7045f` |
| manifest（追検証登録後） | `1dd6fe95162629a355165a9fbc784402ce05d07991e59b7467eddf580171a57d` |
| audit | `067b28d9b30ad4648dddafcb3c20d8b15d154356d4bee3b640f2565e79fd4a0c` |
| prose crosswalk | `4dbf12625b5943fcea9662c26f1a9ad2d4fc1bbefda9294ede898d15d210ff95` |
| new 2020 WorkInputs | `beaacaf3b9ce17637c77e8c54cc7cde17e7fb294193e44cae61fe86e69910c31` |
| protected SHA一覧 | `7779c1a3b8c07e8ea83dc06e81dd99039cb6c91628e7f61b315e0315c445ca17` |

SQLite の `-shm` / 空 `-wal` は通常の読み取りでも変わる一時ファイルであるため、生成 manifest の durable output SHA から除く。DB本体と永続JSON/JSONLは全件SHA照合済み。元スナップショット側の保護一覧は変更していない。再生成時のログ日時等が異なるため、生成DBそのもののSHA一致を再実行の合格条件とせず、元入力・版・scope・関係・権利の監査結果を比較する。

## TDD と残る評価範囲

専用 `tests/test_combined_poc.py` は6件。元入力/NDL/immutable版の保持、既存WD work ID衝突、frozen input drift、manifest SHA不一致、checksumだけ更新されたprovenance差、既存出力拒否を確認する。SQLite一時ファイルを永続checksum扱いしないことも確認した。先に失敗を確認して新scriptを実装した。

```powershell
uv run pytest tests/test_combined_poc.py tests/test_integrate_prose_poc.py tests/test_database.py tests/test_rights.py tests/test_wikisource_prose.py tests/test_prose_wikidata_resolution.py -q
```

関連テストは **68 passed**。これは機械的な snapshot 整合・provenance・scope・条件充足の検証であり、分類の意味精度、whole-work prose representativeness、校正済みconfidence、人手gold評価の完了を意味しない。共通モデル・DB schema・runner・promptには追加変更を加えていない。
