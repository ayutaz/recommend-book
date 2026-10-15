# 推薦PoC v2の誤差分析

2026-10-13時点の固定5foldでは、personalはpopularityに届いていない。shared-feature版はnDCG@10=0.547200、Precision@10=0.52、popularityは0.633978、0.56だった。特徴欠損への対処やnativeテーマ追加だけで改善できるという証拠はない。以下は既存成果物の集計と実装確認による分析で、重み・分割・実データは変更していない。

## 対象と確認範囲

読み取ったのはローカルの`data/recommendation-poc-v2/`にある`source-domain-evaluation.json`、`native-theme-evaluation.json`、`profile.json`、`manifest.json`と、`data/evaluation/recommendation-poc-v2-v1/independent-audit.json`。出力したのはscalar・件数・集計だけで、作品名、個人の作品ID、著者名、個別評価一覧はログにもこの文書にも記載していない。

両評価は335件、5fold、seed=20261013、k=10。各foldはtrain268件、held67件で、全方法のeligible poolが同一、除外は0件。shared/nativeのsplit membershipと各foldのeligible poolも完全一致した。input/splitのdigestはfeature入力の違いを含むため異なるが、実際の分割が異なったという意味ではない。

independent auditは335件の実評価を使い、Precision/nDCGを別計算して一致したことと、source/output/implementation SHAの照合を記録している。これは計算・入力整合性の確認であり、対象分野の推薦品質やbridgeの意味的正しさを検証したものではない。

## 現在の性能

各値は5foldのmacro平均。positiveはrating>=4、nDCG gainは`2**rating-1`、MAPはeligible positive総数を分母とするtruncated AP@10の平均である。全指標が全5foldで定義可能だった。詳細な分母とunknownの扱いは[評価仕様](recommendation-evaluation.md)に従う。

| feature版・方法 | nDCG@10 | Precision@10 | Recall@10 | MAP@10 | score support |
| --- | ---: | ---: | ---: | ---: | ---: |
| shared personal | 0.547200 | 0.52 | 0.190348 | 0.109963 | 328/335（97.91%） |
| native personal | 0.495653 | 0.44 | 0.159164 | 0.090408 | 323/335（96.42%） |
| popularity（両版同一） | 0.633978 | 0.56 | 0.197288 | 0.151004 | 331/335（98.81%） |
| seeded random（両版同一） | 0.538393 | 0.42 | 0.150825 | 0.106011 | 全候補に順序あり |

shared personal−popularityはnDCG−0.086778、Precision−4ポイント、MAP−0.041040。nativeはそれぞれ−0.138325、−12ポイント、−0.060596。sharedはrandomに対してnDCG+0.008808、Precision+10ポイントだったが、固定分割・1つのrandom seedの観測差であり、有意差や改善証明とはしない。nativeはrandomよりnDCG/MAPが低い一方、Precisionは2ポイント高く、単一指標だけで全性能を要約できない。

| fold | held positive件数 | shared personal nDCG | popularity nDCG | native personal nDCG | shared personal−popularity |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 | 30 | 0.528620 | 0.674207 | 0.527205 | −0.145587 |
| 1 | 23 | 0.621932 | 0.621923 | 0.598890 | +0.000009 |
| 2 | 29 | 0.557077 | 0.600670 | 0.409361 | −0.043593 |
| 3 | 26 | 0.528812 | 0.469803 | 0.546378 | +0.059008 |
| 4 | 32 | 0.499560 | 0.803285 | 0.396430 | −0.303726 |

fold1はnDCGがほぼ同じで、fold3ではpersonalが高かった。fold4の差が大きいが、このfoldを見て重みやseedを選び直さない。fold別の分布や正解件数の違いを示す探索的観測として保持する。

## 特徴欠損と識別力

確認できた事実は次のとおり。

- 335件のうちsource annotationあり331件、missing4件。shared featureなし7件、native themeなし12件。
- shared profileは実際に出現した6次元（genre4、theme2）、native profileは14次元。sharedのfeature出現件数合計は433、nativeは1085。出現件数は作品数ではなく、1作品に複数featureがあり得る。
- sharedのfeature別supportは3～292件で、2次元が20件未満。nativeは21～211件。profileのfeature別平均ratingは観測上の関連であり、独立な嗜好効果ではない。
- personalのtop10は両版とも全foldで10/10 supported、sharedのtop10 evidence_countも全50候補で非0だった。

