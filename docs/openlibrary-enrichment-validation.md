# 完全ダンプ候補からの書誌補充

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-13。`scripts/enrich_openlibrary_dataset.py` は取得済みの Open Library 候補を検証し、既存 DB の読み取り専用 backup を新しい namespace に作って補充する。元 `data/poc-combined-v1/dataset.sqlite`、旧分類入力・結果、frozen snapshot を更新しない。取得、WDQS 再試行、LLM 分類、実人手確認は行わない。

## 入力と採用条件

入力は `data/openlibrary-complete-v1/candidate-records.jsonl` の435作品と951著者。全ダンプから、固定 Wikidata 候補枠の明示 ID に対応する行を抽出したものなので、世界の小説母集団を網羅する標本ではない。以前の部分ダンプ選択18レコードの条件は `docs/source-mapping-resolution.md` を参照する。

1. 全候補の元5列行の SHA-256、行の型と key、JSON の型と key を確認する。
2. normalized の全 scoped identifier を raw `identifiers` / `remote_ids` / 自身の key と照合する。題・別名・言語・著者 ref・初出年・subject evidence も raw と照合する。作品・著者候補の `work_refs`、raw `works`、`contents` は未対応として拒否し、normalized だけの ID で対応先を作らない。
3. 元 DB の Wikidata 作品に明示 scoped work ID で単一対応することを必須とする。対応先の自身 QID と P648 の作品 ID 集合を raw entity と完全一致で検証する。
4. Open Library 側の全著者と、元 Wikidata 側の全著者に、単一の authority ID 対応が必要。名前だけの一致は採用しない。共著者数の違い、共有 namespace の矛盾、複数 Wikidata 著者 ID、別の Wikidata 著者を橋渡しする ID は保留する。
5. 元 Wikidata の P50 と、保存済み著者 entity の P648 を SHA と exact locator で確認する。linked authority raw がない場合は保留する。
6. 通常の `compare_records` で、原言語、巻次、作品種別、外部作品 ID、同じ言語の題の整合性を確認する。series の位置を巻番号に変換せず、raw series / position、subtitle の Vol / Book / Part 等の未表現単位は保留する。
7. 使用する著者 authority の表示名は一致する元 Wikidata 著者から補完し、元 OL 名、照合 ID、コピー元 raw SHA/path を `extra.enrichment_audit` に残す。コピーや相互 ID を独立した著者・作品の裏付けと呼ばない。

OL の description は保存済みの原文と一致しても、未確認 prose のため `llm_input_allowed=false` を維持する。書誌補充をテーマや文体の新規判定とみなさない。未知値を新たなあらすじで埋めない。

## 出力と検証

既定の出力は `data/poc-openlibrary-enriched-v1/`。既存の出力先は拒否する。途中失敗の出力には `manifest.json` の `status=pending_validation`、`outputs_are_valid=false` を残し、検証済み成果物として扱わない。

| ファイル | 意味 |
| --- | --- |
| `dataset.sqlite` | 元 DB を backup して、採用したレコードだけ投入した新 DB |
| `accepted-records.jsonl` | raw 由来と補完 lineage を保持した採用作品・著者 |
| `held-records.jsonl` | 未解決の作品。新規作品数へ加算しない |
| `outside-or-unused-records.jsonl` | 枠外作品と、採用作品に使わない著者 |
| `selection-decisions.json` | 各作品の対応先、author check、raw SHA と採用・保留理由 |
| `raw-verification.json` | 保存済み raw と exact locator の検証記録 |
| `canonical-crosswalk.json` | 元 canonical ID と新 canonical ID、元 work node の対応 |
| `group-identities.json` | 新 DB の author / series authority token |
| `group-impact.json` | canonical ID の変更を揃えた connected group の前後比較 |
| `input-comparison.json` | 題・著者・原言語・初出年・地域・series、source refs、evidence の変更 |
| `bibliographic-work-inputs.jsonl` / `bibliographic-expression-inputs.jsonl` | 新 DB から生成した未分類入力。旧分類結果へ結び直さない |
| `protected-inputs-sha.json` | 元 DB・候補 namespace・指定した frozen / 分類ファイルの前後不変性確認 |
| `manifest.json` | 件数、採用・保留理由、DB監査、処理 script と各成果物 SHA |

採用後は、元 canonical partition が1対1で保たれ、作品数が増えず、全採用 OL 作品が確認した対応先へ接続することを検証する。元 immutable source version の全列、既存の版書誌、版から作品への関係を canonical crosswalk で照合する。`PRAGMA integrity_check` と `foreign_key_check` を確認する。元 source head の更新が新 DB 内で必要な場合も、旧 version は保持する。

group 比較は既存 freeze job の `group_identity_tokens` と `connected_groups` を使う。新しい token の追加や内部 agent ID の変化と、作品をまとめる connected partition の変化を別に報告する。過去の評価 split / group ID は変更・再利用しない。

## 実行方法

共有 API は変更しない。Python API は次の形式。`protected_paths` はファイルの集合で、元 DB の namespace と候補 namespace は自動で保護対象に含む。

