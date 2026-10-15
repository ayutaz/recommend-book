# taxonomy v1 人手レビュー用日本語ガイド

この文書の日本語はレビューを助ける補助訳・説明であり、ラベルの意味やholdoutの判定基準を変更しない。正本は[`config/taxonomy.json`](../config/taxonomy.json)の英語原定義である。ラベルIDは変更せず、そのまま注釈に使用する。

- 固定taxonomy SHA256: `688f8e1a0921bcbafabbbf54d99b9039bce8c525ea7e7147bdb01821b449084e`
- 対象: taxonomy v1の6軸、72ラベル。
- このガイド作成ではモデル結果を参照しない。初期判定をblindで保存し、モデル結果を見た後の比較・裁定とは分ける。
- 元のtaxonomy、frozen pack、DB、分類runnerは変更しない。

## 共通の判定原則

### present・absent・unknown

`present`は、そのラベルの定義・include条件・scopeを満たす根拠が確認できる場合に付ける。`absent`は、必要な範囲を確認し、当該ラベルが成立しないと判定できる場合に付ける。`unknown`は、資料が足りない、該当箇所がない、矛盾が解消できない、対象となる版・翻訳・作品が不明など、判定できない場合に使う。

`unknown`は`absent`ではない。短い引用に描写がないことや、検索して語が見つからないことだけでは作品全体の不存在を示せない。原定義のexclude条件が確認できた場合と、確認する資料が足りない場合も区別する。

### 引用の一致と意味の支持

引用が入力本文と一致することは、その引用が実在する確認になる。その引用がラベルの意味を支持するかは別に判断する。単語が登場しても、否定、比喩、登場人物の推測、脇役だけの描写、短い例外場面であれば、定義を満たさないことがある。引用の位置・版・言語と、定義のどの条件を支持するかを確認する。

### primaryとscope

`primary`は評価の品質gate対象を示すtaxonomy上のフラグである。`true`が「作品で中心的」、`false`が「作品で周辺的」という意味ではない。正本のquality_gateは `primary labels only; requires human evaluation` としている。72ラベル中28ラベルが`primary=true`、44ラベルが`false`である。primaryではないラベルも、その定義に従ってレビューできる。

人物や物語の中心性の条件は、各ラベルのdefinition・include・excludeを読む。脇役や挿話、単発の場面が該当するだけでは、作品の主な人物・構造などを対象とする条件を満たすとは限らない。複数ラベルを付けられることと、条件を緩めて付けることは別である。

scopeは、そのラベルを何について判定するかを示す。作品全体、特定の人物、原文、明示された翻訳などの対象を取り違えない。ここでexpressionは、作品の特定の言語表現（原文や特定の翻訳など）を指す。版の情報も記録するが、版IDがあるだけで言語表現まで同定できたと扱わない。文体は確認した原文または明示された翻訳・版について判定し、訳文から原著の文体へ自動的に一般化しない。言語・翻訳者・版が不明な場合は、その不明点を記録する。

### 関連度と信頼度

関連度は、ラベルが作品のどの程度中心を占めるかを表す。信頼度は、手元の資料に基づく判定の確かさを表す。中心的に見えるが資料が少ない場合や、存在は確実でも周辺的な場合を区別する。根拠がない数値を埋めない。taxonomyの関連度anchorは `0.25=peripheral`（周辺的）、`0.5=clear element`（明確な要素）、`0.75=major`（主要）、`1=core`（核心）。これを信頼度のanchorとして使わない。taxonomy自体に信頼度の数値anchorはないため、新しいanchorをこのガイドで追加しない。

正本のその他のsemanticsは `omitted_label=unknown_unassessed`（省略したラベルは未評価のunknown）、`unknown=insufficient_or_conflicting_evidence`（根拠不足・矛盾）、`absent=requires_explicit_counterevidence_or_comprehensive_material`（明示的な反証または包括的な資料が必要）である。

同じevidence locatorに`style.minimalist`と`style.ornate`を付けた場合、正本の `same_evidence_locator` 条件に従い、`record_conflict_and_request_review`（矛盾を記録しレビューを求める）とする。異なる箇所では両者が併存し得るため、作品全体で相互排他的と扱わない。

### blind初期判定と裁定

最初の判定ではモデル出力、モデルのラベル候補、関連度、信頼度を見ない。参照資料とtaxonomyを使って判定・根拠・不明点を保存する。その後、別の工程で不一致の検討や裁定を行う。比較のために初期判定を書き換えたり、結果を見てholdoutのラベル定義を変更したりしない。

## レビュー用pack

