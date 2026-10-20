# 用途を固定した参考推薦 v3

2026-10-20案内: 現在の内容推薦表示は2,038候補・特徴1,040・暫定得点219。履歴内容接続は2/335で増えていないため、同じ推薦比較は再実行していない。本人向け改善は未実証。[最新結果](phase15-bounded-poc-and-delivery-v1.md)と[実行計画v16](autonomous-next-phase-plan-v16.md)を現在地として参照する。

以下は2026-10-15の書名文字類似の固定仕様と当時の評価・表示1,989候補の記録である。書名類似と内容推薦の指標を混ぜない。

追加の本人作業・採点・20/400件レビューを完了条件にしない。保存済み335評点と世界作品の資料を使う。新しい公開・外部LLM・有料処理はこの評価器から起動しない。

旧結果は保存335、既存注釈331、欠損4、旧著者名グループ283だった。旧nested外側選択のnDCG@10は0.573995、人気基準は0.633978で、優越性は未達。再分割を独立holdoutと呼ばない。旧API由来の分類と方式選定を現在の用途許可で再認定しない。

## 固定仕様と用途

`data/autonomous-next-v3/evaluation/preregistration-v1.json` は当初の最大9設定・nested5/3比較案を保存した履歴である。API由来特徴の解析用途を確認できないため、この案の比較は未実行・保留とする。後から勝つ方式だけ採用するための設定変更には使わない。

本人のHTML exportに由来する `rating_observations.title/my_rating` と `imports` だけを読み、API `novels/novel_metadata`・あらすじ・キーワード・人気・既存LLM注釈は読まない独立経路を用意した。source SHA、READMEの来歴、SQL列と本人の各観測値を別目的台帳へ結び付ける。書名がAPIの補完titleへ置換されていないことを用途guardが確認する。元HTMLの未取得部分は未立証のまま記録し、過去のLLM入力許可を遡及認定しない。

`preregistration-owned-title-v2.json` を実順位計算前に凍結し、さらに未計算の段階で、世界作品の別名を独立した用途審査の対象にする `preregistration-owned-title-v3.json` を追加した。実行するlexicalパイプラインは最大1、パラメータ探索0、inner選定0とする。v2ファイルは変更しない。

固定ルールはNFKC・casefold・空白除去後の文字2-gram集合のJaccard係数。世界側は主titleと個別display用途が許可された別名の最大係数を各本人評点へ1度だけ適用し、`sum(similarity*(rating-3))/sum(similarity)` を参考値とする。類似度0はscore=null/unsupported。学習された内容特徴、ジャンル理解、同一作品の推定として扱わない。参考値は本人の世界作品への採点として登録しない。

別名は保存済みwork-scope SourceRecord.titlesの値、source version、raw locator、別の完全WorkInput hashを必要とする。日本語だから原題・翻訳・版の同一性を推定しない。未承認の生Wikidata alias、部分入力への付け替え、タイトル類似によるauthority mergeは禁止する。本人335と世界2020の明示ID照合は0件で、内容ラベルの本人profileとして接続できた作品は0件だった。

## 評価と出力

5-fold、seed=20261013、k=10を固定。本人側の正規化同titleをfold内へまとめ、既知の同名漏洩を防ぐ。ただしこのowned-only入力には著者・シリーズauthorityがなく、旧283グループの再利用はできない。著者・未記録シリーズの漏洩ゼロは認定しない。同titleはidentityの証拠ではない。評価対象の評点を候補から隠し、train側評点だけで参考値を作る。

全方式で同じheld評点集合を用い、lexical、ID順の未採点constant、固定乱数（sorted IDsをRandom(seed+fold)でshuffle）を全件報告する。人気・hybridはAPI列を使わないこのモードでは保留理由とmetrics=nullを残す。nDCGは2^rating−1、Precision@kの正は4以上、RecallとAPは正例0ならnull。未採点による同点が指標へ含まれるためscore supportも併記する。5foldを独立5試行として誤差を小さく見せず、探索的な既観測評点の評価とする。

`UsageGuard.check_private_reference_aggregate` の本物のUsageDecision、payload SHA、全335origin roster、現在ledger SHAを計算前後に照合する。boolや単なるallowedフラグを許可の代わりにしない。元private作品名・ID・引用・個別評点を評価JSONや画面へ出さない。世界出力の用途台帳とtyped lineage契約は別の条件であり、合成テストの成功を実品質として報告しない。

固定CVの実行は `owned-title-fixed-v1/evaluation.json` と `manifest.json` にexit0と原物SHAを記録した。335/335がlexical支持を持ったが、nDCG@10はlexical **0.452627**、未採点ID固定順 **0.497428**、乱数 **0.510569**。Precision@10はそれぞれ0.42/0.48/0.38。改善は未達である。品質指標を見た方式選定や重み修正を行わず、lexical限定の参考表示という用途を維持する。全335の正規化titleは335groupで、著者・シリーズ欠損の制限は残る。guard台帳SHAは `f95a490d424f677716a2ebca2a7ceb38904e30648286efab3a8cc89eecf6df93`、入力canonicalSHAは `e77d0e3f6eb503fa91bb10eca1fb500096a2acf8a71bc61a76a314e871c0dc2e`。元DBは変更していない。

