# 校正候補の offline prediction capacity 診断 v1

`scripts/diagnose_calibration_candidates.py` は、外部で process exit を観測した後の分類結果について、追加人手校正 pack を設計するための数量上限を出力する。実装・テストは合成 fixture のみで行った。本番 run・モデル結果・人手回答はこの作業で読んでいない。

対象は main frozen 2000 の既存 calibration 100 と、固定 400 のどの connected group にも属さない未選択 pool。実行全体の selected 2020（main 2000 + canonical 古典 20）の入力・job・結果を検証するが、追加 20 は scope 件数へ加えない。元 dev 150 / calibration 100 / holdout 150、taxonomy、閾値、分類結果は変更しない。

## 測定対象と限界

各 `truth_level × label_id × original_language × evidence_kind` について次を分けて出力する。

| 出力 | 意味 |
| --- | --- |
| `metadata_upper_n` | 完成結果の有無にかかわらない対象作品数。人手 truth が decidable である数ではない |
| `returned_result_n` / `missing_result_n` | 対象作品の completed 結果あり / 宣言された terminal failure により結果なし |
| `known_prediction_n` | 当該ラベルの present または absent。unknown・ラベル省略を含めない |
| `known_prediction_with_raw_confidence_n` | known prediction のうち raw confidence を持つ数 |
| `bins.low` / `bins.high` | raw confidence の `[0,.5)` / `[.5,1]`。ちょうど .5 と 1 は high |
| `known_prediction_with_valid_five_field_context_n` | 上記のうち実行 context 5 項目が非空文字列の数 |
| `contexts` | 5 項目の組ごとの件数と bin 件数。異なる組を合算して校正可とはしない |

5 項目は `model, prompt_version, taxonomy_version, cli_version, target_language`。`target_language` は本文/根拠の言語から current classifier が導く値を検証する。scope の `original_language` と別項目であり、同じ原言語 scope の内部でも context が分かれる場合がある。原言語 unknown または target language null は保持し、校正用の既知言語へ置換しない。

`evidence_kind` は review importer と同じ、種類の集合を辞書順で `+` 連結した signature。`truth_level=evidence` は `llm_input_allowed=true` の根拠だけ、`truth_level=work` は全根拠を使う。両 truth level は別の候補上限であり、両方を足して標本数を増やせない。モデルの quote が原文と一致することは semantic support や人手 truth の証明ではない。

既存 `metrics._fit_calibrator` の条件は同じ scope 内で scored 人手 truth 40 以上、context が単一かつ全 5 項目有効、各登録 bin 20 以上。現在のコードは **40 以上の scope に少なくとも一つ 20 以上の bin があれば、その bin を登録する**。両 bin に 20 以上ある場合は両方を登録できる。そのため診断は `scope40_prediction_capacity_only`、`one_or_more_bin20_prediction_capacity_only`、`both_bin20_prediction_capacity_only` を別表示する。いずれも未来の人手 truth が decidable であると仮定した数量上限で、校正成功を意味しない。

`compatible_with_all_scored_existing_calibration_contexts` は既存 calibration の scored prediction 全体との context 一致。既存 context が混在する場合、context 別 scenario は上限の説明に留まり、既存 calibration 全体と pool をそのまま混ぜて fit できるとは表示しない。将来、人手 unknown を除いた結果として fit 条件が変わる可能性はあるが、本診断はその truth を推定しない。

known の relevance が null の場合は元値のまま検証する。relevance と raw confidence は交換しない。現分類 validator は known の raw confidence null を拒否するため、そのような入力は診断でも失敗する。confidence の bin 占有数から accuracy・precision・正解数・bin probability は生成しない。

## Terminal proof の共通契約

run の manifest は finally から書かれるため、manifest が存在するだけでは process exit を証明しない。root が実 handle の終了を観測した後、別の新規 private JSON を作成する。

```json
{
  "schema_version": "classification-terminal-proof-v1",
  "process_exit_observed": true,
  "session_id": "実際に観測した handle",
  "exit_code": 0,
  "observed_at": "UTC ISO8601",
  "run_id": "terminal manifest の run_id",
  "manifest_sha256": "raw bytes SHA256",
  "results_sha256": "raw bytes SHA256",
  "ordered_inputs_sha256": "raw bytes SHA256",
  "execution_context_manifest_sha256": "raw bytes SHA256",
  "migration_manifest_sha256": "raw bytes SHA256",
  "cli_profile_sha256_before": "固定 prepared manifest raw bytes SHA256",
  "cli_profile_sha256_after": "同じ raw bytes SHA256",
  "artifact_sha256": {"各 subsystem の入力名": "raw bytes SHA256"}
}
```

上例は schema の説明であり、実証 proof ではない。`exit_code` は bool を拒否する整数で、非ゼロも観測値として受理する。`session_id` は非空文字列または bool でない整数（共通 importer と併用する proof は文字列にする）。日時は UTC offset 0 を必須とする。proof に正解・precision・人手実施を記載しない。本ツールは歴史的な exit 観測を独立に再確認できず、観測者の attestation と raw SHA の一致を検証する。

