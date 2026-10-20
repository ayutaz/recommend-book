# v3 の作品単位DBと用途別入力

2026-10-20案内: 最新の統合表示と補助DBは[phase15報告](phase15-bounded-poc-and-delivery-v1.md)、今後の書誌・版対応の補完は[実行計画v16](autonomous-next-phase-plan-v16.md)を参照する。以下の基準版・件数は各作成時点の固定記録であり、現在の表示候補数ではない。

2026-10-15現在: 以下の基準版・用途投影・取込仕様と過去の点検値は各スナップショットの記録である。後続の[正式主DB](../data/autonomous-next-v3/database/final-delivery-main-v1/classified-database/dataset.sqlite)は2,005候補・特徴778。補助SQLiteから構成した[最新表示](../data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v2/verification.json)は1,989候補・特徴1,004で、主DBを上書きしていない。履歴との確定関係10件と、内容接続2/335を区別する。下記の旧照合0件を現在の全経路の接続数とは扱わない。集計と未達は[実施報告](autonomous-next-delivery-report-v3.md)を参照する。

`next_database.py` は既存SQLiteを immutable/read-only で読み、新しい名前空間へ backup と投影テーブルを作る。歴史的な正規化・分類・名寄せ・原データは保存する。新しい作品統合、モデル呼び出し、通信、人手正解の作成は行わない。

## 現状の独立点検

`data/autonomous-next-v3/database/inspection-v1/manifest.json` は歴史2,020作品を全件点検した。作品として推薦へ採用する書誌対象は2,005、既存の版／翻訳型との衝突による保留は15である。generic P31 に対する追加3件の原作品／版の不確実性は別の注釈で保持する。P7937 の文学形式と Open Library の work 型の証拠があるため、この3件を generic P31 だけでさらに除外しない。

歴史的な正規化形式は novel 1,359、short_story 533、light_novel 108、unknown 20。eligible の短編は518。正規化に短編集専用値がないことを「短編集が存在しない」という判断に転用しない。raw P527 の収録／部分関係50件、P361の部分関係47件等を残す。短編、短編集、版の収録タイトルliteral、シリーズ、巻、翻訳表現は別の単位であり、題名／ISBN／著者の一致だけでは統合しない。未同定の関係先は外部IDを保持した保留である。

eligible 書誌の unknown は著者98、原言語39、初出年468、地域1,059、シリーズ1,701。unknown を国・翻訳言語・版の年から補作しない。P1476 は published-title 候補であり、言語や旧 `kind=original` から原題を断定しない。翻訳者は著者authorityに昇格させない。旧未解決NDL26件と役割曖昧性は歴史のまま保持し、書誌2,005件へ投影しない。

## 基準版とAPI

```powershell
uv run --frozen --offline python scripts/build_next_database.py build --source-database data/autonomous-poc-v1/database/final-v1/dataset.sqlite --output-dir data/autonomous-next-v3/database/bibliographic-v2 --authority-metadata-relations data/autonomous-next-v3/resolution/v2/authority-metadata-relations.json --supplementary-type-audit data/autonomous-next-v3/resolution/v3/supplementary-type-audit.json
```

既存出力と入力ディレクトリ配下の出力は拒否する。UUID付き一時ディレクトリで単一writerが処理し、integrity/FK、元DBとraw/注釈SHAの開始・終了一致を確認して rename する。失敗した場合は公開しない。元の全テーブルに追加・更新・削除を行わず `next_*` テーブルだけを追加する。

`build_next_input_proposals(source_database)` と `get_next_inputs(next_database_path)` は `bibliographic`、`classification`、`aliases` の完全な WorkInput を返す。書誌入力は全2,005件を保存し evidence=[]。内容入力は新hashとして保存し、旧WP prose-v2／Wikisourceの素材を除く。これは新モデルへの候補であり、旧結果の修復・再利用ではない。source_record_ids はimmutable版の全参照を保持する。新分類結果がなくても書誌DBは完結した基準版である。

