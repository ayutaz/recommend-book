# ユーザー工数0のローカルPoC実行結果

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-13。[v2計画](autonomous-completion-plan-v2.md)の収集・統合・分類・評価・保存済み評価による推薦を実行した。新しい本人の採点・試読・感想・履歴入力・画面確認は0件。AIを本人や人手正解の代わりとして登録せず、未検証と保留を成果物に確定する。

これはローカルPoCの処理・検証の結果である。全世界の網羅、全分類の意味的な精度、本人への推薦品質、全素材の商用許諾、サービス公開を達成した報告ではない。旧人手品質gateは未達・未評価の履歴として保持する。

## 納品内容

| 成果物 | 実結果 | ローカル保存先 |
|---|---|---|
| 分類を含む作品DB | 歴史2,020件、旧結果1,941件＋新入力79件、3,628ラベル・4,039根拠参照 | `data/autonomous-poc-v1/database/final-v1/dataset.sqlite` |
| 推薦対象 | 作品/版scope矛盾15件を隔離し2,005件、特徴あり1,063・なし942 | `data/autonomous-poc-v1/delivery-v1/world-works.json` |
| 個人推薦候補 | 保存済み335評価を使用、採点可能731件から100件表示、読書状態はすべて未確認 | `data/autonomous-poc-v1/delivery-v1/recommendations.html` / `recommendations.json` |
| 外部注釈比較 | 実入力60/180作品、55分類完了・5保留、native別schema20件 | `data/autonomous-poc-v1/reference/completion-report.json` |
| 分類blind AI審査 | 固定400件をA/B、相違120件を第三審査。採用候補332・保留68 | `data/autonomous-poc-v1/silver/blind-v1` |
| 名寄せAI審査 | 難例400ペア＋未解決26件を処理。same77・different294・uncertain55 | `data/evaluation/autonomous-resolution-v3-aggregate-v1` |
| 推薦比較 | 8設定のnested grouped CV、選定とworldへの適用を分離 | `data/autonomous-poc-v1/recommendation-evaluation` |

DB SHA256は `c08da11e2ac5a4e18a04192fffb13b8da0f3733e6ee14bf59097d1ce46f9650b`、DB manifestは `16a518b4e2af9acb53ea4a593e1f9ffc665bebd7e0647d0d7ab303a5ef7a1050`。納品manifestは `6349e997b4e3ae4fac52b0b865ba0369ad19b30e0baba12a34dc9a98e1b28d87`。データは個人情報・条件付き素材を含むためGit対象外のprivate保存であり、push・一般公開は行っていない。

候補の原言語は既知13言語、英語717・日本語699に偏る。原言語不明39件、出版年不明468件、国/地域不明1,059件。記録された出版年の範囲は1534〜2026年だが、原刊年の全件検証や世界の年代分布の代表性を保証しない。シリーズ巻・短い小説等も含み、全件が長編小説であることを独立に確認した集合ではない。scope矛盾の隔離は、小説/版の全分類を正解と認定する処理ではない。

## 根拠補充・差分分類

特徴なし1,026作品を調べ、79作品・81Evidenceを採用、947作品は根拠・版・用途条件不足等により保留した。新Evidenceはあらすじ65・導入16。原物、revision、対象作品、帰属、license、抽出変更、4条件の履行を別台帳で確認した。

新入力79件はSol CLI、並列2・batch5で16呼出し、79 completed・failed/pending0・stoppedfalse。root session20797の実exit0を取得し、実イベント最終JSON・raw final・job・durable・index・入力/configを全件照合した。929.490秒、input499,000/output91,735 tokens。cached212,352とreasoning4,977は内数で、請求額ではない。

旧結果を新しいIDや入力SHAへ付け替えていない。DB監査は元SQL20テーブルの型付き全値、旧分類1,941件、差分79件、出典3,210版、根拠、P31原物、NDL役割の一致を確認した。未解決26件は保留、うち16件の著者/訳者曖昧性を保持し、既存2,020件への新投影は0である。

### 権利記録の不足と事後保留

