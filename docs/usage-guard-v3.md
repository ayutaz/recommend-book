# v3 用途別ガードと台帳

この工程は、過去の入力・分類を上書きせず、新しい用途の許可資料を別の台帳へ束縛する。機械的な照合結果は法律上の完全確認・人手評価・過去の送信前承認を意味しない。root の独立監査と実行ゲートは別に保持する。

## 入力と用途

`UsageGuard` は `external_llm`、`display`、`redistribution`、`commercial`、`acquire`、`local_storage` を個別に判定する。WorkInput の全モデルハッシュには内部ID、出典版参照、書誌6項目、全Evidenceモデルを含む。`llm_input_allowed=False` のEvidenceも、渡された入力内にあれば由来検査を省略しない。既存の `inherited_restrictions` や履行条件の名前だけを履行証拠として採用しない。

台帳には元SQLiteのimmutable source version、SourceRecord全文ハッシュ、原物パス・SHA、入力JSONL全文SHAと行番号を束縛する。実行APIは毎回台帳・レジストリ・原物・義務履行資料を再読する。`expected_ledger_sha256` を実行前に固定すると、許可の追加・失効の削除を含む台帳差替えも拒否する。台帳更新は新しい版を審査して実行を分ける。

```python
guard = UsageGuard(ledger_path, expected_ledger_sha256=frozen_sha)
guard.assert_inputs_allowed(works, usage='external_llm')
guard.assert_execution_allowed(config_sha256)
guard.assert_prompt_allowed(actual_stdin_bytes, works,
                            expected_prompt_sha256=expected_complete_prompt_sha)
```

プロンプトは全書誌値とEvidenceを比較する。テンプレート、taxonomy、データ以外の指示、トップレベル要素まで固定するため、runner は独立した固定spec builderから期待する全バイト列を生成し、実バイト列と比較する。ガードへ実入力そのもののSHAだけを渡して期待値と呼ぶ運用は認めない。

## 原物からの再現

書誌の正規化値がimmutableであるだけでは十分でない。採用値について次を再現する。

| Source | 原物に照合する項目 |
|---|---|
| Wikidata | labels・aliases・非deprecatedのP1476、P50とauthor authority、linked-label原物のID/labels/SHA、P407、精度付きP577の最小年、P495、P179 |
| Wikidata Evidence | descriptionsの正確な値、P136/P921、P674、P840、P179、書誌テンプレートの原claimsと寄与者 |
| Open Library | 完全なdump行のkey、title/alternative_titles、first_publish_date、subjects。著者名はwork原物だけから許可せずauthor原物が必要 |
| Wikipedia新補充 | target WD原物・sitelink、pagepropsのQID、名前空間、固定revision、選んだ節全体、追加import notice、第三者テキストのscreen、正確な帰属・変更・継承 |

Wikidataのキャラクター情報は主人公の証明、物語内の場所は作品の原地域の証明として扱わない。複数言語等の未確定値を推測して補わない。Open Libraryのdescription112件の拒否は維持する。

公式条件は、実日付と時間帯を持つ直近の確認、一次資料URL、原文SHA、ライセンス条項を検査する。現在の7日という更新期限は実装側の再確認方針であり、出典が課した法的期限ではない。一般的な公式ライセンス文書の検査だけで、個別の第三者引用や原版権利を確認済みとはしない。

## 派生値とcache

`check_derived` は引用したEvidenceだけでなく、生成に使用した全WorkInputを再検査する。任意の値に許可入力のハッシュだけを付けた包を拒否する。

書誌・別名の包は、固定台帳の `derived_artifacts` に原JSON/JSONL、SHA、行・JSON pointer、全入力ハッシュ、変換recipeを登録する。書誌6値とIDを元入力から再現する。別名の言語は元SourceRecordのTitleへ照合する。

native分類は `source_manifest` のfinal/prompt/request/attempt/stdout/stderrを再読し、事前登録した `execution_recipes` のvalidatorを直接呼ぶ。実終了0、固定実行条件、全batchの元入力、実prompt、agent-final message、結果値の一致を検査する。cacheの単独結果も元batch全体を再検査する。特徴量は `feature_recipes` の固定実装でnative labelから再抽出し、値とjob/run由来の完全一致を要求する。これらは機械的な生成・由来照合であり、内容品質の承認とは別である。

## 私的な本人観測335件

`owned-private-v2` の専用台帳は、本人が保存したHTMLの手動取込由来の `rating_observations.title/my_rating` と `imports` だけを使用する。API補完の作品情報・331件の旧AI注釈・人気値・作者情報を使用しない。READMEと取込実装SHA、SQL観測の来歴、335件全体の値ハッシュ、事前固定specを再検査する。元HTMLはこのcheckoutで再読できないことを明記する。

