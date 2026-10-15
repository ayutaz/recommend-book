# 固定400作品のblind AI silver審査

固定契約は `config/autonomous-evaluation-v1.json`、開始前の原物SHAは `data/autonomous-poc-v1/baseline-manifest.json`。本工程は `ai_silver` であり、人手注釈・curated gold・本人評価を生成しない。同じモデルの共通誤りがあり、判定一致率は正解率ではない。既存human review packへ書き戻さない。

## 入力とblindness

`prepare_blind_inputs(review_pack, latest_inputs, canonical_crosswalk, database, output_dir, *, expected_count=400, baseline_manifest=None)` は400-balanced packの作品membershipを維持し、原WorkInputをprovenance用に別保存する。最新2020入力はSQL `DatasetDB.work_inputs()` と全件のinput hash・source referencesを照合する。crosswalkは全2020のone-to-one、旧work nodeの保持、SQL resolved/canonical relationshipを照合する。ID代表変更を元分類結果への書換えや再bindとして扱わない。

新 `blind-inputs.jsonl` に送るのは最新WorkInputのみ。元予測、confidence、peer判断、過去会話、本人ratingは含めない。`original-membership-inputs.jsonl` は旧入力のprovenanceであり、モデルへ送らない。許可フラグfalseの本文は既存prompt serializerが除外する。licence、attribution、inherited restrictionsはWorkInputのまま渡る。新namespaceの各lane/workspaceは空で開始する。

## 実行

```powershell
uv run python scripts/run_autonomous_silver.py prepare --namespace data/autonomous-poc-v1/silver/blind-v1
uv run python -u scripts/run_autonomous_silver.py run --namespace data/autonomous-poc-v1/silver/blind-v1 --pilot-count 20 --continue-full --adjudicate --timeout 900 --max-run-seconds 7200 --max-observed-tokens 5000000
```

各laneは既存 `run_classification` を独立出力で使い、2 concurrency・batch5。合計最大4 native CLI。既存認証、ephemeral、ignore-user-config、read-only sandbox、web disabled、multi-agent disabled、stdin supplied local evidenceで起動し、tool使用のeventは結果採用を拒否する。native executableは既存resolverが解決する。追加認証やAPI key収集は行わない。

初回pilotはpack順先頭20作品。両laneのcompleted20、failed/pending0、stopなしを確認してからfull400へ進む。fullは同じlaneのjobkey cacheでpilot20を再利用し、pilot reportとrun-ID付きmanifestを保持する。run budgetはlaneごと7200秒・観測5,000,000tokens、timeout900秒。rate/quota等は既存runnerが当該laneの新投入を止める。片方停止時はもう片方のin-flightを強制破棄せず、両方terminal後に次工程を止める。停止した実験を暗黙に再起動しない。

CLIのusageはturn.completed eventsの観測値であり、API請求額ではない。pilot、fullの使用量は各phaseで記録する。fullのreused20を新CLI invocationとして数えない。manifest.jsonに加えrun-ID付きmanifestとphase reportで履歴を保持する。

## 比較と裁定

`compare_silver(inputs, lane_a, lane_b, original_results=None)` は結果を現validatorで検証し、unknown/omissionとabsentを区別する。主一致率の分母はどちらかが非unknown判定をしたscoped label union。両方が省略した大量unknownを一致の分母へ加えない。missing reviewer resultはheld/missingとして別集計し、unknown一致へ数えない。

scope・expression IDも比較keyに含める。関連度は非unknown同判定の両方数値、片方null、両方nullを分け、数値同士の絶対差を報告する。raw confidenceは関連度に使わず、校正値を生成しない。判定差、関連度のexact差、conflictsのある作品を第三審査対象とする。unknown理由文の単なる言い換えは自動裁定対象にしない。

元の最新分類との不一致は両blind結果を固定した後だけに比較する。変更入力57件の分類は既存delta結果から、その他は旧DBで同一ID/input hashのcompleted結果だけを取得し、別hash結果を再bindしない。

第三laneは二重審査の終了後に最大2CLIで実行する。これもblindの同じWorkInputに対する独立判断であり、元結果/peer結果を見せない。`adjudicate_silver` はthirdと一方の判定・関連度がexact一致した場合に、当該thirdの検証済み結果を選ぶ。両方と異なる、conflict、欠測ならheldのまま残す。複数結果のラベルを継ぎ足して架空の分類を作らない。元結果や推薦snapshotへ自動適用しない。

## 実行開始の観測

- 新namespace: `data/autonomous-poc-v1/silver/blind-v1`
- membership400、旧pack入力から変更57、canonical representative変更21。
- blind input SHA256: `00de4d504bac291dc99736cfad03cbb8793c78fcfe63b024c68fbc530824a262`
- native orchestration handle: unified exec session `22605`。最初に観測したstageは `pilot_starting`。
- preparationでprotected source/core/baseline SHA前後一致。新入力のSQL hashとnode crosswalkを照合済み。

この節は開始記録であり、400件審査や裁定が完了したという記録ではない。terminal観測はphase report/manifest/resultに基づき後で追記する。

### Pilot20 terminal

両laneとも20 completed、failed/pending0、4 CLI calls、stopなし。A run IDは `64a827847af3498c8e8b993899ff0a95`、Bは `b6a356eabb8f4bc995f4134c1573f4f2`。`pilot-lanes-report.json` に各phaseのmanifestを保持し、`pilot-terminal-audit.json` でinput/config/job/durable/index/eventを再照合した。両laneともreturncode0が4、tool calls0、event errors0。