日本語別名は work-scope immutable SourceRecord の `Title.language=ja` かつexact値から独立した evidence=[] WorkInput とする。raw上の日本語候補779件全てを未承認で表示へ混ぜない。基準版v1は683の別名入力を保存した。役割は `unknown`。題名別名は作品同定や好みのgenre推定を意味しない。

`load_next_world_database(path, guard=guard, usage='display', content_classified=False)` は書誌投影を返す。入力全体の `check_input` と、表示する exact metadata payload の `check_derived` の双方が必要である。用途保留で表示できない行も基準DBから削除しない。`audit.bibliographic_work_count` と実用途の `eligible_work_count/purpose_held_count` を分ける。書誌経路の特徴は常に `{}`。author名とauthority IDを混同しない。独立に用途／authorityを束縛していないID・URL配列は空にし、名前からIDを補作しない。

`content_classified=True` は新たに検証・取り込みされた分類のsubsetを返す。旧歴史分類を現在の内容特徴として採用しない。書誌だけの行と内容分類行は別経路で扱う。

## 型付き派生と分類取り込み

書誌payloadは `work_id` と title/authors/original_language/first_publication_year/region/series の6値。別名payloadは `work_id/title/language/role` の4値でroleはunknown。rowの `bibliographic_derivation`、`display_title_aliases[].derivation_packet` はexact payload/valueSHA/full origin hash/source版参照/input JSON/recipe SHAを含む。用途台帳の `derived_artifacts` へ、`derivations.jsonl` のraw SHA、1始まりline_number、`/payload` JSONpointerを固定する。候補の保存だけでは表示・商用・再配布等の許可が成立しない。

`import_next_classifications` は新DB、新ordered inputs、run、prepared context、migration manifest、terminal proof、同contextのcli-profile、UsageGuard、新出力namespaceを要求する。元のhistory分類テーブルがあっても新 `next_classification_*` へだけ保存する。selected入力はDBのexact次版content投影と一致し、元バッチ全hash、native source manifest、実送信stdin、CLI実行仕様、prompt/schema/current code config、actual terminal exit=0、pending=0/stopped=false、durableとindexの値・順序、job_key、immutable source refs、evidence source keys、引用・scope・rightsを照合する。heldは結果なしのまま保存する。供給されたCLI文字列だけを実測と主張しない。原run provenanceを保持する。

内容rowの `classification_derivation` はexact native resultと元全バッチorigin hashes、`classification-native-attempt-v3` descriptor。`feature_derivation` は抽出payload、同descriptor、native_result SHAと `features_recipe`。純粋関数 `extract_classification_features(work,result,job_key,run_id)` は present かつwork scopeだけから強度を抽出し、同一featureはmax、relevance=nullは明示方針1。raw confidenceは未校正のまま保存し重みには使用しない。unknown/unreported/除外scopeをnegativeへ変換しない。式／翻訳のstyleをwork推薦へ混ぜない。元結果なしで任意のgenre/featuresを作り、origin hashだけで許可する経路はない。別native-protagonist型は専用抽出recipeが必要であり、旧label型へ補作しない。

用途台帳側は native全バッチ検証と preapproved feature recipe を再確認する。完全な起源hashは `bibliographic_originating_input_hashes`／`classification_originating_input_hashes` で取得できる。引用されたevidenceだけの部分判定では不十分である。旧WP由来の結果は引用0でも新用途へ許可しない。

## 私的評点との独立照合

`rating-world-binding-v1` は本人owned rating_observations最新335件と、全2,020作品のWikidata CC0明示ncode／外部URLの交差を点検した私的artifactである。全propertyの保存済みnondeprecated identifiers/URLも補足確認した。明示候補0、確認済み結合0。題名だけの一致、Narou API注釈・genre・本文へのfallbackは行わない。個別ID・題名・評点はこの私的名前空間だけへ保存し、stdout/文書には集計だけを出す。この0件を個人world推薦profile成立や推薦精度達成と報告しない。

## 検証と未完了の範囲

