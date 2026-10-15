# 著者・シリーズを保持した400作品の分布調整

2026年10月12日。freeze v2の2,000作品を固定元とし、旧blind packを保持して評価設計を修正した。利用予定の新packは `data/evaluation/poc-frozen-v2/400-review-balanced-v1/review.html`、試行20作品は `pilot20-review-balanced-v1/review.html` である。モデル判定は両方0件、人手gold0件、実人手時間null、分類品質・校正は未評価のままである。

## 旧設計の偏りと新設計

旧設計の原言語・年代cellを順番に選ぶ方法は、稀な言語を過剰に選び、2,000作品frameの日本語35%・英語35%・その他30%を反映できていなかった。新設計では各partitionのcoarse原言語件数を先に固定し、著者・シリーズの連結グループを分割せず選ぶ。

| partition | 旧 日本語/英語/その他 | 新 日本語/英語/その他 | 新coarse比率のframeからの最大偏差 |
|---|---:|---:|---:|
| dev150 | 31 / 17 / 102 | 53 / 53 / 44 | 0.67 percentage points |
| calibration100 | 13 / 5 / 82 | 35 / 35 / 30 | 0.00 percentage points |
| holdout150 | 9 / 19 / 122 | 53 / 53 / 44 | 0.67 percentage points |

frameには1,294の著者・シリーズ連結グループがある。そのうちcoarse原言語の混合は5グループ・23作品（日本語3・英語12・その他8）、純一グループのpoolは日本語697・英語688・その他592作品であった。新設計では混合5グループ・23作品をすべて丸ごと採用できた。混合グループ除外0、作品縮小0、グループ分割0、新400作品内の260グループについてpartition間漏洩0である。author authorityが欠損した別表記等の未確認リンクまで保証するものではない。

混合グループをquota内に割り当て、純一coarse原言語のグループを1次元subset sumで補う。固定seed1729から64候補と明示的pure-only fallback1候補を作り、原言語fine・年代・genre-known・資料種類・資料深度のframeに対する平均絶対偏差を比較する。混合グループの除外作品数を最優先で最小化し、次にmetadata偏差を小さくする。実frameでは65候補すべてで正確な件数を構成でき、選択候補31、実処理約0.47秒であった。

有限探索であり、全候補に対する最適性を主張しない。構成不能の場合はエラーにし、quotaの変更・グループ分割・縮小へ暗黙に切り替えない。巨大な混合グループを除外する場合は、group/作品数・work ID・原言語別件数をmanifestへ明示する。

## metadata偏差の実測

以下は各指標内の最大絶対偏差（percentage points）である。genre-knownは許可された固定Wikidata P136資料の明示 `Genres` 欄が空でない作品で、frame784/2,000=39.2%。theme語からgenreを推定せず、モデル・人手の正しさとも区別する。資料種類は作品ごとの許可資料の存在率で、多重所属を許す。

| 指標 | dev 旧→新 | calibration 旧→新 | holdout 旧→新 |
|---|---:|---:|---:|
| 原言語fine | 23.67 → 4.13 | 30.00 → 4.80 | 29.00 → 2.27 |
| 年代 | 17.73 → 5.73 | 14.20 → 2.60 | 8.40 → 3.20 |
| genre-known | 11.20 → 4.13 | 19.20 → 1.20 | 3.87 → 4.80 |
| 資料種類 | 5.22 → 3.60 | 20.40 → 3.40 | 11.88 → 3.60 |
| 資料深度 | 5.22 → 2.55 | 12.75 → 1.55 | 11.88 → 1.70 |

holdoutのgenre-known偏差は0.93ポイント増えており、すべての指標が改善したわけではない。新400作品全体には14の原言語bucketがあるが、calibrationにベンガル語・ヒンディー語、holdoutにベンガル語は含まれない。全言語の校正、最終評価、主要ラベルの正例20件、母集団の代表性や分類精度を保証しない。

全partitionの各category件数・率・符号付き偏差は `data/evaluation/poc-frozen-v2/balance-design/old-distribution.json`、`balanced-distribution.json`、`comparison-summary.json` に保持した。

## 固定性と検証

```python
quotas = {
    "dev": {"ja": 53, "en": 53, "other": 44},
    "calibration": {"ja": 35, "en": 35, "other": 30},
    "holdout": {"ja": 53, "en": 53, "other": 44},
}
manifest = create_split_manifest(
    frozen_inputs, new_directory, group_identities=frozen_identity_map,
    language_quotas=quotas, seed=1729, balancing_attempts=64,
)
```

input/identity digestに加え、quota・候補数・seedを再利用時に照合する。quota変更で既存manifestを再利用できない。混合groupを保持するexact quota、不可能設計の拒否、明示genre判定と主題の分離、0件quota、巨大混合groupの除外記録、異なるPython hash seedでの再現性をTDDで検証した。初回3件の失敗と、追加0件quotaの1件の失敗を確認してから実装・修正した。実frameを一時directoryで再生成したmanifestは同一であった。

新split SHAは `7b4ebe9b0a1599caf155ca13dbcbade13c34269a316f4f768d09a6ed00f4ae6c`。400 pack SHAは `8a888b5b1e84c7a996a662352253b45f63b9873caf93a6686217ac89e862373f`、20 pack SHAは `ee8c0f72f6c15a7599076684b1b1aad7dac421cdb34244b223a0e34185f1f263`。元raw入力SHA `77793ab2c89983cb6174d58ce35d4d353934ac4cb73bd073aafdcae4a3c337fe` とauthority sidecar SHAへの対応を両packの `review-provenance.json` に保持する。

試行20は新devからのみ選び、日本語7・英語7・その他6作品とした。時間templateの実時間はすべてnullであり、試行はまだ実施していない。旧400-reviewと旧pilot20-reviewのpack SHA不変を確認した。WorkInput、freeze v2、benchmark runner、実DBは変更していない。
