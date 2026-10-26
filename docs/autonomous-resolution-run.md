# 自動名寄せ審査 v1

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-13。既存400難例ペアと最新Open Library補充DBの未解決26件を、独立したAI審査として扱う。DB、旧人手pack、classification coreを変更せず、人手gold・precisionは作成しない。

`src/recommend_book/autonomous_resolution.py`の新モジュール未作成によるREDを確認し、引用所属・原物引用・両側の根拠・単一未解決レコードの過剰同定・第三審査・AI provenance・NDL原物record境界を検証する5テストが成功した。

旧pairの選抜は難例選抜のままであり、完全な無作為標本ではない。rule proposal・score・strata・candidate trigger・人手フォームはblind入力から除去した。bibliographic whitelistと`llm_input_allowed=true`のevidenceだけを渡す。NDL未解決26件は、保存済XMLの正確なNDLBibIDに属するtitle、alternative、creator役割、原言語、巻、出版年、extentを独立抽出する。一般description・要約を投入しない。

NDL creator authorityをauthorとして並べる既存派生表現には、訳者が混在する。原物の`dc:creator`末尾の著/訳を審査根拠へ併記する。原タイトル完全一致のcross-provider候補を保存済SourceRecords全件から探索したが26件すべて候補0。ここで内容や未知の原作品IDを推測せず、26件を根拠不足の保留として審査する。外部通信0。

4ペアpilotは`data/autonomous-poc-v1/resolution/v1`、native handle4441 exit0、Sol独立A/B各1呼出し、同意4件すべてdifferent。人手承認ではない。原物補強版v2は準備のみ。正式全件は別namespace v3とし、two blind lanesを最大2並列、同意しないcaseだけthird laneで審査する。

CLIはnative executable解決とraw CLI version、structured schema、ephemeral、ignore-user-config、read-only sandbox、空workspace、web_search disabled、multi_agent falseを使用する。tool use、event error、非0終了、不正引用、結果件数/順序の違いを拒否する。prompt/stdin SHA、argv、stdout、stderr、final、終了観測、usageをattemptへ保存する。

結果のactorはAI、provenanceは`ai_resolution_review`、human_gold=false、authority_merge_approved=false。AI sameの合意はDB mergeを承認しない。accepted authority decisionsは0として別掲する。全未解決の原物/候補不足台帳と判定理由を保持し、完了時に全426件の結果・終了handle・source/code/input SHA不変と使用量を追記する。

## 終了後の別版監査

正式v3の実行中に、sameとauthor check=differentの意味矛盾、およびcacheとraw finalの結合不足を発見した。実行中のmodule・runner・入力・schemaは変更せず、`autonomous_resolution_audit.py`と別CLIで完了済みv3を監査する。different editionは直ちに別作品とせず、両側の明示された同一work IDを確認してsameを許す。役割曖昧なNDLを含む難例は、旧派生authorを確定根拠として承認せず、別版の判定をuncertainへ保留する。原結果は保存する。

監査は入力・schema・原資料・実行コードのSHAを終了時に再照合し、全laneのraw finalとcache、各caseの順序・引用、CLI終了観測、イベント、集計usage・呼出し数・third件数の一致を検証する。promptは生成recipeとsubprocessへ渡した文字列のSHAの照合であり、外部CLIが受信したstdin bytesを別経路で捕捉した証明ではない。

v3はlane/batchごとのsingle attempt、失敗時停止で、自動retryは実装していない。content-keyの固定名を使うため、終了後監査が証明するのは残存raw attemptとの一致であり、過去の上書きがないappend-only履歴の証明ではない。後続subset用のbounded retry helperは最大3回、backoff 1/2秒、全attemptのAI履歴callback、恒久的な引用不正・tool use・認証/利用枠エラーの再試行禁止を持つ。既存v3へ遡及適用したとは扱わない。

