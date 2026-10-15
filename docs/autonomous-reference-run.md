# 外部専門家参照 benchmark B の実行記録

対象は `config/autonomous-evaluation-v1.json` の B。ユーザーの追加注釈作業は0件。既存 core、taxonomy、rights config、DB、固定180参照を変更せず、新namespace `data/autonomous-poc-v1/reference/` で実行する。確認日は2026-10-13。

## 入力成立と対象範囲

CoNSSA固定commit `8fccf669ed1bdbad309ff190f5afb0d6066d70a3` のmetadata SHAは `2c127dbb6f82b33548151e09a5a185a384c36981eb14e7fd1c7cfab54f035400`、既存180作品・200正例のgold SHAは `13b0f25cfbb291705ef6ddc62046e0bc0ca4621bc5a544c9784358b6da5389f0`。baseline manifestと一致を実行前後に検証する。[公式README](https://github.com/cligs/conssa/blob/8fccf669ed1bdbad309ff190f5afb0d6066d70a3/README.md)、[既存変換の範囲](reference-evaluation-sources.md)。

180参照のうち本文availabilityがfreeなのは105。保守的に作者没年<=1940、初出<=1930までに絞ったmetadataだけの上限は96。公式 `master/master/neXXXX.xml` の作品・版まで照合した **実成立は60作品・65正例**。100targetを達成するために審査条件を緩めていない。

| 180件の採否 | 件数 | 理由 |
|---|---:|---|
| 入力成立 | 60 | 本文ID・作者ref・原言語・native正例・初版/底本年・許諾範囲を確認 |
| 公開制限 | 75 | restrictedを取得/入力しない |
| 底本年不明 | 15 | 空の日付・未記載・s.a.を推測しない |
| 底本1931年以降 | 20 | 旧原作でも後の校訂・追加の範囲を未確認として保留 |
| 初出1931年以降 | 7 | 米国外国作品の95年保護期間を保守的に適用 |
| 作者の期間審査外 | 2 | 1967/1975没。freeという表示だけで通さない |
| 未審査の本文要素 | 1 | castList。今回の抽出範囲を広げて通さない |

`pilot20-v2` は20作品・23正例（adult18、child2、artist2、student1）、7作者。`target100-v1` は実成立60を保存したsnapshot名であり、100入力という意味ではない。pilotの本文18,000字×20=360,000字。いずれも全編入力ではなく、作品全体の属性を抜粋から検出できた率を測る。

初回 `pilot20-v1` はTEIで最初に出現する図書館分類keywordsをnative欄として選んだため、全180件を保留し入力0になった機械監査の履歴。修正では `scheme=keywords.xml` を限定して取得し、ライブラリ分類が先行するfixtureで検証した。この失敗を本文利用不能の判断に使わない。

## 作品・版の対応と答えの隔離

公開する原物とGit treeは公式固定commitだけから取得する。各XMLはGit blob SHA-1・byte数に照合し、別途raw SHA-256を保存する。annotated XMLや機械特徴表を入力にしない。metadata/goldのneID、短題、作者ref、native age/professionの正例、原文Spanish、初版年・底本年を同一TEIと照合する。

同じcorpus版・同じnative判定への対応であり、Wikidataのcanonical作品やすべての出版版を人手同定した証拠ではない。原metadataの書誌誤りや独立再注釈の不足は残る。main2kのタイトル一致へ件数を制限しない。

本文のbodyから、header、abstract、keywords、外部subgenre、XML属性、機械anno、note、front/back、章題head、序文・献辞・付録等のeditorial divを除外する。原物内のfloatingTextは物語内の手紙・名刺、stage/abは原文の台詞・地の文として、保存した原物例とfixtureで許可した。未知tagは失敗させる。章題や本文に登場する自然な人物・年齢等を正解列の流出と混同しない。

本文を空白正規化した後、18,000字以下は全body、それ以上は開始・中間・末尾の各6,000字を固定的に切り出す。ラベル・予測を見て位置を探さない。正確なbody SHAと文字offsetをlocatorに保持する。作品名は `External reference neXXXX`、authorsは空、初出年はnullとし、モデルには書誌metadata・参照答えを渡さない。帰属はCLiGSとsource URL・license・changesに限定し、実作者・書誌・正解はsidecarへ保持する。

分類workspaceには正解ファイルをコピーしない。既存promptは供給された証拠だけの判定と、工具/通信/ファイル閲覧禁止を要求する。runnerがtool callを検出した実行は正常結果にしない。モデルの事前学習にこれらの作品があるかは未確認。

## 権利審査

`free`とPDMを世界PDの根拠にしない。[PDM公式説明](https://creativecommons.org/publicdomain/mark/1.0/)。審査対象地域は日本・米国・スペイン。外部LLMの処理地域をその3地域だけと断言せず、未確認の地域をrights ledgerに残す。新たな翻訳・序文・注釈を許可する判断ではない。

日本では2018年改正による延長が失効済み権利を復活させない。戦時加算が残っていた権利は延長され得るため、単に没年1967以前とはしなかった。採用作者は1940年以前没、初出・底本は1930年以前。没年と初出年の大きい方に旧50年と保守的12年の戦時幅を足しても2018年前に失効する範囲に限定する。12年はJASRAC公式表の最長4180日を上回る計算用上限であり、その作者に戦時加算が実際にあるという主張でも正確な失効日の算定でもない。今回成立60作品に死後初出は0件。[文化庁の保護期間・非復活・戦時加算説明](https://www.bunka.go.jp/seisaku/chosakuken/hokaisei/kantaiheiyo_chosakuken/1411890.html)、[JASRAC対象国・日数表](https://www.jasrac.or.jp/aboutus/wartime/pdf/wartime.pdf)。

米国では外国作品のURAA復活も考慮し、初出1930年までの95年満了を採用する。後の底本に追加の著作物がないと断定できないため底本も1930年まで要求した。[米国Copyright Office Circular 38B](https://www.copyright.gov/circs/circ38b.pdf)。スペインでは1987年12月7日より前に亡くなった作者について旧法の80年が適用される範囲を確認し、1940年以前没に限定した。[現行法の移行条項](https://www.boe.es/buscar/act.php?id=BOE-A-1996-8930)、[1879年法第6条](https://www.boe.es/buscar/doc.php?id=BOE-A-1879-40001)。

原小説本文の期間と、CLiGSが付したXML・transcription/metadataのCC BY 4.0を分ける。metadata許諾を原小説全体へ引き継がない。入力にもsource URL・license・改変表示を保持し、作者・指定帰属・免責等の提供された表示は原物/sidecarに残す。外部LLM専用の明記ではなく、期間満了と複製/改変許諾に基づく用途判断である。[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。新しい共通rights registryや商用全世界許可を作成していない。

## 評価契約とnative task

現taxonomy分類では、native `protagonist.age=child/adult` と `protagonist.profession=student/artist` の直接対応4ラベルだけを比較する。モデルは現72ラベルを出力し、このrunのnative欄は同じ予測の再表現。**この欄をnative専用分類器の結果とは呼ばない。** 単一主人公のnative分類と、複数の中心人物を許す現taxonomyは正例だけで接続し、他の属性をabsentにしない。

別namespace `native20-run-v1` には専用schemaによる単一主人公のage/profession予測を実装した。固定公式codebook `keywords/keywords.xml` はSHA `2413a5d27d6a1b95e905a961487de9fd63a013af62ea0b6793d1bfbc468c66cc`。ageはadult/child/young/mature/?/other/unknownのordinal宣言だけで数値境界はなく、professionはカテゴリを持たないopen nominal宣言。原抜粋から単一主人公を特定できなければunknown/null、knownにはその人物を支持する正確な引用とlocatorを要求する。既存の現taxonomy予測やnative答えをプロンプトへ渡さない。職業は原文を根拠とする簡潔な英語名で返し、painter/writerをartistへ統合する定義を作らない。したがって専用nativeのcurated200に対する主比較は確認済み4正例文字列の検出に限定し、意味の近い職業名による不一致は分類粒度の限界として残る。

さらに20作品のnative age20件とprofession20件について、元CSVと固定TEIのnative欄がすべて同じ文字列であることを照合した。`native20-v1/raw-native-reference.json` のSHAは `5004e0ac26661415bf1e1e15251be854322b4c91f509b971235058b57e788d64`。同じ専用native予測をこの40項目と比較し、curated200とは別のselected20限定のpartial excerpt detectionとして別掲する。職業名にはlord/religious/military personnel等もあり、同義語・上位概念の統合はしない。異なる文字列は意味的な誤答の証明ではない。この参照はモデル入力・workspaceへ渡さず、新たなtaxonomy gold/負例/人手注釈を作ったものではない。

語り手の物語参加、年齢の境界、professionのその他値、ジャンル/結末には閉世界の定義・排他性・注釈単位が確認できていない。これらの未評価理由は `native20-v1/codebook-audit.json` に保存した。概念定義を推測して新しい正解・負例を作らない。語り手のhomodiegeticをfirst_personへ写さない。旧研究262と現269を同一集合とせず、元certaintyをconfidence/relevanceへ変換しない。[著者の手動metadataの研究説明](https://dh2018.adho.org/rules-against-the-machine-building-bridges-from-text-to-metadata/)。

全180作品・200正例を固定分母としたpositive detection recallと、実入力 subset の正例検出率・input coverageを別表示する。未実行・held・unknown・absent・omittedを未検出として保持する。常時presentは仮想的に全200へ返せばrecall=1、実入力だけならcoverageを反映した率も別掲する。常時unknownは0。正例のみからprecision/F1/accuracy、confidence校正、全6軸の品質保証を生成しない。native正例のrelevance/confidenceはnullのまま。

集計はexit観測proof、manifestのpending0/stoppedfalse、selected=completed+held、実入力identity/hash、model/CLI/run config、固定10artifactのbefore/after SHA、durable jobs/resultsの一致を検証してから行う。heldをunknownという結果に補作しない。

## 補助資料

Underwood固定Dataverse v1はCC0 metadata 984巻。`locscifi/chiscifi`等はLoC、`det100/anatscifi`等は書誌・批評に由来するnativeジャンル主張で、random/chirandomは内容の負例ではない。巻/短編集/小説の区別、work/editionの対応、現在taxonomyへの定義差が未解決。配布metadataのCC0は原文入力許諾ではなく、保存archiveに一般取得可能な原小説本文はない。別の正当な本文sourceと巻対応を確認する前に、このBの成立件数へ加えない。[固定dataset](https://doi.org/10.7910/DVN/XKQOQM)、[公式作成工程](https://github.com/tedunderwood/fiction/blob/v0.2/gather_data.md)。

LTO固定v2025.04は4225stories中、保存archiveの `novel:` 見出しを再計数すると23distinct entries（近代22、古代の長いnovella/短いnovel `A True Story (180)` が1）。既存調査の22という数を今回の23見出しと区別して訂正する。テーマmetadataはMITで、Choice/Major/Minorのnative中心性を持つが、未記載を負例にしない。寄稿者名があることを全員の専門資格確認や二者読了に置き換えない。説明文はWikipedia由来CC BY-SA 4.0例外として扱い、metadataのMITと分ける。Descriptionだけなら帰属・参照revision・改変・SA条件とAnnotations/Theme commentsの隔離後にsynopsis入力を構成できる候補だが、原文文体の根拠にはならない。23見出しが100小説の代替になるとはしない。native revenge/friendship等の名前一致だけで現taxonomyへの対応を確定しない。[固定release](https://zenodo.org/records/15210539)、[公式license exception](https://raw.githubusercontent.com/theme-ontology/theming/master/LICENSE-EXCEPTION.md)。実件数・SHAは `reference/supplementary-source-audit.json` に保存した。両補助資料の分類は今回未実行。

## 再現コマンドと現在の実行

```powershell
uv run pytest tests/test_autonomous_reference.py -q
uv run pytest tests/test_autonomous_native_reference_runner.py -q
uv run python scripts/build_autonomous_reference.py prepare --output data/autonomous-poc-v1/reference/pilot20-v2 --target 20
uv run recommend-book classify --input data/autonomous-poc-v1/reference/pilot20-v2/work-inputs.jsonl --output data/autonomous-poc-v1/reference/pilot20-run-v1 --model gpt-6.1-sol --concurrency 2 --batch-size 5 --timeout 900
uv run python scripts/build_autonomous_reference.py prepare --output data/autonomous-poc-v1/reference/target100-v1 --target 100
uv run python scripts/run_autonomous_native_reference.py --snapshot data/autonomous-poc-v1/reference/pilot20-v2 --output data/autonomous-poc-v1/reference/native20-run-v1
```

既存snapshotを上書きする再現コマンドではない。新規/空の出力namespaceを指定する。rootのGOを受け、20pilotは既存 `run_classification` APIで上と同じcontrolsを使い実行し、実handleのexit0を観測した。60はpilot20の完全同一WorkInput・job config・durable結果を検証して再利用し、差分40だけを新規実行しexit0を観測した。native専用20は60のterminal後にrepo側scriptから起動し、このレーンの全phaseで同時CLI数2を守る。

専用schedulerのtracked scriptと `native20-v1/native_runner.py` は同じbytes、SHA `0ac04930ed095a8cbc17714408fa419368194917b9f724085f0c3ca475dd67b0`。mock2testsをtracked testsへ保存し、既存追加46testsと合わせた48testsで機械契約を確認する。`native20-v1/prelaunch-context.json` とrunのexecution-contextは両scheduler SHAを保持する。元pilotのraw結果・proof・失敗履歴は変更しない。

| 観測済みrun | selected/completed/held | executable正例検出 | 全200正例に対する検出 | 入力coverage |
|---|---|---|---|---|
| 現taxonomy pilot20 | 20/20/0 | 7/23 (30.43%) | 7/200 (3.5%) | 20/180 (11.11%) |
| 現taxonomy target60 | 60/55/5 | 24/65 (36.92%) | 24/200 (12.0%) | 60/180 (33.33%) |
| 専用native20、直接対応4正例 | 20/20/0 | 0/23 (0%) | 0/200 (0%) | 20/180 (11.11%) |

pilot実時間599.27秒、input tokens213,797（cached14,720）、output tokens41,467（reasoning1,387）。`pilot20-run-v1/terminal-proof.json`、`pilot20-positive-report.json` にrun/結果/入力/context SHAを保存した。常時presentは実入力の正例で23/23、全200への実入力coverage調整後23/200、仮想的な全件出力なら200/200。常時unknownは0。これは正例だけの抜粋検出率であり、低い検出率をprecisionや全編読了によるnative精度へ置き換えない。

60実時間1,869.75秒、追加phase input tokens728,449（cached113,920）、output tokens134,228（reasoning3,660）。このusageは差分40とその既存上限内retryのみで、cacheで再利用したpilot20 usageを含めない。pilot＋差分40の実usage合計はinput942,246、cached128,640、output175,695、reasoning5,047。cached/reasoningは各totalに含まれる内訳で、別に加算しない。60のheld5を除いて分母を縮めず65正例を維持した。実入力常時presentは65/65、coverage調整後65/200=32.5%、常時unknownは0。

60 proof SHA `e679c6dcbd4c82b4c2425f676a98a311cb531aa0b0624f102851fcf334a804b4`、results SHA `26eef4b7c816debe5920769f37698a867593c2373af223e7411a26243adcc381`。input SHAは `80918d58589d933544367b05e476c270109896fec1924db3efb3171d3d27bdcb`。元20のresult/job SHAsと完全同一payload/configは `target60-run-v1/cache-reuse-audit.json`、source before/afterは `after-observation.json` に保持する。未実行120と入力済みheld5を別に記録し、missing125を正解/unknownへ補作しない。

専用native20はexit0、20/20完了・held0、CLI4回・tool call0、source bytes before/after不変。年齢はunknown13、young6、mature1で、原adult18/child2との文字列一致は0/20。職業はunknown/null6、他known14で、原open-professionとの文字列一致は2/20。単一主人公名を17作品で返したが、名前の人手検証はしていない。原native40項目の別掲検出は **2/40=5%**。priestとreligious等の言い方の差を統合せず、年齢young/matureをadultへ再写像しない。この率をnegative/precision/F1/accuracyへ置き換えない。直接対応4ラベルに限る主比較は0/23で、常時unknownと同じ検出率、仮想的な常時presentは23/23である。

native実時間65.37秒、input176,981（cached0）、output6,311（reasoning667）。proof SHA `3dc265a31010f51c45ae87e450eb2ac69892a32a5531462605c0ad0c6a4d5cc8`、results SHA `4fac5994f5fcb83f6a4a73b47e232250b24891b51773e8a998c0a589f472c24c`。全phaseの実CLI回数はpilot4＋差分40の16＋native4=24。実usageはinput1,119,227（cached128,640）、output182,006（reasoning5,714）。60という出力namespaceのselected件数を新規呼出し60として二重計数しない。

native40追加比較は分類sourceと別の `native20-v1/finish_native.py` で行った。分類scheduler/module/prompt/schemaやprelaunchcontextを改訂せず、後追加の集計helperと参照artifactのSHA・準備時刻は `offline-score-context.json` に保持する。tracked native mock2testsは、当初のignored実codebook依存を合成fixture＋parser mockへ補正した。旧ignored testcopyは歴史として保持し、そのtest bytesの差を分類sourceの変更とは扱わない。再検証48testsが成功した。

60とnative20の両実handleでterminal exit0を観測し、予約CLI枠2をrootへ解放した。Bは実成立60の現taxonomy分類とnative20専用予測・正例比較まで実行済み。100targetは未達で、その理由は上の版/権利/抽出範囲による120保留である。全native項目/全6軸のaccuracy、校正成功、400件の新taxonomy人手gold、独立二者読了を達成したという主張はしない。追加本人評価0、commitなし。

権利一次資料のraw snapshotは `rights-evidence/manifest.json` に保存状態とSHAを記録した。文化庁・JASRAC・米国Circular38B・PDMは保存成功。スペインBOEの現行法・1879年法2URLはweb一次確認とは別に今回のローカルraw取得がURLErrorとなり、未保存を記録した。取得失敗を保存済みと扱わず、fallbackや地域の許諾推測をしない。