専用fixtureは原表の全値保持、短編／短編集の分離、P1476の原題非断定、完全別名入力、用途deny、途中改変とatomic公開拒否、native receiptsの生成（実プロセス起動はmock）、exit非0／descriptor欠落／durable差／入力差／任意特徴とモデル値改変の拒否を確認する。

基準版v1の実buildは27.45秒、exit0、歴史2,020／書誌2,005／保留15／内容入力候補1,985／別名入力683／新内容分類採用0。入力候補数と用途許可成立数は別である。新用途のraw再現確認・sealed ledger・分類実runはまだ完了としない。v1は不変とし、最終code SHA固定後の新基準版へ引き継ぐ。LLM実行gateはrootが別に管理する。

正式な書誌基準版は `data/autonomous-next-v3/database/bibliographic-final-v1/manifest.json`。build handle 85481 の実exit0を観測した。件数は上記と同じで、原物束縛済み関係141件（existing-binding74／held67）と追加3件のscope注釈を含む。元27ユーザーテーブルとSQLite内部 `sqlite_sequence`（計28）の全列・値、immutable source版3,210、実raw593ファイルを別のread-only SQL/raw監査で全件確認した。FKエラー0、raw／元DB／出力DBの前後SHA不変。報告は `bibliographic-final-v1-independent-audit.json` に保存している。用途別許可の成立と新モデル内容分類の完了はこの機械的監査からは主張しない。

この歴史基準版のSRCと抽出recipeのSHAは `058b5f961caac4d419ad54f536b6199892972703c0e3bd2b9c9a205faf0da4c4`。DBは `84445979e017799d395fb6bfad556b39eec91c6c85237f969605a5a18f71ea53`、manifestは `c76f29a2bfa80f2b4e115c30fcedd2b42a72af6c69358405ee12d8c3d6bc05ae`。候補v1/v2とformal final-v1は不変であり、古いrecipeを現在の実装として表示・sealへ流用しない。コードや投影入力の変更には別namespaceと新sealを作る。新native分類はactualrun内のartifact namespace、requestのrun/job全batch対応、immutable terminal manifestの同値まで要求する。

## v3.1の読込検証と許可投影DB

v3.1はloaderの `execution_spec.validate_result` を、value hash一致後に必ず呼ぶ。入力にない引用やlocatorでも保存result/label/packet/durableのハッシュを全て更新すると旧loaderが受理する反例をTDDで確認し、修正した。native importerと用途guardの独立検証も維持する。旧DBの値・SHAを書き換える修復は行わない。

`recommend_book.sealed_projection.seal_next_projections(*, base_database, bibliography_inputs, classification_inputs, alias_inputs, projection_manifest, usage_ledger, output_dir)` は、新しいWorkInputの全hashを新namespaceへ登録する単一writer。全候補JSONLはstrict JSONで読み、duplicate property／非有限数値／浮動小数overflow、重複入力・work/expression、bool件数、既存出力・入力配下出力を拒否する。manifestは元DB／3入力／用途台帳／ordered lineage raw SHAとguard version、全canonical件数を束縛する。

各lineageはkind/input_hash/base_input_hash/line_number、source_evidence_keys、excluded_refs、excluded_evidence、purposesを含む。作者など6書誌値とexpressionは元入力と完全一致、source refsは元immutable版のsubset、evidenceは元モデル値・scope・実SourceRecordの全値と一致するsubsetである。除外理由も保存する。別名は元work-scopeの日本語Titleのexact値を独立に用途審査し、原題・翻訳役割はunknownのままにする。

readonly backupへ旧 `next_projection_inputs` と `next_source_refs` の全値archiveを追加し、新入力をactive tablesへ保存する。元27ユーザーテーブル・内部履歴・scope台帳・原注釈は全列multiset digestで保持を検証する。全2,005件の書誌候補は許可保留も保存する。分類はexternal_llm許可subset、別名はdisplay許可subsetをactiveにし、全候補と用途別decisionは `next_sealed_projection_inventory` に保持する。目的別guardを実際に再評価し、許可申告だけで採用しない。

