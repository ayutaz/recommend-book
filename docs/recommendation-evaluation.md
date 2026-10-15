# 推薦のローカル・オフライン評価

`src/recommend_book/recommendation/evaluation.py`は、既存の評価済み作品をgroup単位で保留し、推薦engineのpersonal、観測popularity、独立したseeded randomを同じ候補集合で比較する。APIは`evaluate_recommendations(rows, *, folds=5, seed=20261013, k=10) -> dict`。ファイルの読取り・書込み、通信、LLM呼出しは行わず、呼出し側から渡されたrowだけで計算する。

これは**grouped seeded cross-validation**であり、時間順の評価でも、将来の未読作品に対する前向き評価でもない。現在のfeature snapshotからは過去の評価時点の特徴量を再構築できない。設定は固定された探索的設定であり、独立した品質証明やclassificationの人手goldを作成しない。

## 入力と分割

rowはwork_id/title、非負の有限feature値、rating（nullまたは1～5）、author_ids、series_ids、read_status、external_ids、feature_provenance、popularityを持つ。任意の`author_group_keys`も分割に使用する。入力をdeep copyして正規化し、呼出し元を変更しない。bool・非有限値・不正なrating範囲・重複work_idを拒否する。

以下の記録済みidentityで連結成分を作り、その成分全体を同じfoldに割り当てる。

- author authority IDs。
- stable series IDs。
- adapterから渡された保守的なauthor-name blocking keys。
- exact `(source, value)` external identity。同一作品の別canonical IDによるtrain/holdout漏洩を防ぐ。

評価未入力のrowもgroup graphに含める。例えば評価未入力のrowがauthorとseriesをつないでいた場合、その連結も保つ。IDが欠損したrow同士を空文字や共通unknown tokenでまとめない。どのidentityも共有していない作品はsingletonである。

author-name keyの一致は保守的なblockingであり、人手で確認した同一著者identityではない。例えば提供元にauthority IDが保存されていない場合、adapterが正規化表示名のkeyを供給できる。実際の個人IDや履歴値はこの文書に掲載しない。

splitではrating値の大小を使わず、rated件数をseeded group順で概ね均等化する。requested folds以上のrated groupがなければinformative ValueErrorを返す。全作品が1groupであった場合などに、groupを割ったりfold数を静かに減らしたりしない。

`split`に全rowのgroupと各foldのtrain/held IDs、rated subset、held group IDを保持する。`input_sha256`はwork_id順に正規化した全入力のcanonical JSON digest。`split_sha256`はその入力digest・seed・fold構成を含むdigestである。row順の入替えに対して結果は不変で、seed変更は別の分割として記録される。

各foldでrecorded author/series/name/external identityのtrain/held overlapを検査し、0を報告する。この0は記録されたidentityに限る。未記録seriesの実際の漏洩が0だったと断定しない。identity auditにはauthority ID記録数、name key記録数、series欠損数、authority著者とseriesの両方が欠損する数、name keyも含めてどちらも記録されない数を分けて出す。

## trainと候補の遮断

profile参照はtrainのratingありrowだけ。明示read_historyにもtrainの参照だけを渡す。保留側のratingや読書状態をprofileへ渡さない。

候補はheldのratingあり作品をcopyし、`rating=None`と`read_status='unknown'`へ置換してからengineへ渡す。これは、既に評価された作品の評価を一時的に隠すシミュレーションである。元rowが評価済みだったことや元read_statusを否定する操作ではない。元の読書状態件数と全元情報を入力digestに保持する。confirmed unreadを要求する設定は使わない。

heldのratingなし作品を負例やrating0にせず、評価候補へ加えない。未入力feedbackの好みは分からないためである。engineからpersonal/popularityのeligible poolを受け取り、同一集合であることを確認する。rankingの欠落・重複・勝手な切り詰めを拒否する。randomもその集合だけを、engineや入力順から独立したseedで並べる。各foldの候補・除外件数とeligible pool digestを保持する。

## 指標とゼロ分母

各foldは全eligible作品についてratingの真値があり、未報告feedbackを0正解にしない。kより候補が少ない場合、`effective_k=min(k, eligible_n)`を明示する。

| 指標 | 定義 |
| --- | --- |
| nDCG@k | gain=`2**rating-1`、discount=`log2(rank+1)`。同じ候補のrating降順をidealとする |
| Precision@k | rating>=4のtop-k件数 / effective_k |
| Recall@k | rating>=4のtop-k件数 / eligible全体のrating>=4件数 |
| AP@k | top-kのpositive位置でのPrecisionの和 / eligible全体のpositive件数。途中で打ち切ったAP |
| MAP@k | 定義可能なfoldのAP@k平均 |

macro平均はfoldごとの指標平均で、定義できたfold数を指標ごとに併記する。AP@kの分母は`min(positives,k)`ではなく、eligible positive総数である。

positiveが0のfoldは、既知の低ratingだけならPrecision=0、Recall/AP=null。低ratingにもgraded gainがあるので、nDCGは引き続き定義可能。eligible候補が0ならPrecision/nDCG等もnull。nullを0に置換してmacro平均を下げない。

## scoreと根拠のcoverage

engineのunsupportedはscore=nullのまま末尾の決定的tieとして候補集合に残す。指標はそのtieも含んだrankingで計算し、coverageを別に出す。unsupportedを全て除いて都合のよい候補集合だけで方法間比較しない。

各foldと方法の集約に、supported candidate数・率、unsupported数、正のfeatureがない件数、evidence_countが0の件数、supportedでもevidence_countが0の件数を保持する。top-kにもsupported coverageとno-evidence件数を出す。evidence_countは構造上のfeature provenance参照数であり、その意味的正しさを証明しない。

popularityは入力にある観測値だけを利用する。全てnullなら方法のsupported coverageは0で、未知の人気を推定しない。その場合も同じ候補集合に残るtie rankingの指標は表示するが、利用可能な人気順の性能とは解釈できない。ローカル評価済み作品集合での観測から、全世界の作品の人気分布も推論しない。

randomのsupportedは「順序を定義できる」という意味で、根拠支持ではない。randomのevidence_countは0。randomのscoreは順序を表す便宜的な値で、好みの確率や予測ratingではない。

## 実行と検証

呼出し側はadapterで得たローカルrowを渡し、返されたdictを必要な場所に保存する。実履歴の入力・CLI実行・成果物公開はこのmoduleの処理範囲に含まれない。

```python
from recommend_book.recommendation.evaluation import evaluate_recommendations

report = evaluate_recommendations(rows, folds=5, seed=20261013, k=10)
```

TDDでは初回の未実装import failureを確認してから実装した。

```powershell
uv run --offline pytest tests/test_recommendation_evaluation.py tests/test_recommendation_engine.py -q
```

合成fixtureでgraded指標の厳密値、ゼロpositive、trainだけの参照、held rating/read-status遮断、same pool、group/aliasの推移的連結、未評価bridge、missing identity singleton、permutationとseed、group不足時の失敗、pool不一致の拒否、入力数値境界を確認する。実engineでも、held ratingsだけを変えてheld rankingが変化しないこと、featureなしのunsupported、popularity全欠損のcoverage0を確認する。合成fixtureを実履歴や人手classification goldへ混ぜない。

この評価が示すのは、現在のローカル観測評価をgroup CVで再現する順位性能である。データ選択・評価済み作品だけという選択バイアス、未記録の著者/シリーズidentity、歴史的featureの欠如、設定を見ながら調整した場合の探索的性質は残る。将来の推薦候補に対する人手確認や前向き評価を置き換えない。
