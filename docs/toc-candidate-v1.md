# ToC候補 v1（2万分類完了後のローカル利用状況）

更新: 2026-10-24（JST）。公式条件の確認日は2026-10-22のまま。今回は作業結果と次の計画を反映し、新しい許諾判断は行っていない。

これは、ローカル試作からToC提供へ進むための利用条件の候補表である。公開版の利用規約や公開準備完了の判定ではない。2万作品の分類・監査・推薦動作確認と両OS CIは完了し、main反映が残っている。公開構成は未デプロイである。次の工程は[計画v21](autonomous-next-phase-plan-v21.md)を参照する。

## 対象と前提

検索・詳細分類・推薦画面で扱う product data と、分類に入力する source data を分ける。ライセンス、サービス条件、適用法、プロジェクトの採用条件も分けて記録する。`unknown` は拒否ではないが、用途に対応する受領証がないものを許可済みとは扱わない。本文、画像、音声、レビュー、人物の個人情報を、書誌メタデータの許可から推定しない。

現在の作業ブランチは検索可能100,370作品、詳細分類20,000作品、表示特徴あり18,456作品で、[結果文書](scale-catalogue-v4.md)に最終監査・利用量・実HTTP・画面の確認範囲をまとめた。以下で参照するv19の保存値（検索可能100,368、資料ジャンル候補26,079、詳細分類10,000、表示特徴あり9,375）は前段階の記録である。いずれもToCの許諾済み作品数を意味しない。

## 目的別の候補

| 実際の source / product data | AI入力 | 表示 | 再配布 | 商用 | 証跡と現在の扱い |
|---|---|---|---|---|---|
| Wikidata の構造化 entity metadata（作品、著者、識別子、資料ジャンル等） | **候補可**。構造化フィールドだけを入力し、QID・入力 hash・範囲を残す | **候補可**。出典リンクと source label を表示する | **候補可**。生成した構造化フィールドと provenance に限定する | **候補可**。同じ範囲と証跡を維持する | `data/autonomous-next-v3/scale-v1/source-conditions.json`、`data/autonomous-next-v3/scale-100000-v1/acquisition/source-conditions-v19.json`。Wikidata の構造化データの条件を、リンク先本文へ広げない。 |
| 個別に審査・採用した Wikipedia 記事の抜粋と、それから生成した feature | **採用済み範囲で可**。対象 revision、screen、入力 hash、作品IDを保持する | **採用済み範囲で可**。実画面で revision、CC BY-SA、履歴、変更、source credits を表示できることを確認済み | **採用済み範囲で候補可**。抜粋・派生 feature・provenance に限り、出典ごとの attribution と ShareAlike を保持する | **未確定**。出力の CC BY-SA 表示はあるが、商用 clearance や法的判断を意味しない | v19 最終 manifest の `wikipedia-screened-prose` receipt、usage ledger、個別 `content_sources`。画面・HTTP の最終成果物は下記「現行成果物」に記録する。 |
| 未審査の Wikipedia 本文、未審査の記事、cover image、full text、外部リンク先 | **保留**。個別 revision、権利、入力範囲の証跡がないものは送らない | **保留**。画像や全文の権利を記事抜粋から推定しない | **保留** | **保留** | 個別 receipt がない第三者資料。Wikimedia の一般条件だけを一括許可に変換しない。 |
| Open Library / NDL 等の外部書誌 metadata | **保留**。field と source ごとの条件が固定されたものだけ | **保留** | **保留** | **保留** | `data/autonomous-next-v3/rights/build-v1/official-conditions.json` は一般条件の記録であり、各 field や第三者提供データの一括許可ではない。 |
| CoNSSA の固定 metadata / XML reference roster | **評価用の限定候補**。admitted input と source binding の範囲だけ | **製品表示には未採用** | **製品データとして再配布しない** | **製品データとして商用利用しない** | `data/autonomous-next-v3/rights/conssa-admission-v1/` と `data/autonomous-next-v3/evaluation/conssa-combined-admission-v1/` の receipt / usage ledger / 個別審査。 |
| 分類済み feature、推薦 score、source label、provenance | 原資料の用途と入力 hash を保持した範囲で候補可 | **候補可**。根拠・不明・保留を分ける | **候補可**。許可された入力から生成した範囲と provenance を添付する | **保留から開始**。配布物と各依存物を確認してから決める | 作品ごとの raw / identity / schema 監査、分類入力・出力、source condition の path + SHA。出典のない要約や本文を配布しない。 |
| 個人の評価・既読状態・推薦履歴・設定 | 外部 AI に混ぜない。利用者が明示した入力だけを機能範囲で扱う | **本人のローカル画面のみ候補可** | **しない** | **しない** | 読書状態だけが `data/autonomous-next-v3/scale-v1/catalogue.sqlite` の `read_status` table に保存される。好きな作品と推薦条件は現在の画面内 JavaScript `Map` で一時保持され、永続化されない。削除・export の対象は現状この読書状態 table であり、335件の個人評価や全設定の保存・持ち出しを意味しない。 |