出力stateは `purpose_certified_projections_derivation_seal_pending`。新 `derivations.jsonl` のraw SHA・行番号・`/payload`・新origin hash・現在のrecipe SHAを別のsealed supplementへ固定するまで、派生表示の許可は成立しない。旧origin hashの付替え、旧分類の再bind、旧台帳の無断更新は行わない。入力／ledger／raw／codeの開始・終了一致、元表保持、FK/integrityを確認してatomic renameする。

```powershell
uv run --frozen --offline python scripts/build_next_database.py seal-projections --base-database data/autonomous-next-v3/database/bibliographic-final-v1/dataset.sqlite --bibliography-inputs data/autonomous-next-v3/rights/world-projections-v3/bibliographic-inputs.jsonl --classification-inputs data/autonomous-next-v3/rights/world-projections-v3/classification-inputs.jsonl --alias-inputs data/autonomous-next-v3/rights/world-projections-v3/aliases-inputs.jsonl --projection-manifest data/autonomous-next-v3/rights/world-projections-v3/bridge-manifest.json --usage-ledger data/autonomous-next-v3/rights/world-projections-v3/usage-ledger.json --output-dir data/autonomous-next-v3/database/purpose-projections-v1
```

このコマンドは必要artifact確定後の手順であり、LLMを起動しない。新分類は登録されたexact安全入力から別runを実行し、typed native provenance・terminal proofを検証して別DBへ取り込む。全候補件数、目的別許可件数、新内容分類完了件数を分けて報告する。

## v3.2の特徴採用保留

`native-conflict-hold-v1` は保存済みnative resultのconflictsが1件でもあれば、純粋特徴抽出を空のfeatures/provenanceへ保留する。原result/conflicts/全label/全数値はそのままDBの6分類tableへ保存する。`feature_adoption` packetはpolicy/status/reasons/needs_human_review/human_verified=falseを保存し、loaderが原resultから再現する。needs_human_review=trueだけでは作品を排除しない。conflictsなし・present/workラベルなしはabstainedとして区別し、書誌表示を維持する。

既存guardの抽出recipe名 `work-present-relevance-max-v1` は保持し、新implementation SHAと明示adoption policyで新しいrecipeを束縛する。モデル入力・schema・config・native receiptsは変更しない。旧recipe/DB/用途台帳を現在のrecipeへ再bindせず、新namespace・新sealed supplementを使う。raw classification保存件数、特徴採用作品数、conflict保留、abstention、品質審査保留／不確実を別々に集計する。これらは機械的採用状態であり、人手正解や精度・校正を意味しない。

API任意引数 `quality_adoption_review`（CLI `--quality-adoption-review`）は入力・完全native resultに束縛したAI内部診断を受け取る。厳密schema_versionは `next-quality-adoption-review-v1`、actorは `ai_internal_review`。run_id、ordered_inputs_sha256、run_manifest_sha256、results_sha256、execution_context_manifest_sha256を実原物へ照合する。source_auditsは絶対pathとraw SHAの非空配列。topと全caseは human_gold/authority_merge_approved/confidence_calibrated が全てliteral falseである。

caseはwork_id/input_hash/job_key/native_result_value_sha256/status/reason_codes/reasons/label_ids/source_audit_sha256と上記false flagsを要求する。jobごと1件、全native value SHA、実label IDs、宣言source audit SHAを照合する。reason codeは evidence_subject_scope_mismatch、unresolved_version_scope_conflict、semantic_entailment_uncertain。前2を含むならstatus=held、後者だけならuncertain。選択labelの削除・数値調整は行わない。

`next_quality_adoption_review` に原caseとreview raw bindingを保存し、run provenanceに完全reviewのSHAを保存する。loaderはraw sidecar、source audits、全case inventory、native result/input/runの一致を確認する。保留行の削除で黙って特徴が復活する経路を拒否する。heldの作品全体はcontent_classified経路から除き、書誌-only経路では表示を維持する。uncertainは原機械注釈として保持し、gold・authority・確率校正へ昇格しない。レビュー／原run／原入力／原resultの開始・終了一致も要求する。
