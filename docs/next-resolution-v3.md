# 次段階 v3: 保存済み書誌原物による同定根拠監査

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-13。`scripts/audit_next_resolution.py` を Windows native / uv offline で実行した。新結果は `data/autonomous-next-v3/resolution/v2/`。実行 session 17041 の終了コード 0 を自身の handle から観測した。旧 DB、AI run、426 件 aggregate、入力、旧 v1 結果は変更していない。本人の追加評価・人手入力を完了条件にしていない。

## 全件結果と意味

| 対象 | 件数 | 原物から確認できた範囲 |
|---|---:|---|
| 旧 AI same 候補 | 77 | 74 件の既存 entity/canonical binding、3 件の識別子同定保留 |
| 上記 74 件の原作品 scope 未確認 | 3 | entity binding は成立、原作品としての class/scope は保留 |
| 既存 source/work scope 衝突 | 15 | 原物 P31 の版・翻訳 class を再確認、canonical 自体を edition と断定しない |
| 最新 DB unresolved | 26 | NDL edition 書誌を原物 record 単位で全件確認、original-work authority 未確定のため保留 |

74 件は Wikipedia 保存応答の正確な page ID / revision ID と pageprops.wikibase_item、相手の保存済み Wikidata entity ID が一致する。旧 source version から同じ source/source_id の current head までの parent lineage を全件検証し、両者の current canonical が一致することも確認した。これは既存結合の確認のみ。保存応答の pageprops がその過去 revision 時点でも同じだったという歴史的保証はしない。

3 件の identity 保留は、WD–WP 1 件で QID が異なること、WD–WD 2 件で異なる QID の明示的同一性資料がないことによる。題名、作者名、AI 合意だけで同定していない。77 候補の WD 側は 79 source versions 全件の書誌 claims を保存した。候補集合が旧 AI 提案由来であることは明記し、提案そのものに authority を付与しない。

entity binding と作品範囲は二層で管理する。`status=verified_existing_binding` であっても `work_scope_status=held` があり得る。追加の 3 件は written work 2、book 1 の source class で、短編 2、novel 1 の形式 claim があるが、その情報だけで原作品や版を断定しない。旧 15 件とは別 group に保存し、推薦候補除外の判断や DB mutation をこの監査から自動実行しない。明示的 literary work class がある他の 71 件も、source の class assertion の確認であり、人手による原作品同定ではない。全 relation で `original_work_identity_verified=false`、`authority_merge_approved=false`、`human_verified=false` を維持する。

## 原作品・翻訳・版・短編・作品集・シリーズ

P629 (edition/translation of)、P747 (has edition/translation)、P179 (member of series)、P361 (part of)、P527 (has part) を型付きの元 statement と locator、raw SHA、source version とともに保存する。P179 の P1545 volume/ordinal qualifier も元値を保つ。77 候補には P747 19、P179 5、P361 1 の assertions があった。これら 25 件は対象の original-work scope / existing binding を独立確認していないため `held`。translation、part、series membership を same-work equivalence に変換しない。DB の series name/ID の類似から authority QID を推定しない。作者 P50、訳者 P655、形式 P7937、言語 P407、日付 P577、ISBN、P31 も保存された元 claims の範囲で記録する。

scope 衝突 15 件は保存原物に P31 Q3331189 がある。P629 は 0 件で、P747/P179/P361/P527 も 0 件。形式 claims は novel 12、short story 3、play 1（複数形式があるので計 16）。作品形式の claim は版と原作品の関係を確定する根拠にならない。

15 件中 1 件に保存済みの追加 NDL edition metadata がある。ISBN と作者 authority の一致は版の資料として成立する一方、明示的な原作品関係は不足する。NDL primary BibResource の direct title は 1、partInformation 内の title は 14。旧 derived preferred_titles に混ざっていた短編題名を、この監査では作品集 primary title と分離した。14 題名から story work ID を作らず、edition ISBN から原作品 ID を導出しない。この追加 NDL 原物が DB source version を持つという虚偽の projection を避け、scope 結果の `additional_ndl_edition_metadata` に別保存した。

