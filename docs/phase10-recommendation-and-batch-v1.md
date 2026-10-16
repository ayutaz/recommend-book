# 紹介文の推薦反映と限定バッチの結果

2026-10-16。計画1〜4のうち、採用済み特徴の推薦反映、新資料の限定処理、実測、条件付きの拡張判定を実施した。**表示2,026候補・特徴あり1,028件・得点可能216件。1万作品への大量処理は見送る。** 無料・外注なし・追加本人作業0を維持した。独立精度・校正・本人向け改善は未検証である。

## 計画1〜4と実際の到達点

| 工程 | 実施したこと | 結果 |
|---|---|---|
| 1. 採用済み特徴の反映 | phase9のAge of Innocenceを原作品IDで既存候補へ接続し、その1行だけ再採点 | 恋愛小説に社会小説・成人の主人公・家族を追加。他2,024行は不変。嗜好との共通特徴がなく未採点のまま |
| 2. 新しい紹介文の補充 | 最大20候補の上限内で5候補を事前固定し、各OPFを1回取得。適合した1作品だけを分類・審査・採用 | Dorian Grayに2ラベルを採用し、未表示だった主DBの候補を1件表示へ追加。4候補は保留。追加探索・モデル再試行0 |
| 3. 改善・処理量の測定 | 特徴・表示の差分、用途別読出し、撤回・再適用、ブラウザー、時間・利用量・容量を確認 | 追加特徴の割当5件、表示候補+1、特徴あり候補+1、得点可能数は変化0。独立した精度や満足度の改善とは扱わない |
| 4. 拡張再判定 | 採用・資料成立率、独立評価、対象構成、実測、無料残枠を照合 | 1万→3万→10万へ昇格しない。今回の1件を旧36作品の採用率へ合算しない |

範囲とモデル実行上限は[事前固定仕様](../data/autonomous-next-v3/evaluation/phase10-completion-v1/requirements-and-preregistration.json)、実績は[集計原票](../data/autonomous-next-v3/evaluation/phase10-completion-v1/result-report.json)に保存した。

## 取得と保留

公式catalogの2要求（最初の並べ替え指定では適合候補なし、公式フォームに沿った指定で48行）から、主DBの公開書誌と対応する5候補を固定した。予測ラベル・私有履歴を選定に用いていない。固定後に5つのOPFを各1回取得し、すべてHTTP 200。件数を満たすための探索を継続していない。

| 候補 | 最終判定 |
|---|---|
| Jane Eyre | 主題・副題・完全題の明示的な区別は処理可能にしたが、紹介文の引用表現で保留 |
| Moby Dick | 引用表現に加え、SEと保存済みWikidataのsameAs表記に差があるため保留 |
| Pride and Prejudice | sameAsの表記が保存済みauthorityと一致せず保留。大文字小文字の違いだけで同一視しない |
| Sense and Sensibility | 紹介文の引用表現で保留 |
| The Picture of Dorian Gray | 原作品の対応、引用なしの新紹介文、用途条件が成立。分類・審査・採用 |

主題・副題が明示される複数題と、唯一の著者の`aut`に`wpr`・`ann`・`dto`が併記される形式を扱えるよう修正した。複数の主題、主題の指定不足、曖昧な型・役割は拒否する。作品対応、引用、ライセンスの条件を緩めていない。原物・最初の保留記録は[取得記録](../data/autonomous-next-v3/evaluation/phase10-standardebooks-candidates-v1/candidate-results.json)、修正後の判定は[再照合記録](../data/autonomous-next-v3/evaluation/phase10-source-reassessment-v1/candidate-results.json)として別保存した。再照合時の追加通信0。

5作品のnative genreはすべて`Fiction`のみで、現分類の独立正解0件。`literary`へ読み替えず、外部由来LCSHも独立注釈や分類入力へ転用していない。公式CC0証拠はphase9の現在有効な保存原物を再利用し、本文・表紙・第三者の引用へ適用範囲を広げていない。

## モデルと採用

| 項目 | 実測 |
|---|---:|
| 成立した新紹介文 / 固定候補 | 1 / 5（20%） |
| 初期の分類対象 | 1作品（最大10作品の上限内） |
| 分類CLI / 審査CLI / 再試行 | 1 / 1 / 0 |
| 分類完了 / 失敗 / 未処理 | 1 / 0 / 0 |
| 内部審査のheld / uncertain | 0 / 0 |
| 分類・審査のinput / output | 45,578 / 960 token |
| 分類・審査の合計 | 46,538 token |
| 分類時間 / DB採用時間 | 31.872秒 / 2.454秒 |
| Ageの表示更新 / Dorianの追加表示 | 6.946秒 / 15.452秒 |

