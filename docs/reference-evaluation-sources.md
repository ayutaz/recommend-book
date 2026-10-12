# 独立参照評価に使う公開研究データ

確認日: 2026-10-12。対象は既存研究の人手・専門家由来ラベルと、その利用条件・名寄せ・入力漏洩である。分類器の実行、taxonomy変更、評価へのインポートはこの調査では行っていない。

## 結論と現在の到達点

**CoNSSAの人手由来メタデータが最優先の追加参照候補**となる。現行公式データ358行のうち、`CoNSSA=True` かつ `keywords.cert=high/medium` は269作品。作品ID、作品名、著者、年、著者VIAF・作品BNE/VIAF、語り手・主人公・ジャンル情報がある。メタデータはCC BY 4.0で、商用利用・外部LLMへの利用・再配布は帰属と改変表示等の条件を保持して扱える。ただし、外部LLM利用を個別に記した規約ではなく、公開ライセンスの複製・改変許諾からの用途判断である。

今回保存した269作品は **`curated_reference` のnative taxonomy候補**である。6軸の新taxonomyに対する人手正解へ変換していない。現PoCの取得元フレームとの正規化タイトル一致は3作品に留まり、作品名寄せも未確定。**100作品の独立評価を実施した状態でも、400作品の人手goldを達成した状態でもない。** 語り手・主人公の一部を検証する100〜162作品の追加benchmarkは次の実行段階の候補であり、6軸全体の品質承認に置き換えない。

## 一次資料を確認した候補

