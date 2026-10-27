# 原文 PoC の独立 DB 統合

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

2026-10-12 に、保存済み Wikisource 20 作品・40 抜粋と、`wikidata-resolution-v1` の identity-map / SourceRecord / raw API 応答をオフラインで照合した。出力は `data/prose-poc/database-integrated-v1/dataset.sqlite`。本処理は追加取得、追加 LLM 分類、main / frozen DB の更新を行わない。

実測結果は **8 接続・12 保留**。identity-map 上の matched は 10 件だが、Alice の言語精度差と Frankenstein の言語 statement 集合の差を追加保留した。保存済み値を正規化して接続件数を増やしていない。

## 再実行と入力

初回に実行したコマンドは次の通り。exit code は 0。既存の非空出力ディレクトリは上書きを拒否するため、再実行は新しい出力名を指定する。

```powershell
uv run python scripts/integrate_prose_poc.py --output data/prose-poc/database-integrated-v1
# 再検証用の例（実行済みではない）
uv run python scripts/integrate_prose_poc.py --output data/prose-poc/database-integrated-recheck-v1
```

入力は `data/prose-poc/{source-records.jsonl,work-inputs.jsonl,metadata.json}`、`data/prose-poc/wikidata-resolution-v1/{identity-map.json,source-records.jsonl,manifest.json}` と、それらが指す保存済み raw ファイル。旧分類は `data/runs/prose-style-v1/results.jsonl` を読み、旧 20 WorkInput との ID・schema・引用参照の機械検証を行う。旧結果を書き換えたり DB に分類として移植したりしない。

DB 生成後、同じ保存済み証拠から `diagnose_holds` で保留診断を補完した。これは read-only の診断で DB・元入力・元結果を変更しない。最終スクリプトでは診断ファイル生成も通常コマンドに含まれる。日時を含む DB ログがあるため、再生成した SQLite の SHA 一致を成功条件とはしない。

## 接続条件とレコードの版

1. resolution manifest の output SHA と元 metadata SHA を確認する。
2. 元 Wikisource raw / 各章 render / normalized text / Author render の SHA と、`chars:start-end` の実抜粋文字列を照合する。`fulfilled_for_record` と `RightsRegistry.default()` で個別の利用条件を再確認する。
3. 保存済み API raw を SHA で固定し、版 `P31=Q3331189`、単一 `P629`、完全一致の en-Wikisource sitelink、原作 `P50`、Author 頁 QID・固定 revision、非 deprecated 没年、言語・出版年を照合する。
4. 原 Wikisource SourceRecord を immutable 旧版として保持する。接続できる候補だけ同じ source / source_id の新 SourceRecord 版を作り、`record_kind=edition`、版 scope の Wikidata ID、work scope の `work_refs`、Author 頁 QID を付加する。新旧版は DB の `parent_version_id` で接続する。
5. 選択された原作 authority 8 レコードと本文付き Wikisource 新版 8 レコードを既存 DB API で取り込む。`compare_records` も矛盾がないことを確認する。

`Q3331189` は広い「version, edition or translation」型である。これだけで特定刊行版の詳細を独立確認したとはしない。本文 scope は保存された歴史的英語原版・章・revision の **2 抜粋**であり、全版や全作品の文体代表性は未検証。

Wikidata 29 SourceRecord は `wikidata-support-source-records.jsonl` に全件を保持する。未照合の版や著者情報の乏しい版 authority を DB の active source として一括投入すると、余分な未解決 work node ができるため、本処理は接続先の原作 authority 8 件だけを active にする。版 QID・P629・raw proof は本文新 SourceRecord と DB の edition / edition_contents に保持する。29 件のうち残る 21 件を DB 上の検証済み実体に昇格させていない。

## 接続済み 8 件

| 作品 | 原作 Wikidata | 版 / version Wikidata |
| --- | --- | --- |
| Sense and Sensibility | Q274744 | Q78586600 |
| Pride and Prejudice | Q170583 | Q135550757 |
| Persuasion | Q333179 | Q20720825 |
| Moby-Dick | Q174596 | Q37805498 |
| The Picture of Dorian Gray | Q82464 | Q69637774 |
| Strange Case of Dr Jekyll and Mr Hyde | Q217352 | Q56632103 |
| Jane Eyre | Q182961 | Q50404815 |
| The Scarlet Letter | Q907568 | Q40345777 |

Jane Eyre の WD SourceRecord は英語 label がなくタイトルが `Q182961` である。identity-map は保存済み原作 `P1476` による作品名照合を済ませているが、DB 派生 `scope-inputs.jsonl` の代表タイトルにはこの placeholder が残る。本文の版タイトル Jane Eyre は元版・新版の SourceRecord / titles に保持される。欠けた label を補作していない。この派生入力をユーザー向け表示や追加分類に使う前に、タイトルの出典を明示して選ぶ処理が必要である。