**欠損だけでは最悪foldの差を説明できない。** sharedのfold4はheld67件すべてにscore supportがあり、feature欠損0件でもpopularityとの差が最も大きかった。欠損した高評価作品が下位へ残る影響はあり得るが、その寄与はこの集計だけからは分からない。

sharedではscoreの同点が多かった。丸め12桁で数えても、厳密なfloat値で数えてもscore distinct件数は同じだった。

| fold | shared supported件数 | distinct score件数 | 順位10位と同scoreの候補数 | native distinct score件数 |
| --- | ---: | ---: | ---: | ---: |
| 0 | 64 | 9 | 36 | 59 |
| 1 | 66 | 9 | 48 | 59 |
| 2 | 65 | 13 | 31 | 63 |
| 3 | 66 | 6 | 41 | 64 |
| 4 | 67 | 8 | 45 | 65 |

engineは同score候補をwork_idで決定的に並べ、類似度が同じ参照もwork_idで整列して最大20近傍を使う。この順序は再現性のためのもので、好みの情報ではない。sharedのtop-kが大きな同点群に依存していることは観測できる。粗いfeature表現・同類似度近傍の選択が順位の識別力を制限している可能性はあるが、今回の性能差の原因だと確定したわけではない。

nativeは順位10位の同score群が各foldで1件、distinct scoreも59～65件に増えている。それでも平均性能が低いので、同点解消や次元数の増加だけでは良い推薦を保証しない。featureの意味・強度・評価との関係、近傍選択、選択された作品集合の偏りを別々に検証する必要がある。

## bridge未対応とprovenance

現bridgeは`narou-world-feature-bridge-v1`、statusは`partial_manual_mapping_unvalidated_cross_domain`。native theme14種類のうち、growthはcentral/secondary、revengeとromanceはcentralだけをworld featureへ対応させる。他11種類と、後者2種類の条件外は対応させない。ジャンルの宣言metadataも別途使う。

profileのnative theme出現1085回に対し、reference auditの未対応theme出現は971回（89.49%）、未対応が観測された種類は13。これは971作品という意味ではない。現在のbridgeを通ったprofileに残る情報が限られることは確認できるが、未対応themeを全て似たworld labelへ写せば正しいという根拠にはならない。意味が異なるラベルの強引な統合は避け、別版の対応規則としてレビューすべきである。

native diagnosticでは全335候補のevidence_countが0、323候補はscoreを持つ。これは`pipeline.py`がnative比較用rowのfeature_provenanceを空にする実装による。331件の元annotationが存在しないという意味ではない。nativeのscore coverageと根拠参照coverageを分けて読み、診断表示でprovenanceを引き継ぐ改善を先に検討できる。provenanceを整えるだけで順位品質が上がるとは主張しない。

world候補2020件のうちfeatureあり962件（47.62%）、personal score supported673件（33.32%）。featureなし1058件に加え、featureがあってもsupportedにならない候補が289件ある。個別理由の追加集計はこの分析では行っていないため、bridge非共有、feature強度、参照との類似性等の原因候補を分離して調べる必要がある。

world候補と評価済み参照のoverlapは0件で、world領域のrating真値はない。673件をscoreできたことや100件を表示できたことは、world領域で推薦が正しかったことを示さない。manifestもtarget-domain qualityをunverified、classification human qualityをpendingと記録している。

## 分割・観測の制約

335件の評価平均は3.6806。rating>=4は140件（41.79%）、rating3は173件（51.64%）、rating<=2は22件（6.57%）。評価済み作品だけという選択と、低評価が少ないことはデータの性質である。未評価作品を低評価として補わず、人気との因果関係や一般読者の嗜好へ外挿しない。

記録されたauthor authority IDとseries IDは全335件で欠損。331件に正規化表示名のblocking keyがあり、連結groupは283。記録済みname key・external alias等のtrain/held overlapは0だが、表示名が同一著者だという人手裁定も、未知seriesの漏洩0の確認もない。groupを維持するための保守的な設計であり、無作為work分割と同じ問題設定ではない。

元read_statusは335件すべてunknown。ratingがあるためランキング候補としては除外される実履歴を、CVではratingを隠してunknown状態にするシミュレーションである。未読確認済み作品に対する実推薦成功とは区別する。

