# 新規資料・評価経路の確認とPoC再判定

2026-10-16。phase10後の計画1〜4を実施した。**新規の分類入力・採用特徴・履歴接続は0件。表示2,026候補・特徴あり1,028件・得点可能216件を維持する。** 無料・外注なし・追加本人作業0で進め、条件を通らない資料は分類しなかった。PoC全体と10万作品の最終目標は未完了である。

## 計画1〜4の結果

| 工程 | 実施と結果 |
|---|---|
| 1. 新しい根拠 | 履歴用の公式経路2本、SEの新しいジャンル別catalog3経路を確認。保存144行から新規小説候補10件を固定してOPF各1回・Wikidata API計2回を取得し、公式作品IDを10件確認。完全な分類入力は成立しなかった |
| 2. 独立評価 | 新規2資料を調査。分類定義・作品対応・利用条件・正負注釈が同時に成立する評価組は0。独立precision/recall/F1・校正は未評価 |
| 3. 成立分の分類・反映 | 紹介文の形式検査は1件通ったが、名寄せと完全入力の用途検査で保留。分類0回・審査0回・採用0件。初期・残りバッチを起動せず、画面生成・同じ推薦比較・ブラウザー確認も繰り返さなかった |
| 4. PoC・拡張判断 | 得点可能216、内容接続2/335、既存36件の採用23件を維持。独立評価・代表的な全工程処理量・無料残枠が不足し、1万→3万→10万への大量処理を見送った |

範囲とモデル上限は[分類前の実行仕様](../data/autonomous-next-v3/evaluation/phase11-completion-v1/requirements-before-classification.json)、実際の判定と容量は[集計原票](../data/autonomous-next-v3/evaluation/phase11-completion-v1/result-report.json)に保存した。上限は必要な処理の制限であり、満たすための追加探索はしない。

## 履歴に接続できなかった理由

青空文庫の公式作品一覧19,502行とProject Gutenbergの公式catalog79,576行を確認した。正規化した書名・作者の候補一致はともに0件で、原連載のNコード欄もなかった。書名一致だけでは接続を採用しない。既調査のなろうAPI、著者profile、Open Library・NDL等の経路は再取得していない。[照合記録](../data/autonomous-next-v3/evaluation/phase11-history-evidence-v1/local-match-audit.json)と[取得記録](../data/autonomous-next-v3/evaluation/phase11-history-evidence-v1/source-manifest.json)を保持した。