unresolved 26 件は正確な NDLBibID の primary BibResource を選び、バッチ中の別 record を排除した。primary title と計 12 件の part title を分ける。FOAF generic creator の role は unassigned、explicit dc:creator の著・訳・編・絵の文字列は別記録し、generic authority に推測で役割を結び付けない。言語、originalLanguage、volume、edition、出版情報も edition metadata のまま保持する。全保存 SourceRecord identifiers を ISBN 正規化して別 provider と照合した結果、26 件への一致は 0 件。これはローカル保存範囲の結果であり、外部書誌の不存在・網羅性を主張しない。NDL の既存作者役割 ambiguity 保留を解除しない。

## DB agent への受け渡し

`authority-metadata-relations.json` は以下を持つ。

- subject_work_id (current canonical)、subject_source_version_id、target_work_id / target_source_version_id (nullable)
- relation_type、source/source_id、raw_path/raw_sha256、assertion_locator/assertion_json (元値)
- status (`verified_existing_binding` / `held`)、reason、work_scope_status/reason、lineage (binding の場合)
- authority_merge_approved=false、human_verified=false、original_work_identity_verified=false

旧 AI same 77 は `audit.json` の `same_candidates` に保持される。確定した metadata binding と AI proposal を別フィールドにした。新 merge 0、AI same の authority 昇格 0、人手 gold 0、`accepted-merge-decisions.json=[]`。DB の唯一の writer は database agent。この監査は読み取りのみで、識別子関係や書誌 assertions を投影可能な sidecar として渡す。

## provenance・権利・検証

SQLite は URI mode=ro/immutable=1 と query_only で開いた。DB / 入力 / aggregate / scope sidecar / 監査 code / imported helpers と各使用 raw の SHA を検証し、出力直前にも再照合した。all_source_snapshot SHA は今回の監査での観測であり、過去の AI 実行の prelaunch snapshot と言い換えない。manifest の全 source/output SHA を実行後に別読み取り処理で再確認した。

記事 prose、screening text、abstract 等は新成果物・新 model 入力に取り込まない。既存 rights_policy_id は provenance のみで、新しい本文利用・表示・再配布の許可と扱わない。初回の別診断 tool 出力に旧 extra.screening.text が混ざった事実は root に通知済みであり、その後は書誌 whitelist のみを読み出している。今回の新外部取得 0、新 LLM 呼出 0、追加本文利用許可の推定 0。保存 claims を検査した結果を人手正解・precision・一般化性能と扱わない。

TDD で新 helper 不在、二層 scope 不在、map raw locator の誤りをそれぞれ RED 確認後に実装した。`uv run --offline pytest tests/test_next_resolution.py -q` は 7 passed。exact raw record 境界、generic creator、part title 分離、same 推定禁止、deprecated claim 除外、series qualifier と translation 関係の非同一性、scope 二層を検証した。全対象 77/15/26 未達の場合には監査自体を拒否する。

```powershell
uv run --offline python scripts/audit_next_resolution.py --output data/autonomous-next-v3/resolution/v2
```

結果 namespace は既存出力拒否で固定される。旧 v1 は 71/6 の一層監査を履歴として保存し、二層契約の v2 を採用する。

| 原物・成果物 | SHA256 |
|---|---|
| source DB | c08da11e2ac5a4e18a04192fffb13b8da0f3733e6ee14bf59097d1ce46f9650b |
| all source snapshot | 2bfc3205558ccf2501c70f1b6bff47e7efb869540c2e54993810e567af87ef7d |
| v2 manifest | 501f6c5a0503ae33da856e8546fefb69b035ee5743b6ccfcdb46c3c35bdac615 |
| audit.json | e58cc412b8011a2e9d020f54ebb0e62424d4f4d4af2b626cb28a6a41f0237642 |
| authority-metadata-relations.json | 4eb0adb802c8600192ff6550324d2da1290c0120492a5301601ee82a9b601e16 |
| held-records.json | af903d5b9760c01422514896b009bb3be1c8767577a5f64321eff62d2eb5780f |
| accepted-merge-decisions.json | 37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570 |