- [400件 balanced review pack](../data/evaluation/poc-frozen-v2/400-review-balanced-v1/review.html)
- [20件 balanced pilot review pack](../data/evaluation/poc-frozen-v2/pilot20-review-balanced-v1/review.html)

リンク先は保存済みレビュー用packであり、件数はレビュー完了数や新taxonomy人手goldの確定数を表さない。初期判定は各packのblind段階を維持して記録する。

## scopeと構造の読み方

| 軸ID | 英語scope原値 | 日本語補助説明 |
| --- | --- | --- |
| `genre` | `work_or_expression` | 作品または明示されたexpressionを対象とする。 |
| `theme` | `work_or_expression` | 同上。 |
| `structure` | `work_or_expression` | 同上。plotとnarrationのsubaxisを区別する。 |
| `protagonist` | `work_or_expression` | 同上。中心人物であることは各定義で確認する。 |
| `style` | `original_or_explicit_expression` | 原文または明示されたexpressionを対象とする。翻訳・版を明記する。 |
| `atmosphere` | `work_or_expression` | 作品または明示されたexpressionを対象とする。 |

各ラベルのscopeは所属する軸のscopeをそのまま引き継ぐ。`parent_id=axis.<軸ID>`。structureの`subaxis=plot`は筋・展開、`narration`は語り方を示し、その他のラベルのsubaxisは`null`である。日本語の「物語」「本文」「資料」の語を理由に英語scopeを変えない。

正本のversionは`1.0`、status原値は`provisional_until_pilot_freeze`。このガイドは固定SHAの読み方を説明するもので、そのstatusを変更したり、holdoutの新たな改訂を認めたりするものではない。

## 72ラベルの英語原定義と日本語補助訳
各表の左列は固定JSONの原文、右列は日本語補助訳。include/excludeも原文を保持する。英語の条件を優先し、訳文を新たな境界条件として使わない。

### genre — ジャンル（12ラベル）

#### `genre.mystery` — Mystery / ミステリー
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | An investigation or unresolved puzzle drives the narrative. | 調査、または未解決の謎が物語を動かす。 |
| include | Active investigation, clues, crime-solving | 実際に行われる調査、手掛かり、犯罪の解明。 |
| exclude | Crime merely mentioned without investigative plot | 調査する筋がなく、犯罪に言及するだけ。 |

#### `genre.fantasy` — Fantasy / ファンタジー
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supernatural or magical rules shape the fictional world. | 超自然的または魔法的な規則が架空世界を形作る。 |
| include | Magic, mythical beings or impossible worlds essential to plot | 魔法、神話的な存在、現実には不可能な世界が筋に不可欠。 |
| exclude | Metaphorical magic or ordinary speculation | 比喩としての魔法や、通常の推測。 |

#### `genre.science_fiction` — Science fiction / サイエンスフィクション
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Speculative science or technology structures the story. | 仮想の科学や技術が物語を構成する。 |
| include | Space exploration, future technology or scientifically framed speculation | 宇宙探査、未来技術、科学的な枠組みでの仮説。 |
| exclude | Any futuristic date without scientific speculation | 科学的な仮説を伴わず、未来の日付があるだけ。 |

#### `genre.romance` — Romance / 恋愛小説
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The development of a love relationship is a central plot. | 恋愛関係の発展が中心的な筋となる。 |
| include | Courtship and relationship formation drive major stakes | 求愛や関係形成が、物語の主要な利害・争点を動かす。 |
| exclude | A peripheral romantic relationship | 周辺的な恋愛関係。 |

#### `genre.historical` — Historical fiction / 歴史小説
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A reconstructed past period centrally shapes the story. | 再構成された過去の時代が物語を中心的に形作る。 |
| include | Past institutions/events shape fictional characters and conflicts | 過去の制度や出来事が架空の人物や対立を形作る。 |
| exclude | Old publication date alone | 刊行年が古いだけ。 |

#### `genre.horror` — Horror / ホラー
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Fear or dread is a deliberate central effect. | 恐怖や不安を与えることが、意図された中心的効果である。 |
| include | Threatening uncanny forces or terrifying sustained experiences | 脅威となる不気味な力や、持続する恐ろしい体験。 |
| exclude | Violence without an intended fear effect | 恐怖を与える意図がない暴力。 |

#### `genre.literary` — Literary fiction / 文学的フィクション
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Foregrounds complex interiority, language or social interpretation. | 複雑な内面、言語、社会の解釈を前面に出す。 |
| include | Narrative treatment explicitly prioritizes literary exploration | 文学的な探究を明確に優先する物語の扱い。 |
| exclude | Prestige, nationality or publication imprint alone | 名声、国籍、出版レーベルだけ。 |