旧Wikipedia根拠792件には現policyの第4条件・履行配列の記録がなく、Wikisource40件も履行配列がない。元記録や原runとの一致と、現在の全用途の条件履行は別である。旧WPのorigin内訳は637/148/7で、最後の7件は新79差分の入力にも含まれていた。したがって、全入力を現行条件で事前審査済みだったとは報告しない。

担当が7件の個別事後審査を完了した。全7の外部LLM・商用・表示・再配布は保留、既存保存はregistryに従い保持する。出典extraの条件宣言はあっても、記事履歴等の例外やwhole-section screeningを立証できず、新81とは分ける。新79結果のlabel・conflict・missing axisから当該7Evidenceへの引用は0件だったが、入力した文章のモデルへの影響が0だったという証明ではない。7件中2件は物語事項も含み、全7を純書誌としない。残りWP785とWS40の個別再審査は未実施である。原入力・結果・権利値は保存し、事後確認をprelaunchへ遡らせない。将来の再投入・商用配布の承認に流用しない。

事後審査は `data/autonomous-poc-v1/enrichment/legacy-wikipedia-used7-postrun-audit-v1/manifest.json`（SHA `cd74026193bf451abc74a218037852b9dcd4d1ef2fb743fee1271dbd25aaf269`）に結合した。7原物/revision/入力と43source SHA前後一致、追加LLM0。保留は現在の収集条件と例外の立証不足であり、侵害を認定したものではない。

## 分類と名寄せの評価

外部60作品の現taxonomy比較は、入力内の正例24/65検出（36.92%）、固定180作品の全200正例を分母にして24/200（12%）。保留5件も分母へ含める。全180中120作品は権利・版・取得条件等で未実行、目標100作品には届かなかった。常時presentは同じ65正例をすべて検出できるため、この値だけを合格やprecision/F1/accuracyとはしない。Spanishの部分抜粋と主人公4ラベルに限定した結果である。

別schemaのnative20ではage0/20、profession2/20の直接文字列一致。raw native40項目で2/40、現4正例へ定義互換に対応できた範囲は0/23。語義を結果に合わせて統合していない。検出結果は低く、世界全6軸の意味精度・関連度精度・信頼度校正は未検証である。

分類blind A/Bの既知ラベルunion726組中641組一致（88.29%）。同じモデル系列の安定性であり正解率ではない。関連度が双方既知の593組の平均絶対差は0.032884で、人手中心性MAEではない。文体のwork-scopeラベルは成立していない。固定標本400にはscope保留3件が含まれるため、最終2,005対象の品質評価へそのまま置き換えない。

第三120の実CLIは24呼出し、全120完了・失敗/未実行0。全400の結果はblind一致280・第三がAを支持26/Bを支持26・双方と相違48・第三conflict20。332件をAIの採用候補（うちallunknown187件）、68件を保留として別保存し、本DBを自動で上書きしない。A/B/第三の全184原呼出しは統合担当の別監査でも実イベント最終JSON・raw final・job・indexと一致した。[分類審査記録](autonomous-silver-run.md)を参照。

名寄せは元v3が350件の処理後に意味矛盾1応答を拒否して実exit1となった。元350を保持し、未finalized76だけを別namespaceで回復した。回復と集約監査は実exit0、全426の欠損/重複0、完了判断371・保留55。元v3を単一426件成功runとして扱わない。same77もAI提案で、authority採用・新DB merge・人手goldは0。[名寄せ実行記録](autonomous-resolution-run.md)に失敗、回復、74件の共通work IDと3件の書誌推論、引用/意味保証の限界を保存した。

## 保存済み評価による推薦と画面

保存済み335評価の後ろ向きnested CVでは、内側CVで方式を選ぶ手続きの外側nDCG@10は0.573995、人気順0.633978。人気順を上回る証拠は得られなかった。固定OOFのgroup bootstrapによる差の95%区間は[-0.102220, 0.010428]で、モデル再学習や選択全体の不確実性を再標本化した区間ではない。