local / free operation は、取得元のライセンス、依存ライブラリ、外部モデル・API の条件、公開・商用の権利を一つにする言葉ではない。Python と SQLite は無償で利用できるが、プロジェクトに `LICENSE` / `COPYING` はなく、プロジェクト自体の再配布条件は別途決める。直接依存は lock の固定版で `httpx 0.28.1`（BSD-3-Clause）、`jsonschema 4.26.0`（MIT）、`mwparserfromhell 0.7.2`（MIT）、`pydantic 2.14.1`（MIT）である。依存物を配布する場合は各 package の license と notice を同梱する。既存の Codex CLI 利用は許可済みの実行範囲だが、アカウントの上限・サービス条件を超えた無制限利用や、追加 API 購入を意味しない。

## ローカルの読書状態を扱う手順と確認範囲

画面の「この端末の読書状態」から「読書状態をJSONで保存」を押すと、持ち出し用のファイルの保存操作を開始する。削除する場合は「保存後に読書状態を削除」を使い、ファイルが保存されたことを確認してから確認画面で進める。キャンセルすると削除しない。削除対象は保存された読書状態だけであり、作品・出典・分類証跡は保持する。

実サーバーを終了して別プロセスで起動し直す試験では、合成データ3件の状態とJSON出力が一致した。実Chromeでの保存では、新しく作成された185 bytesのJSONを読み、実HTTP出力との一致・スキーマ・チェックサムを確認した。その時点の読書状態は0件であり、複数件の保持・削除・競合更新は合成データで検証した。本人の読書状態の削除は行っていない。

