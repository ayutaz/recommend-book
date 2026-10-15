# 公開評点による一般書籍推薦評価の実現性 v1

調査日: **2026-10-15（JST）**。状態: **設計・用途条件調査完了、実データ取得と実評価は未実行**。

現在の方針は追加費用0・外部委託なし。既存データ、用途条件を満たす無料公開資料、Codexのみを使い、有料データ購入・有料API・外部への許諾照会を代替経路にしない。本人の追加評価作業も依頼しない。

本人335評点の内容接続が2件である現状とは独立に、公開評点を持つ複数読者について内容推薦が基準方式を上回るか調べる。本人の書名・ID・評点・用途台帳をこの経路へ読み込まない。公開データで良い結果が出ても、本人向け改善や日本語小説での有効性の証明にはしない。

## 結論

今回の上限3候補の調査では、**商用開発を含む本プロジェクトの取得・機械学習評価・再配布までを明示的に許容すると確認できたデータセットは0件**だった。公開URLと研究利用実績だけで取得許可にしない。直ちに評点行を取得せず、以下の比較仕様を先に保持する。

技術的な第一候補は著者・作品・シリーズ情報が揃うUCSD Book Graphだが、配布者が学術用途限定、商用利用・再配布不可を明示している。本プロジェクトを学術研究と自動認定しない。Amazon Reviews 2023は推薦評価用データを公式提供している一方、確認した公式ページ・dataset cardにデータ本体の用途許諾を見つけられなかった。Book-Crossingは原配布ページへ到達できず、許諾を確認できない。これは3候補に限った結果であり、世界に利用可能なデータが存在しないという主張ではない。

## 3候補の用途判定

「条件付き」は配布者の説明上の条件であり、第三者の権利まで確認済みという意味ではない。「不明」は禁止確定とも許可確定とも扱わない。

| 候補 | 公式配布物からの取得 | ML・推薦評価 | 商用開発・商用研究 | 評点・派生行の再配布 | 今回の判断 |
|---|---|---|---|---|---|
| UCSD Book Graph / Goodreads | 学術用途の条件付き提供 | 学術用途の条件付き。今回の用途が該当するとの確認なし | 配布者が不可と明記 | 配布者が不可と明記 | 技術候補、現在の取得はheld |
| Amazon Reviews 2023 / Books | 配布リンクあり。明示許諾は未確認 | 公式にbenchmark用途を説明。ただしデータ許諾は未確認 | 不明 | 不明 | 用途証拠不足でheld |
| Book-Crossing原データ | 原配布ページ到達不可 | 原文の条件を確認できず不明 | 不明 | 不明 | 取得・採用保留 |

### UCSD Book Graph

