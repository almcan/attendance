# D102 出席管理システム (FeliCa Attendance System)

研究室用の、FeliCa リーダー (PaSoRi) を用いた出席管理システムです。学生の入室・退室を管理し、リアルタイムなダッシュボードの表示と Slack への自動通知などを行います。

## 主な機能

1. **FeliCa (NFC) 出席管理**
   - 学生証や交通系ICカードなどの FeliCa をリーダーにかざすことで、「出席」および「退席」を記録します。
   - 記録されたデータはCSVファイルとしてローカルに保存されます。

2. **リアルタイム・ダッシュボード (`http://localhost:5000`)**
   - 座席表形式で現在の在席状況（出席、リモート中、退席、欠席、未出席）をリアルタイムに確認できます（SSEを用いて即座に画面同期）。
   - Chromiumのキオスクモードを用いて、研究室のモニタに常時表示することを想定しています。

3. **管理画面（Admin UI） (`http://localhost:5000/admin`)**
   - 休日カレンダー設定：日本の祝日に加え、夏休みなどの独自の休日期間をカレンダーから追加・削除できます。
   - ダウンロード機能：
     - 在籍時間集計CSV（日別の在席時間、リモートを除く）の個別・一括ZIPダウンロード
     - 欠席記録の全件一括ダウンロード
   - **新規カード登録**: 専用のモーダル画面から、ダッシュボード上で直接未使用FeliCaカードを学生に紐づけて登録できます。

4. **Slack 通知連携**
   - **定期通知スケジューラ**: 平日（設定した休日を除く）の 17:00 に、まだ出席していない学生をメンション付きで Slack チャンネルに通知します。
   - **Slack Bot**: 学生が Slack 上でBot宛に特定のメッセージ（例: `リモート`）をメンションすることで、ダッシュボード上のステータスを「リモート中」に切り替えることができます。

---

## 必要な環境・準備

- Linux (Ubuntu/Raspi OS 等を想定)
- Python 3.10 以上 (推奨)
- SONY PaSoRi 等の NFC リーダー (依存ライブラリ `nfcpy` で読み取れるもの)

### Python パッケージのインストール

```bash
cd /home/d-102/Attendance
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
*(※ `requirements.txt` がない場合は `nfcpy`, `Flask`, `slack_sdk`, `jpholiday` 等が必要です)*

### 環境変数設定

`.env.example` をコピーして `.env` ファイルを作成し、必要なトークンなどを設定します。

```bash
cp .env.example .env
nano .env
```

*設定内容:*
- `SLACK_BOT_TOKEN`: Slack Bot 用トークン (`xoxb-`から始まるもの)
- `SLACK_APP_TOKEN`: Socket Mode 用アプリトークン (`xapp-`から始まるもの)
- `SLACK_CHANNEL`: 通知先チャンネル（例: `#attendance-notices`）
- 管理者認証用ハッシュ (後述)

### 管理者アカウントの設定

管理画面へのログインに必要なユーザー名とパスワードのハッシュ、およびセッションキーを生成します。

```bash
.venv/bin/python generate_admin_hash.py
```
画面の指示に従いユーザー名とパスワードを入力すると、コンソール上に `.env` へ追記すべき内容が表示されるので、それを `.env` ファイルに貼り付けてください。

---

## 起動方法

### 手動での起動（開発・テスト時）

```bash
.venv/bin/python main.py
```
このコマンドを実行すると、以下のプロセスがすべてマルチスレッドで起動します。
1. ダッシュボードの Web サーバー (Flask)
2. Slack 定期通知スケジューラ
3. Slack Bot (Socket Mode)
4. NFC リーダー待受待機 (メインスレッド)

### キオスクモード起動用スクリプト

研究室のPC等でブラウザを全画面（キオスク）で起動させるためのスクリプトが用意されています。

```bash
./start_app.sh
```
これにより、バックグラウンドの systemd サービス (`attendance.service`) が起動確認され、その後 Chromium が自動的に `http://localhost:5000` を全画面で開きます。

### Systemd サービス化 (参考)

`sudo systemctl restart attendance.service` などのコマンドが使用可能に設定されている環境では、Linuxのバックグラウンドサービスとしてシステム起動時に自動的に実行されます。

---

## ファイル構成

- `main.py` : アプリケーションのエントリポイント（全部入り起動）
- `attendance.py` : NFC リーダー (`nfcpy`) を用いたカード読み取りとCSV追記ロジック
- `dashboard.py` : Flask を用いたダッシュボードおよび管理画面（SSE、CSV/ZIPダウンロードAPIなど）
- `register.py` : （CUIでの）新規カード登録スクリプト機能
- `slack_bot.py` : Slack からの「リモート中」ステータス変更を受け付ける Socket Mode Bot
- `slack_notifier.py` : 17:00 に欠席者を確認し Slack Web API を経由して通知する処理。休日判定 (`jpholiday` + `holidays.csv`) もここで行います。
- `generate_admin_hash.py` : 管理者用ハッシュ計算ツール
- `start_app.sh` : ブラウザキオスク起動スクリプト
- `students.csv` : (自動作成) 登録されている学生とIDmのリスト
- `holidays.csv` : (自動作成) 管理画面から登録された独自休日のリスト
- `attendance/` : (自動作成) 学生ごとの日別出席記録 CSV フォルダ
- `templates/` : ダッシュボード等用の HTML テンプレート群
