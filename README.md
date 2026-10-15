# recommend-book

世界の小説を作品単位で管理し、出典・用途条件・根拠付き多ラベル分類を保持するデータ基盤です。原題・翻訳表現・刊行版を分け、関連度、未校正の信頼度、unknown、保留理由を保存します。

更新: 2026-10-15。現在地は[実生成・検証報告](docs/autonomous-next-delivery-report-v3.md)、10万作品以上への実行順序は[計画 v15](docs/autonomous-next-phase-plan-v15.md)を参照してください。[最新推薦画面](data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v2/index.html)は1,989候補・特徴あり1,004件（LLM分類820＋資料記載184）・暫定得点213件です。連載URLとの確定関係は10/335件へ増えましたが、内容特徴に接続した履歴は2/335件のままで、品質改善は未実証です。追加のユーザー作業0を前提とします。Git操作の環境拒否後、通常の作業ブランチ切り替えが成功しました。ソースの反映状況とCIは下記ブランチ先頭に対して確認します。

## 現在の結果

| 対象 | 確認済み |
|---|---|
| 作品候補 | 2,005件。小説1,273・ライトノベル80・短編528・種別未確定124 |
| 本分類 | 対象1,862作品、結果保存1,832・保留30・未処理0 |
| 分類ラベル | 2,131件。採用条件を反映した特徴あり778作品。文体は全1,832作品で不明 |
| ローカル表示 | 書誌1,870・書名文字類似1,870・分類検索1,807作品。日本語別名検索、AND検索、解除、0件表示を実画面で確認 |
| 追加の推薦表示 | 1,989候補・特徴あり1,004件。資料記載184件をLLM分類と区別。直前1,961件のうち、特徴なし2件の追加以外は1,959行が完全一致 |
| 独立資料の別コホート | フランス語原文5作品の編集者ジャンル正例5件中4件を検出。正例限定で、正答率・precision・現72ラベルの品質評価とは別 |
| 検証 | 完成DBの整合性ok・外部キー違反0、元の3HTML再生成一致。最新ローカル全1,634テスト成功（160.90秒）・依存lock確認成功。最新差分のリモートCIは未実行 |

[分類DB](data/autonomous-next-v3/database/final-delivery-main-v1/classified-database/dataset.sqlite)と[修正版画面](data/autonomous-next-v3/database/final-delivery-view-v2/site/index.html)はローカルに保存しています。画面はブラウザで直接開けます。検証用HTTPサーバーは終了済みです。取得原物・DB・評価資料を保存する `data/` はGit管理から除外しており、GitHubのcheckoutだけではデータは揃いません。

## 未完了の範囲

元の「工程4まで」のうち独立した分類精度評価は、適用できる正解データ不足で未完了です。本分類へ適用できる独立専門家正解は0件で、precision・recall・F1・Brier・ECE・信頼度校正は未評価です。限定した別コホートの評価と推薦PoCは実行済みですが、全件AI審査や出力SHAの一致を意味的な分類精度へ読み替えません。今後の改善は計画書の再開条件に従います。新しい本人評価・試読・採点・履歴入力・画面確認は依頼しません。

読書状態不明を未読へ変換せず、本人の満足度や未読推薦精度の改善を検証済みとは扱いません。翻訳・版の対応は一部に限られ、包括的な商用利用許諾・世界全体の網羅性も未確認です。1万作品以上への拡張とToC公開は実行していません。

ソースの反映先は作業ブランチ [`feat/world-novel-dataset-poc`](https://github.com/ayutaz/recommend-book/tree/feat/world-novel-dataset-poc) です。`0b2db9e`は既存38コミット時点の記録であり、追加分の反映状況はブランチ履歴と当該先頭のCIで確認します。以前のブランチ切り替え拒否は履歴として保持し、2026-10-15の通常実行では切り替えに成功しました。mainへの直接push・mergeは行いません。

## 開発環境

Python 3.12以上とuvを使用します。実行・検証環境はWindows/Python 3.14です。

```powershell
uv sync --locked
uv run pytest -q
uv run recommend-book --help
```

上記は開発用コマンドです。最新ローカル試験1,634成功の記録を利用し、今回の文書更新では再実行していません。LLM分類は認証済みCodex CLIの `gpt-6.1-sol` を使用し、実行条件・入力・原応答・利用量を保存します。主DBの特徴あり778、追加表示のLLM分類820、資料記載ジャンル184は分けて集計します。推薦候補への30特徴作品の追加では分類LLM呼出0、公開メタデータ20 QIDを1リクエスト・再試行0で取得しました。別途French評価はCLI1回・5作品で実行済みです。両者の件数・用途・実行量を混ぜず、資料記載を厳密72分類の品質正解や校正済み確率として扱いません。

## 文書と証跡

| 用途 | 参照先 |
|---|---|
| 最新結果・成果物・検証範囲 | [実生成・検証報告 v3](docs/autonomous-next-delivery-report-v3.md) |
| 最終ゴールへの段階計画・阻害条件・判断条件 | [計画 v15](docs/autonomous-next-phase-plan-v15.md) |
| 初期設計・当時の調査 | [世界の小説データセット計画](docs/world-novel-dataset-plan.md) |
| 利用条件と用途の分離 | [利用条件レビュー範囲](docs/source-rights-review-scope-v3.md)、[UsageGuard](docs/usage-guard-v3.md) |
| DB・分類・参照の実装 | [DB](docs/next-database-v3.md)、[分類](docs/guarded-classification-v3.md)、[外部参照](docs/next-reference-v3.md) |
| 過去の実行結果 | [v2最終報告](docs/autonomous-poc-final-report.md)、[過去の工程別証拠](docs/autonomous-completion-progress.md)、[v14計画](docs/autonomous-next-phase-plan-v14.md) |
| CI設定 | [CI検証ガイド](docs/ci-validation-guide.md)。[`0b2db9e`のWindows/Ubuntu CI成功](https://github.com/ayutaz/recommend-book/actions/runs/38668029235)は過去の記録。[作業ブランチのCI一覧](https://github.com/ayutaz/recommend-book/actions?query=branch%3Afeat%2Fworld-novel-dataset-poc)で対象コミットを確認 |

過去の件数・完了状態・テスト結果は、それぞれのスナップショットに対する記録です。現在の確定値と混ぜません。メタデータ、説明文、表紙、本文、翻訳の利用条件を分け、根拠不足や未注釈をunknownとして保持します。
