# PoC①〜④の完了監査

2026-10-13追記: 本文の表は旧実験の証拠・未評価範囲を保持する監査記録である。以後の①〜⑤は[ユーザー工数0の自動完了計画 v2](autonomous-completion-plan-v2.md)に従い実施する。人手レビューの未入力をユーザー作業待ちとせず、限定した外部評価・AI補助評価・制約の確定まで担当が行う。新計画の完了は新しい実行証拠で判定し、この旧表を達成済みに書き換えない。

2026-10-12。対象は [実施計画](world-novel-dataset-plan.md) の①収集、②名寄せ、③分類、④分類精度検証。1,000作品を最初の実行段階とし、固定2,000作品と古典20作品を対象とする。推薦・読書履歴・公開は今回の完了条件に含めない。将来の1万〜3万・10万作品は、今回のPoC件数へ数えない。

## 現在の判定と必要な証拠

| 要件 | 現在の証拠 | 判定・残る作業 |
| --- | --- | --- |
| Open Library・NDL・Wikidata等の収集 | `poc-v1`、`openlibrary-complete-v1`、`prose-poc`の原物・SHA・ソース別出典。works/authorsの全ダンプを末尾・CRCまで検証 | PoC用の取得を実施。full dumpからwork437/author959を抽出し、1,396原物を照合。自動保留work2/author8、次回候補work435/author951。現在の固定DBへのOpen Library反映は先行14 work/4 authorで、新候補を分類済み入力へ混ぜていない |
| 用途別利用条件 | `config/sources.json`、許可根拠、作品別原版条件、帰属・変更・条件の記録 | ソース・項目別に管理。一括商用許可や全世界の本文権利確認は未達。未解決資料は分類入力から除外 |
| 世界・年代・ジャンル横断 | 固定2,000は原言語ja700/en700/その他600、古典原文20を追加 | 実際の便宜的フレーム。長編1,359/LN108/短編533はソースmetadataの分類であり、2,000長編や作品種別の人手確認を意味しない |
| 作品単位のDB | `poc-combined-v1/dataset.sqlite`、`manifest.json`、`audit.json`、`verification.json` | 2,020 resolved canonical works、未解決26、FK0、integrity=ok。原2,000のID/input hash/source refs不変を確認 |
| 分類のSQLite反映 | [派生DB importer](classification-database-import-guide.md)、[実派生DB](../data/poc-combined-classified-v2-v1/dataset.sqlite)、[独立SQL監査](../data/evaluation/poc-combined-v2/derived-database-audit-v1.json) | 実適用済み。全2,020結果・3,194ラベル・3,510根拠参照を保存。元20テーブル値を保持、FK0・integrity=ok。本体全769テスト成功 |
| 原題・翻訳・文庫等の関係 | edition/expression/contents、raw assertion、同定ledger、古典8件の原作接続・12件の保留 | 関係を保持する基盤を実装。NDL26件の作品対応は未確定。原題/翻訳題をP1476単独で認定しない。旧固定資料の題名kindは書き換えていない |
| 誤統合取消・同名別作品・巻違い | DB/resolution/normalizationのfixtureテスト | 実装検証済み。人手の名寄せ400ペアは未判定で、実データのprecision・候補recall・cluster誤統合は未評価 |
| Sol CLIの並列実行 | 同じpilot100の2/4/8試行、全300有効、CLI履歴・資源sample | 運用比較完了、8並列を選択。LLM同士の一致を正解率としない |
| 1,000〜3,000作品の本番分類 | [2,020実行記録](production-v2-run-record.md)、[終了proof](../data/evaluation/poc-combined-v2/terminal-proof-v1.json)、[全件監査](../data/evaluation/poc-combined-v2/terminal-audit-v1.json) | exit 0、completed2,020 / failed0 / pending0、再利用1,000 / 新規1,020。全job/index/durableと新205呼出しのraw/event/使用量を照合。旧1,000のcompleted975/held25は旧記録として保持 |
| ジャンル・テーマ・構造・主人公・文体・雰囲気 | 固定6軸72ラベル、missing_axes、[実分布](../data/evaluation/poc-combined-v2/classification-observation-v1/classification-observation.json)、原文scope制約 | 全2,020の判定/保留を保存。何らかの判定962作品（47.62%）、全軸unknown1,058。原文あり古典20の文体31ラベル、main2,000の文体はunknown。新canonical結果を実分類し、旧ID結果の付替えなし |
| 関連度・信頼度 | 生のモデル値、calibrated値null、scope・根拠・欠損 | 実ラベル3,194中、関連度数値3,145 / null49（known内数値付与98.47%）。raw confidenceを保持し、calibrated値は全null。旧known-null拒否をv2で修正。付与率と存在判断coverageを分離し、人手の意味的正しさ・校正は未評価 |
| 人手正解と分割 | balanced400のdev150/calibration100/holdout150、dev内pilot20、72ラベル日本語ガイド | blind pack作成済み、人手gold0。二名判定/裁定または人数制約を記録した実注釈が必要 |
| 分類精度・関連度精度・根拠支持 | 評価器・構造/引用所属の機械照合・分布レポート | 人手precision/recall/F1、関連度MAE・順位相関、意味的支持は未評価。引用文字列の存在はラベル支持の証拠とは別 |
| 信頼度校正 | calibration/holdout分離、[実行可能性監査](calibration-feasibility-v1.md)、[実予測の診断要約](../data/evaluation/poc-combined-v2/calibration-candidates-summary-v1.json)、最小標本条件 | 固定calibration100の同一scope最大はmetadata16 / 実known scored10で、必要な40件に届かない。全400から分離したfresh pool1,600作品/1,034 groupsを実診断し、追加で数量条件を満たし得るのは英語SF・日本語恋愛の2ラベルのみ。人手truth・whole group配分は未検証、fit/追加partition割当0。閾値を下げず、範囲外はnullを維持 |
| 固定品質gate・再現・復旧 | `poc-quality-gates-v1.md`、immutable入力、lease/cache/再開テスト | 条件は固定済み。全本番結果・人手評価・処理量を照合した最終品質レポートは未完了 |
| fixtureによるCI | `.github/workflows/tests.yml`のWindows/Ubuntu設定、依存lock・workflow構文の検証、追加時点のローカル269テスト成功 | CI設定を追加済み。GitHub hosted実行は未実施。ローカル成功をCI実行成功として報告しない |

