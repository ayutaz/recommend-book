# 保存済み20 works / 74 labels・3 pairsの局所scope診断

2026-10-15。固定packetから実際の診断処理を実行した。**分類20 works / 74 labels、同定3 pairsを全件確認し、実質的な採用可能修正は0、全件heldを維持した。** これは準備だけの`notrun`から、保存済み構造化資料の監査完了への進捗である。新しい意味判定、同定承認、独立した品質評価の完了を意味しない。

採用出力は`data/autonomous-next-v3/evaluation/phase6-bounded-scope-repair-v1/`。`report.json`に23 casesの結果、`scope-quarantine-overlay.json`に、held workに属する74 labelsと3 pairsの継承・merge禁止案を追加保存した。**overlayを既存display/rankingへ適用しておらず、productionの採用挙動は変更していない。** nativeのpresent/unknown、旧finding、missing axes、旧DB・raw・ledgerも変更していない。work単位のcontainmentから個別labelが誤りだとは推論しない。labelごとの旧assessmentをnew statusにも保持する実装であり、今回の選択74件は元observationでもすべてheldだった。新しい内容ラベルや修正済みsource mappingではない。

## 実行範囲と証拠

開始時に既存verifierを外部pin `deea5b26f2c4fe4579abcc592bc8acda2892b35b75019ac3aa49478d1bd2b3dd`で一度実行し、`verified=true, files=37, row_bindings=112`を確認した。診断実行時にもこのpinに対するmanifestと2 packetのbytesを照合した。選択済み20 jobだけをSQLite `mode=ro&immutable=1`、`query_only=ON`で読み、input/result/evidence/labelのhashを照合した。全作品走査、全DB hash、意味判定の再実行はない。

`diagnose_bounded_scope_repairs.py`自身はfull preparation verifierを呼ばない。開始時の一度の検証とは別に、scriptが直接再検証する範囲はpin・2 packet・参照raw hash・旧finding hash・20 classification job rowsであり、元の37 files/112 row bindings全体の鮮度をscript単独の実行成功から主張しない。reportの`verification_scope`に区別を保存した。

構造化rawから確認したのは、正確な`entities.<QID>.id`、P31/P136/P921/P50/P179/P144/P747/P629/P7937/P407のclaim ID・rank・target・locator・値hash、および**正確なrevision IDで選択した**Wikipedia page ID/pageprops QIDである。本文slot、quote、titleを新成果物へコピーしない。複数entity/pageを含むrawを先頭要素だけで判定しない。引用するlabelごとのevidence ID/locatorをpacketと照合した。

既存理由の意味判断を繰り返していない。保存済みfinding/observationはJSON pointerと値hashで保持し、構造化資料がその理由を解消できるかを確認した。新しいmodel用text入力に必要なpurpose-specific UsageGuard admissionはないため、新しい外部model呼出は0。参照packetの`execution_allowed=false`を送信許可へ読み替えていない。新規取得0、DB更新0、有料API0、本人annotation依頼0、私有履歴の参照・export0。provenanceは`llm_or_silver`、raw抽出actorはmechanical、人手goldでも独立accuracyでもない。

## 分類の全20件

各行は**held→held**。短縮したQIDは当該packetのexact source versionを指す。全ての完全なwork ID、source version ID、raw SHA、claim ID/locator、対象label ID、引用evidence IDはreportにある。

