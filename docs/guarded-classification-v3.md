# v3 分類の固定入力と実行証拠

2026-10-15追記: 以下は標準の固定入力・実行・採用契約である。後続のFrench別課題は[`native_genre_v1.py`](../src/recommend_book/classification/native_genre_v1.py)と[専用schema](../schemas/native-french-genre-v1.schema.json)を用い、[`prepare_native_french_runtime.py`](../scripts/prepare_native_french_runtime.py)で隔離したruntimeへ登録して実行済み。元の固定spec/runner/guard等を改変して過去結果を通す処理ではない。5入力・1バッチ・CLI1回・再試行0の[実行観測](../data/autonomous-next-v3/evaluation/phase3-french-native-pilot-v1/actual-observation.json)を保存した。原文の引用結果はローカル保存のみで、標準world72分類の品質や商用採用には加算しない。後述の標準コマンドをそのまま使ってFrench課題を再実行しない。

この文書は新しい実行経路の実装契約を説明する。旧分類、入力、レビュー、silver の結果を新版へ書き換えない。実モデル呼び出しは root の工程1独立監査と、入力・評価・呼び出し上限の固定後に root が開始する。caller が渡す `approved: true` を監査証拠に扱う入口はない。

## 非循環の準備順序

1. runner、guard、spec、派生検証器、入力抽出レシピをコード freeze する。
2. `build_run_config(model, observed_cli_version, taxonomy, batch_size, execution_spec=...)` で設定を生成する。これはローカル読取だけで、CLI の発見・起動・通信・書込をしない。CLI 文字列は実観測の完全な値を使用する。既存実観測例は `codex-cli 0.162.1` であり、短縮した `0.162.1` へ変換しない。
3. 用途台帳の `execution_recipes[SHA256(canonical_json(config))]` に `verifier_path` と `verifier_sha256` を登録する。全入力・公式条件・sourceversion・原物・用途条件の証拠も台帳へ束縛する。
4. 下記 `prepare` で新 namespace を生成する。台帳の完全な bytes SHA は設定レシピの SHA に含めず、immutable admission と実行 context に別途固定する。これにより台帳自身の execution recipe との自己参照を避ける。
5. root の独立監査が preparation manifest SHA、入力、台帳、コード、公式条件、原物、用途条件、プロンプトを照合する。監査後、root が同じ SHA を指定して `run` を開始する。

```powershell
uv run python scripts/run_guarded_classification.py prepare `
  --inputs <fixed-inputs.jsonl> --usage-ledger <approved-ledger.json> `
  --namespace data/autonomous-next-v3/classification/<new-snapshot> `
  --cli-version "codex-cli 0.162.1" --execution-spec definitions-v3 `
  --concurrency 2 --batch-size 5 --timeout 900 `
  --max-run-seconds 1800 --max-observed-tokens 500000

# root が独立監査後に開始する。上の準備処理自身は CLI を起動しない。
uv run python scripts/run_guarded_classification.py run `
  --namespace data/autonomous-next-v3/classification/<new-snapshot> `
  --preparation-sha256 <independently-audited-manifest-sha256>