世界の書誌と分類は別用途の完全入力を審査する。分類の全由来にheldが1つあれば引用0でも分類featureを表示へ昇格させない。style・expression・translation scopeの特徴はworkの参考順位へ使わない。旧歴史分類は新実行の代わりに再bindしない。書誌のみの一覧は未採点で併納し、未知の読書状態を未読へ変えない。画面は閲覧用で追加フォーム作業を要求しない。シリーズ集中、著者集中、根拠件数と欠損分母は観測値として保持し、未記録identityの多様性を保証しない。

## APIとCLI

`load_owned_ratings(database)`、`owned_title_spec(source_sha256,title_alias_mode=True)`、`evaluate_owned_titles(rows,guard=...,origin_ids=...,spec=...)`、`rank_owned_titles(candidates,references,limit=100)` を使用する。pure rank関数は用途許可を付与しない。productionはguardされたwrapperを通す。

`build_next_delivery(world,references=None,guard=...,private_guard=...,origin_ids=...,spec=...,mode=...,limit=100,read_history=None)` は世界用途と本人評点用途を別台帳で審査し、明示IDの履歴除外後の参考一覧と未採点書誌一覧を返す。bibliographic/aliasのtyped packetは表示値、完全入力、valuehashへ一致するものだけをguardへ渡す。rowへ無関係な分類/秘密を加えて許可済みhashを付ける経路は拒否する。`render_next_delivery` は許可済み世界metadataをHTML escapeして表示する。出力は別namespaceへimmutable保存し、既存成果物を書き換えない。

CLIは `--mode` を必須とする。`bibliographic-only` は用途審査済みの書誌と別名だけで未採点一覧を実生成する。旧分類・feature・分類由来のhashを参照せず、本人335・private ledger・specも読まない。`lexical-reference` は保存335の許可された固定ルールによる探索参考表示に限定し、分類特徴を参照しない。`classified-reference` だけが `load_next_world_database(content_classified=True)` と新分類のtyped派生証拠を要求する。内容分類が全held・未実行でも、明示 `bibliographic-only` からの納品は成立可能で、分類モードの失敗から静かに移行しない。実世界への出力はworld ledger最終seal後に行う。既存CV・固定specと旧成果物は変更しない。

分類modeでは `classification_derivation` のpayloadを元のnative結果として審査する。そのSHAは特徴dictのSHAと比較しない。特徴は別の `feature_derivation.payload={work_id,features,feature_provenance}` とvalue SHAが一致することを確認し、guardへ全batchのorigin hashes、元と同じsource manifest、正式features recipeを渡す。特徴packetのnative_resultとrecipeのnative_result SHAも元native結果へ一致させる。guardはnative artifacts/request/run/jobと公開 `extract_classification_features` の再現値を検査する。無引用の同batch入力も由来から落とさない。必要packet・manifest・recipeが欠ければ分類をheldにし、無関係な旧分類で補わない。

この境界はdatabase担当の正式 `next_run` fixtureからimporter・readerが生成したpacketで検証する。native subprocessはfixture内で完全mockしており、実世界分類の成功例ではない。正式native verifierと抽出関数を呼び、元native審査・特徴審査への引数、4種類の証拠欠落、無引用held由来、異なるnative hashを確認する。実datasetへの納品許可は別の用途sealと実run証拠に依存する。

```powershell
uv run --offline pytest tests/test_next_evaluation.py tests/test_next_delivery.py -q
uv run --offline python scripts/evaluate_next_recommendations.py --spec data/autonomous-next-v3/evaluation/preregistration-owned-title-v3.json --ledger <completed-private-ledger.json> --ledger-sha256 <fixed-private-ledger-sha> --origins <private-origin-inventory.json> --output <new-evaluation-namespace>
uv run --offline python scripts/build_next_delivery.py --mode bibliographic-only --database <completed-next-db.sqlite> --ledger <completed-world-ledger.json> --ledger-sha256 <fixed-world-ledger-sha> --output <new-bibliographic-namespace>
uv run --offline python scripts/build_next_delivery.py --mode lexical-reference --database <completed-next-db.sqlite> --spec data/autonomous-next-v3/evaluation/preregistration-owned-title-v3.json --ledger <completed-world-ledger.json> --ledger-sha256 <fixed-world-ledger-sha> --private-ledger <completed-private-ledger.json> --private-ledger-sha256 <fixed-private-ledger-sha> --origins <private-origin-inventory.json> --output <new-exploratory-namespace>
```

人手gold、分類precision、confidence calibration、世界での本人好み精度は未検証であり、この自動参考推薦の成功条件へ置き換えない。
