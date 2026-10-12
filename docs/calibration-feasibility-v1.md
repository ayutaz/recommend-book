# 固定balanced400のcalibration実行可能性監査

2026-10-12。**固定calibration100では、現行コードのどのscopeも校正モデルを作れない。** 追加人手判定をすべてknownにできたとしても、必要な40件に届かない構造的な制約である。固定400、閾値、split、元データ、ラベルを変更していない。モデル結果・人手annotationは参照せず、追加Web/LLM取得・annotation生成・候補partition割当は0。

## コードで決まる単位

- [`metrics._scope`](../src/recommend_book/evaluation/metrics.py)は`label_id | original_language | evidence_kind`。原言語はexact値であり、sampling用の`other`へ統合しない。モデルの出力言語を原言語として使わない。
- [`review.import_annotations`](../src/recommend_book/evaluation/review.py)は原言語をpack作品metadataからコピーする。evidence_kindは可視資料の**全kind集合をsortして`+`結合**する。`bibliographic+description`と`bibliographic+description+subject`は別scopeであり、両方を単なるbibliographicとしてまとめない。truth_level=evidenceではLLM入力許可資料、workでは全提供資料が対象。
- human/evidence/calibrationとhuman/work/calibrationは別にfitする。同じ作品の2つのtruth_levelや複数reviewerを足して40件にしない。reviewerの判定は裁定を経て1つの評価行とする。72ラベルを評価しても、同じラベルのscopeに72行を加える意味にはならない。
- `_fit_calibrator`はhuman truth・predictionがknown、数値confidenceがある行のみを数え、scopeが40件未満、language/kindがunknown、またはモデル・prompt・taxonomy・CLI・target_languageのcontextが混在するscopeを除く。
- binは`[0,0.5)`と`[0.5,1.0000001)`、最低20件。**コードは両bin20件を必須とせず、20件以上のbinが1つでもあれば、そのbinだけを登録する。** scope40件は依然として必須である。40件という件数だけで、両binの校正やconfidence全域の校正を保証しない。

## 固定packの実測上限

dev150 / calibration100 / holdout150を維持。calibrationのcoarse配分はja35 / en35 / other30である。otherを原言語ごとに分けるとar4/de1/es2/fr3/it2/ko8/pt3/ru2/zh3/unknown2。したがって、原言語だけで上限は35、さらに根拠kind集合で分かれる。

evidence/workの両truth_levelで、calibrationの言語×kind集合は30cell、known languageは28cell。**実測最大はenの`bibliographic+description`16件**。jaの最大は`bibliographic+description+subject`11件。全72ラベルに対して各cellのn上限は同じで、全scopeが40未満となる。人手unknown、モデルunknown、confidence欠損はこの上限をさらに下げる。現在の30cellのmetadata signatureは両truth_levelで同じだが、truth判定やfitを混ぜてよい意味ではない。

- pack内宣言SHA: `8a888b5b1e84c7a996a662352253b45f63b9873caf93a6686217ac89e862373f`
- review_pack.jsonファイルSHA: `7216cfd854daa3938e95f7c7fe67b854e7113fd3300f08ca74a4e37b9b2e8ff2`
- split内manifest SHA: `7b4ebe9b0a1599caf155ca13dbcbade13c34269a316f4f768d09a6ed00f4ae6c`
- frozen入力ファイルSHA: `77793ab2c89983cb6174d58ce35d4d353934ac4cb73bd073aafdcae4a3c337fe`

packのworks部分だけをJSONとして読み、predictionsフィールド以降はdecodeしていない。ファイル全体のバイトSHAと宣言pack SHAは記録したが、モデル結果をdecodeする必要があるpackのcanonical payload SHA再計算はしていない。worksは別のselected入力と完全一致、splitのmanifest SHA、全入力＋identityのdigest、frozen入力SHA、group unionは再計算して照合した。

## 固定400を維持する追加calibrationの設計可能性