## 追加保留 2 件と元の uncertain 10 件

Alice は原本文の `en` と WD 原作の `en-GB` の表記精度差を保留する。同じ英語原文を示す可能性があっても、この snapshot では完全一致のみを採用し、WD の immutable 値を `en` に変更していない。

Frankenstein（原作 Q150827 / 版 Q55239420）は元 identity-map が `original_language_qids=[Q1860]` を保存している。一方 raw の非 deprecated `P407` は **39 言語**で、英語 Q1860 が preferred、他 38 値が normal。英語 statement には原文の役割 qualifier がある。今回は全非 deprecated raw 値と identity-map の subset を厳密比較するため不一致となった。原作・版の出版年はいずれも 1818 で一致する。この保留は statement rank / scope の採用方針差であり、取得済み英語本文が翻訳であることを示す判定ではない。rank 採用や値の書き換えで接続成功に変更していない。

詳細は `held.json` と `hold-diagnostics.json` に、raw path / SHA、statement ID、rank、QID、qualifier とともに保存した。Frankenstein 原作 raw SHA は `3f49f97e625ea572db9e62be6b94ad7e09492dbfa890dab99211df6286bab2b1`。

元の uncertain 10 件（Mansfield Park、Emma、Northanger Abbey、A Tale of Two Cities、Great Expectations、Oliver Twist、David Copperfield、Treasure Island、Wuthering Heights、Kidnapped）は保留のまま。特定版不足、単一 P629 不成立、候補 entity 不在、連載年 / 刊行年の差、著者・作品多値などの理由を維持する。記憶で QID を補充しない。

## 権利と分類の扱い

原典の個別確認は JP / US、英語原作、著者没年・初出年・版年、翻訳なし・現代注釈なしという記録済み条件に限定する。世界一律 PD の保証ではない。本文・編集寄与は従来の conditional `wikisource-reviewed-original` / CC-BY-SA を維持し、attribution、share_alike、mark_changes、per_work_original_rights_checked を継承する。WD metadata の CC0 を本文へ付け替えない。

元 20 SourceRecord は byte-identical なコピーと DB の immutable 旧版として残る。新 8 版の `prose_identity_integration` は `human_verified=false`、`independent_identity_confirmation=false`、`text_rights_changed=false`。Wikisource と WD の依存した source mapping を独立裏付けや人手確認として数えない。一般の同定 rule に、根拠説明用 `prose_identity_proof` event を追加した。

`id-crosswalk.json` は元 `ws:en:*` ID、immutable 元 source version ID、active version ID、new canonical ID、旧 / 新 input hash を保持する。全 20 件に `old_classification_valid_for_new_input=false` を明示する。旧 `prose-style-v1` は旧 WorkInput に対する実行結果のまま。新 `scope-inputs.jsonl` は版・本文 scope 確認用であり、旧分類の別 ID への転記や新入力の有効分類への偽装は行わない。機械的な成功を人手精度、校正済み confidence、意味的根拠の精度とはしない。

## read-only 監査の実測

SQLite URI `mode=ro` と `PRAGMA query_only=ON` で検査した。`audit.json` に保存する。

| 検査 | 実測 |
| --- | ---: |
| canonical works / 元候補 | 20 / 20 |
| unexpected canonical works | 0 |
| work nodes / active source records | 28 / 28 |
| immutable source versions | 36 |
| editions / edition_contents | 8 / 8 |
| original expressions | 20 |
| 版の raw-proof 原作 canonical ID との接続検証 | 8 / 8 |
| edition relationship / original prose scope violations | 0 / 0 |
| original prose excerpts / rights fulfilled | 40 / 40 |
| invalid evidence scopes | 0 |
| integrity check / foreign-key violations | ok / 0 |
| human merge / independent identity confirmations | 0 / 0 |
| body license changed / classification rebound | false / false |
| 本処理の Web / LLM 起動 | 0 / 0 |

work nodes 28 は元 20 active 本文 source と接続先原作 authority 8 の内訳で、canonical work の重複 8 件という意味ではない。semantic edition-target 監査は、FK が有効でも別作品へ誤接続した版関係を検出する。元版の immutable trigger、SHA 不一致保留、言語精度保留、uncertain 保留、出力上書き拒否を含む専用 6 テストを TDD で検証した。

## SHA 固定の証拠

元ファイルは実行前後で一致した。完全な絶対パス別一覧は最終 manifest の `protected_inputs_sha256` にある。