私有履歴の照合はローカルだけで行い、書名・作者・Nコード・評点を外部へ送っていない。青空文庫の書誌CSVと本文、Gutenbergのcatalogと個別ebookの条件は区別した。[青空文庫利用規準](https://www.aozora.gr.jp/guide/kijyunn.html)、[Gutenberg機械取得方針](https://www.gutenberg.org/policy/robot_access.html)

取得記録の`gutenberg-offline-catalogs.html`のSHA欄は63桁の転記誤りだった。原記録を変更せず、集計原票へ現在の原物の64桁SHAを別記した。原記録の不正なSHAで取得時の整合を証明したとは扱わない。

## 新規作品を既存DBとの一致だけで除外しない

SEのmystery・fantasy・science-fictionの公式catalogを各1回、計144行確認した。既存の保存書誌との候補一致は0件。`SourceRecord`の題名は`titles[].value`、著者は`contributors[]`であり、正しい配列を使った再照合でも0だった。これは候補探索の結果であり、タイトルだけによる同定ではない。

既存DBにない作品も収集対象なので、同じ保存catalogから単一小説10件を個別取得前に固定した。新しいcatalog取得は追加せず、10個のOPFと公式Wikidata原物を保存。全10件で正確なenwiki対応・直接のnovel主張・作者対応を確認した。[固定選定と取得結果](../data/autonomous-next-v3/evaluation/phase11-new-authority-v1/acquisition-report.json)に原物・日時・SHAを結び付けた。IDの成立を本文利用や分類採用の成立へ読み替えない。

最初のparserは外部リンク5件・引用2件・著者役割3件を保留した。唯一の著者`aut`に`ill`が併記される形式を確認したため、明示的な挿絵担当の兼任に対応した。`ill`はMARCのillustratorである。[米国議会図書館の公式役割コード](https://www.loc.gov/marc/relators/relacode.html)を確認し、著者なし・重複著者・翻訳者役割の不整合は引き続き拒否する。原物を変更したり、引用・外部リンク・用途条件を緩めたりしていない。

修正後は『アクロイド殺し』1件が紹介文検査を通ったが、Wikidata Q224078にOpen Library作品IDが2つあり、既存の名寄せは`multiple work identities in one authority namespace`で保留した。別の小さなDBで書誌と編集紹介文を取り込む試行も、異なるcanonical作品が2件となり採用を拒否した。authorityを入力の外へ分離する試行は6用途すべてで`Standard Ebooks work authority not bound in complete input`となった。[保留された取込み](../data/autonomous-next-v3/preparation/phase11-standardebooks-batch-v1/sources/source-bindings.sqlite)、[用途拒否](../data/autonomous-next-v3/preparation/phase11-standardebooks-batch-v2/admission-receipts.json)を保存し、試作した別経路は本番コードへ採用していない。

最終的な資料保留は、紹介文の引用2件・外部リンク7件、完全な名寄せ・用途入力の保留1件。新しい根拠や明確な訂正なしに、同じ10件を再取得・再分類しない。

## 独立評価資料の適合性

| 新規資料 | 確認したこと | 評価へ採用しない理由 |
|---|---|---|
| CONLIT | 2,754行。MY234・SF223。公式配布APIとデータ論文を確認 | MYはMystery/Thriller/Suspense、SFはScience Fiction/Fantasyの混合集合。現定義の正負注釈ではなく、配布APIのCC BYと論文記載のCC BY-NCにも差がある |
| Gutenberg genre companion data | 3,403行・3,324 ebook ID。2015年研究のcatalog subject検索によるジャンル正例 | 明示負例と現分類の操作的定義がない。未記載を負例へ変換せず、独立した分類精度・校正には使わない |

[適合性報告](../data/autonomous-next-v3/evaluation/phase11-independent-reference-v1/report.json)と[一次資料receipt](../data/autonomous-next-v3/evaluation/phase11-independent-reference-v1/source-receipts.json)を保存した。作品の無変更再照合や新しいLLM正解作成は行っていない。[CONLIT公式配布](https://figshare.com/articles/dataset/CONLIT/21166171/1)、[CONLITデータ論文](https://openhumanitiesdata.metajnl.com/articles/88/files/submission/proof/88-1-1498-1-10-20221011.pdf)、[Gutenberg研究論文](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0141922)

## 修正・検証と拡張判断

今回採用したコード変更は2点に絞った。最新画面が2,026候補になった後も更新処理が2,025件を固定要求していた不具合を修正し、非空・作品IDの一意性・公開auditの実件数整合を検査するようにした。明示的な著者と挿絵担当の兼任形式にも対応した。原物hash・完全入力・用途・不確実な特徴の保留は維持する。

[実ベースラインの読取り検証](../data/autonomous-next-v3/evaluation/phase11-completion-v1/baseline-validation.json)は2,026/1,028/216を確認した。新規入力が採用されていないため、新しい表示生成・撤回画面・ブラウザー確認の成功は主張しない。phase10の公開3ファイルと集計原票に結び付いた旧証拠のSHAは一致した。

紹介文・用途検査73件、件数固定修正の対象14件と既存統合24件が成功。最終コード`fe45415`の[両OS CI](https://github.com/ayutaz/recommend-book/actions/runs/38854021948)はUbuntu 1,765 passed・7 skipped（43.54秒）、Windows 1,766 passed・6 skipped（291.32秒）で成功した。[CIガイド](ci-validation-guide.md)にskip内訳を記録する。push/PRの重複実行を避け、手動dispatchで両OSを一組だけ起動した。先行する件数修正だけのCIも成功したが、その後に役割形式を修正したため最終版を別途検証した。結果を記録する後続変更はMarkdownだけである。実装と報告は[PR #13](https://github.com/ayutaz/recommend-book/pull/13)に集約する。

分類・審査の実CLI呼出と観測tokenは0。開発agent・会話の総利用量ではない。phase11の新しい取得物・失敗した準備を含む保存量は38,862,445bytesで、completion報告・ログを除く。新規バッチの全工程時間・採用率は測定できず、採用率や出力検証保留率は0%とせず未測定とする。

1万→3万→10万の判定はNO GO。主DB2,005候補、小説・LN候補上限1,353、既存採用23/36、内容接続2/335、独立正解0、無料残枠不明を維持する。新しい10 authorityを採用小説10件へ加算しない。今回の経路はここで終了し、次は具体的な保留を解消する新しい資料や訂正が得られた場合だけ再開する。
