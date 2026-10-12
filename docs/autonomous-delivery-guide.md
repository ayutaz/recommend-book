# ユーザー入力を追加しない推薦成果物の生成

2026-10-13。[自動完了計画](autonomous-completion-plan-v2.md)の工程⑤〜⑥で使用する新しい納品処理。旧推薦v1/v2/v3を上書きしない。以下のコマンドは最終DBと選定結果が確定した後に実行する。

```powershell
uv run --offline python scripts/build_autonomous_delivery.py `
  --world-database data/autonomous-poc-v1/database/final-v1/dataset.sqlite `
  --narou-database data/reference-code/narou-personal-recommender/data/narou.sqlite `
  --annotations data/reference-code/narou-personal-recommender/data/processed/content-llm/annotations.json `
  --selection data/autonomous-poc-v1/recommendation-evaluation/selection.json `
  --output data/autonomous-poc-v1/delivery-v1 --limit 100
```

DB pathは実際のfinalize出力に合わせる。中間の書誌専用DBを分類完了DBとして渡さない。新DBの全分類入力・結果・ラベル・根拠・出典は既存world adapterが再照合する。

## 入力と方式

元評価DBと注釈は読み取り専用で使用する。既存の本人評価を集計する処理であり、新しい本人評価や履歴の入力は要求しない。元評価の一致がある作品は既存の候補除外規則で除く。未知の読書状態は未知のまま残す。

source領域の選定結果とworld領域への適用は別に記録する。選定結果がpopularity/hybridで、対象側に比較可能な人気指標の裏付けがない場合はpersonalの参考順位へ戻す。なろうの人気値をworldへ付け替えず、未知人気を0として生成しない。full-source内側CVの候補選定も、新領域での性能検証ではない。

画面には、保存済みの評価だけを使うこと、追加入力は不要なこと、worldでの好みの一致は未検証なことを表示する。候補0件時もユーザーへの履歴入力依頼を表示しない。

## 出力と検証

`world-works.json`、`world-audit.json`、`reference-audit.json`、`profile.json`、`recommendations.json`、`confirmed-unread.json`、`selection-audit.json`、`recommendations.html`、`manifest.json`を新namespaceへ保存する。個人データを含むためGit管理しない。

生成前後で固定baseline16項目と直接入力のSHAを検査する。変更があれば成果物を公開先ディレクトリへ移さない。ここでいう公開先はローカルの確定成果物であり、Web公開やpushではない。

TDDでは未実装moduleによるREDを確認してから実装し、5件のテストで人気値の領域間流用防止、ユーザー入力要求の除去、評価済み除外とunknown保持、baseline変更/path逸脱の拒否を確認した。fixtureによる成功と、最終DBを使った生成・ブラウザ検証は別の実行記録として扱う。
