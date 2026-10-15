# ユーザー入力を追加しない推薦成果物の生成

2026-10-13。[自動完了計画](autonomous-completion-plan-v2.md)の工程⑤〜⑥で使用する納品処理。最終DBを使った生成・再生成・実ブラウザ確認まで実行した。旧推薦v1/v2/v3を保持し、以下は実行に使用したコマンドである。再実行する場合は未使用の出力ディレクトリを指定する。

```powershell
uv run --offline python scripts/build_autonomous_delivery.py `
  --world-database data/autonomous-poc-v1/database/final-v1/dataset.sqlite `
  --narou-database data/reference-code/narou-personal-recommender/data/narou.sqlite `
  --annotations data/reference-code/narou-personal-recommender/data/processed/content-llm/annotations.json `
  --selection data/autonomous-poc-v1/recommendation-evaluation/selection.json `
  --output data/autonomous-poc-v1/delivery-v1 --limit 100
```

DB pathは実際のfinalize出力に合わせる。中間の書誌専用DBを分類完了DBとして渡さない。新DBの全分類入力・結果・ラベル・根拠・出典は既存world adapterが再照合する。その後、`load_autonomous_world_database` がDBに束縛されたscope保留台帳を検証し、作品扱いと版classが矛盾する対象を推薦候補から隔離する。必須台帳がないDBは拒否する。

## 入力と方式

元評価DBと注釈は読み取り専用で使用する。既存の本人評価を集計する処理であり、新しい本人評価や履歴の入力は要求しない。元評価の一致がある作品は既存の候補除外規則で除く。未知の読書状態は未知のまま残す。

元の2,020分類履歴と、隔離後の推薦対象を分ける。全active WD原物を再検査した現台帳は15 canonicalのscope矛盾を保持し、最終候補は2,005件になる。canonical全体が必ず版だと断定せず、矛盾が解決するまで保留する。79作品の新補充と重複しないため、差分分類の件数は79のまま。

source領域の選定結果とworld領域への適用は別に記録する。選定結果がpopularity/hybridで、対象側に比較可能な人気指標の裏付けがない場合はpersonalの参考順位へ戻す。なろうの人気値をworldへ付け替えず、未知人気を0として生成しない。full-source内側CVの候補選定も、新領域での性能検証ではない。

このPoCでは対象側の比較可能な人気指標を確認していない。APIの `target_popularity_comparable=True` も拒否し、呼出し側のbooleanだけで未検証の人気方式へ切り替えられないようにする。

画面には、保存済みの評価だけを使うこと、追加入力は不要なこと、worldでの好みの一致は未検証なことを表示する。候補0件時もユーザーへの履歴入力依頼を表示しない。

## 出力と検証

`world-works.json`、`world-audit.json`、`reference-audit.json`、`profile.json`、`recommendations.json`、`confirmed-unread.json`、`selection-audit.json`、`recommendations.html`、`manifest.json`を新namespaceへ保存する。個人データを含むためGit管理しない。

生成前後で固定baseline16項目と直接入力のSHAを検査する。変更があれば成果物を公開先ディレクトリへ移さない。manifestの `historical_work_count` と実候補の `work_count`、`scope_quarantined_count`、`scope_quarantine_verified` を別掲する。ここでいう公開先はローカルの確定成果物であり、Web公開やpushではない。

TDDでは未実装moduleによるREDを確認してから実装し、7件のテストで人気値の領域間流用防止、ユーザー入力要求の除去、評価済み除外とunknown保持、baseline変更/path逸脱の拒否、booleanによる人気検証の迂回拒否、scope隔離済み候補の使用を確認した。迂回拒否と歴史pool混入は反例で失敗を確認してから修正した。推薦評価の17件と合わせて24件pass。fixtureによる成功と、最終DBを使った生成・ブラウザ検証は別の実行記録として扱う。

## 最終DBを使った実行結果

finalizeはsession84614、納品生成はsession42574で実exit0を観測した。歴史2,020件、scope矛盾15件を保留、候補2,005件。候補の分類特徴あり1,063件・なし942件、既存335評価との共通特徴から採点できる731件のうち100件を表示した。確認済み未読は0件、新しい本人評価・ユーザー作業は0件である。

`delivery-reproduction-v1`への別生成もsession12361で実exit0となり、8つの内容出力は元納品と全bytes一致した。manifestの生成日時は別実行として保持する。差分79件も別cache検証で79件再利用・新LLM呼出し0・結果bytes一致を確認した。

Chromeの `http://127.0.0.1:8772/recommendations.html` で、初期100件、未読確認済み0件の空表示、読書状態未確認100件、すべてへの復帰、推薦理由と出典リンクを確認した。入力欄は0、追加評価不要とworld品質未検証の注意書きがあり、console warning/errorは0だった。ユーザーによる画面確認は要求していない。CUAの画面画像はtool出力で確認し、ファイル画像を保存済みとは記録しない。

実行・SHA・再現証拠は `data/autonomous-poc-v1/independent-audit/{finalization-delivery-terminal-v1,browser-delivery-validation-v1,browser-payload-binding-v1,delivery-reproduction-coverage-v1,cache-replay-raw-audit-v1}.json` に保存した。実DOM100件の表示値・出典リンクと保存JSONから再構築した値の非暗号checksumも一致した。画面はstatic inline HTMLで、推薦JSONをネットワーク取得して表示する経路ではない。

この候補集合には短い小説・シリーズ巻等が含まれ、全件が長編小説であることや、全世界・全年代の網羅は保証しない。旧Wikipedia根拠792件とWikisource根拠40件は旧許諾記録を保存しており、現在の全用途の条件履行を新しく認定したものではない。新Wikipedia根拠81件の条件履行と分ける。候補の本人の好み、校正、全素材の商用配布は未検証として納品する。