## 次の受け渡し

旧1,000のterminal監査、本体へのv2採用、旧値を保持した新namespaceへの移行、[2,020実行](production-v2-run-record.md)の実終了確認・全件監査・派生DB保存・校正候補診断、Open Library works/authors全ダンプの取得・末尾検証・抽出原物照合は実施済み。元入力・旧結果・移行原本を保持している。人手の名寄せ・分類精度・関連度・根拠支持・校正の評価が残る。

校正2ラベルの追加数量を、全400から独立したconnected groupを分割しない条件で試算した。[数量試算](../data/evaluation/poc-combined-v2/calibration-whole-group-capacity-v1.json)では、追加known scoredの最低71件を確保するwhole groupの最小費用は74作品 / 35 groups。既存calibrationの人手判定も未入力であり、新74作品がすべて判定可能なtruthを持つとは限らない。追加partitionやレビュー回答は作成せず、他ラベルの校正可能性へ一般化しない。

人手の最初の作業は [pilot20 blind review](../data/evaluation/poc-frozen-v2/pilot20-review-balanced-v1/review.html)。[ラベルガイド](human-taxonomy-v1-guide.md) と [インポート・評価手順](evaluation-guide.md) を使用する。入力資料への忠実性と作品内容truthを分け、実施人数・時間・判定不能・裁定を記録する。LLM、synthetic UI操作、positive-only外部参照をこの人手goldへ混ぜない。

以上の未完了項目が残るため、①〜④全体の達成は現時点では確認できない。最終監査では、この表の各要件に対応する実際の入力・DB・terminal manifest・独立注釈・評価結果を確認する。
