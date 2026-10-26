# stage 2 名寄せの人手レビュー候補

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

2026年10月12日、保存済みSourceRecordsとDBのsource_headsを読取専用トランザクションで固定し、**400 pairの未裁定候補**を作成した。作品の名寄せ精度を検証した結果ではない。人手裁定数0、gold数0、precision未計算。現行resolverの出力は`rule_proposal`として保存し、正解とは区別する。

## 固定成果物

正式なサンプルは`data/poc-v1/resolution-review-20261012-v4/`。

`v1`〜`v3`は構築時の中間成果物であり、レビュー・評価には使わない。v4のmanifestとsample SHAのみを固定対象とする。

- `review.html`: 書誌情報を左右に並べた静的レビュー画面。書誌assertionsを折り畳み表示し、ルール提案は別の折り畳みに入れる。すべてのソース文字列をHTML escapeする。
- `pairs.jsonl`: 400候補の完全SourceRecord、両側source/version IDs、全field_assertions、source URL、provider、retrieved_at、rights policy、原物path/hash、strata、候補生成trigger、rule proposalを保存。
- `manifest.json`: 入力スナップショット、件数、selection、コードと各成果物のSHA256。既存内容が異なる場合、生成器は上書きを拒否する。
- `adjudications.template.json`: 人手入力を記入するための空テンプレート。自動humanタグは一件も生成しない。
- `adjudications.schema.json`: このsample SHAに固定したJSON Schema。case ID、reviewer、same/different/uncertain、reasonが必須。

Sample SHA256は`70e43d843d53b308607d9011c744d23a49457864b5529fbbae8e7917a0481485`。入力source_headsは2,621件、schema 3。DB自体のbyte hashではなく、同一トランザクションで読んだsource_heads原レコード列のcanonical JSONのhashを記録する。DBを後から更新してもこのサンプルのSourceRecordは変更しない。

## 難例の選び方と偏り

ランダムな無関係negativeを生成せず、実データの共有work ID、正規化題一致、同シリーズ、著者キー一致かつ近似題から候補を作る。共有work IDに書誌差分がない通常の一致pairは除外する。著者キーはagent IDを優先し、IDがない場合だけ正規化名前を使う。近似題は5文字以上かつSequenceMatcher比率0.68以上1未満を候補生成の条件とする。これは同一性の証拠でも確率でもない。

複数strataは重複して数える。400 pairは1,142 eligible pairから決定的な優先順とsource-version ID順で選択した。同シリーズのpairは他stratumがあっても最大15 pair/シリーズ、250 pair全体に制限した。

| stratum | pair数 | 人手で確認する点 |
|---|---:|---|
| same_author_near_title | 277 | 同一著者の別巻、題の変種、シリーズ共通部分の一致 |
| series_sibling_different_title | 204 | シリーズと個別作品の単位、別巻、別著者の共同シリーズ |
| shared_id_title_variation | 80 | 外部IDが同じでも別題・言語・作品単位の整合性 |
| coauthor_set_difference | 38 | 著者数・共同著者・名前表記差・未取得寄与者 |
| same_title_distinct_identity | 19 | 同名異作品と外部ID競合 |
| same_title_author_difference | 6 | 著者差が実差か同一人物の別表記か |
| volume_difference_or_missing | 3 | 巻番号の差、巻番号の欠損 |

この構成は難例の意図的なsampleであり、世界の小説全体や全DBの誤統合率を代表しない。共同著者差は名前集合上の差であり、同一著者の別表記を含む。近似題とシリーズの重複が多い。短編単体対短編集、翻訳表現、版対作品の境界はこの固定sampleで十分に網羅できておらず、その層の精度は未評価とする。外部IDもsource由来の主張であり、人手goldではない。

## 裁定手順

1. `review.html`の両側の題、著者ID/役割、work refs、作品種別、巻、言語、出版年、assertions、出典を確認する。原作初出年と再版年を分け、タイトル一致だけでsameにしない。
2. 同一作品なら`same`、別の作品単位なら`different`、証拠不足や矛盾があれば`uncertain`とする。短編と短編集、シリーズと各巻、翻案と原作を区別する。人手の追加調査を行った場合はreasonに出典と根拠を追記する。
3. 空templateを別名の裁定ファイルへコピーし、`adjudications`に以下の形で記入する。reviewerは実際に確認した人の識別子。モデルや既存ルールの判断を人手として登録しない。

```json
{"case_id":"対象sampleのcase_id","reviewer":"実際の確認者","decision":"uncertain","reason":"根拠不足の具体的理由と確認した出典"}
```

4. JSON Schemaでvalidateし、sample SHA256一致、未知case IDなし、同一caseの重複裁定なしも確認する。同じcaseに独立レビューを行う場合はreviewerごとに別のファイルへ保存して、最終裁定と混ぜない。Schemaは同一case IDの複数行を自動禁止しないため、import時には別途重複検査が必要。
5. 全件裁定やルール評価は未実施。`uncertain`の扱い・分母を固定してからprecision等を計算する。生成器はDBへの裁定importを行わない。

## 再生成と検証

Native Windows、uvで実行する。

```powershell
uv run python scripts/build_resolution_review.py --db data/poc-v1/dataset.sqlite --output data/poc-v1/resolution-review-new-snapshot --limit 400
uv run pytest tests/test_resolution_review.py -q
```

出力先は新しいディレクトリを指定する。最新DBへの変更で結果が違う場合は元sampleを置き換えない。生成器はネットワーク、新しいソース取得、LLM、DB mutationを行わない。testsは通常の共有ID一致を難例に混入しないこと、human/goldを生成しないこと、HTML escape、provenance保持、SHA一致、schemaの人手必須項目、再実行の決定性、変更済み成果物の上書き拒否、入力DB不変を確認し、2 tests passed。