元5テストと追加監査6テスト、計11件が成功した。追加のsummary検証は先に未実装ImportErrorによるREDを確認し、呼出し数・usage・third件数を0へ偽装したfixtureを拒否する。引用の一致は入力中に存在する証明であり、主張を意味的に支持する正解保証ではない。同一モデルの独立実行の合意率を人手precisionや一般的な同定性能へ読み替えない。

追加review後、監査出力がrun tree・原物の親・protected schema/source/codeの親へ入り込む指定を拒否し、出力先を`data/evaluation/autonomous-resolution-v3-strict-audit-v1`へ分離した。stdoutの全eventをduplicate key/nonfinite拒否JSONで読み、unique turn.completedを要求し、最後のitem.completed agent_message payloadとraw final/cacheの完全一致を確認する。final/cacheを一緒に書き換えた反例も拒否する。これらの境界・event結合テストも未実装REDから確認し、計13件が成功した。

strictの意味検査は限定的で、differentと全checks=consistentの形式上可能な出力を禁止するものではない。differentの根拠が意味的に十分か、sameに明示shared work IDがなく書誌推論のみかはAI提案の品質問題として残る。全提案のauthority承認は空配列とする。終了時source hash照合は独立した実行前snapshot観測とは異なる。またretry helperのcallback契約自体はrawのappend-only保存を保証しない。v3で残存rawを1attemptずつ結合できても、期待外を含む全file inventoryの妥当性を証明したとは扱わない。監査中のinventory不変のみを確認する。

## v3 の実停止と未完了 subset 回復

v3 は native session38383、2026-10-12 21:37:31 UTC の終了観測で **exit1**。350件をfinalizedした後、351–360のbatchに1件のsame＋contradictionsという不正semantic応答があり、既存validatorが拒否した。全426成功ではない。73 CLI呼出し、3389.615秒、input tokens 2323442、cached input tokens 338688、output tokens 255911、reasoning output tokens 11948。CLIの各provider processはexit0でも、上位の意味検証を通らないためrun成功とは扱わない。

v3とその入力・schema・原物・coreは変更しない。新`autonomous_resolution_recovery.py`は元finalized350をstrict partial監査し、元426の全caseを最新の元immutable SQLとraw XMLから再構成して完全一致を確認した。原lane、cache、stdout最終payload、final、argv/input recipe、終了観測、原usageを照合した。v3には全体results.jsonlがなく、350の個別finalを原indexとして照合する。ここでは期待raw73とcache/350 finalsを列挙し、全期待file inventoryの一致も確認した。前節のv3 generic監査のinventory限界を、このpartial専用検査へそのまま当てはめない。

未finalized76だけを元426の完全一致subsetとして、`data/evaluation/autonomous-resolution-v3-recovery-v1`へ新規作成した。native session19563。元のprompt・schema bytes・モデルgpt-6.1-sol・raw CLI codex-cli 0.162.1・2並列を維持し、元350を再LLMしない。実prelaunchのsource/code/原run全files/subset/schema/contextのSHA観測を保存してから起動し、終了後の実snapshotと比較する。これはv3実行前観測を遡って作ったものではない。

回復は各lane/job/attemptの独立directory、exclusive file creation、raw command/prompt/stdout/stderr/final/観測/hash保存を使用する。transient/rate-limit/timeoutだけ最大3attempt、backoff 1/2秒。invalid semantic・tool・auth・quotaはretryせず、decision=nullのheld ledgerに原model応答を保持する。勝手にsame/different/uncertainというAI判断を補作しない。A/Bの有効な判断が不一致ならCだけを追加し、片側不正は保留する。

別`autonomous_resolution_aggregate.py`は350原判断と76回復のcase provenanceを全426へ合わせ、欠損・重複・結果差替え・usage/attemptの0偽装を拒否する。最終集計はこのaudited-proposalsを使う。新module未作成REDを確認し、元/strict/recovery/aggregateを含む19テストが成功した。回復の実terminalと最終aggregate数値は完了後に追記する。

## 全426件の処理・保留確定

