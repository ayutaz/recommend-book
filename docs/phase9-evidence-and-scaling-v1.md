# phase9：新規根拠と拡張判定

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-16。新しい無料資料から既存1作品の編集説明を追加し、**実分類・内部審査・別補助DBへの採用と用途別読戻しを完了した。1万作品への拡張は停止を維持する。** 無料・Codexのみ、外注・有料API・外部連絡・本人への追加作業は0。旧DB・raw・runtime・台帳を変更せず、同じ根拠の無変更再試行をしていない。

今回の範囲と結果を見る前の処理条件は[要件・事前固定仕様](../data/autonomous-next-v3/evaluation/phase9-completion-v1/requirements-and-preregistration.json)に保存した。直近の3項目は、新しい資料の用途・作品対応確認、適合した小バッチの実処理、実測による拡張判定である。1万件の実行は含まない。

## 新資料で確認できた範囲

| 経路 | 確認対象と実結果 | 今回できること・保留 |
|---|---|---|
| Standard Ebooksの分類参照 | 限定catalog48行、選択5候補。保存ページ8件、選択workページのHTTP404が2件。適合する独立genre正例0 | 分類精度・precision/recall/F1・校正の追加評価0。今回の限定経路を終了する |
| Standard Ebooksの新説明 | 編集説明2件のうちwork対応成立1件、版・翻訳対応不足1件 | *The Age of Innocence* の新しい編集説明だけを小バッチ候補とする |
| 原連載メタデータの用途 | 前回未確認の公式制定告知1件。既読条件ページの再取得0、連載API要求0 | 私的推薦・外部AI推論に適合する新しい肯定根拠は得られず、内容取得・採用を保留する |

[Standard Ebooks報告](../data/autonomous-next-v3/evaluation/phase9-standardebooks-reference-v1/report.json)のCC0確認はSEが制作したwebsite・編集metadataに限定する。第三者のebook本文・表紙・source toolの権利へ広げない。独自編集の説明文は別出典の根拠にはなるが、事実の独立正解や専門家goldではない。`dc:subject` のLCSHはGutenberg/LOC等からの輸入由来を区別し、独立注釈と見なさない。

Age of InnocenceとFather Goriotのnative genreはFictionのみ。Fiction、著名作家、作品の評価から `genre.literary` を作らない。Twilight SleepのHTTP200はplaceholder、Carmilla単体と選択したForged CouponのURLはHTTP404だった。コレクションのgenreを収録作品へ転用しない。今回の48行・5候補の結果はSE全catalogや世界中の無料参照の不存在を示さない。LOCへの代替取得や別候補の探索もこの分岐では起動していない。

[説明の作品対応監査](../data/autonomous-next-v3/evaluation/phase9-standardebooks-reference-v1/description-identity-audit.json)では以下を分離した。

- *The Age of Innocence*：`work_142cd3fd4bc8e309b492b209`、Wikidata `Q1045464`、Open Library `OL98491W`。SEのwork sameAsと保存済みWikidataのenwiki、P648、現行外部IDが対応し、原題・著者・1920年が補強する。新説明は保存済み根拠と同一bytesではない。これは作品全体の編集梗概であり、原文・翻訳の文体の根拠にはしない。
- *Father Goriot*：保存済み `Q102046537` は1885年の版classで、enwiki・P629・P648の橋渡しがない。SE側は原作品とEllen Marriage英訳を扱う。タイトル・著者の一致から同一作品へ統合せず、今回の分類入力から除外する。

既存作品への説明補充は、新しい異なる小説数の増加ではない。説明の非同一性と編集方針の確認も、全世界での文章の独創性や剽窃不存在の証明にはしない。

[原連載条件の報告](../data/autonomous-next-v3/evaluation/phase9-original-serial-terms-v1/report.json)は、未読だった2026年8月20日の公式制定告知だけを追加確認した。運営対応の猶予や個別質問に原則回答しない旨は用途許可ではない。既存登録・目的適合、保存、外部LLMへの推論送信の条件は未解決で、新しい用途receipt・WorkInput・履歴内容接続・推薦特徴は0。私有履歴の外送や事業者への問い合わせは行っていない。この限定結果を他の無料経路の不可能性へ一般化しない。

## 事前固定した実処理の結果

新しいSE説明を得たAge of Innocenceの最大1作品のみ。`provenance-v4`、Codex Sol、分類1call・v2内部審査1callまで、無変更再試行0、観測input+output上限100,000tokenとした。旧予測は新しいpromptへ渡さず、結果を見て対象追加・閾値変更・再分類をしていない。

成功した準備版は `phase9-standardebooks-pilot-v3`、固定runtimeは `phase9-standardebooks-runtime-v1`。準備時の旧失敗v1と出力先封じ込め確認の失敗v2も保持し、それらではモデルを呼び出していない。実分類runは `6c773b871b8f4949b4f04907600b2815` である。

1作品の成果は[別の補助DBのmanifest](../data/autonomous-next-v3/database/phase9-standardebooks-v1/manifest.json)へ保存した。[既存loaderの用途別読戻し](../data/autonomous-next-v3/evaluation/phase9-completion-v1/purpose-readback.json)はdisplay・commercial・redistributionの各用途で1行4特徴を返し、旧表示の更新なしを確認した。これは用途条件付きloaderの結果であり、本番公開の実行ではない。