| 対象 | SHA-256 |
| --- | --- |
| 元 prose SourceRecords | `156269338489c10b7b723d205bc7de7c3ca6f71bd7c7abc7b15d42d1e1f360d9` |
| 元 20 WorkInputs | `d60b1f2908ccb59fe1e784246c5e3e2e0950ad1f478e1e3b6b6ffcc4f22e6bf2` |
| 元 style results | `5fe8844148e04a2269eafa889d2a54e6cf88eb8d49d637bef9a029040325cf05` |
| 独立 SQLite | `383260c997af8eb942ede4296faed418dd814950d9948604641b13541ec5e19a` |
| id-crosswalk | `bb7cc88bb9b6038533e89cd30ba3b8f1546a4fc6c1a7b0893ed672810e17a64a` |
| audit | `4c4582d6ead2a3f58d2c68b9b28b8e61433f0e858db7b4d1a9a4615fb9436913` |
| 最終 manifest（診断補完後） | `f172ad6d66a4e348bdd1af02bebc38abd4dfb7cd25a0f694bfddb180ca9fd22b` |

最後の manifest には各生成ファイルの SHA があり、manifest 自身の SHA は循環を避けて本文に記載した。DB の出力は独立成果物で、元 raw path を参照するため単独ファイルだけでは raw 証拠を持ち運べない。移動時は元 raw / normalized / metadata を含めて保持する。

## resolution v2 による独立 DB v2 の観測

2026-10-12、専用 resolver が保存済み原作 `P1476` を SourceRecord の alternative title として保持する修正を行った後、オフラインで生成された `wikidata-resolution-v2` を同じ統合スクリプトへ渡した。resolver v2 SourceRecords の SHA は `734ecb005505e72c42434d98ceaacc16aae1e5541ae2c234967b74e0e90774ca` で照合済み。同定結果 10 matched / 10 uncertain は変更されていない。

```powershell
uv run python scripts/integrate_prose_poc.py --resolution data/prose-poc/wikidata-resolution-v2 --output data/prose-poc/database-integrated-v2
```

実行は exit code 0。v2 は **8 接続 / 12 保留、20 canonical / unexpected 0、28 work nodes / active sources、36 source versions、8 editions / edition_contents、20 original expressions**。8 / 8 の版接続を raw-proof 原作 canonical ID へ照合し、edition relationship / original prose scope の違反は 0、40 / 40 の抜粋は従来の権利条件を満たす。FK 違反 0、integrity `ok`、invalid evidence scopes 0。v1 と v2 の `audit.json` は byte-identical な SHA で一致する。

Jane Eyre の原作 authority には preferred `Q182961` が immutable 値として残り、alternative `Jane Eyre` / `en` が保存済み raw `Q182961.P1476` の `{"text":"Jane Eyre","language":"en"}` から追加された。v2 の派生 WorkInput 代表タイトルは **Jane Eyre** と実測した。本文の版タイトル・権利・抜粋は変更していない。上記 v1 の placeholder 記述は旧 v1 の観測履歴であり、v1 を後から修正したものではない。

Alice の `en` / `en-GB` 保留と Frankenstein の全非 deprecated `P407` 39 値 / identity-map subset の保留は解除していない。v2 の `held.json` と `hold-diagnostics.json` に同じ根拠を保持する。原言語・年・本文・license を変更して成功件数を増やしていない。旧分類は旧スナップショットの結果を維持し、v2 crosswalk も全 20 件に `old_classification_valid_for_new_input=false` を保持する。

v2 実行直前に v1 成果物 14 ファイルと元 SourceRecords / WorkInputs / style results の計 **17 ファイル**の SHA を固定し、実行後の全件一致を確認した。リストは v2 の `protected-v1-original-sha.json` にあり、各実行の追加 protected-input SHA と全 output SHA も再照合した。20 件の crosswalk は、元 immutable source version が元 source / source_id と一致し、新 active 版の parent が元版を指すことを read-only で再確認した。`verification.json` に別途検証結果を記録する。このレーンは root の 393 ファイル監査を再実行したと主張しない。

v2 は本統合スクリプト・models・DB・runner・prompt を追加変更せず実行した。追加 LLM / 通信は 0。関連テストは `uv run pytest tests/test_integrate_prose_poc.py tests/test_database.py tests/test_rights.py tests/test_wikisource_prose.py tests/test_prose_wikidata_resolution.py -q` で **62 passed**。人手精度・独立確認・校正済み confidence の評価へは昇格させていない。

| v2 対象 | SHA-256 |
| --- | --- |
| 独立 SQLite | `283e11a00319ffa08289aa99bc1ee40b1c28f7e5ceb472e93ca7d36d743eb609` |
| id-crosswalk | `bcdbbbb516b33482285d94081e53045002e78dc78f8ec7603d40cb5ae298e7fc` |
| audit（v1 と同一） | `4c4582d6ead2a3f58d2c68b9b28b8e61433f0e858db7b4d1a9a4615fb9436913` |
| 最終 manifest（追検証ファイル登録後） | `bb01a76daa836a22d37b72f086455822ba1b3fdef5fcca9a0851112193fd07e0` |

成果物はすべて `data/prose-poc/database-integrated-v2/` に置き、v1 / 元入力 / 元結果には変更を加えない。