#### `genre.thriller` — Thriller / スリラー
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Sustained urgent suspense drives consequential danger. | 重大な危険をめぐる、切迫した持続的なサスペンスが物語を動かす。 |
| include | Escalating threat, pursuit or time-critical jeopardy | 高まる脅威、追跡、時間制約のある危機。 |
| exclude | A single dangerous incident | 危険な出来事が一度あるだけ。 |

#### `genre.adventure` — Adventure / 冒険小説
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A sequence of risky external exploits drives the plot. | 危険を伴う外的な冒険の連続が筋を動かす。 |
| include | Exploration, daring action or survival exploits | 探検、大胆な行動、生き延びるための冒険。 |
| exclude | Travel as background detail | 旅行が背景情報としてあるだけ。 |

#### `genre.social` — Social fiction / 社会小説
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Social institutions and conditions form the central conflict. | 社会制度や社会状況が中心的な対立を作る。 |
| include | Institutions, collective inequity and societal critique drive plot | 制度、集団的な不平等、社会批判が筋を動かす。 |
| exclude | Any mention of a society | 社会に言及するだけ。 |

#### `genre.satire` — Satire / 風刺
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Ridicule exposes social or institutional folly. | 嘲笑によって社会や制度の愚かさをあらわにする。 |
| include | Ironic exaggeration aimed at a recognizable target | 識別できる批判対象に向けた皮肉な誇張。 |
| exclude | Humor without a critical target | 批判対象を持たないユーモア。 |

#### `genre.dystopia` — Dystopia / ディストピア
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.genre`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | An imagined oppressive social order structures the story. | 想像上の抑圧的な社会秩序が物語を構成する。 |
| include | Systemic coercive future or alternative social order | 体系的な強制を伴う未来、または別の社会秩序。 |
| exclude | An unhappy individual in an otherwise ordinary world | それ以外は通常の世界で、個人が不幸であるだけ。 |

### theme — テーマ（12ラベル）

#### `theme.revenge` — Revenge / 復讐
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Retaliation for a wrong is a central concern. | 受けた不当な扱いへの報復が中心的な関心事である。 |
| include | Retaliatory motive examined over the story | 物語を通じて報復の動機が検討される。 |
| exclude | A fleeting angry reaction | 一時的な怒りの反応。 |

#### `theme.growth` — Growth / 成長
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Personal maturation or change is explored. | 個人の成熟や変化を探究する。 |
| include | Learning, identity development or sustained transformation | 学習、アイデンティティの発達、持続する変容。 |
| exclude | A protagonist merely ages | 主人公が単に年を取るだけ。 |

#### `theme.family` — Family / 家族
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Family relationships and obligations are examined. | 家族関係や義務を検討する。 |
| include | Kinship conflicts, loyalty, estrangement or intergenerational ties | 親族間の対立、忠誠、疎遠、世代間のつながり。 |
| exclude | Family listed only as biographical context | 家族が経歴の背景として挙げられるだけ。 |

#### `theme.loss` — Loss / 喪失
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Grief or separation and its consequences are explored. | 悲嘆や別離と、その結果を探究する。 |
| include | Bereavement, irreversible deprivation or sustained absence | 死別、取り返せない剥奪、持続する不在。 |
| exclude | A death mentioned without thematic treatment | 主題として扱わず、死に言及するだけ。 |

#### `theme.class` — Class / 階級
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Socioeconomic hierarchy and inequality are explored. | 社会経済的な階層や不平等を探究する。 |
| include | Class barriers, labor relations or wealth hierarchy | 階級間の障壁、労使関係、富の序列。 |
| exclude | Rich/poor character descriptors alone | 人物を裕福・貧しいと形容するだけ。 |

#### `theme.ethics` — Ethics / 倫理
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Moral choices and responsibilities are examined. | 道徳的な選択や責任を検討する。 |
| include | Competing duties and explicit moral dilemmas | 衝突する義務、明示的な道徳的ジレンマ。 |
| exclude | A clearly good or bad character with no moral inquiry | 道徳的な問いがなく、人物が明らかに善または悪であるだけ。 |

#### `theme.migration` — Migration / 移住
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Displacement or migration and belonging are examined. | 移住や故郷からの移動・離散と、帰属を検討する。 |
| include | Migration experience, exile and cross-border belonging | 移住の経験、亡命、国境を越えた帰属。 |
| exclude | Foreign setting or author's nationality alone | 外国が舞台であることや、作者の国籍だけ。 |

#### `theme.identity` — Identity / アイデンティティ
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The construction or uncertainty of identity is examined. | アイデンティティの構築や不確かさを検討する。 |
| include | Self-definition, conflicting identities or recognition | 自己の定義、相反するアイデンティティ、承認。 |
| exclude | Any character introduction | 人物の紹介があるだけ。 |

#### `theme.friendship` — Friendship / 友情
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Friendship and mutual trust are explored. | 友情や相互の信頼を探究する。 |
| include | Friendship development, loyalty or rupture | 友情の発展、忠誠、断絶。 |
| exclude | Companions merely share a scene | 仲間が同じ場面にいるだけ。 |

#### `theme.power` — Power / 権力
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Acquiring, maintaining or resisting power is examined. | 権力の獲得、維持、権力への抵抗を検討する。 |
| include | Authority, domination or political control as a sustained concern | 権威、支配、政治的統制が継続的な関心事となる。 |
| exclude | Any powerful character | 権力を持つ人物がいるだけ。 |

#### `theme.war` — War / 戦争
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Armed conflict and its human consequences are examined. | 武力紛争と、それが人に及ぼす結果を検討する。 |
| include | Warfare, militarization or trauma in sustained treatment | 戦争、軍事化、トラウマを持続的に扱う。 |
| exclude | A battle as a passing adventure event | 一過性の冒険上の出来事としての戦闘。 |

#### `theme.memory` — Memory / 記憶
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.theme`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Remembering, forgetting or contested recollection is examined. | 記憶すること、忘れること、争われる回想を検討する。 |
| include | Memory shapes identity, relationships or interpretations | 記憶がアイデンティティ、関係、解釈を形作る。 |
| exclude | A flashback used only for exposition | 説明のためだけに使う回想。 |

