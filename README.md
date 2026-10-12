# recommend-book

世界の小説を作品単位で管理し、出典付きの多ラベル分類を作成するデータ基盤です。書誌・原題・翻訳表現・刊行版を分け、元資料と利用条件、根拠、関連度、未校正の信頼度、保留理由を保持します。

現在の対象は、データ収集・統合・分類・分類精度検証までです。実施計画は [世界の小説データセット計画](docs/world-novel-dataset-plan.md)、実取得・実分類の確認状況は [PoC実行記録](docs/poc-execution-report.md) に記録しています。

各要件の完了証拠と残る作業は [PoC完了監査](docs/poc-completion-audit.md)、Windows/Ubuntuのテスト設定は [CI検証ガイド](docs/ci-validation-guide.md) に記録しています。

## 開発環境

Python 3.12以上とuvを使用します。Windows/Python 3.14で検証しています。

```powershell
uv sync --locked
uv run pytest -q
uv run recommend-book --help
```

LLM分類には、認証済みのCodex CLIから `gpt-6.1-sol` を使用します。小バッチ・並列実行・上限・キャッシュ・再試行・停止状態を記録します。ライブ分類はアカウントの利用量を消費するため、通常のテストでは固定fixtureを使用します。

## 処理と文書

| 工程 | 内容・参照先 |
|---|---|
| 収集・利用条件 | Open Library/Wikidata/NDL等の原物とSHAを保存し、用途別の利用条件を保持。[計画](docs/world-novel-dataset-plan.md)、[実績](docs/poc-execution-report.md) |
| 作品DB・名寄せ | SQLiteで作品/表現/版・author/series authority・immutable source versionを管理。[出典対応](docs/source-mapping-resolution.md)、[NDLの接続不足](docs/ndl-resolution-coverage.md)、[古典原文の版関係](docs/prose-integration-guide.md)、[2,020作品の単一DB](docs/combined-poc-guide.md)、[分類結果の派生DB保存](docs/classification-database-import-guide.md) |
| LLM分類 | 6軸72ラベル、根拠・関連度・raw confidence・unknownを保存。[1,000→2,000作品実行](docs/production-classification-guide.md)、[分類の観測](docs/classification-observation-guide.md)、[2,020入力のv2実行](docs/production-v2-run-record.md) |
| 精度検証 | 独立レビュー・dev/calibration/holdout・根拠支持・校正。[評価手順](docs/evaluation-guide.md)、[固定品質条件](docs/poc-quality-gates-v1.md)、[分布調整した400作品](docs/evaluation-balanced-v1.md)、[校正標本の実行可能性](docs/calibration-feasibility-v1.md)、[実予測からの校正候補診断](docs/calibration-candidate-diagnostic.md) |
| 人手判定 | [72ラベル日本語ガイド](docs/human-taxonomy-v1-guide.md)、[名寄せ400ペア](docs/resolution-review-guide.md) |

## データと評価の扱い

取得原物・SQLite・固定入力・分類結果・レビュー資料は `data/` に保存し、Git管理から除外しています。ローカルの実取得データを使う手順は実行記録にあり、文書中の件数やSHAはそのスナップショットに対する値です。

利用条件はソースと項目ごとに異なります。メタデータの条件を本文へ広げず、原物・帰属・変更記録・用途・作品別条件を保持します。根拠不足はunknownとし、省略ラベルを負例に変換しません。

Schema・ID・引用所属の機械検証と、作品内容やラベルの意味的な正しさは別に評価します。人手goldがない段階では分類精度と信頼度校正は未評価です。進捗は実行記録と各manifestで確認します。