配布者公式ページは、2017年末に公開shelfを収集しuser/review IDを匿名化したこと、2019年5月にinteractionの重複修正を行ったこと、書誌・interaction・reviewの3群を持つことを説明する。用途について学術用途限定、再配布・商用利用不可を明示している。小さなgenre subsetにもこの条件がかかると解釈し、少量なら例外とはしない。[配布者公式説明・用途条件](https://sites.google.com/eng.ucsd.edu/ucsdbookgraph/home)（確認2026-10-15）。

同ページは新しい[配布者ページ](https://mengtingwan.github.io/data/goodreads.html)への移転を告知しているが、今回のWeb取得では新ページ本文を抽出できなかった。旧ページの禁止条件が解消されたとは判断しない。実取得前には新旧版の整合、配布先、ファイル名、revision、用途条件を再確認する。

Goodreads自身の利用規約は、2021-04-28改訂と表示され、サービスの個人・非商用利用を許す一方、通常の許諾から書誌・説明・review等の収集、data mining、商用利用等を除外する。利用者の投稿に関するGoodreadsへのライセンスは、今回の利用者全員への再許諾ではない。[Goodreads Terms of Use §§1–4](https://www.goodreads.com/about/terms)（確認2026-10-15）。配布者の研究用途説明から元サービスへの新規scraping許可、第三者本文・画像・説明の権利まで推定しない。

評価設計に必要なuser/book対応と評点・読書状態の区別は[公式Shelves仕様](https://sites.google.com/eng.ucsd.edu/ucsdbookgraph/shelves)を起点に確認する。本文review、shelf名、読者属性を内容特徴として投入しない。genre名から小説であることを断定せず、fiction判定の由来を別に持つ。

### Amazon Reviews 2023 / Books

公式説明はBooksカテゴリー約29.5M評点、約10.3M利用者、約4.4M商品と、1996年から2023年9月までのinteraction、時刻・rating・user ID・ASIN/parent ASIN等を示す。商品IDは作品IDではなく、Booksには小説以外も含まれる。[公式プロジェクト・仕様](https://amazon-reviews-2023.github.io/)（確認2026-10-15）。

公式[Hugging Face dataset card原文](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/raw/main/README.md)にも、今回確認した範囲でデータ本体のlicense宣言・商用利用許諾を見つけられなかった（確認2026-10-15）。これは許諾不存在の法的断定ではなく、採用に必要な肯定的証拠の不足である。

公式がリンクする[処理コードのrepository](https://github.com/hyp1231/AmazonReviews2023)はスクリプト・BLaIR実装等を対象とし、[MIT LICENSE](https://raw.githubusercontent.com/hyp1231/AmazonReviews2023/main/LICENSE)はsoftwareと付随文書について記載する（確認2026-10-15）。このMITからAmazon利用者のreview、商品説明、画像、評点集合の利用権を推定しない。Amazon.comのConditions of Use取得は503で失敗し、本調査では原サービス規約による用途許可も立証していない。別のAmazon配布コーパスや第三者ミラーのlicenseを2023版へ流用しない。

### Book-Crossing

原配布元として知られる[大学内の配布URL](https://www2.informatik.uni-freiburg.de/~cziegler/BX/)にHTTP/HTTPSで到達を試みたが、今回のWeb取得ではtimeout等で原文を確認できなかった（2026-10-15）。研究利用条件やnative scaleに関する二次配布元の説明は、今回の許可根拠として採用しない。件数・評点scale・0の意味も原仕様を確認するまで確定値として採用しない。

データ所有側の[現行FAQ](https://www.bookcrossing.com/faqs)は物理本の追跡・共有サービスを説明するが、過去の評点データセットのML利用許諾ではない（確認2026-10-15）。原配布元の条件を確認できるまで、ミラーのCC表示、コードのlicense、Web閲覧可能性から用途を昇格させない。

## 既存APIとの関係

現行の[ローカル評価仕様](recommendation-evaluation.md)と `src/recommend_book/recommendation/evaluation.py` は、単一利用者のwork rowを受ける `evaluate_recommendations(rows, folds=5, seed=20261013, k=10)` を提供する。著者・シリーズ・author-name blocking・外部IDの連結成分、held評点の遮断、未評価bridge、同一候補集合、nDCG/Precision/Recall/AP、coverage報告を再利用できる。入力ratingは有限の1～5またはnullで、重複work_idを拒否する。複数読者のinteractionを一つのrow配列へ混ぜるAPIではない。

現在の比較は `personal`・`popularity`・`random` であり、fixed順と下記の複数利用者集計は未実装。`engine.py` の `personal` を内容特徴とtrain評点による方式として固定候補にする。`content` methodは同じ方式の別名ではなく、現在の実装は正例参照に対する類似性を扱うため、勝つ方を後から選ばない。private用の `next_evaluation.py`、`load_owned_ratings`、`scripts/evaluate_next_recommendations.py` を公開データのloaderとして流用しない。[本人用経路と制限](next-recommendation-v3.md)。本調査でAPIやコードは変更していない。

## 固定比較プロトコル案

以下は権利と利用可能列が確認された後に実装する**未実行の仕様案**であり、実行済みpreregistrationではない。初回順位計算前に入力snapshot・特徴recipe・コードcommit・spec JSONを固定し、結果を見た設定探索を0とする。本人335の成績や語彙から方式を調整しない。

### 入力、同定、評点

| 保存列 | 契約 |
|---|---|
| `dataset_id / revision / row_locator / source_sha256` | 原配布物の版、原行位置、取得物digest。license文書のURL・確認日・digestもmanifestに保存 |
| `user_key` | dataset内だけで有効な匿名token。外部アカウントや本人335と照合しない |
| `item_id / item_scope / work_id / external_ids` | native商品・版とcanonical workを区別。未解決はunresolved、タイトル類似だけで統合しない |
| `rating_raw / rating_scale / rating / rated_explicitly` | native値を保持し、公式仕様で有効な明示評点だけ正規化。未入力・implicit・sentinelをnullへ。未知を0へ変換しない |
| `timestamp / read_status` | 時刻欠損はnull。ratingがあるだけでconfirmed unread/readを新規認定しない |
| `author_ids / series_ids / author_group_keys` | 出典つきID、保守的な名前blocking、欠損率。unknown tokenを共通identityにしない |
| `features / feature_provenance / usage_decision` | ratingやreviewと独立した用途審査済み内容特徴、原資料、scope、完全入力hash |

native 1～5なら無変換を第一仕様とする。他scaleを採用する場合は、そのnative仕様を確認したうえで `1 + 4*(r-min)/(max-min)` の写像、raw正例閾値、丸めなしを別versionに事前固定する。たとえばnativeの0がimplicitなら写像対象から除く。異なるscaleを同じnDCG母集団へ混ぜない。

同一user/workの複数版評点は、版のwork同定が確認できたものだけまとめ、時刻があれば最新、同時刻/時刻欠損は原行locatorの辞書順最小という固定規則で1観測にする。競合数と除外数を保存する。ISBN/ASINだけをwork同一性の証拠にせず、未解決項目と短編集・シリーズ全体は別scopeに保持する。

### 分割と候補集合

1. userを `sha256(dataset_revision + user_key + seed)` 順で80%のbackground cohortと20%のevaluation cohortへ固定分離する。評価利用者の評点は、他利用者の人気基準・方式選定・分類入力にも使わない。少量pilotは母集団を代表する標本と主張しない。
2. 認定済みworkの著者・シリーズ・版aliasの連結成分を作り、seed=20261013で5-foldへ固定する。分割を全利用者で共有し、各利用者のprofileにはそのfold以外の明示評点だけを渡す。未評価項目もidentity graphのbridgeとして含める。rated group不足時はfoldを割らず、未評価理由を残す。
3. 評価利用者は正規化済み明示評点30件以上、独立group 10以上を事前条件とする。fold単位ではtrain 20件以上・held 10件以上を必要とする。条件はrating値の大小で選ばず、0positiveの利用者/foldも除外しない。達しない利用者・foldの件数と理由を分母つきで示す。pilotで条件不足でも都合よく緩和しない。
4. 利用者/foldの候補 `C(u,f)` はheld側の**その利用者による明示評点ありの全作品**。全方式で同じID集合・同じdigestを使い、候補のratingをnull、read_statusをunknownへ置換してrankerへ渡す。評価専用の真値は別領域へ置く。未評価作品を負例として追加するnegative samplingはしない。
5. author/series train-held overlapは対象利用者のprofileと候補の間で0を要求し、authority/name blocking/series欠損を別々に報告する。未記録identityの漏洩0は主張しない。background cohortにはheld作品への観測が存在してよく、それを人気lookupにだけ使う。これは**未知著者の全体cold-start評価ではない**。分類器・内容特徴をbackgroundのreview/評点で学習しない。全体item cold-startを行う場合は別仕様が必要。

### 比較4方式

| 方式 | 固定した順位規則と利用情報 |
|---|---|
| 内容推薦 | 現行 `personal`、neighbors=20、prior=0.1、train利用者評点と用途許可済み内容特徴。hybrid・再学習・重み探索なし |
| popularity | background cohortの明示評点を付けた異なるuser数の降順。対象利用者やevaluation cohortのheld真値、データ全体のrating_count/average_ratingを使わない。取得snapshot以前の観測人気であり、時間予測ではない |
| fixed | canonical work_idの辞書順。score supportとは区別して「固定順」と表示 |
| random | sorted候補IDを `sha256(seed,user_key,fold,purpose)` 由来の独立seedで1回だけshuffle。良いseedの選択をしない |

同点はwork_id順。内容特徴や人気が欠損した候補はscore=nullのまま決定的な末尾tieとして残し、方式ごとに候補を削らない。人気が観測されなかったことと、人気が0と観測されたことを区別する。内容特徴の支持率・根拠なし率、top-k支持率、identity欠損、同定成功率を全対象を分母に併記する。入力に利用者由来shelf、review、average_rating等が混入した場合は失敗とする。

### 指標、集計、解釈

k=10、effective_k=min(10,候補数)。nDCGのgainは `2**rating-1`、正例は正規化1～5のrating>=4、Precisionはeffective_k、Recall/APは全候補positive数を分母にする。positive0ならRecall/AP=null、Precision=0、graded nDCGは定義可能。候補0は指標null。nullを0へ補完しない。

各利用者で定義可能なfoldを平均し、次に利用者を等重みで平均する。5foldを独立5試行として扱わない。方法差は同じ利用者のpaired差と利用者単位bootstrap（固定seed、2,000反復）95%区間を示すが、pilotの小標本や作品group共有による依存の制限を明記する。nDCG@10を主指標、Precision/Recall/MAPを副指標とし、全4方式・分母・coverageを公開可能な集約で報告する。主方式のnDCG差がpopularityに対し正で区間下限も正、かつfixed/randomを上回った場合にのみ、この標本の支持とする。満たさなくても全結果を保持し、再調整した場合は探索結果へ格下げする。

これは既観測評点を隠す横断的評価であり、未読候補への推薦、読まなかった本への拒否、将来の満足、世界全体・日本語作品への一般化を測らない。新しい特徴snapshotから過去時点の情報を再構築できない。評点は嗜好feedbackであり、内容分類の人手gold、confidence calibration、実際に推薦本を読んだ後の品質承認ではない。

## 次に取得するものと停止条件

**現在は適合データ未確定なので、評点pilotの取得対象は0件**。次の権利確認はデータ本体ではなく、候補の現行利用条件・配布version・data cardの確認に限定する。外部への問い合わせ、追加の本人入力、分類API、外部LLM投入は本作業では行わない。

技術上の第一候補を使える用途が明示的に確認された場合だけ、UCSD公式の移転先から `goodreads_interactions_mystery_thriller_crime.json.gz` と対応する `goodreads_books_mystery_thriller_crime.json.gz` の**所在・版・サイズ・利用条件を先に確認**する。著者・work・seriesの全体archiveは自動取得しない。元の非商用・再配布禁止が残れば商用開発の取得へ進めない。商用許諾が得られるという見込みは本調査では確認していない。

権利が解決した後の初回は評価成績を測る本番ではなくschema/identity pilotとし、転送量20MiB・保存interaction最大5,000行・書誌最大200項目・処理10分を上限に、配布物revisionと転送SHAを記録して停止する。gzipは展開後件数だけで通信量を制限できないため、明示byte上限付きstreamと途中切断の記録を要する。先頭断片の偏り、利用者履歴の不完全性を明記し、そこから品質指標を出さない。必要列はuser/book ID・rating・read flag/時刻、同定用最小書誌とし、review本文、画像、年齢・locationは保存しない。著者/シリーズがこの上限で揃わなければ欠損を報告して止め、無断で大規模archive取得に切り替えない。

その次の評価pilotは完全履歴を持つ少人数の取得方式・転送上限を別途固定し、上の参加条件を満たす件数を確認した後に開始する。小さなschema pilotの成功を実データの推薦改善と混同しない。許可条件の確認、同定、十分な観測、内容特徴の用途審査のいずれかが不足すれば、結果は `not_run` と不足理由を保存し、合成fixtureでのmechanics確認だけを別に報告する。

## 今回保存した証拠の範囲

公式Web説明・条件とローカル評価実装を読み、比較仕様を本文へ保存した。評点ファイル、private入力、review本文のdownload・分類実行・新たな実評価・API変更・公開・外部連絡は行っていない。Web文書の固定snapshot/digestはまだ作成していないため、実取得時には再確認が必要。資料を見つけられなかった候補を許可済みへ変更していない。