### structure — 物語構造（12ラベル）

#### `structure.investigation` — Investigation plot / 調査の筋
`primary=true` · `scope=work_or_expression` · `subaxis=plot` · `parent_id=structure.plot`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A question is pursued through discovery and inference. | 発見や推論を通じて、ある問いを追究する。 |
| include | Evidence gathering and solution-seeking organize plot | 証拠収集と解答を求める行為が筋を組み立てる。 |
| exclude | A detective role without an investigative plot | 調査の筋がなく、探偵役がいるだけ。 |

#### `structure.journey` — Journey plot / 旅の筋
`primary=true` · `scope=work_or_expression` · `subaxis=plot` · `parent_id=structure.plot`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Movement through successive places structures progression. | 複数の場所を順に移動することが展開を構成する。 |
| include | A goal-oriented quest or consequential travel arc | 目的を持つ探索、または重要な結果をもたらす旅の展開。 |
| exclude | A single commute or incidental movement | 一度の通勤や、付随的な移動。 |

#### `structure.multiple_perspectives` — Multiple perspectives / 複数の視点
`primary=true` · `scope=work_or_expression` · `subaxis=narration` · `parent_id=structure.narration`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Distinct focalizing viewpoints structure narration. | 異なる焦点化の視点が語りを構成する。 |
| include | Repeated narration from different consciousnesses | 異なる意識を通した語りが繰り返される。 |
| exclude | Many characters viewed by one focalizer | 一人の焦点化主体から多数の人物を見るだけ。 |

#### `structure.nonlinear` — Nonlinear narration / 非線形の語り
`primary=true` · `scope=work_or_expression` · `subaxis=narration` · `parent_id=structure.narration`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Narrative order departs substantially from chronology. | 語られる順序が時間順から大きく離れる。 |
| include | Recurring time shifts or rearranged chronology | 繰り返される時間の移動、または時間順の組み替え。 |
| exclude | A brief explanatory memory alone | 説明のための短い記憶だけ。 |

#### `structure.ensemble` — Ensemble plot / 群像的な筋
`primary=false` · `scope=work_or_expression` · `subaxis=plot` · `parent_id=structure.plot`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Several interdependent major character arcs structure plot. | 互いに関係する複数の主要人物の展開が筋を構成する。 |
| include | Multiple substantially developed arcs share narrative focus | 十分に発展した複数の人物の展開に、物語の焦点を分ける。 |
| exclude | Large background cast | 背景の人物が多いだけ。 |

#### `structure.frame` — Frame narration / 枠物語の語り
`primary=false` · `scope=work_or_expression` · `subaxis=narration` · `parent_id=structure.narration`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | An enclosing story introduces or contextualizes an inner story. | 外側の物語が内側の物語を導入し、または文脈を与える。 |
| include | A sustained framing situation around embedded narrative | 内包される語りを囲む、持続的な枠の状況。 |
| exclude | An isolated anecdote | 独立した逸話が一つあるだけ。 |

