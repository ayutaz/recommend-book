# PoC 実行記録

更新日: 2026-10-12。実装のコミットと、実データの取得・分類・人手評価の完了は分けて記録する。

## 固定した取得フレーム

Wikidata の bounded query は、創作形式 P7937 と従来の P31 を併用し、P629 を持つ版候補を作品探索から除外した。取得した 3,154 候補から、seed 1729 で原言語・初出年代を層化して 2,000 作品を選んだ。QID の辞書順で上限を設けた convenience frame であり、世界の小説の無作為標本ではない。

| 原言語の層 | 採用数 |
|---|---:|
| 日本語 | 700 |
| 英語 | 700 |
| その他・不明 | 600 |

作品形式は novel 1,359、light_novel 108、short_story 533。2,000 件を長編小説 2,000 冊と表現しない。年代は 1900 年前 65、1900–1949 年 191、1950–1999 年 584、2000 年以降 688、不明 472。『Pride and Prejudice』等の古典 20 件はこのフレームに含まれていなかったため、原文取得を別セットとして実施した。

選定元 `data/poc-v1/selected-sources.jsonl` の SHA-256 は `415eeb9db180b4c07cb1b47a9abb9b4ff2f40ed0c96e21193e33f7d62a9cc00c`。書誌だけの入力を後の分類スナップショットと同一視しない。

## ソース別の実績と制約

| ソース・段階 | 取得・保存の実績 | 未完了・制約 |
|---|---|---|
| Wikidata | 3,154 候補、2,843 関連 entity のラベル・原物 | 古典、年代、原言語の偏り。出典は構造化データの CC0 に限定 |
| NDL | 29 応答レコードから 26 版を採用、3 件を隔離 | 対応作品の未確定 26 件を作品数に加算しない。検索打切りを全件成功としない |
| Wikipedia | 採用 2,000 候補に対し 595 記事レコード、792 使用可能 Evidence | 固定 revision の原物を保存し prose-v2 で除外処理。CC BY-SA の条件を継承。自動除外は人手の著作権審査ではない |
| Open Library | 2026-09-30 authors dump 782,352,949 bytes の取得・SHA・gzip EOF 照合完了。works の固定 889,192,448 byte prefix と完全 authors から 91 work・867 author を抽出 | works は取得中の partial_sample。日付のみの author 名等を隔離し、次回用候補は 90 work・859 author。現 DB へ統合済みは先行抽出の 14 work・4 author のみ |
| Wikisource | 原文 20 作品、40 抜粋、64,000 Unicode 文字。保存した Wikidata 原物から 10 件の版または version と P629 対応を確認 | JP/US の原作・原版条件に限定。10 件は同定保留。実分類は固定した仮 `ws:en:` ID の結果を保持し、新 DB ID の分類結果へ付け替えない |

NDL と Wikipedia の原物は内容 SHA で照合した。同一 Wikidata QID に複数の Open Library ID があって止まった Wikipedia 4 件は、固定 page/revision・pageprops・継承元 Wikidata 原物を照合する `source_mapping` で統合した。独立した同定や人手判断ではなく、依存する出典同士の対応である。一般の複数 work ID に対する保留条件は維持した。

2026-10-12T01:13:15Z の read-only 監査は source version 2,639、resolved canonical count 2,000、未解決 26、根拠 6,723、field assertion 53,364、入力 2,000。原物 SHA・必須参照は全件照合し、外部キーエラーは 0 件だった。`data/poc-frozen-v2/` に入力・著者/シリーズ ID・DB backup・分類用設定とコードの SHA を固定した。

この時点の監査指摘 792 件は conditional policy の義務を監査器が独立認証していない旨の警告。出典・raw SHA の成功を一律の商用許可、原著本文の権利消滅、法律上の認証に置き換えない。取得器の帰属・変更記録・除外結果を原物と一緒に保持する。