first rating日時は335件で記録されているが、last rating日時は320件で欠損。加えてsource metadataとLLM featureは現在のsnapshotで、過去時点のfeatureを再現できない。今回を「日時が全てない」または「時間順評価済み」と記述しない。seeded group CVはtemporal/prospective評価ではない。

## 原因仮説と開発側の優先順

以下の原因仮説は未検証である。現在の固定5foldに合わせた重み調整やfold4だけを改善する変更は行わない。

| 優先 | 開発側で試すこと | 根拠となる観測・未検証仮説 | 独立評価へ持ち込む条件 |
| --- | --- | --- | --- |
| 1 | provenanceと欠損理由の診断を整備 | nativeはscoreありでも参照0。worldではfeatureあり・unsupportedが289件。表示/監査の欠損と品質を混同しやすい | 順位を変えない診断として構造テストを行い、欠損をラベルabsentへ置換しない |
| 2 | bridgeとfeature意味をレビュー | native出現の89.49%が未対応。shared6次元は同点群が大きい。ただしnative追加の性能は低い | ratingを見ない資料レビューで対応・不対応・unknownを決め、新mapping versionと根拠を凍結する |
| 3 | 近傍と同点の頑健性を開発側で検討 | score distinct6～13、rank10同点31～48。最大20近傍の同類似度参照選択が影響する仮説 | 別の開発用group分割だけで方式を比較し、近傍数・tie処理・重み等を選択後に固定する |
| 4 | popularityとの比較と必要なら統合方式を設計 | popularityが平均で高い。現在の評価集合の選択や人気と好みの関連が作用する仮説 | 単独baselineを保持し、重み選択は開発側限定。現在の5foldへの事後適合を成功と呼ばない |
| 5 | identity・時間・対象分野の評価材料を増やす | authority/series欠損、歴史的feature不足、world rating真値0 | 明示的に得られた記録と許可された評価だけを使い、未読/unknownや未入力feedbackを推測で埋めない |

featureごとの平均ratingだけで重みを増減する方法は、共起・著者・ジャンル・作品選択を分離できない。既存の5foldを見た後の試行は開発判断として記録し、その同じ結果を独立検証として再利用しない。

## 独立評価の条件

- 現v2の入力・split・baseline・実装hashと今回の未達結果を保存する。seedを探して良い値だけを報告しない。
- 開発用groupと最終評価用groupを分離する。著者/series/name/external identityの連結は全rowと未評価bridgeを含める。未知identityの限界も保持する。
- bridge・feature重み・近傍設定・popularity統合・k・主指標を開発側で選び、最終評価前に固定する。資料からfeatureを作る段階はratingを見ない。
- 最終評価でpersonal/popularity/randomの候補集合を共通に保ち、unsupported・provenance・未知feedback・ゼロ分母を従来通り報告する。
- 主指標の方法差とcoverageを併記する。将来の再標本化をするならauthor/series等のgroup単位で不確実性を評価し、5foldを独立5試行として有意差を主張しない。
- world領域は別の実評価が必要。明示的に提供された好み・読書状態の確認や、未読候補への前向き判断を別記録として扱う。自動feedback収集や擬似ratingを追加しない。
- temporal評価を行う場合は、推薦・評価時点以前に得られていたfeature snapshotと必要な日時を確保する。現在featureで過去の評価を再現した結果とは分ける。

現在の結果を保持しながら、診断と意味整合性を改善する開発を先に行い、推薦品質の向上は独立条件を満たした評価で確認する。

## 読取り成果物のSHA256

| ローカル成果物 | SHA256 |
| --- | --- |
| source-domain-evaluation.json | 6af27193a6c1c04ae2b127a28d510eac2a22089e2fdfe908501da46f9ba33cdd |
| native-theme-evaluation.json | abde234832bc2663a9ae3c7d6ecb9f162b55dc66bdbce0b8d12a2052ab426964 |
| profile.json | f8014558edca069d819850dbf7059d68316f9ace1b969ff535c284341a46ac3a |
| manifest.json | 542e159c29bade92cda67be368014b4bf67dd29af5f75b7b7671eff09dbb21fa |
| independent-audit.json | c3892e5fff164cce0459c759971b1ba62fb4dbccfe54d4172d98fd12f5bca6f6 |
