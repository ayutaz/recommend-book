# 実データ監査レポート

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

`scripts/audit_dataset.py` は実SQLite DB、固定WorkInput、分類結果、任意の正解注釈から、`dataset-audit.json` とローカルHTMLを生成する。コード上のテストfixtureの成功を、実DBの品質達成へ置き換えない。

```powershell
uv run python scripts/audit_dataset.py --database data/poc/dataset.sqlite --inputs data/poc/work_inputs.jsonl --results data/runs/poc --output data/evaluation/poc-audit
```

`--inputs` を省略するとDBから入力を再構成する。分類時の固定入力ファイルがある場合は必ず指定し、分類結果のinput hashを正確なスナップショットへ照合する。`--gold` は実際に作成済みの注釈だけを指定する。人手goldがない場合は `human_validation_pending`、人手注釈があっても `human_annotations_available_evaluation_required` となり、この監査だけでは品質完了にならない。

## 監査対象

| 対象 | 集計・検査 |
|---|---|
| 作品数 | `resolved=1` の異なるcanonical ID。作品ノード、表現、版、履歴レコード、入力行と別に表示 |
| 原言語 | 明示的な `original_language` のみ。英語翻訳題、Wikidata英語表示ラベル、edition language、LLM target languageから推測しない |
| 初出年代 | DBの原作品初出年を1900年以前・1900〜1949・1950〜1999・2000年以降・不明に分ける |
| 地域 | 明示的な作品・作者の地域来歴。複数所属を数え、舞台や言語で補完しない |
| 作品種別 | ソースの明示値を集計。小説・短編・ライトノベル・不明を勝手に統合しない |
| 欠損 | 題名、著者、原言語、初出年、地域、作品種別、シリーズの件数・割合。シリーズの不明は必ずしも誤りではない |
| 出典・権利参照 | active SourceRecord、Evidence、field assertions、WorkInput source refsを台帳と照合。未知のpolicy、適用範囲外、提供者不一致、URL欠損、参照不整合を指摘 |
| 原データ | ファイル実在とSHA-256を確認。明示した `--root` 外のファイルは読まず、監査未確認として報告 |
| 資料coverage | 保存資料、LLM許可宣言、台帳の無条件許可を分け、資料種類別の異なる作品数・全作品割合を表示 |
| 入力の固定性 | WorkInputが現在のcanonical ID、所属source version、同じ資料内容・scopeに対応しているかを検査 |
| 分類構造 | 本番validatorでJSON Schema、ID、input hash、taxonomy、根拠ID、引用、判定scope、unknownの扱いを再検証 |
| 分類coverage | 有効結果のある作品、何かを判断した作品、taxonomy全ラベル枠の判断率を分ける。表現行を作品数へ重複算入しない |
| 人手データ | 明示的provenance、担当ID、truth level、partition、作品知識宣言を検査。未知のDB作品は除外して指摘 |

SQLiteはURI `mode=ro`、`PRAGMA query_only=ON`、読取トランザクションで開く。DB作成・migration・名寄せ変更は行わない。レポートは別ファイルに書く。

「必須参照100%」は、参照の存在、登録policy、適用範囲・source/provider binding、URL形式、外部キーが揃ったという構造検査である。商用利用、再配布、本文の権利がすべて解決したという意味ではない。条件付きexternal LLM許可は、義務履行の証拠を監査者が確認する必要があるため、無条件許可coverageへ入れず別の警告に残す。raw integrityは別項目であり、`--skip-raw-hash` は明確に `not_checked` になる。

## 400作品固定分割・20作品試行の前提

DBの `work_inputs()` にはwork-levelの基本行に加えてexpression-specificの行が含まれる場合がある。`create_split_manifest()` と `create_review_pack()` に400作品セットを渡す前に、同じwork IDから一つの評価対象入力を選ぶ。分割の基本単位は作品であり、翻訳表現・近縁版を別作品として数えない。詳細な表現レビューは別パックとし、同じ作品のgroup IDと分割を保持する。

監査の事前検査は異なる作品数400、著者・シリーズの連結グループ数と最大サイズ、150/100/150の正確なグループ分割の構成可能性を記録する。author missing、duplicate base rows、DB/input不一致、source refs欠損を先に確認する。事前検査のグループ順序と最終manifestの原言語・年代順序は異なるため、最終のmanifest生成成功が確定条件となる。件数不足や分割不能でグループを勝手に分割したり、同じ作品を複製したりしない。

入力全体、名寄せ、権利フラグ、taxonomy、プロンプトを固定してから分割する。凍結後に書誌や根拠を変更した場合は新しい評価版を作り、既存manifestを上書きしない。元言語の偏りと不明率を併記し、便宜的標本を世界の小説母集団の代表標本と主張しない。

20作品パックの生成は、判定時間の計測完了ではない。担当人数、実作業時間、中断、判断不能の割合、追加資料・読書時間を実際に記録する。UIのセッション経過時間を作品ごとの人手時間や独立レビュー800件へ置き換えない。

試行20作品は固定分割の `dev` から選び、校正・最終評価用の作品へ試行の入力やモデル判定公開を混入させない。400作品と20作品のpackには同じ分割manifestを保持し、試行後もpartitionを変更しない。分類結果を後からpackへ追加する場合は、packの不変性検査に従って別versionを作る。

`scripts/freeze_poc_inputs.py` は基本入力だけを固定し、100作品pilotを原言語・初出年代・資料深度から選ぶ。ja/en/その他の35/35/30 quotaに不足がある場合は残りの作品へ再配分し、不足数をmanifestへ残す。英語題名から原言語を補完しない。既存出力directoryはDBを開く前に拒否し、既存snapshotを上書きしない。

## レポートに残す人手量と未検証項目

基本評価には400作品を開発150・校正100・最終150へ分ける。2名の独立判定は800作品レビューに相当し、さらに不一致裁定が必要になる。人手リソースが不足する場合は対象ラベル・作品を明示して縮小し、未評価の範囲は残す。作品内容への正しさは初期50〜100作品の詳細benchmarkを別に作り、入力資料への忠実性と混ぜない。名寄せは300〜500ペアに原題と翻訳、訳者違い、版違い、同名異作品、巻違い、短編と短編集、改作を含める。

監査レポートは人手precision/recall/F1、関連度MAE・順位相関、不適切な断定、原言語の文体・構造、校正、高信頼採用のprecisionとcoverage、独立人手一致・裁定、クラスタ誤統合を未検証の項目として保持する。人手goldがあっても、これらは別の精度評価と監査手順で測る必要がある。主要ラベルのholdout正例20件は `evidence` と `work` のtruth levelごとに数え、合算しない。

## TDD記録

最初の `uv run pytest tests/test_evaluation_audit.py -q` は未実装moduleのImportErrorで失敗した。実装後、異なる作品数、原言語非推測、権利policy欠損、DB読取専用、HTMLエスケープ、400作品の分割不能を確認した。追加の未知gold・入力根拠改変のテストは実装前に2件の失敗を確認し、対応後は従来評価と合わせて成功している。テストの人手注釈はfixtureであり、実データのgoldへ利用しない。

固定処理の `tests/test_evaluation_freeze.py` は初回に0件・負数のpilotを受理する2件の失敗を確認し、正整数の入力検査を追加した。異なる100作品、入力順に依存しない選定、quota不足の再配分、重複・expression入力の拒否、固定処理でのexpression行除外、SHA-256、既存snapshotのbyte不変性を検証する。DB取得境界は明示的なtest fixtureで置き換え、実ファイルの固定・再実行拒否を確認している。実DBの資料品質や人手正解をこのテストで保証しない。