#### `structure.epistolary` — Epistolary narration / 書簡・記録による語り
`primary=false` · `scope=work_or_expression` · `subaxis=narration` · `parent_id=structure.narration`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Letters or documents principally deliver the narrative. | 手紙や文書が主として物語を伝える。 |
| include | Correspondence, diaries or records form the main telling | 書簡、日記、記録が主要な語りを構成する。 |
| exclude | A letter appears as a minor prop | 小道具として手紙が登場するだけ。 |

#### `structure.unreliable` — Unreliable narration / 信頼できない語り
`primary=false` · `scope=work_or_expression` · `subaxis=narration` · `parent_id=structure.narration`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Narration is demonstrably misleading or limited in a consequential way. | 語りが、重要な影響を持つ形で誤解を招く、または限界を持つことが実証される。 |
| include | Evidence contradicts or problematizes narrator account | 語り手の説明を否定し、または問題化する根拠がある。 |
| exclude | First person alone or presumed dishonesty | 一人称であるだけ、または不正直だと推測するだけ。 |

#### `structure.first_person` — First-person narration / 一人称の語り
`primary=false` · `scope=work_or_expression` · `subaxis=narration` · `parent_id=structure.narration`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A character narrates using first-person perspective. | 登場人物が一人称の視点で語る。 |
| include | Excerpt shows sustained I/we narration | 抜粋に持続的なI/weの語りが見られる。 |
| exclude | Synopsis uses I or a first-person quotation only | あらすじがIを使う、または一人称の発言が引用されるだけ。 |

#### `structure.third_person` — Third-person narration / 三人称の語り
`primary=false` · `scope=work_or_expression` · `subaxis=narration` · `parent_id=structure.narration`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Narrator principally refers to characters in third person. | 語り手が主として三人称で登場人物を指す。 |
| include | Narrative excerpt uses he/she/they or names as telling stance | 物語本文の抜粋で、語る立場としてhe/she/theyや人物名を使う。 |
| exclude | Synopsis grammar alone | あらすじの文法だけ。 |

#### `structure.cyclical` — Cyclical structure / 循環する構造
`primary=false` · `scope=work_or_expression` · `subaxis=plot` · `parent_id=structure.plot`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The ending returns consequentially to an initial situation. | 結末が、重要な意味を持って初めの状況へ戻る。 |
| include | Explicit narrative return or repetition connecting start and end | 開始と終了をつなぐ、明示的な物語上の回帰や反復。 |
| exclude | A repeated motif without structural return | 構造的な回帰を伴わないモチーフの反復。 |

#### `structure.open_ending` — Open ending / 開かれた結末
`primary=false` · `scope=work_or_expression` · `subaxis=plot` · `parent_id=structure.plot`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Major narrative questions remain deliberately unresolved at the close. | 終わりにおいて、主要な物語上の問いが意図的に未解決のままである。 |
| include | Supplied ending material leaves central outcomes unresolved | 提供された結末の資料で、中心的な成り行きが未解決のまま残る。 |
| exclude | Incomplete source material or unanswered minor detail | 資料が未完であること、または細かな未回答事項。 |

### protagonist — 主人公（12ラベル）

#### `protagonist.detective` — Detective protagonist / 探偵の主人公
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character acts as an investigator. | 中心人物が調査者として行動する。 |
| include | Professional or amateur investigator is a protagonist | 職業的またはアマチュアの調査者が主人公である。 |
| exclude | Detective appears only in a supporting role | 探偵が脇役としてだけ登場する。 |

#### `protagonist.student` — Student protagonist / 学生の主人公
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character has an explicit student role. | 中心人物が明示的に学生の役割を持つ。 |
| include | Protagonist attends an educational institution | 主人公が教育機関に通っている。 |
| exclude | Youth inferred to be a student | 若いことから学生だと推測する。 |

#### `protagonist.child` — Child protagonist / 子どもの主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character is explicitly a child. | 中心人物が明示的に子どもである。 |
| include | Specified childhood or unambiguous child age | 子ども時代との明記、または明確に子どもと分かる年齢。 |
| exclude | Short stature or youthful behavior alone | 背の低さや子どもらしい振る舞いだけ。 |

#### `protagonist.nonhuman` — Nonhuman protagonist / 人間以外の主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character is explicitly nonhuman. | 中心人物が明示的に人間以外である。 |
| include | Animal, robot or supernatural being is central focal character | 動物、ロボット、超自然的存在が中心的な焦点化対象である。 |
| exclude | A human likened metaphorically to an animal | 人間を動物に比喩でなぞらえる。 |