Aはinput109,711/output11,265/reasoning843/cached0、Bはinput109,711/output11,560/reasoning600/cached14,720 tokensをCLI eventから観測した。cache項目はinputの内訳として報告し、請求額へ変換しない。

`pilot-comparison-report.json` の非unknown scoped-label unionは36判定、同判定33（91.67%）。判定差3作品、関連度差も含む裁定対象6作品、両方allunknown8作品。両方数値関連度30対の絶対差平均0.025、片方null3対。Aは34、Bは35labelsで、全ラベルwork scope。この一致はAI間の安定性であり、正解率や全400件の最終結果ではない。

pilot成功後、同じhandleでfull400が開始した。20件の既存結果を再利用し、盲検のまま残り380件をlaneごとに分類する。

固定400件はhistorical scope cohortであり、別途保存された `data/autonomous-poc-v1/independent-audit/silver-cohort-scope-v1.json` によると、本文と作品scopeの対応が保留された15件のうち3件を含む。live標本・入力を変更せず、その3件もAI間安定性の観測に含める。最終推薦eligibleで15件を除外する処理は別工程であり、この審査によってscope保留を解除しない。

`audit_silver_run(output_dir, inputs)` はterminalのread-only検査API。実DBや分類へ書戻さず、index/durable/job/config/input、events usage/exit/toolと引用所属を検証する。full時に再利用したpilotのunique eventsも一度ずつ数えるので、phase usageを合計した値と混同しない。

### Full400 terminal / 第三審査開始

同じhandle `22605` で両lane400 completed、failed/pending0、stopなしを観測した。A run IDは `7968b4daec1e405eb3b4953f1a41f64c`、Bは `f1e8c3dd622e43a6a5946504d1e2eb79`。各full phaseはpilot20を再利用し76 CLI calls。`lane-a-full-terminal-audit.json` と `lane-b-full-terminal-audit.json` は、それぞれpilot込み80 unique attemptsの全returncode0、tool/event errors0、index/durable/job/input/config一致、読み取り前後SHA一致を確認した。

非unknown scoped-label union726対の同判定641対（88.29%）、判定差55作品、関連度またはconflictも含めた第三審査対象120作品。双方allunknown195作品。数値関連度593対の絶対差平均0.03288、片方null36対、両方null12対。元latest分類との同一input hash比較は400件で、判定・関連度vectorの差はA/Bとも114件だった。A685/B682 labels、すべてwork scope、文体ラベル0。いずれも機械検証とAI間安定性の観測であり、accuracyや校正済み評価ではない。

pilotを含むunique CLI events usageは、A input2,206,985/output227,665/reasoning11,781/cached808,832、B input2,205,553/output229,762/reasoning10,771/cached840,832。第三審査はfull terminal後に120件・2 concurrency・batch5で開始した。この節は第三審査完了を意味しない。

### 第三120 terminal / 最終保存

同じ top session `22605` の `write_stdin` completion（chunk `c03e77`）で実exit code0を取得した。第三run IDは `93610f59e02c4a998c49a0464a1f8da4`、120 completed、failed/pending0、24 CLI calls、stopなし。`adjudication-terminal-audit.json` は全24 unique attemptsのreturncode0、tool/event errors0、index/durable/job/input/config一致と読み取り前後SHA一致を確認した。

裁定は blind_lanes_agree280、third_supported_lane_a26、third_supported_lane_b26。332件の検証済みAI結果を保持し、third_differs_both48・third_conflicts20の計68件を未解決のまま保持した。保持結果にはallunknownの一致も含むため、332件を根拠十分な分類件数や正解件数として扱わない。`ai-silver-report.json` は元の検証済み結果とthird結果を区別し、raw値を変更せず保存する。推薦snapshotや既存human packへ自動適用していない。

第三120の入力は固定400のraw line bytes/orderを維持した `adjudication-inputs.jsonl` に保存した（SHA256 `9b2cc5933fc0c5883fd28c351b7341596666c940d9172fea8e2ed7d2257de1c6`）。実第三開始後に、実際のselectionと同一であることを照合して作った入力snapshotであり、batch stdin捕捉ではない。full400の原物は変更していない。

`orchestration-terminal-observation.json` と各laneの `terminal-proof.json`（classification-terminal-proof-v1）は、同じtop終了観測をrun ID、manifest/results/input raw SHAへbindする。topプロセスの終了と各native CLI attemptの終了は区別し、後者はsaved job/event auditで確認する。A/Bのproofは再利用したpilot run IDとimmutable pilot manifestのSHAも保持する。

pilotを一度だけ含む3lane全184 unique CLI eventsの観測usageは input5,112,595/output577,034/reasoning28,431/cached1,939,584/cache-write0 tokens。第三だけではinput700,057/output119,607/reasoning5,879/cached289,920。この値をAPI請求額へ換算しない。

最終集計は `terminal-stability-report.json`（SHA256 `aa8f2a867f5f82358ea7219643f3ecce804b22306af3040dd3bee951627b6cb0`）。準備時の27 source SHA、classifier core10とその保存copyを最終時点に再照合し、不変を確認した。historical cohortのscope-conflict holds3件は解消しておらず、最終推薦eligibleの15件除外とは別の安定性観測である。監査で追加LLM/Web/本人評価を起動していない。

専用/既存classifier/jobsテスト44 passed。親レーンの最終全体テストは1132 passed、source170の前後SHA一致という別観測。終了後のmodule/script/test変更はなく、最終記録後は所有4ファイルとsilver出力をfreezeする。