```

準備は入力行 bytes/order、台帳の原本 copy、コード/schema/元 prompt 依存、単件 prompt preview と用途 receipt を保存する。単件 preview は実バッチ送信文を捕捉した証拠ではない。実バッチ送信文は runner が別途 `.stdin.utf8` に保存する。既存 namespace の上書き、入力 tree への出力、重複 identity、原物・台帳・preview・現行 code の変化を拒否する。途中失敗の directory は保留として残し、暗黙の再生成は行わない。

`root_context_artifacts` に `config/autonomous-next-v3.json` と `docs/source-rights-review-scope-v3.md` の実 SHA を記録し、原本 copy と現行 bytes の一致を再確認する。これらは工程1〜4、候補数、共有枠、pilot/full resource bounds と用途審査範囲を独立監査へ渡す operating context であり、source permission や GO bool として代用しない。root が実 control values とこの operating context の整合も監査する。

## 実行とキャッシュの拒否条件

`run_classification` と `recommend-book classify` は `usage_ledger` 必須。resource controls の誤りを先に報告し、その後に全 WorkInput の metadata、sourceversion、全 Evidence を検査する。`llm_input_allowed` が false の素材も権利確認の対象であり、true だけで許可根拠にならない。`limit=0` でも渡された入力の確認を行う。空入力は guard が拒否する。

用途台帳欠落・失効は CLI 発見より前に拒否する。CLI の `--version` は許可確認後に観測し、登録された設定レシピと完全一致する必要がある。wrapper は準備した `expected_run_config` と `expected_usage_ledger_sha256` を渡し、CLI の文字列差も `exec` 前に拒否する。

同一 run は初期台帳 bytes SHA と入力ごとの proof hash set に固定する。許可・撤回のどちらの方向でも台帳の更新には、新 namespace と新しい独立監査が必要。batch、retry、実 prompt、実行レシピ、result adoption、cache、最終 result index の各段階で現在の台帳・原物・条件を検査する。元 completed が存在しても許可の失効を無視して再利用しない。既存 unguarded manifest の namespace は変更を拒否する。

resume は既存 manifest の完全 config と、順序付き work_id/expression_id/full input hash が同一の場合だけ許可する。異なるモデル、CLI、spec、schema、taxonomy、batch size、入力集合、入力順は別 namespace が必要。新 v3 silver の pilot は `pilot-<count>/lane-a` / `lane-b`、full は `lane-a` / `lane-b` と分離し、pilot の namespace を入力数を変えて再利用しない。過去の400実行時の再開履歴は保存済み historical 仕様であり書き換えない。

実送信文は固定 spec の独立 builder が生成する完全 bytes と一致させる。work 部分だけでなく taxonomy、output identity、指示文、余分な annotation や末尾文字列も対象。実行直前にコード依存と実行バイナリの SHA を再確認する。各 subprocess は stdin UTF-8 bytes、`shell=False`、read-only、ephemeral、ignore-user-config、web 無効、subagents 無効で実行し、tool use を拒否する。

再試行は rate limit、timeout、識別可能な一過性 transport error に限り合計最大3 attempts。未知の CLI エラー、壊れた JSON/UTF-8、schema/identity/参照の誤り、不完全バッチ、tool use は保留し、架空の all-unknown を生成しない。quota/auth は新規投入を停止。観測 token 数は CLI events の値であり API 課金ではない。共有 CLI 上限8は root が各レーンの予約で管理し、初期分類は2並列/batch5。失敗した process launch は `launch_attempted=true`、`actual_cli_started=false` と分け、実 CLI invocation に足さない。

## immutable attempt と typed 派生

実行ごとに request/argv/config、バイナリと version 観測、実 stdin、raw stdout JSONL、stderr、最後の completed agent message、CLI final JSON、binding、protocol attempt、source manifest、adoption receipt、最終 attempt を保存する。attempt 原本は作成一度だけ。`manifest.json` と `results.jsonl` は最新 index であるが、`manifest.<run_id>.json` と `results.<run_id>.jsonl` は別途 immutable 保存する。

`classification-native-attempt-v3` source manifest は全 batch の originating input hashes、config SHA、固定 ledger SHA と、final/prompt/request/attempt/stdout/stderr の path/SHA を含む。guard は登録された verifier の実装 SHA を照合し、`native_attempt.verify_native_attempt` が原物を直接再読する。exit0、tool/error0、最後の agent JSON と final の canonical-value 一致、完全 prompt、固定 argv/schema、実行依存、全 batch identity と grounding を再現する。cache の単件 projection も元 batch 全件を再検証する。有効な一部だけ拾って元 batch を valid と扱わない。

追加の `run_dir` / `run_id` / `attempt_stem` と immutable `run.<run_id>.admission.json` の path/SHA が元 namespace を束縛する。全 artifact は元 run の同じ attempt stem の決められた suffix と一致し、外ディレクトリや symlink の外への逸脱を拒否する。同じ bytes/SHA のファイルでも別 run の位置へ付け替えない。DB の転記時も元 source manifest の run_dir を維持し、転載先を元実行の位置として偽装しない。

feature packet は raw classification result と別の typed 抽出レシピが必要である。source manifest を arbitrary feature dict の許可証として使わない。保留により JobStore の歴史的 completed が残る場合、現在 manifest の `usage_holds` が現在用途の不許可を示し、現在 result index から除外する。機械的な lineage/grounding の成功は分類の正解率・人手確認を意味しない。

## 候補2 families と別 native 任務

world72 taxonomy は変更しない。`definitions-v3` は定義を明確にし、元の18k抜粋 recipe を維持する。`context-v3` は同じ定義と、別に固定された最大72kの context recipe を用いる。入力 recipe は reference lane が事前固定し、モデルが本文を追加取得する処理はない。旧 prompt/schema/validator は保存し、新 spec が独立版の結果 identity と schema を検証する。旧 grounding validator を使う場合は throwaway copy の旧 version view に限り、実 result 値は書き換えない。

`native-definitions-v3` / `native-context-v3` は同じ2 families の独立研究任務で、world labels へ写像しない。出力は1人の最重要人物、age known enum `child/young/adult/mature`、profession exact free string。age/profession known は known single protagonist、各 field の exact quote と locator を必須とする。unknown は null、根拠なし、理由あり。原 codebook の age は初めの行動期を基本に、少なくとも1完全章で描かれた若い時期を選び短い回想は除く。数値境界は発明しない。章の全体を確認できない抜粋では不確実性を保持する。

native known の根拠は primary excerpt に限定し、input expression と evidence.expression_ref の完全一致を求める。expression を指定しない原作 scope では original language も一致させる。書誌や synopsis の文字列が quote と一致するだけでは native の既知判断を許可しない。

職業30カテゴリーの完全な閉集合は一次資料から確認できないため、自由文字列の exact 比較を維持し、同義語を事後追加しない。原物 book PDF、keywords XML、native source contract の SHA を native 設定へ束縛する。reference answer、カテゴリー頻度、元予測、相手判断を prompt に入れない。`?` / `other` は reference 欠損として別の分母で扱う。native narrator と world 一人称/三人称を同値扱いしない。AI silver、agreement、mechanical checks を human/curated gold、accuracy、校正済み確率へ昇格しない。

旧専用 reference/native/resolution live command は historical-only 拒否、またはこの guard 経路へ接続する。silver public runner と script は台帳と world spec を透過し、injectable runner でも事前用途確認を省略しない。旧再現コマンドは保存された historical source snapshot でのみ再現し、新 v3 経路に無権利入力の互換バイパスを作らない。
