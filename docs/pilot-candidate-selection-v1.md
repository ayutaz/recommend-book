# 新しい世界小説小バッチの候補確定 v1

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-15。計画v15の「次に実施する3項目」の候補準備として、保存済みの小さなWikidata候補一覧とWikipedia記事原応答を再利用した。主DBに未収録の小説55作品を準備した後、最新の1,989作品表示との追加照合で、資料記載ジャンルによって追加済みの19作品を除いた。**実行対象は新しい小説36作品**であり、最初の10作品を確認した後に残り26作品へ進む。上限100件を埋めるための追加収集はしていない。書誌だけの入力は0件で、全作品に700文字以上の、出典と利用条件を再現できる記事あらすじがある。

## 最初の10作品

| 順序 | Wikidata作品ID | 作品名 | 原言語 | あらすじの言語 |
|---|---|---|---|---|
| 1 | Q100997892 | Shuggie Bain | 英語 | 英語 |
| 2 | Q1034591 | Jacob the Liar | ドイツ語 | 英語 |
| 3 | Q111904001 | Elena Knows | スペイン語 | スペイン語 |
| 4 | Q105840426 | The Lost Daughter | イタリア語 | 英語 |
| 5 | Q10262050 | Crônica da casa assassinada | ポルトガル語 | 英語 |
| 6 | Q112489757 | The Stealth | アラビア語 | 英語 |
| 7 | Q106103769 | Lonely Castle in the Mirror | 日本語 | 英語 |
| 8 | Q10936605 | Ballad of the Desert | 中国語 | 英語 |
| 9 | Q1083331 | The Defense | ロシア語 | 英語 |
| 10 | Q108006399 | The Worst Intentions | イタリア語 | フランス語 |

9原言語を含む便宜標本であり、世界小説を代表する標本ではない。原言語と記事あらすじの言語は区別する。入力の`expression_id`はnullであり、翻訳や原文の文体を記事の文章から判定する許可ではない。

## 作品単位と新規性

主DBを読み取り専用で開き、Wikidata ID、個別候補の外部ID、正規化した完全一致タイトルを照合した。一致候補は今回の対象から除いた。ただし、これだけでは最新の追加表示19作品を見落としたため、分類開始前に最新`delivery.json`の1,989作品を照合する条件を追加した。最初の55件・入力・台帳は上書きせず、36件の新しい選択ファイルを作成した。新規入力の原Wikidataでは小説またはライトノベルの直接の型・形式宣言を要求し、非deprecatedの版・翻訳関係`P629`、版型、短編・シリーズ型との競合を保留した。シリーズ所属がある候補も今回は除いた。作品・シリーズ・原文表現・刊行版の同一視はしない。

既知の18個のnative分類入力索引、計2,810行を対象に、今回55作品のwork IDと原Wikidata IDを照合し、一致は0件だった。元の保留25ケースの再分類ではなく、保存候補からの新しい作品追加である。未発見の全履歴や世界全体について重複がないことを証明するものではない。

## 内容と利用条件

保存済み記事のraw SHA256、対象Wikidata原物のSHA256、sitelinkの完全一致、pagepropsの作品ID、ページID、固定revision、名前空間、節全体を照合した。既存の`screened-paragraphs-v1`で完全な段落だけを選択し、除外段落の位置と理由を保持する。追加import notice・著作権noticeのある記事を利用可能に変える処置はしていない。帰属、履歴URL、改変表示、CC BY-SA 4.0の継承をEvidence単位で保存する。

公式条件の原物3件は今回rootが再確認したものを用いる。内容の追加取得は0件。WikidataのCC0書誌とWikipediaのCC BY-SA記事を混同しない。記事あらすじは小説本文や独立した専門家正解ではなく、自動screeningも法律上の完全確認・人手評価ではない。

最初の台帳では、外部LLM・商用・表示・再配布の55作品は許可されたが、取得・ローカル保存は全件保留された。原因は、混合出典のWikidata proposalに付いた記事Evidenceについて、既存guardの無条件許可経路がenclosing sourceの一致を要求することだった。この試行の台帳・receipt・manifestはそのまま保持した。

新しいローカルregistryでは、記事の取得・保存を`allowed`から`conditional`へ強め、既存の帰属・継承・改変表示・第三者テキストscreening義務と原物照合を要求した。他の用途・対象field・source・rights IDは変えず、既存guardも変更していない。この追加条件で**55作品すべてが6用途の原物・由来照合を通過**した。法的保証や独立した精度合格とは区別する。

## 後工程へ渡すもの

準備先は`data/autonomous-next-v3/preparation/phase7-world-pilot-v1/`。データと記事の文章はGit対象外に保つ。

- `candidate-inventory.json`：準備55作品の固定順序、原一覧の行番号とモデルSHA、作品種別、言語、年、原物・revision、根拠ID。
- `ordered-inputs.jsonl`と`first-ten-inputs.jsonl`：最初の55作品とその先頭10作品。履歴として保持し、実行対象にはしない。
- `new-selection-manifest.json`：最新表示SHA、除外19作品、実行対象36作品と最初の10作品、残り26作品を固定する。
- `new-ordered-inputs.jsonl`・`new-first-ten-inputs.jsonl`・`new-remaining-inputs.jsonl`：実際の36・10・26作品。全モデルは元の55件台帳・next source DB内の分類projectionと一致する。
- `source-bindings.sqlite`：新しいsource versionだけを持つ独立したSQLite。checkpointと整合性確認を済ませ、WALに依存しない。
- `prior-run-exclusion-audit.json`：既知の分類入力索引の照合範囲・SHA・一致0。
- `manifest-admitted.json`と`admission-receipts-admitted.json`：最初の10件、最大55件、6用途の結果、失敗した最初の試行への参照。
- `usage-ledger-projections.json`：準備55作品の分類・書誌・別名と型付き派生値をまとめる後工程用台帳。60派生packetは表示・商用の120照合後、6用途360照合でもすべて許可された。結果は`projection-six-purpose-admission-receipts.json`に保存した。native実行recipeの追加は分類開始前の新しいruntimeコピーで固定し、モデル投入は36作品に制限する。

`data/autonomous-next-v3/database/phase7-world-pilot-source-v1/dataset.sqlite`は既存の`build_next_database()`で作成したversion 3.2の新しいsource DBである。`get_next_inputs()`は分類55・書誌55・別名5を返し、分類55件の全モデルは固定入力と作品単位で完全一致した。比較結果は`next-projection-comparison.json`に保存した。原主DB、過去のsource・分類・proofは変更していない。

再現用コードは[`prepare_world_pilot_v1.py`](../scripts/prepare_world_pilot_v1.py)。版・短編・シリーズ型の競合と未解決の著者・題名を拒否する3件のテストを追加し、成功した。分類CLIの呼出はこの候補準備では0件。分類・審査・採用・表示の結果と所要時間は実行側の報告へ記録する。