| work ID | labels | source QID | 実際に確認した構造と残る必要証明 |
|---|---:|---|---|
| `work_046bfd6fdaa246f20aad7afa` | 2 | Q1314114 | P31 Q7725634とP136 Q15637301/Q15712918が同居。novel向けジャンル証拠なしにmedium限定categoryを継承しない。 |
| `work_3e7c1aa5f574dccfffc6b0aa` | 1 | Q11310834 | P31 Q277759。旧series説明とsingle-work targetの間を結ぶvolume同定がない。 |
| `work_6036049268268b0568a5bc87` | 1 | Q136649775 | P136 Q15637301とP179 Q135496524を確認。所属seriesからvolumeの内容証明は作れない。 |
| `work_72176ef7c57946b540371594` | 6 | Q12614896 | P31 Q7725634。revision39150286/page400444のpagepropsも同QIDだが、旧disambiguation説明との衝突を解消する証明にはならない。 |
| `work_738ea67c63af25ac83aed7af` | 1 | Q136649875 | P136 Q15637301とP179 Q135496524。volume向けnovel証拠が必要。 |
| `work_7ca1d05c46a2f6016b8b29c0` | 1 | Q136649803 | P136 Q15637301とP179 Q135496524。volume向けnovel証拠が必要。 |
| `work_9cbb9844ff65a549b4f23d87` | 9 | Q10374276 | P50 Q598052/Q521613、P747 Q126726084。同QIDのrevision1378689739でも旧author/work衝突は解消しない。各author assertionとoriginal workの対応が必要。 |
| `work_9cee2111749dc1fedb3c18dc` | 1 | Q136649921 | P136 Q15637301とP179 Q135496524。volume向けnovel証拠が必要。 |
| `work_ba78e200b8fffb78840fc1f7` | 2 | Q113159272 | P31 Q7725634だけでは旧series説明とsingle-work targetの衝突を解消しない。volumeを特定するauthorityが必要。 |
| `work_1f9de0a6181b1439a4ea4dc2` | 1 | Q113547689 | P31 Q1667921/Q7725634。original workとseries/adaptationのscopeを分離する必要がある。 |
| `work_27190142dac501797418ebfb` | 4 | Q11443124 | P31 Q7725634、revision1359283232も同QID。Plotのoriginal novel/adaptation区別の明示証明なし。 |
| `work_288cb187494e85911a8fd406` | 5 | Q11647645 | P31 Q7725634、revision111254132も同QID。sectionのnovel/film scope未証明。 |
| `work_39a3da6c99d1851e6dba9c99` | 7 | Q11430142 | P31 Q7725634、revision110190000も同QID。sectionのoriginal/adaptation scope未証明。 |
| `work_3abd51667d801b143130c9e8` | 1 | Q120336260 | P747 Q132673016はedition assertion。旧film/novel説明衝突や全genreのoriginal novel帰属を解消しない。 |
| `work_3d8303340126f22c9378681b` | 6 | Q11442816 | P31 Q7725634、revision102858371も同QID。sectionのoriginal/adaptation scope未証明。 |
| `work_458e9559c946b90f221bb10f` | 7 | Q11271131 | revision110406523の2 synopsis evidenceを確認。同じpage/revisionでもsectionごとのnovel/adaptation帰属が必要。 |
| `work_46ea6c3921ee2ff255103a88` | 3 | Q11087771 | P31 Q5398426のみ。novel bindingをtitleで正当化せず、original/adaptation authorityで置換・分割する必要がある。 |
| `work_4eddfbbb2b19d09769fd71cb` | 5 | Q120333101 | P31 Q11424/Q7725634が同居。revision109599419 sectionをnovelへ帰属させる明示証明が必要。 |
| `work_58e858bfc847faf5ba0d7c4e` | 1 | Q136695603 | P31 Q1667921/Q277759と旧manhua説明の衝突。novel bindingへのoriginal/adaptation/series対応が必要。 |
| `work_803a2d249c0b35b9eb129ff2` | 10 | Q11636436 | P31 Q11424/Q7725634/Q5398426が同居。revision1355250691 Plotのnovel scopeが未証明。 |

Wikipedia synopsis evidenceを実際に持つのは10 works。全10件で正確なrevisionのpageprops QIDがWikidata sourceと一致した。残る10件の`structural_binding_matches`は`null`（当該synopsisなし）。**QIDの一致はentity bindingだけを確認し、sectionがoriginal novelの内容かを確認しない。** この区別が今回の具体的な継承拒否の根拠である。既存の追加semantic uncertainty 3 findingsも原reason codesのまま残る。

## 同定の全3件

| case ID | exact QIDs | 診断結果 |
|---|---|---|
| `pair_67b1ede8f868893c1ee8f4ee` | Q134529112 / Q130237229 | Wikipedia rawの`query.pages[39].pageprops.wikibase_item`はQ130237229、revision1365797984/page77817804。対象Wikidataの`entities.Q134529112.id`とは異なる。held→held。 |
| `pair_8b37ef1f3f4fbe17b41f7c45` | Q116826284 / Q115478551 | Q116826284のP747はQ126688303を指し、相手QIDへの関係ではない。共通author/languageや同形式P31は明示的同一性の代用にならない。held→held。 |
| `pair_e48f9f7d4710a6f7f3b6f6b3` | Q134529112 / Q130237229 | 保存raw中のauthor/date等が近くても、両exact QID間の明示的同一性はない。held→held。 |

保存raw中のP144/P629/P747を確認し、3 pairsの相手QIDへ向くcross-relation候補は0だった。この限定property検査は「全authorityに同一性が存在しない」という証明ではない。採用には、両exact QID間の明示的authority equivalenceとwork/expression/edition scopeが必要である。adaptation/edition edgeを見つけても、それ自体をsame-work equivalenceへ昇格させない。新しいauthority証拠がないのでold AI sameを採用しない。

## 回帰検証と次工程への受渡し

新規5 testsはmodule不存在のRED後にGREEN。複数entity cacheでのexact ID不一致、複数pageの先頭/parent revision誤選択、重複revisionの曖昧性、compound subject locator内の各property/claim/rank保持、変更されたcitation locator拒否を検証した。既存production codeの意味判断バグを直したと主張する変更はない。各caseの`regression`と`minimum_new_proof`は将来の具体的修正を受け入れる際の条件であり、未実装の回帰fixtureを合格済みとは扱わない。

```powershell
uv run --offline pytest tests/test_bounded_scope_repair.py -q
uv run --offline python scripts/diagnose_bounded_scope_repairs.py
```

生成は新しいworkspace内namespaceだけを許可する。再実行には別の`--output`を指定する。元の固定packetや既存出力を上書きしない。最終manifest SHA256は`bbe9f2cb2117c64fe09675918443c4f8091c509bf81a143fafeb36813af981f2`。

今回のchanged classification dataは0、changed identity bindingsも0。そのため、内容接続の比較や同じ入力の再分類で改善を測ったことにはならない。次工程はこの保留を保持したまま、別途新しい有効証拠が得られた対象だけを比較対象にする。10000件拡大の品質承認材料として、この局所23件を独立human accuracyへ転用しない。