凍結2000作品は1294 connected groups。固定400は260groupを丸ごと使い、dev/holdoutで190groupを使う。未選択**1600作品・1034groupは全400groupと非重複**である。現frozenのauthor/series authority tokensと名前fallbackのunionを再計算して検証した。未確認authorityや入力の名寄せ誤りまで独立性を保証するものではない。

以下は作品metadataだけの楽観的な件数下限であり、同じラベルで人手truthと予測がknownになること、同一context、bin占有を仮定している。候補を実際に選択・割当・注釈していない。

| scopeの言語×kind集合（各ラベル別） | 固定cal上限 | 40へ追加する同cell下限 | 未選択同cell pool | groupを丸ごと追加する最小総作品数 |
| --- | ---: | ---: | ---: | ---: |
| en / bibliographic+description | 16 | 24 | 269 | 24 |
| en / bibliographic+description+subject | 8 | 32 | 143 | 32 |
| ja / bibliographic+description+subject | 11 | 29 | 162 | 29 |
| ja / bibliographic+description | 8 | 32 | 171 | 32 |
| ja / bibliographic+description+synopsis | 4 | 36 | 63 | 42 |
| ja / bibliographic+description+note | 2 | 38 | 39 | 74 |
| ko / bibliographic+description | 7 | 33 | 37 | 34 |

例えばen/basicとja/basic+subjectを既存calibrationへ補助するなら、数量上は最低24+29=**53作品**の追加で両scopeを40へ増やせる。最小総作品数が同cell不足数より大きい行は、同じgroupに別kindや言語の作品が含まれ、groupを割らず追加するための余分な作品も数えたもの。groupを分ける案ではない。

既存100と合算せず、新しい独立calibrationだけで40を作る設計なら、en/basicとja/basicはそれぞれwhole-group最小40作品で、両方の数量条件に**80作品**が必要。今回のpoolで独立40件を満たせる言語×kind集合はen4cell/ja4cellの計8cell。現在calibrationにあるknown28cellのうち、既存100と補助を合算して40へ届くcellは10、残る18は同cell poolをすべて使っても不足する。少数言語や細かいkind集合の不足を、otherへの統合や閾値引下げで埋めていない。

必要な全scopeを一括でカバーする最小追加数はここでは確定しない。scopeごとのgroup集合は重なることがあり、個別の最小数を単純に足せない。40ちょうどの設計にはunknown脱落への余裕もない。原文を必要とする文体などは、書誌metadataの件数が多くても判定可能性を保証できない。confidenceのbin分布をモデル結果で調べていないため、必要な上乗せ数も未確定である。

実行する場合は、元400を保持して別の追加calibration packを固定し、group非重複・source/input/context・対象scopeを別途検証する必要がある。今回の成果は候補poolと件数上限の設計資料であり、人手作業開始や新partition確定を意味しない。

## 独立成果物

- [summary.json](../data/evaluation/poc-frozen-v2/calibration-feasibility-v1/summary.json)：構造的不可の結論とコード条件。
- [scope-upper-bounds.json](../data/evaluation/poc-frozen-v2/calibration-feasibility-v1/scope-upper-bounds.json)：固定calibrationの全cell上限と追加不足数。
- [pool-scope-upper-bounds.json](../data/evaluation/poc-frozen-v2/calibration-feasibility-v1/pool-scope-upper-bounds.json)：未選択poolの全cell、独立40件可能性とholdoutに同scopeがある件数。
- [group-pool.jsonl](../data/evaluation/poc-frozen-v2/calibration-feasibility-v1/group-pool.jsonl)：凍結groupの候補metadata。新partitionは全行null。
- [manifest.json](../data/evaluation/poc-frozen-v2/calibration-feasibility-v1/manifest.json)：入力・コード・成果物SHAと整合検証。
- [prepare.py](../data/evaluation/poc-frozen-v2/calibration-feasibility-v1/prepare.py)：オフライン再現コード。完成snapshotを上書きしない。
