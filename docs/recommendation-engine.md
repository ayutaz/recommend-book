# 推薦 engine の数式と候補条件

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

`src/recommend_book/recommendation/engine.py` は、domain adapter が用意した特徴と読書記録をローカルで比較する。HTTP、外部 LLM、Codex CLI、DB 書き込みは使わない。世界作品と narou の特徴対応・外部 ID 対応は adapter の担当であり、engine はタイトルや記憶から作品・嗜好を補完しない。

```python
from recommend_book.recommendation.engine import rank_candidates

result = rank_candidates(
    candidates, references, method="personal", limit=20,
    read_history=None, require_confirmed_unread=False,
)
```

## row と戻り値

候補・参照は `work_id` が必須の dict の list。共有 row は `title`、`features: dict[str, float >= 0]`、`rating: None | 1..5`、`author_ids`、`series_ids`、`read_status: read | unread | unknown`、`external_ids: list[{source, value}]`、`feature_provenance: feature_id -> list[dict]`、`popularity: None | finite number` を持つ。欠落した任意項目には空・None・unknown の既定値を設定する。rating は範囲内の有限実数を許容する。bool を数値として受け付けない。

戻り値は `{"ranking": list[row], "audit": dict}`。入力 dict を変更せず、追加項目を含む元の row を保持する。ranking には `score`、`score_status`、`explanations`、`evidence_count` を追加する。計算可能なら status は `supported`、計算できなければ `unsupported` / `score=None` とし、理由を explanation に記録する。ここで supported は入力特徴を使って数式を計算できた意味であり、人手による推薦承認や内容の正しさを意味しない。

スコア降順、同点は work_id 昇順。None は末尾で work_id 順。limit は非負整数で、0 の場合も全候補の audit を返す。references/candidates/history の明示 work_id の重複、空 ID、無効設定、非有限値、負の特徴、bool rating を拒否する。非常に大きい整数等、浮動小数点で扱えない入力も ValueError とする。特徴ベクトルは最大値で縮尺してから正規化し、1e308 程度の有限値でもノルム計算を overflow させない。

## 共通候補集合と未読状態

personal/content/popularity の全方式は同じ eligibility 判定を使う。特徴不足や人気値欠落を理由に候補集合から作品を消さない。方式間比較では limit を全候補件数以上にし、`audit.eligible_work_ids` の一致を確認できる。

以下は推薦候補から除外する。

- references と同じ work_id。同じ作品の翻訳・別 expression も work_id が同じなら除外する。
- references と同じ `(external_ids.source, external_ids.value)`。文字列の exact match のみで、別 source に同じ value があることは一致ではない。
- candidate 自身が `read_status=read`、または rating が既知。
- 明示 history の同一 work_id/external ID が `read`、または rating が既知。
- `require_confirmed_unread=True` の場合、確認済み unread 以外。

history は `[{work_id?: str, external_ids?: list, read_status?: str, rating?: number}]`。work_id または一つ以上の外部 ID が必要で、外部 ID だけの記録も受け付ける。status 省略は unknown。history に存在するだけでは既読としない。同一 ID の unread 記録は、候補が read でなければ unread 確認に使う。unknown 記録だけでは除外せず、未知の読書状態を未読へ変えない。

複数 alias 記録が一致した場合、read または既知 rating が最優先で除外する。unread と unknown が混在すれば unknown のまま扱い、strict unread の対象外とする。競合は `audit.history_conflicts`、履歴を照合した候補の元状態・history 状態集合は `read_status_basis` に保持する。明示 work_id が重複する history は入力不正として拒否する。

history 未指定の場合も candidate の明示 unread は使えるが、残りの unknown を世界全体の未読と断定しない。narou の「評価していない作品は未読」という仮定を engine から世界作品へ適用しない。`audit.read_history_supplied` と `unknown_read_status_count` でこの範囲を表示できる。

## スコア

### personal

全 rating 既知 references の平均を μ とし、特徴 cosine similarity が正の参照を similarity 降順 / work_id 昇順に最大20件選ぶ。参照の特徴不足は cosine 0 となり近傍に入らないが、その既知 rating は全体平均には含む。s_i を cosine、r_i を参照 rating とすると、

```
weight_i = s_i²
score = μ + Σ(weight_i * (r_i - μ)) / (0.1 + Σ(weight_i))
```

0.1 は分母の prior で、近傍情報が弱いと偏差を平均へ縮める。下位評価への類似は負の偏差としてスコアを下げるが、その特徴を持つ候補を hard exclude しない。近傍が一件も無い、候補特徴が空/全0、評価済み references が無い場合は score None。根拠のない平均値だけを予測として返さない。候補の rating は除外条件にだけ使い、スコア式へ渡さない。fold 評価では held-out candidate の rating を None にし、train references だけを渡す必要がある。

explanations は各近傍の reference_work_id、similarity、weight、rating、全参照平均、centered rating、スコアへの contribution、および共有正特徴の provenance を持つ。参照平均と contribution の和からスコアを再計算できる。

### content

rating 4 以上、かつ正の特徴を持つ references への cosine の算術平均を使う。特徴 centroid への cosine ではない。候補に正特徴が無いか、使える高評価参照が無ければ None。

利用可能な二つの特徴ベクトルに共有正特徴が無ければ、観測された特徴の一致が0なので cosine 0 を使う。これは資料欠落を関連度0へ変換する処理ではなく、adapter が渡した正特徴の比較である。`comparison_status=no_shared_positive_features` と explanation の理由で示す。特徴の欠落や未分類が多ければ、作品の内容自体が非類似だとは断定できない。この content score と根拠のない personal 予測 None は意味が異なる。

### popularity

candidate に保存された有限 popularity 値だけを使う。値0は観測値として有効。欠落は None とし、rating 件数・タイトル・他の評価を使って人気を推定しない。popularity の定義・母集団・時点は adapter 側の provenance で管理する。異なる domain の生値が比較可能かどうかは engine では保証しない。

## 根拠と評価上の限界

共有特徴ごとに candidate/reference の `feature_provenance` をコピーして explanation へ残す。`evidence_count` は、explanation に実在する `(work_id, feature_id, provenance record)` の重複を除いた件数。特徴があって provenance が無ければ0であり、空の根拠を捏造しない。popularity baseline の根拠件数は0。特徴量はすでに adapter が決めた値を受け取り、LLM の raw confidence を校正 confidence や ground truth に読み替えない。

author_ids/series_ids は保持するが、この初版では作者・シリーズの hard limit を掛けない。機械評価の候補集合を方式ごとに変えないためで、`diversity_limits_applied=false` を audit に保存する。既読除外は提供された明示 ID と記録の範囲であり、対応する外部 ID がない別名・別版・未解決作品まで検出した保証はない。推薦精度と人手による納得度は、この単体テストの合格と別に評価する。

## TDD

`uv run pytest tests/test_recommendation_engine.py -q` は **35 passed**。未実装 import の RED を確認してから実装した。設定/巨大整数の追加 RED と、history unknown/unread/external-only/競合の仕様補正 RED（計8 failures）を確認して修正した。

試験は個人式の cos²/平均中心化/prior、近傍20件、負嗜好の非除外、候補 rating 漏洩防止、共通候補集合、作品/別 expression/外部 ID の既読除外、世界の unknown 保持、history の未読確認・競合、content 平均と0の意味、観測 popularity の欠落、provenance、determinism、無効入力、有限極値、limit0を扱う。外部 LLM・通信は行っていない。
