# freeze v2 の人手評価準備と実DB監査

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

旧400作品packには原言語の偏りがあったため、新しい評価設計とblind packは [evaluation-balanced-v1.md](evaluation-balanced-v1.md) に記録した。この文書の400-review・pilot20-reviewは初回生成の比較記録として保持する。実DB監査の結果は同じfreeze v2についての記録である。

2026年10月12日。入力は `data/poc-frozen-v2/work-inputs.jsonl` の異なる2,000作品である。分類結果未完了のため、レビューpackはモデル判定を含めないblind版として生成した。人手goldは0件、実人手時間は未計測であり、分類精度・校正・品質完了を主張しない。

## 固定した成果物

| 成果物 | 場所・内容 |
|---|---|
| 400作品レビュー | `data/evaluation/poc-frozen-v2/400-review/review.html`、72ラベル、全判定未入力 |
| 20作品試行 | `data/evaluation/poc-frozen-v2/pilot20-review/review.html`、dev内20作品、全判定未入力 |
| 分割manifest | 両packの `split_manifest.json`。dev150・calibration100・holdout150 |
| 固定元との対応 | 両packの `review-provenance.json`。freeze・元入力・authority sidecar・taxonomy・split・pack・選定入力のSHA-256を記録 |
| 選定入力 | 両packの `selected-work-inputs.jsonl`。元freezeのWorkInputを保持 |
| 時間計測template | 試行packの `review-timing-template.json`。担当ID・各実時間・中断・追加資料時間はnull |
| 実DB監査 | `data/evaluation/poc-frozen-v2/dataset-audit/dataset-audit.json` と `.html` |

元入力のSHA-256は `77793ab2c89983cb6174d58ce35d4d353934ac4cb73bd073aafdcae4a3c337fe`、authority sidecarは `1f7a6610680d9167ecae636536e28d71b5e1e4223cdc97d741a814f416707d3c`。両者がfreeze manifestと一致することを確認して生成した。

splitのmanifest SHAは `1cfa62ac9a8e96de6fcb81e646d5f95fbc3283958751fda30973f0bb2a4d8d5c`。400作品pack SHAは `1f8e770f166d87a313538a4b0feb23b7e26e30464854107179fb71d090f22594`、20作品pack SHAは `a2ab5cd415bf66d2b5ee66f7227bf6449cc1f61ac34728c85bd678b6c85db9ea`。

著者agent ID・シリーズID・scope別外部IDと表示名fallbackの連結グループを使い、選定400作品の242グループについて複数partitionへの分割が0件であることを確認した。20作品はdevのみから固定seed 1729で選び、日本語7・英語7・その他6作品となった。既存freezeや既存UI試験packは変更していない。モデル結果を追加する場合は別pack versionを作る。

## 標本の制約と未実施作業

400作品には14の原言語bucketがあるが、グループの不分割と正確な件数を優先した便宜的標本である。holdoutはベンガル語・ヒンディー語0作品、日本語9作品であり、全言語の精度や主要ラベルの人手正例20件を保証できない。原言語・年代・地域別の構成はsplit manifestに記録した。authority情報がない別表記の漏洩リスクは別途残る。

時間計測試行は実施していない。20作品の実作業時間、判断不能率、追加資料・読書時間を実際の担当者が記録する必要がある。基本評価は2名×400作品の独立判定と不一致裁定、作品知識benchmarkは初期50〜100作品、名寄せは難例を含む300〜500ペアを計画量として残す。モデルや合成UI試験の判定を人手正解へ採用しない。

## 実DBの読取専用監査

実行コマンド:

```powershell
uv run python scripts/audit_dataset.py --database data/poc-v1/dataset.sqlite --inputs data/poc-frozen-v2/work-inputs.jsonl --root . --output data/evaluation/poc-frozen-v2/dataset-audit
```

| 対象 | 確認結果 |
|---|---|
| 作品・レコード | resolved2,000、unresolved26、source versions2,639 |
| 表現・版 | expressions2,040、editions26 |
| raw integrity | 2,639/2,639の実在・SHA-256一致 |
| 出典参照 | SourceRecord2,639/2,639、Evidence6,723/6,723、field assertions53,364/53,364、入力2,000/2,000が構造・policy binding検査に合格 |
| 入力との一致 | snapshot一致、外部キー違反0 |
| 指摘 | error0、warning792。すべて条件付き許諾の義務履行が機械的には未検証 |
| 欠損 | 著者98、原言語39、初出年472、地域1,049、シリーズ1,696作品 |
| 分類・人手 | 分類結果0、gold0、`human_validation_pending`、校正未実施 |

原言語・初出年・地域・シリーズはwork資料のみからWikidata、Open Library、その他、Wikipediaの優先順で既知値を選ぶ。editionの言語・出版年や英語翻訳題から補完しない。書誌資料は全2,000作品で確認できる一方、synopsis469作品・note317作品は条件付き許諾の確認を別に必要とする。出典参照100%は、法的義務の履行や分類品質の完了を意味しない。
