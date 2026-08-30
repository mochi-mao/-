# jp-stock-screener

日経225（取得できない場合はフォールバックの主要銘柄）を対象に、テクニカル指標
（移動平均ゴールデンクロス・RSI・MACD・出来高急増・52週安値からの反発）と
簡易ファンダメンタル指標（PER・PBR・配当利回り）を組み合わせた「買い候補スコア」を
毎朝自動計算し、`results/latest.json` に出力するツールです。

データ取得は無料の yfinance（Yahoo Financeの非公式ライブラリ）のみを使用しており、
費用は一切かかりません。GitHub Actions の無料枠（public repoなら無制限）で
平日朝に自動実行されます。

## セットアップ手順

### 1. GitHubアカウントを作成する

https://github.com/signup にアクセスし、無料アカウントを作成してください。

### 2. 新しいリポジトリを作成する

GitHubにログイン後、右上の「+」→「New repository」から新規作成します。

- Repository name: 例 `jp-stock-screener`
- Public / Private: **Public を選択してください**
  （Claude側が `results/latest.json` を認証なしで読み取れるようにするため。
  分析結果の銘柄スコアのみを公開する形になり、個人情報は含まれません）
- 「Add a README file」はチェックしなくてOK（このフォルダに既にREADMEがあります）

作成したら、表示されるリポジトリのURL（例: `https://github.com/あなたのユーザー名/jp-stock-screener`）を
控えておいてください。あとでClaudeに伝えます。

### 3. このフォルダの中身をリポジトリにpushする

ご自身のPCのターミナルで、このフォルダ（`jp-stock-screener`）がある場所に移動し、
以下を実行してください（`あなたのユーザー名`と`リポジトリ名`は実際のものに置き換えてください）。

```bash
cd jp-stock-screener
git init
git add .
git commit -m "Initial commit: jp-stock-screener"
git branch -M main
git remote add origin https://github.com/あなたのユーザー名/リポジトリ名.git
git push -u origin main
```

初回pushの際にGitHubのログインを求められる場合があります。ブラウザでの認証、
または Personal Access Token の入力を求められたらそれに従ってください
（GitHub Desktopアプリを使っている場合はそちらの手順でも構いません）。

### 4. 動作確認（任意）

pushが完了すると、GitHubリポジトリの「Actions」タブにワークフローが表示されます。
毎朝自動実行されるのを待たずに今すぐ試したい場合は、Actionsタブ →
「Daily JP Stock Screening」→「Run workflow」ボタンから手動実行できます。

数分後に `results/latest.json` が更新されていれば成功です。

### 5. リポジトリURLをClaudeに伝える

pushが完了したら、Claudeとの会話でリポジトリのURL
（例: `https://github.com/あなたのユーザー名/jp-stock-screener`）を教えてください。
Claude側の毎朝の通知タスク（Slack送信）を、このURLに合わせて設定します。

## スケジュール

`.github/workflows/daily.yml` で平日（日本時間 月〜金）朝7:10頃に自動実行されるよう
設定済みです。時間を変更したい場合は cron の値を書き換えてください
（GitHub Actionsのcronは協定世界時（UTC）基準です）。

## 免責事項

本ツールが出力するスコアや候補銘柄は、公開されている株価データに基づく
機械的な分析結果であり、投資助言ではありません。投資判断は自己責任で行ってください。