Wikidata 版探索では、提供側の WDQS 障害と 429 を観測した。最初の応答には Retry-After を保存できていなかったため、その値を推定しない。再試行では Retry-After=1000 を保存して合計約 2,000 秒待機したが、最終試行も 429 だった。2026-10-12T01:24:24Z に終了コード 1・版取得 0 件で停止し、次の自動要求は行わない。host cooldown と原応答を保存した。別途 Wikidata entity API で得た古典の対応資料とは集計を分ける。

## 実装検証

コミット `5e8c5c1` の全体テストは Windows / Python 3.14 で 173 passed、`07dd591` は 228 passed。その時点では作業中の独立原文 DB 統合スクリプトとそのテストをコミットから除外した。`29e4f38` は統合・単一DB snapshot・本番入力準備・分類観測・Wikidata題名の扱いの修正を含めて258 passed。続くcombined分類入力・安定性比較を含む全体検証は271 passed、依存lockとGitHub Actionsのactionlintも成功した。Windows/Ubuntu向けCI設定は追加したが、hosted CIは未実行。機械テストの成功を実分類精度の達成とは扱わない。

Migration 3 は evidence の work/scope/order と works の resolved/canonical index を追加した。実 DB の backup コピーで同じ 100 件の根拠検索が 11.895 秒から 0.00685 秒、全 2,004 件の入力生成が 3.710 秒、stats が 0.170 秒となった。これは特定 snapshot・Windows 環境の観測値であり、10 万作品の性能保証ではない。元資料 version/payload hash と undo の検証を保持した。

Windows のジョブ lock は、空の lock file に初期化 byte を書いてから取得する競合を除き、byte-range lock を先に取得する。空 file を別 owner が保持する再現テストと、複数 JobStore の排他的 claim テストが成功した。

## 分類・評価の進め方

原文 20 作品は Sol CLI `gpt-6.1-sol` / CLI 0.162.1、並列 2、batch 5 で実分類が完了した。20 completed・0 failed、587.108 秒、CLI 5 回（1 バッチの厳密な引用照合エラーを再試行）。入力 SHA は `d60b1f2908ccb59fe1e784246c5e3e2e0950ad1f478e1e3b6b6ffcc4f22e6bf2`。全 20 件が schema・ID・参照・引用文字列の機械検証を通過し、原文 scope の文体ラベルは 31 件だった。意味的支持・作品全体の代表性・分類精度は人手未評価であり、calibrated confidence は全件 null。

本体の固定入力は 2,000 作品、SHA `77793ab2c89983cb6174d58ce35d4d353934ac4cb73bd073aafdcae4a3c337fe`。100 作品の pilot は日本語 35・英語 35・その他 30 とし、その他の 12 言語/不明 bucket を保持した。固定 pilot の並列 2→4→8 比較は終了コード0で完了し、全試行100有効・最終失敗0だった。時間は763.832/454.693/371.687秒、p95は97.264/97.911/117.886秒。8並列の1バッチを再試行した。処理量増加・p95悪化・最終失敗率の暫定gateから8並列を選択した。合計観測input token1,700,503・output185,064で、API課金額ではない。

2並列結果の何らかの判定は52/100作品で、全軸unknownは48。genre44、theme30、structure14、protagonist28、style0、atmosphere21作品に判定があった。全72ラベル枠では190/7,200のpresent/absent判定（present190・absent0）で、省略ラベルを負例に変換しない。引用160参照の入力内存在を確認したが、意味的支持・summary忠実性は人手未評価。原文のない本体pilotからstyleを断定していない。

同じpilotの2/4/8結果をオフラインで比較し、片方以上がpresent/absentの枠における同じ判定の割合は86.70%/83.09%/84.39%だった。判定の報告有無が27/35/32枠で変わり、全3runで共通するknown状態は167/214=78.04%。これは安定性の観測で、人手正解率や並列度の因果効果ではない。詳細は [classification-stability-guide.md](classification-stability-guide.md)。

