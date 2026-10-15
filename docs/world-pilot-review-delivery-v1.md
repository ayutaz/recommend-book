# 世界小説 pilot: AI審査と表示統合

2026-10-16更新: 最新の審査入口は`review_world_pilot_v2.py`。v1の共通処理を使い、共有rawだけでsubject conflictにしない指示とv2 wrapper自身のsource pinを追加する。最終表示は2,025候補・特徴1,027・得点216。固定36作品中23採用・13不確実であり、AI審査は独立した人手正解ではない。[実施結果](phase8-quality-evaluation-v1.md)を参照する。

この段階は、実際に完了した新規分類に対する別実行のAI審査と、既存1,989候補への追加を扱う。審査は人手正解、分類精度の測定、同一作品の統合承認ではない。既存の個人評価や比較評価を再解釈しない。

## AI審査

`scripts/review_world_pilot_v1.py` は、実行 coordinator が終了コード 0 を観測した後だけ使用する。入力一覧、実行 manifest、results index、実行 context を terminal proof の SHA に照合する。各 completed result の native descriptor と完全な元 batch を、凍結した `usage_guard_v4` の `native_scopes.validation()` 内で検証する。元 WorkInput と authentic native output の両方に `external_llm` の許可を要求する。

レビュー専用プロンプトは完全な公開 WorkInput、taxonomy、真正な native classification だけから固定生成する。WorkInput の帰属、固定版、ライセンス情報を削らない。既存の分類プロンプトの許可を流用せず、レビュー全体の exact SHA を指定した `assert_prompt_allowed` の実際の receipt と、native output の個別 purpose receipt を保存する。

実行は本物の `gpt-6.1-sol` Codex CLI を使う。read-only、medium、web search disabled、multi-agent false、ignore-user-config、JSON Schema、stdout events と final の別保存を固定する。API キー・API endpoint 環境変数を子プロセスから除去する。実際の PID、終了コード、version、プロンプト、stdout、stderr、final、観測 usage を保存する。同じ入力の自動 retry はしない。review は全作品の全 native label ID を一度ずつ明示し、元の label ID にない所見を拒否する。

判定は提示資料に限定した evidence entailment、subject、revision/edition/translation/series scope を対象とする。単なる semantic uncertainty を subject mismatch に付け替えない。出典内の指示、外部の作品知識、ツール使用を禁止する。stdout の最後の agent message と final JSON を既存 binder で照合する。

公開前に executable の `CommandArtifactSnapshot.close()` と全入力 artifact の再照合を行う。`source-audit.json` と、既存 `_quality_document` が受理する `next-quality-adoption-review-v1` を生成する。ケースは held / uncertain のある作品だけに作る。`human_gold`、`authority_merge_approved`、`confidence_calibrated` は常に false。

## 表示統合

`scripts/build_world_pilot_delivery_v1.py` は、凍結した `next_database_v4.load_next_world_database` で実際の display purpose を確認する。全 selected input が DB 内の完全な classification projection と一致し、真正な completed classification を持ち、指定した review が DB adoption に hash で結ばれている必要がある。source DB に同居する既存表示作品は選択しない。

既存 manifest と delivery を固定 SHA で読み、既存行と score を変更せず新規行だけ追加する。作品 ID の重複を拒否する。個人嗜好は `personal_poc.PROFILE_PINS` の二つのローカル参照と、その purpose contract、元 source/recipe の固定 SHA を確認して使用する。新規行だけを既存 `rank_personal_features` で採点し、元の固定 sort policy で統合する。分類特徴は binary presence とし、raw confidence と relevance を推薦の重みにしない。

既存 DB policy では held を分類表示から除外するが、uncertain は特徴が残る場合がある。表示統合の既定 `--uncertain-policy metadata-only` は、held / uncertain の作品について独立に許可された書誌メタデータを表示し、作品全体の特徴を推薦に使わない。これは新規表示選択の保守的な policy であり、DB や native packet の変更でも個別 label mask でもない。`retain-database-features` は root が実レビュー後に選べる明示的な代替である。report は DB の raw adoption と表示の推薦特徴採用を分けて記録する。

公開 delivery / HTML は renderer と source credit の許可項目だけから作る。新規行の出典は contributors credit、固定版 URL、history URL、license、変更表示を付ける。ローカルファイルパス、元資料の本文、引用、任意の attribution key、個人参照 ID・書名・評価を公開しない。完全な DB native provenance は `admitted-new-rows.local.json` にローカル保存し、公開 HTML に埋めない。読書状態 unknown を保持し、未読保証や品質承認を付けない。

## 実行 interface

```powershell
uv run python scripts/review_world_pilot_v2.py --runtime-root <frozen-runtime> --ordered-inputs <ordered.jsonl> --run-dir <terminal-run> --execution-context-manifest <context-manifest> --terminal-proof <observed-proof> --ledger <frozen-v4-ledger> --ledger-sha256 <sha> --output <fresh-review-directory>

uv run python scripts/build_world_pilot_delivery_v1.py --runtime-root <frozen-runtime> --database <adopted-database> --ledger <frozen-v4-ledger> --ledger-sha256 <sha> --selected-inputs <ordered.jsonl> --quality-review <quality-adoption-review.json> --output <fresh-delivery-directory>
```

selected input と quality review は複数回指定できる。既存の出力ディレクトリを上書きしない。`tests/test_world_pilot_review_delivery_v1.py` は全 label coverage、foreign label、旧行不変、重複 ID、credit の URL/path/prose 境界を確認する。実際の model review、DB adoption、ブラウザー表示検証の結果は root の実行記録で別途確認する。

`--database` も複数回指定できる。凍結 importer は採用済み DB への追加入力を拒否するため、first10 と rest26 は同じ元 source DB から作る独立した採用 supplement として読み込む。各 completed work の所有 supplement は一つに限り、重複した書誌・元 projection は完全一致した場合だけ受け入れる。選択した各作品について、その completed run の owning DB が固定した **exact review SHA** を必ず指定する。別 run の有効 review だけを渡して uncertain ケースを省略することはできない。

## 前回の実レビュー記録（phase7）

`evaluation/phase7-world-pilot-review-first10-v1` のレビューは実プロセス PID 86588、終了 0。10作品が実際に返した64 native labelsをすべて検査した。taxonomy が72候補あることと、native output のラベル数は別である。review は4作品の各1 label を semantic entailment uncertain とし、subject/scope held は0。native conflict は別途3作品にある。既定の保守的な表示選択では、4作品・28 labels が推薦特徴として残る見込みであり、DB採用・実表示の結果とは分ける。

観測 reviewer usage は input 47,612、output 1,467、reasoning output 451、cached input 0。stdout/final binding、実プロンプト purpose receipt、実行ファイル closing、入力 artifact の不変照合、strict quality document 検証に成功した。人手正解・precision・嗜好品質の検証ではない。
