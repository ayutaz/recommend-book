# relevance v2 の採用前検証

2026-10-12。関連度の契約不整合を修正する次版を、`data/development/relevance-v2` の隔離コピーで準備した。**本体への適用・実キャッシュ移行・次のLLM分類はまだ実施していない。** 旧1,000作品の分類は現在の契約で継続する。

## 準備した変更

- present/absentには根拠とraw confidenceを引き続き要求し、中心性不明のrelevance=nullを保持する。ID・入力hash・許可・scope・引用・locator・unknown・未校正の検証条件は維持する。
- validation version `2.0` と実装bytes SHAをconfigへ追加する。prompt/schema/taxonomyの版を変えず、job keyとcache namespaceを新しい検証契約に分ける。
- 終了済み旧runの入力・job・index・durable・raw final・成功イベントを照合し、batch全件が通る結果だけを新namespaceへ値を変えず移す。追加CLI呼び出し・token使用量として計数しない。
- 移行原本を`migration-manifest.json`へ保存し、将来runnerが通常manifestを更新しても保持する。旧履歴・旧usageは移行台帳へ別に記録する。
- 観測器は存在判断coverageと関連度の数値/null coverageを分ける。旧実行に記録されていないvalidation policyと、今回使った再検証policyを分けて表示する。

対象10ファイルのSHA、基準commit、テスト結果は[採用レビューmanifest](../data/development/relevance-v2/integration-review-manifest.json)に保存した。manifest SHAは`b787ca437d68efd43210edf1344a48267555bb6de3428f7e6c7bcfd553c192bd`。この開発コピーと実データはGitの追跡対象外であり、本体の実装済み変更とは数えない。

## 検証結果

隔離コピーをcwdにした`uv run --offline pytest tests -q`で**322 passed / 12.47秒**。fixtureはknown-nullの受理、他の検証条件の拒否、新しいjob key、旧completedのbyte copy、heldの値保持、batchの一部だけを採用しないこと、元データの途中変更の拒否、CLI起動を禁止した将来cache再利用を確認した。fixtureを実データの移行実績や人手正解とは数えない。

さらに、以前の観察が参照する**保存済み6 attempt・30結果出現**を読み取り専用で旧版と次版のvalidatorへ渡した。[実出力の再検証記録](../data/evaluation/poc-frozen-v2/relevance-v2-readonly-v1/observation.json)では6 batch全件が次版を通過し、12個のknown-null出現をそのまま保持した。同じ作品・ラベルの再試行を含むため、30独立作品・12独立ラベルとは呼ばない。JSON値の修正、追加LLM、job状態変更、cache移行は0。

再検証記録SHAは`df1f4a44ba27ec7b4516cb1c4a2c6a15e275922f978c8056ffb29ed4b5f62f0a`。保存済みfinalのSHA、入力ID/hash、根拠/locator/引用、agent最終messageとの値一致、tool/event error不在を確認した。ただし旧run全体のterminal manifest・history・returncode等を照合する移行適格性監査はまだ行っていない。

完了済みの2/4/8並列benchmarkを次版の観測器で各100結果、計300結果出現について再照合した。同じ固定100入力の3試行であり、300独立作品ではない。[観測manifest](../data/evaluation/poc-frozen-v2/relevance-v2-benchmark-observation/manifest.json)のSHAは`98d7ebaf6c6e8eb553f1532d29feca2052169025f360f3489cef9d46b18aca34`。knownラベルは190/189/189、すべて関連度数値あり、known-nullは各0。旧runのpolicyは未記録のまま保持し、今回の再検証version/hashを別に記録した。これは新たなLLM試行や本番1,000件の完了監査ではない。

基準snapshotの本体106ファイル（src/scripts/tests/schemas/config）と、凍結した処理context6ファイルは元SHAに一致した。人手gold0、意味的精度・関連度の正しさ・信頼度校正は未評価である。

## 後続の評価・実行条件の補強

次の採用候補には、実行条件のraw artifact保存処理と評価器の数値境界修正を追加した。[採用レビューmanifest v2](../data/development/relevance-v2/integration-review-manifest-v2.json)は16ファイルを記録し、SHAは`0764f2c1214fefedf033d0b40d073d2728cffd29871594ca2f24e5da73676f36`。隔離コピー全体のテストは**655 passed / 16.35秒**。前の10ファイル・322テストの記録を上書きしていない。

- 実行条件preparerは正確な入力・manifest bytesと、sources/taxonomy/schema/prompt/validation/runner/models/jobs/pyproject/lockの10 artifactを新namespaceに保存する。入力provenanceと新execution contextを分け、途中変更時は公開しない。`prepared_not_started`と`supplied_unverified`を保持し、実行・CLI版の実測を偽装しない。
- 評価器はgold relevanceと全predictionの関連度・採用confidenceに、nullまたは有限な非boolの0〜1を要求する。空gold・未注釈labelでも不正値を拒否し、known-nullを存在判断から除外しない。
- 校正の学習・適用で低帯`[0,0.5)`、高帯`[0.5,1]`を共有する。0.5を低帯へ誤適用しない。model/prompt/taxonomy/CLI/target_languageが非空文字列として揃わないscopeには校正器を作らない。最小scope40件・帯20件と固定分割の条件は維持する。

本体の旧106ファイルも[変更前archive](../data/development/root-before-relevance-v2/manifest.json)にraw copyで保持した。SHAは`f724175cc1cf7e95d7740474120f188fe60d6aa60679dae32fe3c4ec20c283c4`。校正実行可能性監査時の旧metricsコードSHAもこのarchiveに一致する。これは変更前ファイルの保存であり、旧runの実行policyを独立証明したことにはしない。

## 採用に残る条件

旧runの同じ実行handleがterminalになったことを確認し、停止理由と全1,000入力のmanifestを監査する。その後に本体へ修正を適用して再検証し、新しい実行contextを固定する。移行処理で旧結果を保護した新namespaceを準備し、古典20を含む固定2,020入力を同じSol・batch size・並列数で処理する。実際の移行件数・再利用件数・追加CLI量は、その実行後の成果物から確認する。