```python
import json
import runpy
from pathlib import Path

api = runpy.run_path("scripts/enrich_openlibrary_dataset.py")
protected = json.loads(Path("data/poc-combined-v1/protected-inputs-sha.json").read_text(encoding="utf-8"))
report = api["enrich"](
    "data/poc-combined-v1/dataset.sqlite",
    "data/openlibrary-complete-v1/candidate-records.jsonl",
    "data/poc-openlibrary-enriched-v1",
    protected_paths=list(protected),
)
```

CLI の `--protect` はファイルまたはディレクトリを受け取る。再実行は別の新 namespace を指定し、既存成果物へ上書きしない。

```console
uv run --locked python scripts/enrich_openlibrary_dataset.py --protect data/poc-frozen-v2 --protect data/runs --protect data/prose-poc --protect data/poc-v1
```

元 SQLite に空でない WAL がある場合は停止する。安定した snapshot を使い、他の writer と同時に元データを変更しない。処理後も元ファイルの SHA が不変であることを検証する。

## TDD

実装前の失敗9件から開始。著者 namespace 矛盾、名前のみ、共著者不足、巻次・種別矛盾、raw 改変、出力先の上書き、未表現 series、複数作品への ID 対応、subtitle 巻次、失敗時の pending manifest をテストした。

レビューで normalized `work_refs` の注入が採用される反例を確認し、失敗テストを追加して修正した。raw で関係のない OL 作品に Wikidata QID を normalized ref だけで追加しても、出力先の作成前に拒否する。複数著者 QID のうち1個だけが一致する反例も、採用される失敗から保留へ修正した。

```console
uv run --locked pytest tests/test_enrich_openlibrary_dataset.py -q
```

現時点の専用テストは14件成功。実データ処理の結果は、検証完了後の manifest と合わせて以下に記録する。

## 実データ結果

2026-10-13 に新 namespace への処理を完了し、別 Python プロセスから読み取り専用で再監査した。`manifest.json` は `verified_offline_bibliographic_enrichment`、`outputs_are_valid=true`。独立した監査記録は `data/poc-openlibrary-enriched-v1/verification.json`。

| 確認 | 結果 |
| --- | --- |
| 候補 | 435作品・951著者 |
| 採用 | 264作品・192著者、計456 source version |
| 保留 / 対象外 / 未使用 | 171作品保留、作品の対象外0、未使用著者759 |
| 新 DB の投入 | 新規 source head 439、既存 source head の新版17。旧版は全件保持 |
| canonical 作品数 | 2020 → 2020、元 unresolved 26件も26件のまま |
| immutable source version | 元2675件を全列そのまま保持、新 DB の総数3131件 |
| 既存の版 | 34版の書誌と版から作品への関係を保持 |
| 元ファイル | 元 DB・分類等1737ファイルの SHA が前後一致 |
| raw 検証 | 1502ファイルの SHA、一致する5列行 / entity / claim locator |
| canonical crosswalk | 全2020件が1対1。うち97件は内部 canonical ID の表記が変化 |
| 著者 / series | agent数1454、series数213は不変 |
| connected groups | 1304 → 1304、作品の partition は完全一致 |
| group token | 41作品で変更、追加42・削除41。内部 authority ID等の変化として報告 |
| 書誌値 | 19作品で変更。題7、著者表示名9、既知でなかった初出年3 |
| 原言語 / 地域 / series 値 | 変更0 |
| DB 検証 | integrity `ok`、foreign key 違反0、空でない WAL なし |
| 実通信 / LLM / 分類結果の結び直し | 全て0 / false |

題7件は、元の不明な QID 表示を raw の作品題で補完した。初出年は未知から1937、1944、1998へ補完した。著者9件は、同じ authority の日本語表示名から `Kugane Maruyama`、`Ryohgo Narita`、`Natsume Akatsuki` への表示変更であり、著者が変わったとは判定していない。採用した authority が、同じ著者を持つ他の作品の代表表示にも適用されるため、source refs / evidence が変わらない作品の表示名変更も `input-comparison.json` に含めた。

書誌・source refs・evidence や canonical ID が変わった新入力のハッシュは、旧分類入力と別のものになる。旧分類結果の input hash や ID を書き換えず、そのまま保持した。今回の新入力に旧結果を転用する承認・分類実行は行っていない。

| 保留理由（最初に該当した理由を計上） | 件数 |
| --- | --- |
| 著者欠損 / 完全な共著者集合の不一致 | 79 |
| 著者 authority ID が未一致または曖昧 | 61 |
| 疑わしい著者 preferred label | 5 |
| 未表現 series / position | 7 |
| 著者 authority namespace の矛盾 | 10 |
| 同一 namespace の複数作品 ID | 6 |
| 未表現 subtitle の巻 / Book 単位 | 3 |
| 合計 | 171 |

この保留は人手レビュー済みという意味ではない。ID・raw・書誌上の安全条件を満たせなかった候補を列挙したもの。全ダンプの抽出完了と、保留作品の名寄せ完了は別である。

専用テストに既存の DB・resolution・normalization・group identity テストを加えた検証は72件成功。

```console
uv run --locked pytest tests/test_enrich_openlibrary_dataset.py tests/test_database.py tests/test_resolution.py tests/test_normalization.py tests/test_evaluation_group_identities.py -q
```
