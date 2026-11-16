# Lyric Analyzer

Suno向け歌詞制作を支援する、Windows用ローカルWebアプリの開発プロジェクトです。

過去曲で繰り返し使う語句と、辞書に登録した定型表現を組み合わせて分析します。指摘の根拠を確認し、修正するかを利用者が判断できます。

## 現在の状態

M1の初期実装です。歌詞の保存・検索・編集・削除と改訂履歴を利用できます。分析機能、タグ、JSON入出力は今後追加します。

- 想定構成：Python / Streamlit / SQLite / SudachiPy
- MVP：歌詞管理、語彙・フレーズ集計、過去曲比較、定型表現辞書、除外設定、分析履歴
- 外部AIによる文脈評価と言い換えは次段階

仕様・データ設計・受入条件・開発計画は [仕様書](docs/specification.md) を参照してください。

歌詞、データベース、APIキーはGit管理に含めません。

## Windowsで起動

1. Python 3.12（64bit、Python Launcherを含む）をインストールします。
2. このリポジトリを取得し、`start.cmd` をダブルクリックします。
3. 初回は依存ライブラリをインストールします。表示された `http://127.0.0.1:8501` をブラウザで開きます。
4. 終了するときは起動したウィンドウで Ctrl+C を押します。

起動はローカルPCに限定します。Streamlitの利用統計送信も起動時に無効化します。歌詞を外部AIへ送る処理はありません。

保存先は `%LOCALAPPDATA%\LyricAnalyzer\library.db` です。`LYRIC_ANALYZER_DATA_DIR` 環境変数で変更できます。曲の削除は全改訂も削除するため確認が必要です。バックアップ機能は今後追加します。

## 開発とテスト

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pytest -q
```

Python 3.12 / Windows向けに依存を固定しています。CIもWindowsで保存処理と画面操作のテストを実行します。