採用したDorianのラベルは`theme.identity`・`atmosphere.humorous`。genre・structure・protagonist・styleはunknownを維持する。紹介文から原文や翻訳の文体を推定していない。原confidenceは未校正で、推薦特徴は採用ラベルの有無を使い、confidence・relevanceを好みに合う確率や重みへ変換しない。

初期バッチは1/1採用、出力検証保留0で運用条件を満たしたが、残りの適合入力は0件。追加の分類・審査を起動していない。1件の採用率をSE全体の採用率や分類精度としない。上のtokenは開発agent・会話を含む総利用量やAPI料金ではない。

## 表示・撤回・再適用

主DBの候補IDと、現在の表示候補IDは同じ集合ではない。最初の更新専用処理はDorianが表示にいないことを検出し、出力前に拒否した。主DBの作品ID・Wikidata ID・現行source head・原物SHA、小説適格性と既存表示との重複を確認してから、明示的な追加表示経路を使った。既存作品へ誤って上書きしていない。モデルを再実行せず、保存済みの実出力を使った。

最終表示は[phase10-world-delivery-final-v1](../data/autonomous-next-v3/database/phase10-world-delivery-final-v1/index.html)。主DB2,005候補は再生成せず不変で、Dorianも主DBにあった作品である。表示+1を世界小説DBの異なる作品+1とは数えない。phase8の2,025行と比べ、Ageの1行を更新、Dorianの1行を追加、他2,024行の値とスコアを保持した。

新しい撤回台帳で実際のloader拒否を確認した。Ageの撤回はphase8の公開3ファイルへ、Dorianの撤回はAge反映後の公開3ファイルへbytes一致で戻る。再適用も適用後の3ファイルとbytes一致。旧DB・旧台帳・固定runtimeは保持し、原文・引用・ローカルパス・個別履歴を公開HTMLへ出していない。

Chromeで[Ageの4分類→撤回後の恋愛小説のみ](../data/autonomous-next-v3/evaluation/phase10-completion-v1/age-ui-observation.json)、[Dorianの検索1件→撤回後0件](../data/autonomous-next-v3/evaluation/phase10-completion-v1/final-ui-observation.json)、未採点・読書状態不明・理由不足・SE/CC0の出典リンクを確認した。嗜好との対応がない2作品に推薦理由や得点を作っていない。

## 評価と拡張判断

資料に適合する独立正負正解がなく、precision/recall/F1・confidence校正は未評価。履歴の内容接続も2/335のままで、新しい推薦比較0。本人の試読・採点・正解作成は依頼していない。内部AI審査を独立した人手正解へ置き換えない。

ゴール開始から最終ブラウザー観測まで863.643秒を記録した。資料取得・開発・試験・モデル・調整を含み、残るCI/Git作業は含まない。新規phase10の中間表示を含む保存先は61,334,235bytesで、旧raw・主DB・実行ファイル・全体backup・completion報告類を除く。1作品の開発費用や固定runtime容量を1万件へ比例外挿しない。

**1万→3万→10万の大量処理はNO GO。** 既存36作品は23採用・約64%で目標70%未達。今回の固定1件とは標本が異なり、合算して停止条件を取り消さない。限定取得の成立率1/5、独立評価不足、既存測定の小説・LN候補上限1,353と分布欠損、代表的な全工程処理量、無料残枠未確認が残る。今回の成立分は取り込んだが、新しい根拠なしの再分類や残り候補の補充探索は続けない。

## Gitと試験

[PR #12](https://github.com/ayutaz/recommend-book/pull/12)に実装を集約した。関連試験は紹介文取込み・用途検査72件、更新・追加表示・撤回など14件で成功。最終実装head `d12228f` の[CI](https://github.com/ayutaz/recommend-book/actions/runs/38829155501)は両OSで成功した。

| OS / Python 3.14 | 結果 | pytestの時間 |
|---|---|---:|
| Ubuntu | 1,762 passed・7 skipped | 44.66秒 |
| Windows | 1,763 passed・6 skipped | 245.02秒 |

両OSのskipは旧取得済みscheduler依存2件と非配布のローカルOPF4件。UbuntuはWindows専用lock1件を追加する。実OPFの取込み・モデル処理・表示・撤回は上記のローカル記録で確認した。旧head `f808641` の中止CIと重複pushの取消CIは成功扱いしない。以後の変更は結果を記録するMarkdownだけとし、テスト済みコードとの差分を確認して統合する。CI成功は独立した分類精度や本人向け改善の証明ではない。