保存先はローカル台帳の `read_status` table である。この持ち出しと削除は、既存335評価、好きな作品、推薦条件、全設定をまとめて扱う機能ではない。好きな作品と推薦条件は現在の画面内での一時保持である。利用手順は[README](../README.md#ローカルで開く)を参照。

確認記録は `data/autonomous-next-v3/scale-20000-classifications-v1/stage3/history-process-restart-v1/report.json` と、同 `stage3/history-browser-download-v1/report.json` に保存した。両試験は外部通信・モデル呼出し0である。2万分類時の再確認では入口と履歴管理モジュールのhashが一致し、画面のPythonハンドラーと持ち出し・削除・読書状態変更のJavaScriptも同一だった。画面ファイル全体は検索カードの説明文が変わっており、全体のhashが同一とはしない。対応は `stage3/final20000-http-v1/history-implementation-continuity.json` に記録した。2万分類での推薦・作品詳細・出典表示と履歴JSONの実HTTP確認、全体の最終監査は完了した。

## v19 最終成果物で確認できる Wikipedia 抜粋の範囲

確認対象は `data/autonomous-next-v3/scale-100000-v1/classification-exports-v3/scale100k-content-5001-5100-v8/manifest.json` である。manifest は次を指している。

- `usage_ledger`: `data/autonomous-next-v3/preparation/scale30k-content-runtime-v1/scale-usage-ledger.scale100k-content-5001-5100-v8.v4.json`
- `output_rights_review`: 同じ classification export ディレクトリの `output-rights-review.json`
- `output_license`: `CC-BY-SA-4.0`（出力の表示条件であり、商用 clearance や原作品の権利変更ではない）
- usage ledger の `official_conditions` に `wikipedia-screened-prose` があり、Wikimedia Terms と CC BY-SA 4.0 の個別 receipt、SHA、確認日時、個別審査が保存されている。
- ledger の 269 input records のうち、100 records に `wp:*` の screened-paragraphs evidence がある。この数は20,000全体の件数ではない。

個別採用された記事抜粋は、revision、section、`screen:wikipedia-prose-v2`、`screened-paragraphs-v1`、attribution、license URL、変更内容を `content_sources` と provenance に保持する。v19 の output rights review は、exact citation、screening、変更表示、ShareAlike output license、quote mismatch hold を確認している。

cover image、novel full text、未審査の段落はこの receipt の範囲に含めない。採用済み記事抜粋の表示実装も未確認扱いにはしない。最終画面では、30件すべてに source credits と未校正 warning があり、最初の source に revision があり、CC BY-SA 表示と attribution を確認した。

## v19 完了時の画面・HTTP成果物

以下はv19の1万分類時に保存した実測である。拡張中のDB件数や、2万分類後の再検証結果ではない。

- `data/autonomous-next-v3/scale-100000-v1/audit/scale100k-catalogue-audit-v1.json`: searchable 100,368、recommendable 26,079、native total 10,000。
- `data/autonomous-next-v3/scale-100000-v1/audit/native-10000-stats-http-v1.json`: eligible classified works 10,000、`bibliographic_history_links=0`、`content_history_links=0`。これはv19完了時の10,000 native artifact の stats fields の値であり、既存335評価の追跡結果ではない。
- `data/autonomous-next-v3/scale-100000-v1/preference-ui-final-v1.json`: 100,368件表示、10,000件分類済み、9,375件 feature-bearing、推薦30件、30件すべて source credits・未校正 warning、画面目視確認済み。個人の品質改善や独立正解率は確認していない。
- `data/autonomous-next-v3/scale-100000-v1/audit/recommendation-http-after-projection-v2.json`: theme weight 0 / 2 の2リクエストが HTTP 200、各30件。先頭項目にも Wikipedia revision、CC BY-SA、screened-paragraphs locator がある。

## 2万分類完了時の評価状態

ここでは、既存335評価の保存値と、v19完了時の10,000 native artifact の link fields を分けて扱う。`data/autonomous-next-v3/evaluation/phase15-history-v1/report.json` と `data/autonomous-next-v3/evaluation/phase15-independent-reference-v1/report.json` の保存値は、owner observation 335、既存 content-connected 2（2/335、0.597%）、new evaluation-ready sources 0、new gold pairs 0 である。一方、v19完了時の stats artifact は `bibliographic_history_links=0`、`content_history_links=0` である。両者は同じ母数でも同じ測定ではない。

2万分類完了後の追加1万作品との差分照合も完了した。既存335評価はすべて作品範囲の確証不足で保留となり、新台帳への確実な対応は0件、推薦比較の母数は0である。記録は`data/autonomous-next-v3/scale-20000-classifications-v1/stage4-history-delta-v1/report.json`に保存した。従来の内容接続2件を新台帳の接続へ加算しない。

したがって、本人向け推薦改善は評価不能で、独立accuracy、precision / recall / F1、identity quality、confidence calibrationも未測定である。title-only match、翻訳・版・巻・シリーズの類推、LLMの仮ラベルをgoldやcorrespondenceに昇格しない。新しい一次資料と作品関係・著者・識別子等の根拠が得られた対象だけ個別に照合し、評価母数が成立した場合に漏洩を確認する。根拠が変わらない335件の全件再照合は繰り返さない。

## 次に更新する確認

1. 2万分類の追加行の原資料・用途条件・入力範囲・作品対応・分類証跡は最終監査済み。次の差分拡張でも同じ区別と根拠を保持する。
2. 読書状態の持ち出し、合成データでの再起動・削除と2万分類時の推薦・画面は確認済み。関係する変更がなければ同じ確認を繰り返さない。
3. 将来配布するコード、依存ライブラリ、分類データ、source credits、利用者入力経路をrelease manifestへ固定する。現在のローカル利用準備と配布・一般公開は区別する。
4. 実際の公開構成と用途条件が成立するまで、一般公開・商用提供・「2万作品が公開可能」といった主張をしない。

## 現行の公式条件

公式一次ページは 2026-10-22（JST）に確認済みである。

- [Wikidata:Licensing](https://www.wikidata.org/wiki/Wikidata:Licensing)
- [Wikimedia Foundation Terms of Use](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use)
- [Creative Commons BY-SA 4.0 deed](https://creativecommons.org/licenses/by-sa/4.0/)
- [Creative Commons BY-SA 4.0 legal code](https://creativecommons.org/licenses/by-sa/4.0/legalcode.en)

## 参照した保存成果物

- `docs/autonomous-next-phase-plan-v21.md`（次の計画。固定済みv20は保持）
- `docs/scale-catalogue-v4.md`（2万分類の最終結果と証跡の位置）
- `docs/source-rights-review-scope-v3.md`
- `docs/personal-recommendation-current-assessment-v1.md`
- `data/autonomous-next-v3/scale-100000-v1/classification-exports-v3/scale100k-content-5001-5100-v8/manifest.json`
- `data/autonomous-next-v3/scale-100000-v1/audit/scale100k-catalogue-audit-v1.json`
- `data/autonomous-next-v3/scale-100000-v1/audit/native-10000-stats-http-v1.json`
- `data/autonomous-next-v3/scale-100000-v1/preference-ui-final-v1.json`
- `data/autonomous-next-v3/scale-100000-v1/audit/recommendation-http-after-projection-v2.json`
- `data/autonomous-next-v3/evaluation/phase15-history-v1/report.json`
- `data/autonomous-next-v3/evaluation/phase15-independent-reference-v1/report.json`
