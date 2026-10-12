# recommend-book

2026-10-13更新: [ユーザー工数0の計画](docs/autonomous-completion-plan-v2.md)に従い、資料補充・外部注釈評価・AI審査・推薦比較・ローカル納品を実行しました。新しい本人評価・試読・感想・履歴入力・画面確認は0件。旧人手品質gateの未検証記録を保持します。

[最終実行結果](docs/autonomous-poc-final-report.md)と[工程別の証拠](docs/autonomous-completion-progress.md)に、実行・保留・未検証の範囲を記録しています。分類品質、世界作品の本人への推薦品質、全素材の商用利用・全世界網羅は未検証です。

世界の小説を作品単位で管理し、出典付きの多ラベル分類を作成するデータ基盤です。書誌・原題・翻訳表現・刊行版を分け、元資料と利用条件、根拠、関連度、未校正の信頼度、保留理由を保持します。

データ収集・統合・分類・評価と、保存済みの個人評価を使う推薦PoCを対象とします。初期計画は[世界の小説データセット計画](docs/world-novel-dataset-plan.md)、旧実績は[PoC実行記録](docs/poc-execution-report.md)に保持します。

Windowsの最終テストは1,132件成功、実装170ファイルの前後SHA一致。Windows/UbuntuのCI設定は[CI検証ガイド](docs/ci-validation-guide.md)にあり、GitHub hosted CIは未実行です。旧[完了監査](docs/poc-completion-audit.md)は当時の履歴で、現在のユーザー作業条件はv2計画を適用します。

最終[分類DB](data/autonomous-poc-v1/database/final-v1/dataset.sqlite)は歴史2,020件・3,628ラベル。旧1,941結果と根拠補充した79件の新分類を保存し、作品/版scope矛盾15件を推薦から隔離します。分類blind400件の審査はAI採用候補332（allunknown187）・保留68、名寄せ400ペア＋26未解決は全件処理・保留55・authority採用0。人手gold・意味精度・関連度精度・校正は未検証です。

保存済みのなろう評価335件を読み取り専用で使用し、2,005候補から[推薦100件](data/autonomous-poc-v1/delivery-v1/recommendations.html)を生成しました。分類特徴あり1,063件・なし942件、採点可能731件。実画面と別再生成8内容出力のbytes一致を確認しています。確認済み未読は0件で、候補は「読書状態未確認」です。人気順を上回る根拠はなく、worldの比較可能な人気指標がないためpersonalの参考順位を表示します。[納品手順](docs/autonomous-delivery-guide.md)、[8設定比較](docs/autonomous-recommendation-evaluation.md)を参照してください。

外部60/180作品の正例検出は24/65、全200正例分母では24/200。専用native20の直接文字列一致は2/40。正例だけの抜粋評価をaccuracy/F1や全分類の合格へ読み替えません。新81根拠の条件履行と、旧WP792/WS40の現在の許諾未再承認を区別し、新79入力に含まれた旧WP7は事後保留に確定しました。[外部評価](docs/autonomous-reference-run.md)、[商用化・拡張の制約](docs/autonomous-commercial-scale-preparation.md)に記録しています。

Open Library補充後の[変更268作品の固定入力](docs/enrichment-classification-preparation.md)を8並列で分類し、[新分類の推薦への統合](docs/recommendation-overlay-guide.md)と独立監査まで実施しました。旧IDの既読履歴もSQLで検証した対応表から引き継ぎます。[推薦の誤差分析](docs/recommendation-error-analysis.md)では、特徴欠損と大きな同点群、対象分野の実評価不足を分けて記録しています。

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
| 自動審査 | [blind分類400件](docs/autonomous-silver-run.md)、[名寄せ400ペア＋26件](docs/autonomous-resolution-run.md)。新しい本人評価0 |
| 旧人手レビュー資料（履歴） | [72ラベル日本語ガイド](docs/human-taxonomy-v1-guide.md)、[旧名寄せレビュー資料](docs/resolution-review-guide.md)。ユーザー作業の依頼ではない |
| 書誌補充 | 完全ダンプの候補から264作品・192著者を新DBへ統合。[raw照合・保留・ID crosswalkの検証](docs/openlibrary-enrichment-validation.md) |
| 個人推薦 | [推薦PoC手順](docs/recommendation-poc-guide.md)、[実行・評価結果](docs/recommendation-poc-run-record.md)、[嗜好の参照adapter](docs/narou-reference-adapter.md)、[世界DB adapter](docs/world-recommendation-adapter.md) |

## データと評価の扱い

取得原物・SQLite・固定入力・分類結果・レビュー資料は `data/` に保存し、Git管理から除外しています。ローカルの実取得データを使う手順は実行記録にあり、文書中の件数やSHAはそのスナップショットに対する値です。

利用条件はソースと項目ごとに異なります。メタデータの条件を本文へ広げず、原物・帰属・変更記録・用途・作品別条件を保持します。根拠不足はunknownとし、省略ラベルを負例に変換しません。

Schema・ID・引用所属の機械検証と、作品内容やラベルの意味的な正しさは別に評価します。人手goldがない段階では分類精度と信頼度校正は未評価です。進捗は実行記録と各manifestで確認します。