#### `protagonist.multiple` — Multiple protagonists / 複数の主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Several central characters share protagonist status. | 複数の中心人物が主人公の地位を共有する。 |
| include | Explicit parallel central arcs or co-protagonists | 明示的に並行する中心人物の展開、または共同の主人公。 |
| exclude | A large cast without shared centrality | 中心性を共有していない、多数の登場人物。 |

#### `protagonist.adult` — Adult protagonist / 成人の主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character is explicitly an adult. | 中心人物が明示的に成人である。 |
| include | Specified adult age or clear adult status | 成人である年齢の明記、または明確な成人の立場。 |
| exclude | Employment alone if age remains unclear | 年齢が不明なまま、仕事をしているだけ。 |

#### `protagonist.elderly` — Elderly protagonist / 高齢の主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character is explicitly in later life. | 中心人物が明示的に晩年にある。 |
| include | Explicit old age or specified elderly age | 高齢との明記、または高齢と分かる年齢の指定。 |
| exclude | A grandparent role alone | 祖父母という役割だけ。 |

#### `protagonist.outsider` — Outsider protagonist / 社会の外部に置かれた主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character occupies explicit social outsider status. | 中心人物が明示的に社会の外部に置かれた立場にある。 |
| include | Exclusion or marginal social position shapes central role | 排除や社会の周縁的な位置が、中心人物の役割を形作る。 |
| exclude | A foreign name or assumed nationality | 外国風の名前や、推測した国籍。 |

#### `protagonist.artist` — Artist protagonist / 芸術に携わる主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character practices an artistic vocation. | 中心人物が芸術的な仕事に従事する。 |
| include | Writer, musician, performer or visual artist as central role | 作家、音楽家、演者、視覚芸術家であることが中心的な役割である。 |
| exclude | Art appreciated as a casual hobby | 気軽な趣味として芸術を鑑賞する。 |

#### `protagonist.soldier` — Soldier protagonist / 軍人の主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character has an explicit military role. | 中心人物が明示的な軍務の役割を持つ。 |
| include | Active or former soldier with a central narrative role | 現役または元軍人が物語上の中心的役割を持つ。 |
| exclude | Participation in conflict as a civilian | 民間人として争いに参加する。 |

#### `protagonist.caregiver` — Caregiver protagonist / ケアを担う主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character has a sustained caregiving role. | 中心人物が継続的にケアを担う役割を持つ。 |
| include | Caregiving obligations are explicitly central | ケアの義務が明示的に中心である。 |
| exclude | A single helpful action | 一度、役に立つ行動をするだけ。 |