| データ | 人手・専門家由来と実測範囲 | 現行の利用条件 | 採用判断 |
|---|---|---|---|
| [CoNSSA公式repo](https://github.com/cligs/conssa) | José Calvo Telloによる文学メタデータ。358作品、確実度中以上269。2018年発表では当時の350作品中262を人手注釈した評価に使用 | メタデータ・XML markupはCC BY 4.0。本文の公開可否は別 | 最優先。100以上の追加参照候補を作れるが、新taxonomy対応・入力・名寄せを別検証 |
| [The Life Cycles of Genres](https://doi.org/10.7910/DVN/XKQOQM) | 書誌・批評・LoCカタロガーのジャンル主張を著者が収集し手動tagging。公式2016 archiveは984巻、タイトル+著者の表記ペア938 | 取得したDataverse dataset v1はCC0-1.0。GitHubコードはMIT。原小説本文の許諾ではない | 歴史的ジャンルとの一致参照。小説・短編・短編集・分冊が混在するため938作品の小説とは数えない |
| [CLiGS textbox](https://github.com/cligs/textbox) | 編集者付きのFrench36、Spanish39、Spanish-American24、Portuguese30のmetadataを取得。Spanish39はCoNSSAと重なる旧subset | collection別の条件を優先。French metadata CC0、Spanish/Spanish-American CC BY 4.0、Portuguese CC BY-SA 4.0 | 補助候補。Portuguese subgenreは暫定で厳密なtypologyではない。129件をCoNSSAに単純加算しない |
| [LTO v2025.04](https://zenodo.org/records/15210539) | 共同theme annotation。releaseの4,225 storiesのうち `novel:` IDは実測22。Golden Assの章・挿話を別小説に数えない | 現行repoはmetadata/theme等MIT、Wikipedia由来story descriptionsはCC BY-SA 4.0例外 | テーマ定義・少量照合用。100小説の参照要件は満たさない |
| [CMU Book Summary](https://www.cs.cmu.edu/~dbamman/booksummaries.html) | Wikipedia概要とFreebase author/title/genre。16,559はbooksでありnovelsだけではない。各ラベルの判定者・独立読了・裁定情報は公式配布ページから確認できない | 公式licenseリンクはCC BY-SA 3.0 US | 今回は未取得・非推奨。Wikidata/Freebase/Wikipedia由来入力との循環を避ける必要があり、独立専門家goldとはしない |

CoNSSAの人手由来の一次証拠は [著者のDH2018発表](https://dh2018.adho.org/rules-against-the-machine-building-bridges-from-text-to-metadata/) にある。主人公の性別・年齢・社会階層、舞台、語り手、表象、時代、結末等の手動注釈を明記している。過去262件と現行269件は同一集合と断定しない。研究による自動予測や数値特徴 `am.*` を人手ラベルへ混ぜない。`subgenre.edit.wikidata` 等の外部分類列も独立した読了注釈として採用しない。

Underwood archive内の `meta/datadictionary.csv` は `locscifi/chiscifi` のLoC由来、`det100` の展示書誌由来、`anatscifi` のSF史由来、`pbgothic` の批評書由来等を説明する。`random/chirandom` は対照標本の抽出属性であり「すべてのジャンルが不存在」の正解ではない。`teamred/teamblack/stew` も作品内容ラベルとして使わない。[データ作成説明](https://github.com/tedunderwood/fiction/blob/v0.2/gather_data.md)、[データ辞書](https://github.com/tedunderwood/fiction/blob/v0.2/meta/datadictionary.csv)。予測確率を含む `final/` は今回参照goldにしていない。

## 利用条件の適用範囲

CoNSSA READMEはメタデータのCC BY 4.0と本文の公開可能範囲を分離している。269候補の本文availabilityは `free=162`、`restricted=107`。この調査では本文を取得していない。**`free`、公開サイト上のPDM、米国のpublic domainを、日本・世界全体の本文利用許諾として扱わない。** 実本文入力は作者・原作・翻訳・デジタル底本の法域と条件を作品ごとに確認してから行う。メタデータのCC BYを本文に引き継がない。[CoNSSA固定commit README](https://github.com/cligs/conssa/blob/8fccf669ed1bdbad309ff190f5afb0d6066d70a3/README.md)、[Project Gutenberg利用条件](https://www.gutenberg.org/policy/license.html)

CC BY 4.0データを実際に再配布する際は、作者と指定された帰属先、元資料URL、ライセンスURL、copyright/免責等の提供された表示を保持する。列抽出・正規化・フィルタ・taxonomy対応を行ったことを明記し、元の内容と変更を追跡できるようにする。ライセンスで許された利用を制限する追加条件を付けない。外部LLMに渡すコピーにも元ライセンス・出典を保持し、出力をCC0と断定しない。[CC BY 4.0公式条件](https://creativecommons.org/licenses/by/4.0/)

LTOの旧release READMEはMITと記すが、現行の [LICENSE-EXCEPTION](https://github.com/theme-ontology/theming/blob/master/LICENSE-EXCEPTION.md) はWikipediaを編集したstory descriptionsをCC BY-SA 4.0と明示する。この調査ではzipをローカル参照保存したが、説明文をLLM入力・公開fixtureへ抽出していない。旧MIT表記だけから説明文を自由再配布する判断はしない。[現行README](https://github.com/theme-ontology/theming/blob/master/README.md)

CLiGSのcollection条件は全体READMEより具体的である。[French](https://github.com/cligs/textbox/blob/master/french/roman19/readme.md)、[Spanish](https://github.com/cligs/textbox/blob/master/spanish/novela-espanola/readme.md)、[Spanish-American](https://github.com/cligs/textbox/blob/master/spanish/novela-hispanoamericana/readme.md)、[Portuguese](https://github.com/cligs/textbox/blob/master/portuguese/romances19/readme.md)。[Italian21作品](https://github.com/cligs/textbox/blob/master/italian/romanzi/readme.md) は本文・付加annotation・metadataがCC BY-NC-SA 4.0であり、商用を含む共通参照候補から除外した。

## 現PoCとのかぶり検査

`data/reference/overlap-audit.json` は次の保存済み入力を固定ハッシュで検査した。これはsource候補フレームであり、最終分類入力や2,000作品DBへの対応表ではない。

| フレーム | 行数 | SHA-256 |
|---|---:|---|
| `data/wikidata-poc-v2/works.jsonl` | 3,154 | `312d18db751e27cd39ac39ae1c7e086a5004a46b35f93f1b47e2379c9128413b` |
| `data/openlibrary-prefix-v2/works.jsonl` | 27 | `d6f6b1e588f22af037b49d46dbe6f70b18f7846d10ea6a28b45c24e582ee6090` |
| `data/ndl-poc/records.jsonl` | 26 | `65f1b5915d3a99e58cbd2b44c594b5b930d527d5a408e6ba88bd963a55da71f6` |

Unicode NFKC、casefold、空白・記号除去後のタイトル完全一致を候補検出にだけ使った。結果はCoNSSA3行、Underwood4行、CLiGS Spanish1行、他の取得CLiGS0行。旧Spanishの1行はCoNSSA候補と同じ `ne0175`。タイトル不一致でも翻訳名・省略名があるため、0件は真の非重複を証明しない。

CoNSSAの候補は `ne0121 La voluntad → Q104767383`、`ne0157 Pequeñeces → Q112262940`、`ne0175 El niño de la Bola → Q106963817`。保存済みWikidataの著者名は対応を支持するが、後二者は年が1891/1890、1880/1884と食い違い、初出/版の区別を要する。3件とも名寄せ未確定のままとする。該当WDレコードのsubjectsは空だが、分類に最終使用するWikipedia本文まで検査した結果ではない。

Underwoodでは `Shadows` が1896年Palmarini作品に対し2012年Jennifer Armentrout作品、`Autumn` が1921年Robert Nathan作品に対し1920年芥川龍之介作品を拾った。これはタイトルだけの結合が誤統合する実例である。`Oliver's story`、`The forever man` は著者・年も対応を支持するが、work/edition/collection同定を完了していない。

独立評価を開始する前に、作品ID・著者ID・原作年・シリーズ/分冊を解決し、最終promptのラベル、主題、書誌説明、synopsis、taxonomy例示に参照ラベルが含まれていないか検査する。同じラベルがinputにある場合は `input_label_consistency` として分離する。外部参照ソースと別のサイトでも、同じLoC/Freebase/Wikipedia分類のコピーなら独立性を主張しない。モデルの事前学習に既知作品が含まれるかは今回検証できておらず、holdoutは研究作業内の分離を意味する。

## 6軸taxonomyへの対応設計と不足

269候補のnative支持は `protagonist.age=child:20/adult:145`、`profession=artist:22/student:13/military personnel:13`。child/adult/artist/student等は既存taxonomyへ近いが、単一主人公の分類と複数主人公を含む定義の差を確認してから対応表を固定する。`military personnel` を全件soldierに、`young` をchildに、`mature` をelderlyに変換しない。native未記載値、`?`、mixed、unknownは不存在へ変換しない。

語り手は `heterodiegetic:178/homodiegetic:35/autodiegetic:42/epistolary:1/mixed:4/dialogue:9`。語り手が物語の登場人物かどうかと、文法上のfirst/third personは同じ概念ではない。単純な一対一変換をしない。ジャンルの `fantastic→fantasy`、`gothic→horror`、`sentimental→romance`、`bildungsroman→theme.growth` も概念差があり、人手で対応の適用範囲を確認する。

新taxonomyのテーマ、文体、雰囲気のまとまった人手ラベルはCoNSSAだけでは揃わない。LTO22小説は少量テーマ参照、LitBankは100作品のentity/event/coreference/quotation annotationであり、作品全体の6軸ラベルではない。[LitBank公式仕様・CC BY 4.0](https://github.com/dbamman/litbank)。ELTeCの基本的な年代・著者・長さ・語彙annotationも、そのまま6軸正解にはならない。

次の段階ではCoNSSAの100作品を選ぶ前に、対象native labelと対応定義、本文権利、独立入力、作者グループ分割を固定する。最初は `curated_reference` でnative taxonomyとの一致を報告し、元研究の注釈を今回の独立2名人手レビューと名乗らない。元データのcertaintyはモデルのraw confidenceや関連度0〜1ではない。これらからconfidence calibrationを作らない。新taxonomyの400作品人手注釈と6軸校正は引き続き別の未完了作業である。

## 保存物と再現方法

- `data/reference/download-manifest.json`: 公式URL、固定commit/版、byte数、SHA-256。UnderwoodはDataverseの非restrictedファイル、LTOは公式Zenodo release。
- `underwood-finalmeta.csv` / `underwood-datadictionary.csv`: 元archiveのmetadataとラベル出典辞書。原小説本文は含まない。
- `conssa-metadata.tsv` / `conssa-README.md`: commit `8fccf669ed1bdbad309ff190f5afb0d6066d70a3`。metadata SHA-256 `2c127dbb6f82b33548151e09a5a185a384c36981eb14e7fd1c7cfab54f035400`。
- `conssa-eligible-native-reference.jsonl`: 中以上269件の部分列・native label fixture。attribution、license、本文availability、本文権利未検証を保持。分類器用WorkInputでも、評価APIにそのまま投入できるgoldでもない。
- `cligs-*-metadata.csv` / `cligs-*-readme.md`: commit `1b91e940475c8a7e866457a3953fed5edb0a29f1` の小量metadata。本文は未取得。
- `overlap-audit.json`: 検査入力ハッシュ、実件数、タイトルだけの対応候補。確定identityを生成していない。
- `snapshot_sources.py` / `audit_reference_sources.py`: 調査再現用。前者は公式archiveを取得、後者は小量metadata取得と機械集計を行う。live LLMは使わない。データ・zipをGitへ入れず、docsとハッシュで出典を保持する。

今回追加したファイルだけを扱い、既存コード・設定・DB・READMEの変更、git操作、LLM分類は行っていない。

## 追記: ステージ4用の限定した補助参照の準備

上記は最初の調査時点の記録である。その後、`scripts/prepare_curated_reference.py` により269件の固定native fixtureから、**4ラベルの正例だけ**を既存評価APIが読める `curated_reference` 形式へ変換した。新taxonomyの6軸全体を変換・検証したものではない。

| 原native field/value | 現taxonomyへの正例変換 | 支持件数 | 定義照合の理由 |
|---|---|---:|---|
| `protagonist.age=child` | `protagonist.child=present` | 20 | 原metadataが主人公を子どもと明示し、中心人物がexplicitly a childという定義へ直接対応 |
| `protagonist.age=adult` | `protagonist.adult=present` | 145 | 原metadataのadultという明示判定へ限定。young/matureを成人扱いしない |
| `protagonist.profession=student` | `protagonist.student=present` | 13 | 明示された学生という役割へ対応。年齢や舞台から学生を推測しない |
| `protagonist.profession=artist` | `protagonist.artist=present` | 22 | 芸術の職業を示す原カテゴリへ限定。writerやjournalistを推論で追加しない |

現taxonomyのdefinition文字列を変換器内の確認済み定義と照合し、変更された場合は自動処理を止める。原metadataにおける単一主人公の記述は「少なくとも一人の中心人物にその属性が明示される」という正例の適用範囲で扱う。他の主人公に別属性がある可能性を排除せず、`absent` を生成しない。ソース注釈の確実度high/mediumは文字列で保持するが、数値confidenceやrelevanceに変換しない。すべての出力labelのconfidence/raw_confidence/relevanceはnull。

`narrator=homodiegetic/autodiegetic/heterodiegetic` は語り手の物語参加を述べるカテゴリであり、現taxonomyの文法的first/third personと同値だと確認できない。`epistolary` についても現definitionが要求する「主にletters/documentsで届ける」という範囲を、保存されたカテゴリだけから検証できない。このため**structure.first_person等のgoldは生成していない**。不確かな値をgoldの `unknown` にすることも避け、`mapping-audit.jsonl` の `conversion_status=unsupported` に保存した。これは判定不能の人手正解ではなく、変換できないという実装上の状態である。end/subgenre/cligsの分類、外部由来のsubgenre列、機械生成の `am.*` もgoldには使わない。

実出力は `data/reference/conssa-curated-v1/gold.jsonl` の180作品・200正例。89作品は直接対応する正例がないためgoldから除外したが、269件すべての非対応理由を監査sidecarに保持する。上表の支持件数は元研究との一致を照合するための件数であり、今回の人手評価の支持件数ではない。正例のみの参照から一般的なprecision/accuracy、全ラベルの不存在、校正済みconfidenceは主張しない。評価を実施する場合も、まずは限定された正例検出のrecallとして解釈する。

各出力はsource commit、metadata SHA-256、native field/value、変換rule/reason、taxonomy version、帰属、license URL、changesを保持する。fixtureと原metadataのSHA-256が違えば出力作成前に失敗する。`work_id` は `reference:conssa:neXXXX` という参照専用の未同定IDで、`identity_status=unresolved`、`evaluation_ready=false`、`input_status=independent_input_not_constructed` として保存する。元研究の注釈を今回の独立2名human goldへ昇格しない。

現main PoC入力 `data/poc-v1/work-inputs.jsonl` の実2,000行（SHA-256 `5a8502845254a7369e2fc250a13e6101147c33da3fc077ab79d9c09396e88bc0`）との正規化代表タイトル一致を追加測定したところ、269候補中2件だった。`ne0157 Pequeñeces` と `ne0175 El niño de la Bola` で、上記の年の相違を含め同定は未確定。別題探索・本文・入力ラベル漏洩の検査ではない。この**ほぼ重ならない未同定の参照集合を2,000作品評価へ流し込んでいない**。詳細は `data/reference/current-poc-title-overlap.json` に保存した。

再現コマンド:

```powershell
uv run python scripts/prepare_curated_reference.py --output data/reference/conssa-curated-v1
uv run pytest tests/test_curated_reference.py tests/test_evaluation.py -q
```

出力先は新規または空のdirectoryを指定する。既存の非空snapshotを上書きしない。変換scriptはローカル保存済みmetadataだけを読む。Web/WD/LLMの取得、本文入力、DB変更は行わない。

入力fixture SHA-256は `4acc51caff87ee9f12815dda0644301b912742e2e5c67c960b7645744ba7c66a`、taxonomy SHA-256は `688f8e1a0921bcbafabbbf54d99b9039bce8c525ea7e7147bdb01821b449084e`、gold SHA-256は `13b0f25cfbb291705ef6ddc62046e0bc0ca4621bc5a544c9784358b6da5389f0`。出力manifestにも保存した。TDDでは実装前に変換器ファイル未存在による9件の失敗を確認し、実装後に9件が成功した。原資料の意味の全正解、新しい人手400作品、実作品での分類精度が確認されたという意味ではない。

既存評価器との回帰検証を含む `tests/test_curated_reference.py tests/test_evaluation.py` は23件成功した。参照を形式上評価器へ渡せることと、human集計・校正へ混ざらないことを確認したテストであり、実PoCへ参照を投入した結果ではない。