全評価元の内側CVによる選定はhybrid_50。世界側には比較可能な人気指標がないためpersonalの参考順位へ戻す。なろうの人気値をworldへ付け替えず、未知の読書状態を未読へ変換しない。確認済み未読集合は0件で、全件既読という意味ではない。未評価のworld作品の評価や本人の感想を補作せず、worldの実効推薦精度は未検証として表示する。

実Chromeで初期100件、未読確認済み0件の空表示、未確認100件、全表示への復帰、推薦理由・出典リンク、入力欄0と注意書きを確認した。console warning/error0。静的HTMLにはJSONを取得する処理がなく、実DOMの100件の見出し・状態/スコア・分類表示・出典リンクと保存JSONから再構築した100件のchecksumが一致した。checksumは非暗号FNV32であり暗号学的なDOM一致証明ではない。

画面検証は `independent-audit/browser-delivery-validation-v1.json` と `browser-payload-binding-v1.json`、保存9ファイルのSHAと再生成は `delivery-reproduction-coverage-v1.json` に結合した。CUAで画像を確認したが、画像ファイルを保存済みとは記録しない。ユーザーの画面操作は要求していない。

## 再現・テスト・拡張

差分79件を別cacheディレクトリで再検証し、79再利用・新CLI分類0・結果bytes一致を確認した。実際のCLI version照会1回は行ったが、モデル実行はguardで禁止しており、試行も0。元runの16実呼出しは継承来歴として照合し、再実行費用へ二重計数しない。

推薦も別ディレクトリへ再生成して実exit0、8内容出力は全bytes一致、直接入力とbaseline16は前後不変。生成日時だけは別manifestに保持した。原物のモデル数値、旧人手pack、評価335件、classification core10を保持する。

最終全体テストはWindowsで `uv run --frozen --offline pytest -q`、**1,132 passed / 88.74秒**。170実装/設定/テストファイルの実行前後SHAとinventoryが一致した。pytest子プロセスの観測とwrapper終了は別に記録し、wrapper session77538の実exit0は `full-tests-terminal-v2.json` を根拠とする。旧1,123件の結果だけで最終全体成功とはしていない。GitHub hosted CIは未実行。

生成コマンドと再現手順は[DB](autonomous-database-guide.md)、[推薦納品](autonomous-delivery-guide.md)、[外部benchmark](autonomous-reference-run.md)、[推薦比較](autonomous-recommendation-evaluation.md)にある。既存成果物を上書きしない出力指定で再実行するための記録で、ユーザーへの作業依頼ではない。

[商用化・拡張準備](autonomous-commercial-scale-preparation.md)に用途別の成立/未立証条件、1万・3万・10万作品の資源計算を保存した。旧書誌268件/8並列と、Spanish抜粋20件/2並列の実績は別負荷である。後者と同じ抜粋分類だけの線形外挿は83.23/249.70/832.32時間で、取得・権利・版審査・利用枠待ち・他評価・保管・retryを含まない。実1万〜10万件は未収集、性能SLAや商用費用の保証ではない。

最終証拠manifestは `data/autonomous-poc-v1/evidence-manifest-v1.json`、SHA `7ee847f752a4354fa92ee0279bec64a5b4f1e24507b5b90f582657704307538e`。116成果物・baseline16・tested source170を実SHAで結合した。本実行の4系統のモデル呼出しは314回、input9,533,067/output1,155,121 tokens、cached2,758,016/reasoning53,405は内数。初期2020分類・旧268・前段の探索pilotはこの集計に含めず、cache継承を二重計数しない。

別担当の `independent-audit/final-requirements-review-v1.json`（SHA `4d801fa1be5fa691cd2696099010ec102a5e42261084567d29ba6217828779b5`）は52参照の存在/SHAとv2条件を照合した。文書commitとclean-statusを含む最終receiptを `completion-manifest-v1.json` に結合し、A〜Fの**ローカル自動PoC処理・納品を完了**する。保留と未検証はここで示した成果物の状態であり、ユーザーの追加作業待ちではない。商用公開・human品質・実10万作品の完了へ読み替えない。