既存の表示スナップショット2,025候補・特徴1,027件・得点可能216件は履歴として保持する。新しい説明の採用を異なる小説+1、特徴あり候補+1へ自動換算しない。1件のための新しいoverlay/UI基盤は追加していない。

| 項目 | 現在の判定 |
|---|---|
| 適合作品候補 | 1：既存作品への新しいwork-level説明 |
| 新しい独立genre正例 | 0 |
| 実分類 | 1call、完了1・失敗0・未処理0・再試行0、37.854秒 |
| v2内部審査 | 1call、1作品審査、case/hold/uncertainは0 |
| 根拠/分類の採用 | 1/1作品・4特徴を別補助DBへ採用。既存表示の件数は据置き |
| モデル利用量 | 分類input23,630/output1,523、審査input22,356/output146、計47,655token |
| 保存・読戻し時間 | 採用1.237秒、用途別読戻し4.942秒。収集から表示までの総所要時間ではない |
| 新しい履歴内容接続 | 0 |
| 本人335評点の比較再実行 | 接続増加条件が成立していないため実行しない |

presentは `genre.romance`、`genre.social`、`theme.family`、`protagonist.adult`。structure・style・atmosphereはunknownを維持した。[内部審査](../data/autonomous-next-v3/evaluation/phase9-standardebooks-review-v1/quality-adoption-review.json)と[採用観測](../data/autonomous-next-v3/database/phase9-standardebooks-v1/adoption-observation.json)を保持し、原confidenceを確率校正済みの値として扱わず、重みへ転用していない。

今回の採用1/1は事前選定した1作品の経路確認であり、独立正解に対する精度・全SE作品の採用率・本人満足度ではない。採用・用途読戻しだけの時間に収集・準備・審査時間を含めたとは主張しない。

[最終集計原票](../data/autonomous-next-v3/evaluation/phase9-completion-v1/result-report.json)では、分類coordinator開始から最終用途読戻しまで184.720秒を観測した。調整・debug待ちを含み、前段の資料調査・開発は含まないため、代表的な全工程の採用単価にはしない。

新しい資料確認・失敗準備2版・成功準備・runtime・審査・補助DBの8ディレクトリは計27,779,991bytes。このうち固定runtimeが25,474,071bytesを占める。参照した旧raw、実行ファイル、全体backup、今回のcompletion報告は対象外である。1件だけのruntime固定費を含む容量を1万件へ比例拡大せず、逆に除外範囲を総容量0として扱わない。47,655tokenも分類・審査の観測量で、開発agent・会話・無料残枠の消費全体を表すものではない。

## 1万→3万→10万の判定

**昇格しない。** [直前の36作品](phase8-quality-evaluation-v1.md)は23採用・13不確実、23/36＝63.89%で運用目標70%未達。今回の1作品は新しい入力根拠の小検証であり、旧コホートと合算して既存の停止条件を覆さない。

[拡張準備の基準](scaling-readiness-v1.md)にある母集団・分布は保存済みphase6測定を継承し、今回DBを再走査していない。2,005候補のうち小説1,273・LN80、種別適合の上限1,353で、1万まで最低8,647不足する。国・地域の欠損43.75%、原言語2.73%、年27.57%も当時の小説・LN候補の値であり、SEの1説明追加で解消したとはしない。

再判定には、新規作品ごとの用途と原作品/表現/版の対応、採用率70%以上・出力検証保留5%以下、評価可能範囲に適合した独立正負正解、全工程の時間・token・raw/派生/backup容量、無料枠の確認を必要とする。独立正解がない範囲の通常開発は継続できるが、品質合格と主張しない。無料残枠は不明のままで、モデル工程だけの速度や過去58件runを納期へ単純外挿しない。

1万の観測結果から3万、3万の観測結果から10万を再判定する順序は維持する。今回の限定調査が見つけた実行可能な1件を報告しつつ、未解決の参照・履歴・母集団・運用条件を残す。大量分類、本番公開、有料経路への切替は行わない。

## Gitの検証状態

実装は[PR #11](https://github.com/ayutaz/recommend-book/pull/11)に集約した。実装head `8ce559c` の[PR CI](https://github.com/ayutaz/recommend-book/actions/runs/38777725136)はUbuntu 1,741 passed・4 skipped（47.08秒）、Windows 1,742 passed・3 skipped（385.89秒）で成功した。両OSのskipは旧取得済みscheduler依存2件と未配布のローカルOPF1件、UbuntuはWindows専用lock1件を追加する。ローカルOPFは本節の実処理で別途確認しており、CIで実データを取得していない。

CI後は結果を記録するMarkdownだけを更新し、コード差分0を照合して同じ全件試験を繰り返さない。重複push run `38777710217` は取消済みで、成功した検証へ数えない。用途付き補助DBの読出しとfixture CIの成功を、分類精度や旧推薦画面への反映とは区別する。最終Git状態と要件別の確認はローカルの `phase9-completion-v1/completion-audit.json` に記録する。