```python
guard.check_private_reference_aggregate(
    {'mode': 'owned_title_fixed_ranking', 'input_sha256': fixed_rows_sha,
     'preregistration_sha256': fixed_spec_sha, 'aggregate_only': True},
    complete_private_origin_ids,
    context='private_local_owned_title_ranking')
```

許可する用途は本人が明示したreadonly・private localの固定文字列順位計算に限る。公開、外部LLM、元個別タイトル/評価/IDの表示、過去AI処理の遡及承認を許可しない。判定receiptには元IDを並べず、rosterハッシュと件数を返す。world側の書誌・別名・分類の表示判定は別途必要である。文字列類似をgenreや内容の真値とは呼ばない。

なろうAPI資料については、公式ガイドライン第4節がAI学習や類似するデータ解析を禁止している。私的という理由だけで旧API由来注釈を許可しない。[公式ガイドライン](https://dev.syosetu.com/site/guideline/)、[APIマニュアル](https://dev.syosetu.com/man/api/)。一次資料のHTMLは `rights/narou-primary-v1` に2件保存し、利用規約の直接取得403は再試行せず記録した。手動の本人観測とAPI補完タイトルは既存取込コード・SQL列・READMEで区別する。

## 実行状態と履歴

- `build-v1` は初回の暫定計算であり、原物書誌値の直接再現不足のため承認済み納品に使わない。quarantine台帳の16項目と、実際に除外したcanonical15件を区別する。2,005件を残したことと権利がすべて確認されたことは別である。
- `nonempty_content_n=1985` はEvidenceが空でないという件数であり、書誌や短いWDdescriptionを含む。1,985件のあらすじ・物語理解が成立したという意味ではない。
- 旧Wikipedia792件とWikisource40件は新しい外部LLM・商用・表示・再配布用途についてheld。旧分類の引用0件も利用許可根拠にしない。
- `raw-proposal-review-v1` は250件時点で停止した。`raw-proposal-review-v2` も旧v3.2コードによる診断として停止し、session52842のexit1と理由をsidecarへ保存した。両namespaceは履歴であり、現ガードの許可件数ではない。旧DB・入力・結果・schema・sourceは変更しない。
- 新81資料の条件検査、2,005書誌・683別名の許可候補、corpus100+5の候補は、それぞれ用途別の新台帳と独立監査が必要。CLiGSだから一括許可とはしない。個別author authority、原版、本文範囲、変換、帰属を確認する。処理地域の追加保守方針と出典の明文利用条件を区別し、明示自由ライセンスのworld資料へPD審査を波及させない。

現v3.4では、検証済みラベルがないQIDを著者名・地域名・series名に昇格させず、相対rawパスも拒否する。CoNSSAは個別TEI著者VIAF、メタデータのID、BnFの明示的sameAsと同一人物URIの氏名・死亡年、JSON pointerと正確byte rangeを再現してから限定用途を検査する。国の関連情報は国籍へ変換しない。版報告年の順序矛盾、死亡年不足、版・本文範囲の不足は保留する。日本の旧期間と保守的戦時加算が2018年以前に終わる境界、米国の原文・報告版が2026年時点で95年期間を終える境界を、公式条文・[Circular22](https://www.copyright.gov/circs/circ22.pdf)・URAA資料に束縛する。米国で1996年に権利が復活していても、その後の期間満了は別に評価する。これはJP/USと実際のprivate処理用途の審査であり、全世界PDの主張ではない。

`world-pilot-v2` は当時v3.3の原物検証で、あらすじ5件（日本語2、英語・イタリア語・ロシア語各1）と書誌のみ5件の入力を用意した。書誌6値とexpression/source参照を原入力から変更せず、未再現Evidenceだけをハッシュ・理由付きで保留した。あらすじ候補65件中54件がこの原物検査を通り、11件はタイトル由来が再現できず保留した。この旧pilotは未実行で保持し、新v3.4 recipeへの登録と独立監査を別工程として扱う。

`world-projections-v3` は全2005書誌候補、1985分類候補、683別名候補の用途別inventoryを作る。許可しなかった候補も保持し、DBの正式採用は用途判定が通るsubsetだけとする。Evidenceがある件数、短いWDdescription、書誌、あらすじ、原文を別集計し、短いdescriptionを物語理解の成立数には数えない。実件数はこのnamespaceのterminal manifestを参照する。

検証は `uv run pytest tests/test_usage_guard.py -q`（35件）。原物著者・地域・seriesのID誤認、hidden prompt、相対パス、CoNSSA限定用途を先に失敗させてから実装した。独立data fixtureのprojection3件とauthority4件を加えた明示実行は42件が通る。data fixtureは通常CIのテスト収集とは別であり、原物再現の検証を示す。追加LLM呼出0、本人追加評価0、外部連絡0。
