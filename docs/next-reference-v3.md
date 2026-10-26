# 外部専門家参照 v3: 候補監査と評価契約

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-15現在: 以下は2026-10-13時点の準備・契約の記録。後続の[CoNSSA実評価と適用範囲](../data/autonomous-next-v3/evaluation/phase3-reference-route-v1/report.md)および[French原文5作品の正例限定評価](../data/autonomous-next-v3/evaluation/phase3-french-native-pilot-v1/report.json)は実行済みである。下表の許可0・未観測・実行前という値を現在値へ一般化しない。Frenchは5正例中4件を検出したが、正例限定でprecision・F1・校正は評価不能。現72ラベルに適用できる独立専門家正解0は維持する。最新状況は[実施報告](autonomous-next-delivery-report-v3.md)を参照する。

確認日: 2026-10-13。本人の追加作業0、新しい人手注釈0。ここでの本文候補成立は外部LLMへの送信許可・分類の実行・精度達成とは別である。既観測60作品は開発用診断であり、新しいholdoutとは呼ばない。

## 実測された本文・著者群の上限

固定CoNSSA metadata SHA `2c127dbb6f82b33548151e09a5a185a384c36981eb14e7fd1c7cfab54f035400` と269 native参照を全件監査した。原TEIは公式commit `8fccf669ed1bdbad309ff190f5afb0d6066d70a3` のtreeに対してblob SHA1・byte数・SHA256を照合した。既存原物・旧入力・旧結果を変更していない。

| 段階 | 件数 | 解釈 |
|---|---:|---|
| Native参照候補 | 269 | 専門家native metadata、72ラベルgoldではない |
| Metadata権利包絡を通る上限 | 150 | 本文・版・追加寄与の監査前 |
| v1本文scope候補 | 98 | 厳密TEI抽出ではcastList2件を保留 |
| v2本文scope候補 | 100 | 人名一覧のcastList subtreeを除外して2件成立 |
| 既観測dev | 60 / 18著者群 | 新holdoutに使わない |
| 未観測作品・観測著者群に重なるdev | 37 | 著者独立性はない |
| 未観測作品・著者独立候補 | 3 / 3群 | `ne0191`, `ne0373`, `ne0397`、guard/入力/契約固定前 |
| 新registryで外部LLM許可済み入力 | 0 | 監査時点、実行待ちを品質成功にしない |

原年齢値は100件中adult55/child4/young32/mature9。追加40件はyoung30/mature9/adult1である。youngとmatureは現在のchild/adult/elderlyへ写像できないので、追加件数を現在の4ラベルgold増加とは数えない。

実auditは `data/autonomous-next-v3/reference/conssa-audit-v2/manifest.json`、全269件の理由は同ディレクトリの `cohort-audit.jsonl`。集計SHA `45b313f62b038bf31e7eb58ed1a49a5a431a88fdd05d40b13629e039f8d69182`。入力5ファイル・実行source3ファイルのbefore/after SHAは一致した。v1原物/manifestは保持した。v2終了後に固定context recipeを追加したsource改訂は後続準備として扱い、過去のsource SHAを付け直さない。

## 旧180参照の保留120件

| 旧理由 | 件数 | 今回の結論・次に必要な証拠 |
|---|---:|---|
| 配信元restricted/本文未公開 | 75 | 全件保留。metadataのCCライセンスを本文へ流用しない。公式に許可された別版の原物・対応証拠が必要 |
| print-source刊行年未確認 | 15 | 全件保留。日付を別版・初版年から補完しない。配信に使った正確な版の一次書誌が必要 |
| 配信基底版が1930年より後 | 20 | 全件保留。近代訳・序文・校訂の追加寄与を初版PDで消さない。古い許可版を独立に取得できるまで不可 |
| 初刊年の米国期間未満了 | 7 | 全件保留。今回の地域・年の範囲を変更しない |
| 著者死亡年が保守的範囲外 | 2 | 全件保留。個別条文・国籍・復活・戦時加算証拠が必要 |
| 未審査body castList | 1 | `ne0009`のみ本文から一覧を完全除去して候補化。原TEI保持、作品positive参照は別sidecar |