回復session19563は2026-10-12 21:53:12 UTCに実exit0を観測した。76件処理、17 CLI calls、retry0、third1件、681.971秒。input tokens478803、cached input tokens138752、output tokens48435、reasoning output tokens2335。意味不正によるdecision=nullの回復保留は0件。AIがuncertainとした30件を含むためterminal statusは`processed_with_holds`であり、全76を確定同定した意味ではない。

全件strict aggregate CLIも実exit0。元350と回復76を元426の順序へ戻し、欠損0・重複0、全426の元SQL/raw再構成一致、旧73＋新17の原stdout/final/観測と保存結果の結合、旧/新全期待file inventory、新実pre/post snapshot一致を確認した。原v3はexit1のまま保持し、成功へ書き換えていない。

| 対象 | 件数 | audited same | audited different | audited uncertain / held | A/B label一致 | third対象case |
|---|---:|---:|---:|---:|---:|---:|
| 難例pairs | 400 | 77 | 294 | 29 | 396 | 4 |
| unary未解決 | 26 | 0 | 0 | 26 | 26 | 0 |
| 合計 | 426 | 77 | 294 | 55 | 422 | 4 |

strictの追加issueはNDL派生creator役割未検証の2ペア。どちらも原modelはuncertainであり、原ラベルの上書きなしに保留理由を追加した。same77のうち74は保存入力にscope=workの共通識別子があり、3はその識別子がない書誌推論である。この構造的な共通IDの存在もsource解釈や意味の正しさを保証しない。A/Bの396/400という一致は同じモデルの判断の一致で、precisionではない。未解決26は対応authority不足という理由付き保留として処理を完了し、ユーザーに追加入力・本人評価を要求しない。

成果物は[aggregate audit](../data/evaluation/autonomous-resolution-v3-aggregate-v1/audit.json)、[426 audited proposals](../data/evaluation/autonomous-resolution-v3-aggregate-v1/audited-proposals.jsonl)、[outcome summary](../data/evaluation/autonomous-resolution-v3-aggregate-v1/outcome-summary.json)、[terminal proof](../data/evaluation/autonomous-resolution-v3-aggregate-v1/terminal-proof.json)、[artifact manifest](../data/evaluation/autonomous-resolution-v3-aggregate-v1/artifact-manifest.json)。[accepted authority](../data/evaluation/autonomous-resolution-v3-aggregate-v1/accepted-authority-decisions.json)は空配列。人手gold0・authority受理0・DB merge0を維持する。

artifact manifest SHA256は`dedd7567917255a357f4601548dcbb0f1d7cf756a4bf3c67c72c0d343c443b2c`。audit SHA256は`87a0d5c65e65e1a577cdc9a6303a2bc1d0bab17c65ddc2a4f881aa61c168d82a`、audited proposalsは`0c2b32625a36daf21df61844c6978c8339fa55542a2f5765547c3a508f85f164`、terminal proofは`29325cf8afd897ff01175195f7a32e9cf550d0658a2c18050391667f5898b6b1`。audit自身のmodule/script/testのSHAもauditへ保存し、監査開始/終了で同一を確認した。

実行commandは`uv run --offline python scripts/run_autonomous_resolution_recovery.py --original data/autonomous-poc-v1/resolution/v3 --output data/evaluation/autonomous-resolution-v3-recovery-v1 --role-audit data/autonomous-poc-v1/resolution/role-audit.json --original-terminal data/autonomous-poc-v1/resolution/v3-terminal-observation.json`。全件監査は`uv run --offline python scripts/audit_autonomous_resolution_aggregate.py --original data/autonomous-poc-v1/resolution/v3 --recovery data/evaluation/autonomous-resolution-v3-recovery-v1 --role-audit data/autonomous-poc-v1/resolution/role-audit.json --terminal-observation data/autonomous-poc-v1/resolution/recovery-terminal-observation.json --output data/evaluation/autonomous-resolution-v3-aggregate-v1`。
