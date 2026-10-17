# 保存済み小説の名寄せ訂正・分類・推薦反映

2026-10-17。計画1〜4を進め、保存済み10候補のうち5小説を分類・内部審査・別DB採用・推薦画面への追加まで実行した。**表示2,031候補、特徴あり1,033件、得点可能216件**。新規16特徴を採用したが、履歴接続・独立した分類精度・本人向け推薦改善の検証は進んでいない。無料・外部委託なし・追加本人作業0を維持した。

## 1. 単独作品と合本のIDを区別した

『アクロイド殺し』Q224078の保存済みWikidataには、Open LibraryのOL471932WとOL471547Wの2つが作品IDとして紐づいていた。両者の公式JSONを各1回だけ取得し、別のworkキー・HTTP200・redirectなしを確認した。OL471547Wは『Agatha Christie, tome 2 / Les Années 1926-1930』で、`Contains:`に6作品を明示する合本だった。[単独作品](https://openlibrary.org/works/OL471932W.json)、[合本](https://openlibrary.org/works/OL471547W.json)

元raw・正規化記録・P648全主張・正確なlocatorを保持し、合本IDだけを同一作品の識別子から隔離した新しい正規化記録を作った。訂正packetと根拠raw・recipeのSHAを用途検査で再検証する。一般の複数ID保留ルールは変更していない。

合本の収録リストでリンクされるIDはOL472086Wの1件だけで、他5件は書名文字列である。OL471932WとOL472086Wの同一性は未確認で、その同一性を仮定した統合も行っていない。現行Wikidataのrevision 2508621567にも両P648が残っており、外部サイトの訂正は行っていない。

証拠はローカルの[同定報告](../data/autonomous-next-v3/evaluation/phase12-identity-v1/report.json)と[訂正packet](../data/autonomous-next-v3/preparation/phase12-targeted-editorial-v3/identity-correction.json)に保存した。

## 2. 紹介文のリンクと引用を区別した

代表『The House of the Arrow』で拒否されていたリンクは、Wikipedia本文へのリンクではなく、Standard Ebooks自身の`/collections/inspector-hanaud`だった。HTTPS・正確な同一host・1つのcollection slugだけを許可し、既存のauthor/ebookリンクと同様、見えている文字と全段落を保持する。credentials・port・query・fragment・類似host・javascriptは拒否する。リンク先は取得しない。

紹介文3段落の抽出は成立したが、この作品自体はWDの著者フルネームとOPFのイニシャル名の対応を通常の名寄せで証明できず保留した。タイトルや作品QIDだけで著者の不一致を無視しない。引用検査・版・著者役割・用途条件も維持する。[リンク審査](../data/autonomous-next-v3/evaluation/phase12-link-review-v1/review-report.json)

再判定は保存済み10 OPFだけに限定した。取込み可能5件、著者対応保留1件、引用保留4件となった。名称の出典を完全入力へ結び付けるため、保存済み作者rawを再利用し、不足した国・シリーズ4 IDの英語labelだけを公式APIで1回取得した。OPFやcatalogの再取得は0。旧記録は上書きせず、新しい名前の正規化とその出典を保存した。

## 3. 5作品を実分類・内部審査・画面反映した

| 新規作品 | 採用特徴 |
|---|---|
| The Hound of the Baskervilles | ミステリー、探偵の主人公、調査の筋、緊張した雰囲気 |
| The Murder of Roger Ackroyd | ミステリー、探偵の主人公、調査の筋 |
| The Mysterious Affair at Styles | ミステリー、探偵の主人公、調査の筋 |
| The Sign of Four | ミステリー、探偵の主人公、調査の筋 |
| The Valley of Fear | ミステリー、探偵の主人公、調査の筋 |

WD書誌・SE版・SE独自紹介文の3記録を含む完全入力を作り、6用途すべての許可を確認した。Sol CLI `gpt-6.1-sol`、実CLI `codex-cli 0.162.1`、provenance-v4の固定環境で分類1回・別の完全入力内部審査1回を実行。5/5完了・審査保留0・再分類0で、5作品16特徴を別DBへ採用した。関連度と未校正confidenceを保存し、紹介文から原文や翻訳の文体を推定していない。AI内部審査は独立した人手goldではない。

最初のruntimeは固定CLI版を`0.162.1`と渡したため、実測の`codex-cli 0.162.1`との厳密照合でモデル起動前に拒否された。失敗記録を残し、正しい完全な文字列で新環境を固定した。この失敗にモデル呼出はなく、旧runtime・入力・台帳を書き換えた再試行ではない。

新しいauthority DBを明示指定し、原作品QID・現行source head・raw SHA・小説型・版や合本との競合を検査した。旧主DBには同じwork QIDがなく、既存表示のID・rawに記載された題名や別題との重複もないことを確認し、5行を追加。既存2,026行の特徴・得点は不変である。

| 項目 | 前回 | 今回 |
|---|---:|---:|
| 表示候補 | 2,026 | 2,031 |
| 特徴あり | 1,028 | 1,033 |
| LLM分類の特徴あり | 844 | 849 |
| 資料記載ジャンルの特徴あり | 184 | 184 |
| 得点可能 | 216 | 216 |
| 履歴の内容接続 | 2/335 | 2/335 |

私有プロフィールは許可済みの2行をローカル推薦処理で使用し、モデル・Git・画面へ出していない。今回の5作品は嗜好との対応根拠がなく、未採点・読書状態不明のまま表示した。未採点を「好みに合わない」、不明を「未読」と扱わない。

表示・商用・再配布の各用途で読戻しを確認した。撤回台帳で新規結果の利用を拒否し、公開3ファイルを前回とbytes一致で復元。再適用も今回の3ファイルとbytes一致した。Chromeでは全5作品を個別検索し、表示1件→撤回0件→復元1件、16特徴、SE著者・出典・CC0の帰属を確認した。ブラウザーにはDB・raw・私有資料を含まない公開ファイルだけをloopbackの開発用HTTPで表示した。[画面確認記録](../data/autonomous-next-v3/evaluation/phase12-completion-v1/browser-observation.json)

## 4. 実測と拡張判断

分類はinput 28,508＋output 4,000、審査はinput 28,470＋output 421、合計**61,399 token・2呼出**。分類約59.82秒、DB採用約4.61秒、画面生成・撤回確認約19.18秒。審査や調査・開発・Git・CIを含む代表的な全工程時間ではなく、開発agent・会話の総利用量も含まない。無料残枠は不明で、API料金への換算や10万作品の納期外挿は行わない。

今回の保存量は失敗した準備と2つのruntimeを含め76,559,815 bytes。完了集計自身の出力は除く。既存DBを全コピー・全hashしていない。原物と旧入力を保持するための小さな新DBと固定環境は分離している。

今回の入力成立率は5/10、モデル特徴採用は5/5。ただし全件が英語の古典ミステリーで、世界横断の代表標本ではない。旧36作品の採用23/36（約64%）と合算して70%達成にはしない。旧主DB2,005候補・小説/LN候補上限1,353は不変。新規5小説は追加したが、候補の上限合算1,358を独立検証済みの異なる小説数とは主張しない。

**PoC全体は未完了、1万→3万→10万はNO GOを維持する。** 独立gold0、precision/recall/F1・校正未測定、履歴接続2/335、代表的な処理量・無料枠不足が残る。履歴が変わっていないため同じ推薦比較の再実行も0。保留5件は追加根拠なしに再取得・再分類せず、世界横断の対象構成と評価に有効な新しい根拠がある場合だけ次へ進む。

全体原票は[集計結果](../data/autonomous-next-v3/evaluation/phase12-completion-v1/result-report.json)、固定条件は[分類前の仕様](../data/autonomous-next-v3/evaluation/phase12-completion-v1/requirements-before-classification.json)と[名称出典の補足条件](../data/autonomous-next-v3/evaluation/phase12-completion-v1/linked-label-addendum-before-fetch.json)を参照する。最終コード`30bf83e`の[Windows・Ubuntu CI](https://github.com/ayutaz/recommend-book/actions/runs/39000544589)は成功した。Windowsは1,847 passed・6 skipped（229.66秒）、Ubuntuは1,846 passed・7 skipped（44.41秒）。後続変更はMarkdownだけでコード差分0を確認する。旧raw・DB・台帳・runtime、rootの`config/sources.json`と`next_database.py`は変更していない。