旧保留120のうち119は保留のまま。269全体では169保留（restricted107、後期版33、版年不明17、初刊年9、著者3）。処理不能・根拠欠損をunknown予測や正答へ変換しない。

## 原本文・版・権利と答えの分離

[公式CoNSSA README](https://github.com/cligs/conssa/blob/8fccf669ed1bdbad309ff190f5afb0d6066d70a3/README.md)はXML/metadataのCC BY 4.0と原本文のPublic Domain Markを区別する。PDMは世界中の全権利を許諾する契約ではない。原作者・初刊年・配信基底版・追加寄与・原言語をTEI headerと固定metadataで対応し、公開freeだけで送信可としない。

今回の保守的旧包絡は2026年、日本・米国・スペインの審査範囲、著者死亡<=1940、初刊・配信基底版<=1930である。[文化庁の延長・非復活説明](https://www.bunka.go.jp/seisaku/chosakuken/hokaisei/kantaiheiyo_chosakuken/1411890.html)、[JASRACの戦時加算表](https://www.jasrac.or.jp/aboutus/wartime/pdf/wartime.pdf)、[米国Copyright Officeの外国作品復活期間](https://www.copyright.gov/circs/circ38b.pdf)、[スペイン移行規定](https://www.boe.es/buscar/act.php?id=BOE-A-1996-8930)、[旧スペイン法](https://www.boe.es/buscar/doc.php?id=BOE-A-1879-40001)を一次資料として記録する。戦時加算の最大4180日より長い12年を旧包絡で使い、公開年が死亡年後の場合も保守的に扱う。これは個別の正確な満了日確定ではない。処理地域がこの3国だけとは断言せず、その他provider処理地域を未確認条件として保持する。

`research-rights/manifest.json` に確認日時・原応答bytes SHAを記録した。JP/戦時/US/CC BY/PDMの5原物は保存済み。BOEの2ページはwebで確認したがローカル直接取得はURLErrorで原物未保存。未保存を保存済みと扱わない。個別source verifier/UsageGuardが新registryで材料を通過するまではapproved0とする。

[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.en)の作者・source・license・変更の表示をderived inputのattributionへ保持する。本文の新訳・序文・脚注・校訂追加を除外する。bodyだけからheader/metadata/native annotations/XML属性/章見出し/editorial matterを除去し、`choice`はoriginal/sicだけを採用する。`castList`は[TEI P5公式定義](https://www.tei-c.org/release/doc/tei-p5-doc/en/html/ref-castList.html)の人物一覧であり、`ne0009`の2subtree・`ne0084`の1subtreeを完全除外、後続本文tailを保持する。他の未知tagは引き続き拒否する。filter原物・原SHA・derivedSHAは `castlist-review-v1` と各source_auditに保存する。Native回答・専門家書誌・abstract・年齢頻度・参照ラベルをLLM入力へ入れない。

作品positive参照は作品単位、入力は正確な配信expressionとその抜粋単位である。翻訳・異版・校訂版へ参照を無条件転用しない。文体軸は今回未評価で、原文と明示された訳のscopeを分ける。

## Native定義と72ラベルの対応

固定[codebook](https://raw.githubusercontent.com/cligs/conssa/8fccf669ed1bdbad309ff190f5afb0d6066d70a3/keywords/keywords.xml) SHA `2413a5d27d6a1b95e905a961487de9fd63a013af62ea0b6793d1bfbc468c66cc`、[著者研究書](https://www.transcript-publishing.com/media/pdf/90/5b/45/oa9783839459256.pdf) pp138–145を原物保存した。研究書SHA `779e7e9aae4bd3eeaf1853997bf07d7880466c008ac87e597ab2db85fae45f6a`。詳細は `research-codebook/semantic-mapping.md`。

| Native | 測れる範囲 | 現taxonomyとの関係 |
|---|---|---|
| 最重要人物1人 | 明確な場合のsingle protagonist、曖昧ならunknown | narratorと別。複数中心人物のgoldではない |
| 年齢child/adult | source作品positiveとの限定検出 | current child/adultと互換positive、他人物のabsenceは作らない |
| 年齢young/mature | 専用native4ordinal enum | numeric境界無し。child/adult/elderlyへの変換不可 |
| 職業student/artist | native exact文字列positive | current student/artistへの狭い対応のみ、artist内部粒度未検証 |
| その他職業 | 専用native free-string exact | 本は30分類と6例を記述するが全30一覧は未公開。軍人→soldier等の上位概念写像不可 |
| narrator参加/提示様式 | 今回は専用予測対象外 | homodiegetic/autodiegetic≠first_person、heterodiegetic≠third_person |
| gender mixed/人物一覧 | 曖昧な選択人物群のmetadata | multiple-protagonistへの変換不可 |
| certainty high/medium/low | 著者全文読了/abstract/その他の由来 | confidence・関連度・校正targetではない |

年齢は通常行動開始時を記録し、少なくとも1完全章で描かれるより若い時期がある場合はその時期を用い、短い幼少回想は除外する。数値境界は捏造しない。抜粋だけでは章・全作の人物選択を確定できない場合があり、unknownの理由に残す。書籍にある30職業一覧をコーパス頻度から推測して閉じたenumにしない。古いnative20実験のopen職業・年齢定義を後から修正した実験として再bindしない。

## 低検出の診断と最大2候補

既観測native20は年齢0/20、職業exact2/20。現在の4ラベルは60入力で24/65 positive検出、held5を成功分母から除いていない。これらは本文全体のaccuracyではない。考えられる説明は最重要人物と語り手の混同、native1人scopeとcurrent複数中心人物scopeの違い、年齢4ordinal定義未提示、職業表記粒度、章/行動期が抜粋外にあること。原因寄与は未実証なので低値から自動確定しない。

候補familyはA `definitions-v3` とB `context-v3` の2つだけ。world72任務とnative任務は別schema・別metricとして実行する。Aは定義説明の修正、旧devの3×6000char inputは変更しない。BはA定義とラベル非依存の広い本文を使う。B recipeはbody72k以下なら全文、それを超える場合はwidth12000の6窓、start=`i*(body_length-12000)//5` (`i=0..5`)、nonoverlap、最大72k。Aはbody18k以下全文、超える場合はstart0/中央/末尾各6000。評価前に同じwork order・recipe・原body SHA・exact Unicode range・原TEI SHA・expression・filter SHA・evidence SHAを固定する。回答を見て窓や同義語を選び直さない。

Native v3 required fieldsはidentity/contextと `protagonist`, `age`, `profession`。年齢known enumはchild/young/adult/mature。reference ?/other/unknownは欠損として別表示。職業knownはfree string exact比較のみ、正規化・同義語統合無し。known age/profにはknown protagonistが必要。各knownに原input内exact quoteとevidence locator、unknownにnullと理由を要求する。引用の機械一致は人物同定やsemantic supportの人手確認を意味しない。Native ageとprofessionの分母は別に保持する。

正例のみの比較はpositive検出・input coverage・unknown coverageを出す。常時present/常時unknownも同じ固定分母で併記する。held/unknown/omittedは検出失敗のまま。未注釈をnegativeにしないのでprecision/F1/closed-world accuracy/calibrationは算出しない。現在の4ラベル検出をnative精度へ読み替えない。既存180/200positive、今回100native入力候補、未観測3群は分母を分離し、未測定のgenre/theme/structure/style/atmosphereを六軸検証済みとしない。

## 別言語・別軸の追加資料

英語調査の原物/date/SHAは `research-english/source-manifest.json`。人手由来metadataと本文の許諾を分離する。

| 一次資料 | 検証事実 | 現taxonomy用途の限界 |
|---|---|---|
| [REMAN](https://www.ims.uni-stuttgart.de/en/research/resources/corpora/reman/), [論文](https://aclanthology.org/C18-1114/) | 3大学院注釈者と専門家レビュー、1720 sentence triples、187 PG URL/125著者 | 感情は文・span単位。作品atmosphere goldへ変換不可。注釈CC BY4とPG本文条件は別 |
| [LitBank](https://github.com/dbamman/litbank), [論文](https://aclanthology.org/N19-1220/) | 100小説/約2000語、entity/event/coref/quote speaker | 主人公年齢・職業・genreのgoldはない。本文の原版権利を別確認 |
| [NovelTM metadata](https://github.com/tedunderwood/noveltmmeta) | 3文学史研究者manual title資料、novel1952、初刊<=1930が216 | manual novel/nonfiction分類と、外部genres列を混同しない |
| [PDNC](https://github.com/Priya22/project-dialogism-novel-corpus) | 28小説/20著者、metadata first7/third21、初刊<=1923が24 | narrative person定義・annotator由来・exact本文版が通ればstructure専用候補。現時点未承認 |
| [CharacterRelations](https://github.com/dbamman/characterRelations) | 109作品/2170関係注釈、複数crowd注釈 | expert goldではない。SparkNotes summaryを本文許可へ流用不可 |

別agentがFrench CLiGS roman19の4native genreと原French XMLを取得・版審査した。実25作品/12著者の原物とstrict body previewを保存し、そのうち古い配信版の日付が成立した候補は5作品/4著者、残20件は版年欠損・曖昧source年・1910年版で保留と報告された。独立metadata上限を本文・UsageGuard approved入力と混同しない。crime→mystery、fantastic→fantasy、education→growth等の自動写像をせずnative genreとして測る。Underwoodの984参照は別genre laneが公式source/許可mirrorを検証する。Gutenberg websiteへの自動本文取得制限を回避しない。LTOはmetadataやWikipedia由来説明を原本文rightsに転用しない。

## Guard接続用の再現材料

### 個別著者authorityの追加審査

`author-authority-v1/revision-v2` に、全21著者群・100作品の一次author氏名/没年contextを保存した。[BnF公式API仕様](https://api.bnf.fr/fr/sparql-endpoint-de-databnffr)は専門家が検証したauthority metadataの公開を説明し、[再利用条件](https://data.bnf.fr/licence)はsourceと取得日の表示を求める。両原物SHAは `2726cab2ee9391193758a74bea3525f02ebc9b9f53d7892e0d40aa37d9a4d1bc`、`a5f34356538ae372154f94924ec28c5679130bd38b348a1c48777f9c21958108`。metadata許諾をBnF画像・Gallica本文・CoNSSA原本文へ流用しない。

元metadataの明示VIAF IDをBnF `owl:sameAs` に対して照合し、全21群で一意person URI、同一personの `foaf:name` と `bio:death` を得た。Primary literal原物106182bytes SHA `4eac6fd5134bec2ce3556d382ca63c1c830da23a2200d36f1a412c49500b2df9`、URI link原物SHA `3d57e70d883e9f4bfb1248244fa1373292b6197c58047bdef719f1211ba0fcab`。原JSON bindingsのexact byte range/JSON pointer、明示VIAF関係、氏名/没年同一person contextを保存した。名前だけの推測・結合ではない。各原TEIの作者ref/name/titleStmt byte context/原SHA、固定metadataのVIAF/BNE/没年、初版・配信版へのcrosswalkを全100作品へ保存した。

没年は21/21で元年と一致した。4群の原値は年だけであり、year precisionを明記し、月日を補完しない。初回v1のYYYY-MM-DD専用パーサがこの4群を保留した17群/94作品という履歴を保存し、新v2で年精度を正当に扱う訂正を記録した。原v1の原物やmanifestを上書きしない。

BnFの `countryAssociatedWithThePerson` はLibrary of Congressの原country code metadataで20群Spain/1群Nicaraguaと対応した。これはcitizenshipや出生地から推測した国籍へ変換していない。Rubén Darío / `ne0218` は元native `author.country=Spain` と一次関連国Nicaraguaが異なる。著者ID/没年は一致するが、このcountry差は用途レビューの明示事項として保持し、元metadataやTEIを書き換えない。

先に保存済みWikidata auxiliaryを原SHA/原entityまで検証し、明示IDとP570が一致した4群/8作品は `wd-auxiliary-author-facts.jsonl` へ別保存した。Wikidata主張の再現を独立primary biographyへ昇格していない。BNEの最初のauthority原ページは403のため同host追加取得を停止し、別機関BnFの公式公開APIを利用した。初回BnF slash route404とformat不一致406、公式仕様に合わせた訂正も保持する。直接HTTP取得は計12回、原物に氏名・死亡年・用途条件だけを使用し、新しいLLM呼出0。

`primary-author-facts.jsonl` SHA `adaef0c4c08d5f9c816c8d9d7531ae9286af8fbf484349295533a40ca30535a5`、`work-author-crosswalk.jsonl` SHA `13ab60a288a267de7fe941537c2d9c7089f431117c83e3dd5eebc1e1e422689a`、`authority-id-link-proofs.jsonl` SHA `8827f677ba11f9233d8081e3f662b411197ff04a04b2fb8982c140642f4b62f9`。これらは送信権利の承認、作品注釈gold、precisionの証明ではない。法域・版・追加寄与・本文・帰属を結び付けるUsageGuardレビューに引き渡した。処理国一覧が未公開であるだけで一律保留する追加規則は [source-rights-review-scope-v3.md](source-rights-review-scope-v3.md) では採用せず、未審査法域・処理地域は残留不確実性として保存する。

`verify_conssa_evidence(record,evidence,proof,root=...)` は署名されたmetadata/source audit/context JSONLの原byte SHA、原TEI、strict filter、body、expression、固定recipe、exact range、text、creditと変更表示を再現するpure helperである。返値はfactsとproof hashesだけで許可判定を返さない。個別著作権/審査地域/条件/外部送信可否はUsageGuard担当に残す。定義書を引用支持と混同せず、SourceRecordとWorkInputは別である。

`context-proofs-v1` は100作品×2レシピの範囲/SHA200件を保存した。source audit、原物、source code before/afterを検証し、本文やnative回答をsidecarへ複写しない。後続 `candidate-inputs-v1` はA/B各100の入力提案とsource record、evidenceごとのdescriptorを作る。titleは空、著者/年/地域/seriesは入力に入れず、原言語esとopaque identityだけを保持する。新policy `conssa-reviewed-original-v3` はconditionalであり、提案全件の `llm_input_allowed=False` とapproved0を保つ。追加author一次authorityの照合後も、元native metadataとTEIの死亡年/国/版年一致を新しい法的許可として扱わない。

旧 `scripts/run_autonomous_native_reference.py` のcurrent live入口はhistorical-onlyで即failclosedとなり、入力読取・CLI発見・出力作成・subprocessの前に拒否する。歴史の原物copy SHA `0ac04930ed095a8cbc17714408fa419368194917b9f724085f0c3ca475dd67b0` をmockでだけ検証し、原実行結果を付け直さない。新native実行はUsageGuard/full payload audit必須のv3 runnerのみで行う。

再現: `uv run pytest tests/test_next_reference.py tests/test_autonomous_native_reference_runner.py -q`。候補監査: `uv run python scripts/build_next_reference.py --output data/autonomous-next-v3/reference/conssa-audit-v2 --source-cache data/autonomous-next-v3/reference/conssa-audit-v1/raw --exclude-cast-list`。文脈証拠: `--context-proof-from data/autonomous-next-v3/reference/conssa-audit-v2 --output <new>`。未承認提案: `--candidate-inputs-from data/autonomous-next-v3/reference/conssa-audit-v2 --context-proofs data/autonomous-next-v3/reference/context-proofs-v1 --output <new>`。既存namespaceは拒否するので再実行時は新outputを指定する。新分類はsource/use guard・全入力・metric/context固定後のroot GOを待つ。