#### `protagonist.antihero` — Antihero protagonist / アンチヒーローの主人公
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.protagonist`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | A central character lacks conventional heroic virtues in a consequential way. | 中心人物が、重要な影響を持つ形で従来の英雄的な美徳を欠く。 |
| include | Explicit morally compromised conduct structures protagonist role | 明示的に道徳的な問題のある行為が主人公の役割を構成する。 |
| exclude | Imperfection alone or villain as a minor character | 欠点があるだけ、または悪役が脇役である。 |

### style — 文体（12ラベル）

#### `style.concise` — Concise prose / 簡潔な文章
`primary=true` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied prose uses economical phrasing. | 提供された文章が、無駄の少ない表現を使う。 |
| include | Excerpt shows compact direct clauses and little elaboration | 抜粋に短く直接的な節があり、詳しい展開が少ない。 |
| exclude | Short synopsis or brief input length | あらすじが短いこと、または入力の長さが短いこと。 |

#### `style.lyrical` — Lyrical prose / 叙情的な文章
`primary=true` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied prose emphasizes rhythm and evocative imagery. | 提供された文章が、リズムや喚起力のあるイメージを重視する。 |
| include | Excerpt contains sustained musical or poetic language | 抜粋に、持続的な音楽的・詩的な言語表現がある。 |
| exclude | Synopsis describes the work as beautiful without prose evidence | 文章そのものの根拠なく、あらすじが美しい作品だと説明する。 |

#### `style.colloquial` — Colloquial prose / 口語的な文章
`primary=true` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied narration uses conversational register. | 提供された地の文が、会話的な言葉遣いを使う。 |
| include | Excerpt shows informal idiom in the narration | 抜粋の地の文にくだけた慣用表現がある。 |
| exclude | Dialogue alone when narratorial prose is formal | 地の文は改まっており、会話部分だけが口語的である。 |

#### `style.experimental` — Experimental prose / 実験的な文章
`primary=false` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied prose departs markedly from conventional presentation. | 提供された文章が、通常の表現・提示方法から大きく離れる。 |
| include | Excerpt shows sustained syntactic, typographic or linguistic experimentation | 抜粋に持続的な構文・印刷表現・言語上の実験がある。 |
| exclude | Unusual plot alone | 筋が珍しいだけ。 |

#### `style.psychological` — Psychological focus / 心理に焦点を当てる文章
`primary=true` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied prose foregrounds detailed inner experience. | 提供された文章が、詳しい内的経験を前面に出す。 |
| include | Excerpt develops mental processes and introspection | 抜粋が心の過程や内省を展開する。 |
| exclude | Synopsis reports emotions without prose treatment | 文章としての扱いを示さず、あらすじが感情を報告する。 |

#### `style.ornate` — Ornate prose / 装飾的な文章
`primary=false` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied prose uses elaborate syntax and dense embellishment. | 提供された文章が、複雑な構文と密度の高い修飾を使う。 |
| include | Excerpt has sustained complex clauses and rich modifiers | 抜粋に持続的な複雑な節と豊富な修飾語がある。 |
| exclude | One decorative adjective | 装飾的な形容詞が一つあるだけ。 |

#### `style.minimalist` — Minimalist prose / ミニマリスト的な文章
`primary=false` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied prose withholds exposition and relies on sparse detail. | 提供された文章が説明を控え、少ない細部に依拠する。 |
| include | Excerpt shows deliberate omission and suggestive spare narration | 抜粋に意図的な省略と、示唆に富む簡素な語りがある。 |
| exclude | Conciseness alone without meaningful withholding | 意味のある説明の抑制を伴わない簡潔さだけ。 |

#### `style.dialogue_driven` — Dialogue-driven prose / 会話が主導する文章
`primary=false` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Conversation carries much of the supplied narrative passage. | 会話が、提供された物語の一節の多くを担う。 |
| include | Substantial excerpt develops events through spoken exchanges | 十分な抜粋が、話し言葉の応酬を通じて出来事を展開する。 |
| exclude | A single quoted line in a synopsis | あらすじに引用された台詞が一行あるだけ。 |

#### `style.stream_of_consciousness` — Stream of consciousness / 意識の流れ
`primary=false` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied prose follows associative thought flow. | 提供された文章が、連想的な思考の流れに従う。 |
| include | Excerpt shows unfiltered shifting thought and associations | 抜粋に、整えられないまま移り変わる思考や連想がある。 |
| exclude | Any interior monologue with orderly exposition | 順序立った説明を伴う内的独白があるだけ。 |

#### `style.descriptive` — Descriptive prose / 描写を重視する文章
`primary=false` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Detailed sensory description dominates supplied narration. | 詳しい感覚的な描写が、提供された語りの大半を占める。 |
| include | Excerpt substantially develops observed sensory particulars | 抜粋が、観察された感覚的な細部を十分に展開する。 |
| exclude | A synopsis inventories settings | あらすじが舞台を列挙する。 |

#### `style.ironic` — Ironic prose / アイロニーを持つ文章
`primary=false` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied prose maintains a consequential ironic stance. | 提供された文章が、重要な影響を持つアイロニーの立場を維持する。 |
| include | Excerpt creates discernible gaps between saying and meaning | 抜粋に、述べる内容と意味との識別可能な隔たりがある。 |
| exclude | Sarcastic character dialogue alone | 登場人物の皮肉な台詞だけ。 |

#### `style.archaic` — Archaic register / 古風な言葉遣い
`primary=false` · `scope=original_or_explicit_expression` · `subaxis=null` · `parent_id=axis.style`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | The supplied prose uses historically marked language. | 提供された文章が、歴史的な特徴のある言語を使う。 |
| include | Excerpt shows sustained obsolete diction or syntax | 抜粋に、持続的な古い語彙や構文がある。 |
| exclude | Historical setting alone | 過去が舞台であるだけ。 |

### atmosphere — 雰囲気（12ラベル）

#### `atmosphere.tense` — Tense atmosphere / 緊張した雰囲気
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence creates sustained pressure or suspense. | 提供された根拠が、持続する圧力やサスペンスを作る。 |
| include | Passages or explicit narrative description support mounting unease | 一節、または明示的な物語の説明が高まる不安を支持する。 |
| exclude | A lone danger with no sustained atmosphere | 持続する雰囲気を伴わない、一度の危険。 |

#### `atmosphere.dark` — Dark atmosphere / 暗い雰囲気
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence supports bleak or oppressive affect. | 提供された根拠が、陰鬱または抑圧的な情動を支持する。 |
| include | Persistent dread, harshness or hopelessness | 持続する恐怖、厳しさ、絶望。 |
| exclude | Nighttime setting alone | 夜が舞台であるだけ。 |

#### `atmosphere.warm` — Warm atmosphere / 温かな雰囲気
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence supports tenderness and emotional welcome. | 提供された根拠が、優しさと心情的な受容を支持する。 |
| include | Supportive interactions or consistently comforting affect | 支え合うやり取り、または一貫して安心させる情動。 |
| exclude | A happy ending alone | 幸福な結末だけ。 |

#### `atmosphere.humorous` — Humorous atmosphere / ユーモラスな雰囲気
`primary=true` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence supports a recurring comic effect. | 提供された根拠が、繰り返される喜劇的な効果を支持する。 |
| include | Comic situations, phrasing or explicit sustained humorous tone | 滑稽な状況・言い回し、または明示的に持続するユーモラスな調子。 |
| exclude | One character smiles | 一人の人物が微笑む。 |

#### `atmosphere.bittersweet` — Bittersweet atmosphere / ほろ苦い雰囲気
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Joy and sorrow coexist in the supplied emotional treatment. | 提供された感情の扱いにおいて、喜びと悲しみが共存する。 |
| include | Mixed tenderness, regret and qualified happiness | 優しさ、後悔、留保を伴う幸福が混ざり合う。 |
| exclude | Any sad event alongside an unrelated happy event | 悲しい出来事と、それと無関係な嬉しい出来事があるだけ。 |

#### `atmosphere.melancholic` — Melancholic atmosphere / 憂いのある雰囲気
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Reflective sadness or wistfulness pervades supplied material. | 内省的な悲しみや物思いが、提供された資料に広がる。 |
| include | Sustained sorrow, longing or contemplation of loss | 持続する悲しみ、憧れ、喪失についての思索。 |
| exclude | A tragic event alone | 悲劇的な出来事だけ。 |

#### `atmosphere.hopeful` — Hopeful atmosphere / 希望のある雰囲気
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence supports anticipation of possibility or renewal. | 提供された根拠が、可能性や再生への期待を支持する。 |
| include | Sustained affirmative expectation amid circumstances | その状況の中で、肯定的な期待が持続する。 |
| exclude | Success in a single incident | 一つの出来事で成功する。 |

#### `atmosphere.uneasy` — Uneasy atmosphere / 落ち着かない雰囲気
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence supports pervasive uncertainty or discomfort. | 提供された根拠が、広がる不確かさや不快感を支持する。 |
| include | Subtle disquiet and unsettled perception | かすかな不安や、落ち着かない知覚。 |
| exclude | Reader uncertainty due to missing input | 入力不足によって読み手が判断できないこと。 |

#### `atmosphere.wonder` — Wonder atmosphere / 驚異の雰囲気
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence supports awe or astonishment. | 提供された根拠が、畏敬や驚きを支持する。 |
| include | Evocative encounters with extraordinary beauty or possibility | 並外れた美や可能性との、心に訴える出会い。 |
| exclude | Fantasy genre alone | ファンタジーというジャンルだけ。 |

#### `atmosphere.tranquil` — Tranquil atmosphere / 静穏な雰囲気
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence supports sustained calm and repose. | 提供された根拠が、持続する平静や安らぎを支持する。 |
| include | Quiet unhurried emotional treatment | 静かで、急がない感情の扱い。 |
| exclude | Low action count alone | 行動の数が少ないだけ。 |

#### `atmosphere.intense` — Emotionally intense atmosphere / 感情的に強い雰囲気
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence foregrounds strong sustained emotional charge. | 提供された根拠が、強く持続する感情の高まりを前面に出す。 |
| include | Extended urgent emotional conflict or passion | 長く続く、切迫した感情的な対立や情熱。 |
| exclude | A single exclamation | 感嘆が一度あるだけ。 |

#### `atmosphere.absurd` — Absurd atmosphere / 不条理な雰囲気
`primary=false` · `scope=work_or_expression` · `subaxis=null` · `parent_id=axis.atmosphere`

| 項目 | 英語原文 | 日本語補助訳 |
| --- | --- | --- |
| definition | Supplied evidence supports pervasive incongruity or irrationality. | 提供された根拠が、広がる不調和や非合理性を支持する。 |
| include | Repeated inexplicable situations treated as a narrative mood | 説明できない状況が繰り返され、物語の雰囲気として扱われる。 |
| exclude | A factual inconsistency in input data | 入力データの事実上の不整合。 |