診断が要求する `artifact_sha256` のキーは `frame, frame_manifest, identities, split, review_inputs, taxonomy, run_inputs, run_manifest, results, execution_context_manifest, migration_manifest, cli_profile`。DB importer 用 `database` 等の追加キーは許容する。proof 自身の SHA は出力へ保存するが自己参照 hash は要求しない。completed 各 job の durable file SHA は別途計算して出力し、診断前後で一致を確認する。

`--cli-profile` は **固定 prepared execution context manifest 自体**を指定する。ユーザーの設定・認証 file は読み取らない。profile の現在 raw SHA / before SHA / after SHA / execution context manifest SHA の同値を要求する。prepared manifest の `execution_context.run_config` を terminal run config と照合し、current `build_run_config()` とも照合する。`new_execution_context` という独立キーは使わない。

prepared `controls` と terminal manifest の batch size、initial concurrency、timeout / budget / rate-limit controls を照合する。10 個の source artifact の raw SHA、code root、taxonomy raw SHA、入力 provenance SHA と selected 数も照合する。prepared 状態の `prepared_not_started` 等は準備時点の記録であり、実起動の証明として扱わない。旧 processing context を新 execution context と混同しない。migration manifest は厳格 JSON と byte SHA を検証し、書誌の人手同定を推定しない。

## 拒否条件と分母

全 JSON/JSONL は duplicate key（ネストも含む）、NaN、Infinity、`1e999` 等の有限範囲外への浮動小数 overflow を拒否する。selected / completed / failed / pending、任意 held 数、artifact count、selected provenance 数、exit / session の整数位置で bool を整数として受理しない。

最初に proof と manifest を確認し、`pending=0`、`stopped=false`、stop reason なし、`completed+failed=selected=2020` を必須とする。未完 manifest は prediction bytes を読む前に拒否する。全 selected の work / expression / input hash / job key / run config が一致し、status は completed / held / invalid_cached_result のみ。failed は後二つの合計と一致させる。

completed は結果束にちょうど一件を要求する。held / invalid_cached_result は結果束に存在できず、対象 scope の `missing_result_n` と `missing_result_status_counts` へ残る。held を unknown / absent / 正解へ変換せず、全件 completed に絞って metadata の分母を縮めない。

completed の保存パスは run manifest の親を基準に、期待する `results/<jobkey>.json` へ安全に解決する。`/` と Windows `\\` の正常表記は受理し、drive・absolute・rooted・`..`・別 key・ディレクトリ外 symlink を拒否する。durable result は strict JSON で読み、結果束と内容が一致しなければ失敗する。current `validate_result` に加え model / CLI / profile / context を検証し、診断中に入力または source artifact が変わった場合も失敗する。

frame manifest の raw SHA、authority identities の全 2000 coverage、split の self SHA / input SHA / identity SHA / partition 数 / 全 identity を検証する。既存 review input 400 は固定 split の選択作品と一致し、各レコードが main frame と一致する必要がある。main 2000 の全入力も selected 2020 の入力と一致させる。

`connected_groups()` の author / series / authority union を再計算し、固定 partition 間の group leakage を拒否する。fresh pool は **固定 400 のいずれの group とも重ならない**未選択作品だけ。除外された group neighbor 数も表示する。`group_pool` に全 member IDs と missing 数を保存するが、追加 partition を割り当てない。scope 別上限が十分でも whole group の配分・人手 truth の品質・bin 成立は保証しない。

## 実行方法

追加取得、CLI モデル起動、ネットワーク接続は行わない。root が terminal を観測し proof を固定した後にのみ、次の入力を明示する。値は実行者が確定する。frame/review は既存 main2000/固定400の入力、run-inputs/results は新2020run、cli-profile は execution-context-manifest と同じ file を渡す。

```powershell
uv run python scripts/diagnose_calibration_candidates.py `
  --frame $frame --frame-manifest $frameManifest `
  --identities $identities --split $split --review-inputs $reviewInputs `
  --taxonomy config/taxonomy.json `
  --run-inputs $ordered2020 --run-manifest $terminalManifest --results $terminalResults `
  --execution-context-manifest $preparedManifest --migration-manifest $migrationManifest `
  --cli-profile $preparedManifest --terminal-proof $observedTerminalProof `
  --output $newDiagnosticDirectory
```

CLI の母数・partition サイズは 2000 / 2020 / (150,100,150) 固定。小さな fixture に限り Python API の期待値を渡している。出力先は新規または空 directory とし、全検証が終わってから `diagnostic.json` と `manifest.json` を保存する。入力・DB・固定 pack・split は書き換えない。manifest は入力 SHA、diagnostic SHA、実行 script SHA を記録する。

fixture 検証: `uv run pytest tests/test_calibration_candidates.py -q`。TDD で terminal / JSON / identity / context / artifact / Windows path / durable consistency / connected group の失敗を先に確認した。この検証は本番 run の terminal 成立や実校正可能数の確認ではない。

関連する設計資料: [数量だけの校正実行可能性監査](calibration-feasibility-v1.md)、[日本語 taxonomy 補助ガイド](human-taxonomy-v1-guide.md)。実人手の作業は [400 balanced review pack](../data/evaluation/poc-frozen-v2/400-review-balanced-v1/review.html) / [pilot20 balanced review pack](../data/evaluation/poc-frozen-v2/pilot20-review-balanced-v1/review.html) で blind 初期判定を維持し、本診断の prediction 結果を人手 truth に転用しない。