固定2,000作品を内容変更なしで並べ替え、最初の1,000を日本語350・英語350・その他300、レビュー400とpilot100の和集合479作品を全件含む順序にした。`data/runs/poc-frozen-v2-production-v1` へ8並列・batch5で1,000作品の本番分類を開始した。詳細・再開条件は [production-classification-guide.md](production-classification-guide.md)。起動は完了件数を意味しない。

実行中に、中心性不明のrelevance nullを許容するprompt/schemaとknownでnullを拒否するvalidatorの不整合を確認した。[契約レビュー](relevance-contract-review.md) に原因と次版でのvalidation/hash/cache再検証案を保存した。live/frozen設定は変更していない。新しいcanonical古典20件を元2,000行へ末尾追加した [2,020件の次段階入力](combined-production-inputs-guide.md) も準備済みだが、実行は未起動であり、旧contextは入力provenanceとしてのみ保持する。

端末資源は scheduler と生存する子プロセスを別途サンプリングする。測定間隔内に終了したプロセスは記録できず、sample の最大値を厳密な peak と表示しない。

人手の gold は現在 0 件。400 作品の著者・シリーズを分離した dev 150 / calibration 100 / holdout 150 と dev 内の 20 作品の blind review を生成した。初版の言語偏りを修正し、新版の日本語/英語/その他は dev と holdout が 53/53/44、calibration が 35/35/30。混合言語 5 グループ・23 作品を丸ごと保持し、グループ分割・漏洩は 0 件だった。別途名寄せ 400 ペアのレビュー資料を生成したが、人手裁定はまだ 0 件。UI の synthetic annotation、Sol の一致、外部研究の native label は独立した人手裁定ではない。人手の支持判定・作品内容確認がないラベルは精度と confidence calibration を未検証として残す。

## ローカル成果物

固定2,000作品と原文20作品を新しい単一DBへ統合し、resolved canonical work 2,020、外部キーエラー0、integrity checkはokを確認した。元2,000作品の入力hashとsource-version refsは全件不変、古典20件は原作authorityへ8接続・12保留。旧原文分類のIDを書き換えず、新しいcanonical入力に対する分類は未実行として扱う。詳細は [combined-poc-guide.md](combined-poc-guide.md)。

取得原物、DB、モデル結果は `data/` 内に保存し、Git へ含めない。公開・再配布向け成果物を生成する場合は各用途の policy と作品別条件で別途抽出する。今回の作業は推薦ランキングや読書履歴の取り込みへ進めていない。

- `data/poc-v1/`: 選定元、統合 DB、ソース補完。
- `data/poc-frozen-v2/`: 固定 2,000 作品入力、pilot100、著者/シリーズ ID、DB snapshot、実行設定の SHA。
- `data/poc-combined-v1/`: 固定2,000作品と原文20作品の単一DB、版関係・入力不変の監査、旧IDとのcrosswalk。
- `data/evaluation/poc-frozen-v2/`: read-only 監査、分布調整した 400/20 作品レビュー資料。
- `data/poc-v1/resolution-review-20261012-v4/`: 正式な名寄せ 400 ペアレビュー資料。途中版は保持する。
- `data/openlibrary-prefix-v3/`: works 固定 prefix と完全 authors の原物照合・隔離・次回統合候補。
- `data/prose-poc/`: 本文原物、原版・権利条件、固定抜粋、品質の機械チェック。
- `data/runs/prose-style-v1/`: 原文抜粋の実分類。
- `data/reference/`: 外部研究の固定 metadata と帰属・native annotation。本文・現taxonomyの人手正解とは区別。

実行完了後に、この記録へ terminal status と新しい固定入力・分類・評価の観測を追記する。

## 後続の本体採用と2,020実行

旧1,000の実行はterminal（completed975/held25/pending0）になり、旧結果を保持したままv2で1,000件を再検証・移行した。本体の全655テスト成功後、新canonical古典20を含む2,020入力を8並列で起動した。上記の「実行中」「未起動」はその観測時点の履歴であり、現在の進捗・実行条件・残る検証は[2,020入力の実行記録v2](production-v2-run-record.md)を参照。人手gold0・意味的精度/校正未評価は継続している。