## 追加 3 件の反証照合 (v3 補足)

v2 を変更せず、`data/autonomous-next-v3/resolution/v3/supplementary-type-audit.json` に全 active source の補足を保存した。3 件とも identity binding 有効、保存 WD raw P7937 に文学形式の直接根拠がある。Q11594779 / Q11631368 は short story Q49084、Q104797833 は novel Q8261。P136/P629/P747 は 0。保存 source descriptions も形式と整合することを読取確認したが、その文字列や記事本文は新成果物にコピーしていない。

| ケース | identity | literary type | work vs version |
|---|---|---|---|
| pair_5297617fd51a99b0e6e43718 | existing binding 有効 | short story supported | strict original/version 未確定 |
| pair_d003cf507816789d35e6a338 | existing binding 有効 | novel supported | bibliographic work unit supported |
| pair_1bc4743313c69c67657838c0 | existing binding 有効 | short story supported | strict original/version 未確定 |

Q104797833 には active OL `/works/OL26419285W` があり、保存 WD P648 が一致する。保存済み 462 byte TSV 原物の先頭 `/type/work`、正確な work key、JSON `type.key=/type/work` を確認した。これは別出典の書誌的 work unit 根拠であり、最初の版・原語版・人手原作品同定の保証ではない。短編 2 件の WP derived work_kind は既存 WD 書誌の継承なので、独立 type 根拠として重複計上しない。

generic written work/book P31 だけによる edition・非小説判定、3 件の一律 novel eligibility hold / 2002 件への削減は支持しない。現在の 2005 文学候補を保つことと、strict original/version uncertainty を別記録することは両立する。原作品 scope を候補 eligibility の必須条件にする場合は、その別ポリシーと不確定理由による明示的保留として root が判断する。追加取得/LLM/merge は 0。補足 manifest SHA `0ba3fd71dd5fabbadd0b55c1df0520f665349b9049f9f27f04558cd9d11124f0`、補足 audit SHA `be70ffb908be1249a8f32ef66b0c019705470213fd8cc2cc5524912e73d7c8f8`。

## 旧 direct-native runner の新呼出拒否

工程 1 の central usage guard を迂回する旧専用 live 経路は、`scripts/run_autonomous_resolution.py` の main/run と `autonomous_resolution_recovery.recover` の入口で `RuntimeError: Archived runner: no fresh calls; guarded path only` を発生させる。CLI discovery、version subprocess、元入力読取、output mkdir/write より前に拒否する。`--prepare-only`、pending_subset、validate_batch、failure_category、write_once、offline material/partial audits、command recipes は残した。暗黙の test-only bypass はなく、新名寄せ機能自体の削除ではない。fresh call は正規の usage-guarded pipeline でのみ可能。

3 件の入口拒否テストが RED となることを確認後に変更し、prepare-only の offline テストおよび既存 recovery helper tests を含め、次の 15 件が GREEN。module subprocess と executable discovery を観測するテストで両方 0 回、output 未生成を確認した。元 recovery tests は offline helper 4 件であり、live fake-success test の削除・偽装や historical code への test-only 接続は不要だった。

```powershell
uv run --offline pytest tests/test_autonomous_resolution_runner_guard.py tests/test_autonomous_resolution_recovery.py tests/test_next_resolution.py -q
```

実 native script も新空 namespace を指定して実行し、期待する拒否の exit 1 と namespace 未作成を確認した。新モデル呼出 0。旧 raw/result/prep/data は不変。historical source snapshots は保存物として残り、現在の入口の許可根拠にしない。過去 manifest の current-code path に対する SHA を新コードへ付け替えない。過去実行の再監査では独立保存済み historical source bytes と当時の artifact binding を保持する。
