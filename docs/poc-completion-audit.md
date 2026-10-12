# PoC①〜④の完了監査

2026-10-12。対象は [実施計画](world-novel-dataset-plan.md) の①収集、②名寄せ、③分類、④分類精度検証。1,000作品を最初の実行段階とし、固定2,000作品と古典20作品を対象とする。推薦・読書履歴・公開は今回の完了条件に含めない。将来の1万〜3万・10万作品は、今回のPoC件数へ数えない。

## 現在の判定と必要な証拠

| 要件 | 現在の証拠 | 判定・残る作業 |
| --- | --- | --- |
| Open Library・NDL・Wikidata等の収集 | `poc-v1`、`openlibrary-prefix-v3`、`prose-poc`の原物・SHA・ソース別出典。全authors dumpはEOF検証済み、worksは固定prefix抽出 | PoC用の取得を実施。works全ダンプは取得中。partialの未発見を全体の不在としない |
| 用途別利用条件 | `config/sources.json`、許可根拠、作品別原版条件、帰属・変更・条件の記録 | ソース・項目別に管理。一括商用許可や全世界の本文権利確認は未達。未解決資料は分類入力から除外 |
| 世界・年代・ジャンル横断 | 固定2,000は原言語ja700/en700/その他600、古典原文20を追加 | 実際の便宜的フレーム。長編1,359/LN108/短編533はソースmetadataの分類であり、2,000長編や作品種別の人手確認を意味しない |
| 作品単位のDB | `poc-combined-v1/dataset.sqlite`、`manifest.json`、`audit.json`、`verification.json` | 2,020 resolved canonical works、未解決26、FK0、integrity=ok。原2,000のID/input hash/source refs不変を確認 |
| 分類のSQLite反映 | [派生DB importer](classification-database-import-guide.md)、元テーブル・scope・数値保持のfixture | 実装済み。本体全769テスト成功。終了証拠を要求するため、live2,020 runへの実適用は終了後に行う |
| 原題・翻訳・文庫等の関係 | edition/expression/contents、raw assertion、同定ledger、古典8件の原作接続・12件の保留 | 関係を保持する基盤を実装。NDL26件の作品対応は未確定。原題/翻訳題をP1476単独で認定しない。旧固定資料の題名kindは書き換えていない |
| 誤統合取消・同名別作品・巻違い | DB/resolution/normalizationのfixtureテスト | 実装検証済み。人手の名寄せ400ペアは未判定で、実データのprecision・候補recall・cluster誤統合は未評価 |
| Sol CLIの並列実行 | 同じpilot100の2/4/8試行、全300有効、CLI履歴・資源sample | 運用比較完了、8並列を選択。LLM同士の一致を正解率としない |
| 1,000〜3,000作品の本番分類 | `poc-frozen-v2-production-v1`の実行handleと個別durable results | 旧1,000はterminal completed975/held25。v2で旧値を保持したcache1,000件を準備し、[2,020実行](production-v2-run-record.md)を開始。全2,020のterminal manifestと結果照合は未完了 |
| ジャンル・テーマ・構造・主人公・文体・雰囲気 | 固定6軸72ラベル、missing_axes、根拠参照、原文scope制約 | 各軸の判定/保留を保存。原文なしで文体を補わない。古典20の旧ID結果は新canonical入力の結果に付け替えない |
| 関連度・信頼度 | 生のモデル値、calibrated値null、scope・根拠・欠損 | 付与基盤あり。旧validatorのknown-null拒否をv2で修正し、本体655テスト成功。中心性不明はnullを保持し、存在判断coverageと関連度availabilityを分離。人手の意味的正しさ・校正は未評価 |
| 人手正解と分割 | balanced400のdev150/calibration100/holdout150、dev内pilot20、72ラベル日本語ガイド | blind pack作成済み、人手gold0。二名判定/裁定または人数制約を記録した実注釈が必要 |
| 分類精度・関連度精度・根拠支持 | 評価器・構造/引用所属の機械照合・分布レポート | 人手precision/recall/F1、関連度MAE・順位相関、意味的支持は未評価。引用文字列の存在はラベル支持の証拠とは別 |
| 信頼度校正 | calibration/holdout分離、実装テスト、[実行可能性監査](calibration-feasibility-v1.md)、最小標本条件 | 固定calibration100は同一scopeの最大が16件で、必要な40件へ構造的に届かない。元400を維持した別の追加calibrationが必要。[実予測の候補診断](calibration-candidate-diagnostic.md)を実装。terminal後にknown予測・confidence帯・同一contextを照合し、人手truthと追加packの対象範囲を決める。範囲外はnullを維持 |
| 固定品質gate・再現・復旧 | `poc-quality-gates-v1.md`、immutable入力、lease/cache/再開テスト | 条件は固定済み。全本番結果・人手評価・処理量を照合した最終品質レポートは未完了 |
| fixtureによるCI | `.github/workflows/tests.yml`のWindows/Ubuntu設定、依存lock・workflow構文の検証、追加時点のローカル269テスト成功 | CI設定を追加済み。GitHub hosted実行は未実施。ローカル成功をCI実行成功として報告しない |

## 次の受け渡し

旧1,000のterminal監査、本体へのv2採用、旧値を保持した新namespaceへの移行は実施済み。[2,020実行](production-v2-run-record.md)の終了を同じhandleで確認し、新canonical古典20を含む全結果、実再利用数、新CLI量、関連度欠損を照合する。元入力・旧結果・移行原本は保持する。

人手の最初の作業は [pilot20 blind review](../data/evaluation/poc-frozen-v2/pilot20-review-balanced-v1/review.html)。[ラベルガイド](human-taxonomy-v1-guide.md) と [インポート・評価手順](evaluation-guide.md) を使用する。入力資料への忠実性と作品内容truthを分け、実施人数・時間・判定不能・裁定を記録する。LLM、synthetic UI操作、positive-only外部参照をこの人手goldへ混ぜない。

以上の未完了項目が残るため、①〜④全体の達成は現時点では確認できない。最終監査では、この表の各要件に対応する実際の入力・DB・terminal manifest・独立注釈・評価結果を確認する。
