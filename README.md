# OBS KWin Scene Switcher

KDE Plasma（KWin Wayland）のアクティブウィンドウに合わせて、OBS の Program シーンを切り替える個人用ツール。

## 機能

- フォーカス中のウィンドウ class を監視し、対応する OBS シーンへ切替
- 未登録ウィンドウは Fallback / Safe シーンへ
- OBS WebSocket の接続テスト・再接続
- 起動時に Safe へ切替、OBS 切断時は自動再接続して目的シーンを戻す
- 設定は `~/.config/obs-kwin-switcher/config.json`

前提: Plasma Wayland、OBS WebSocket（既定 `127.0.0.1:4455`）、`qdbus6` / `qdbus`、ユーザー journal（`plasma-kwin_wayland.service`）。

## 動かし方

Python 3.14 と [uv](https://docs.astral.sh/uv/) を使う。

```bash
uv sync
uv run python main.py
```

OBS 側で **ツール → WebSocket サーバー設定** を有効にし、ホスト / ポート / パスワードを GUI に合わせる。OBS に切替先シーン（例: `Safe`, `Firefox`）を用意する。

テスト:

```bash
uv sync --group dev
uv run pytest
```

## 使い方

1. Host / Port / Password を入れて **Test OBS** で接続確認
2. Fallback / Safe scene に、映したくないときに出すシーン名を入れる
3. **Window → Scene** にマッピングを追加（**Use current** で今のウィンドウ class を流用できる）
4. **Save** して **Start**
5. ウィンドウを切り替えて Status / Log を確認。止めるときは **Stop**

実行中にマッピングを変えたら **Save** すれば次回のフォーカス変更から反映される。OBS が落ちたら **Reconnect**。

## 挙動

| きっかけ | 結果 |
|----------|------|
| Start | 監視開始。まず Safe シーンへ |
| 登録済みウィンドウがアクティブ | 対応シーンへ（約 150ms デバウンス、連打は最後だけ） |
| 未登録 / 空のフォーカス | Safe シーンへ |
| 同じシーンが続く | OBS がつながっていれば再送しない |
| OBS 切断 | 約 2 秒ごとに再接続し、出したいシーンを再適用 |
| Stop / ウィンドウを閉じる | 監視終了。OBS シーンはその場のまま |

ウィンドウ識別は KWin の `resourceClass`（なければ `resourceName` / タイトル）を小文字化したもの。Status の Active window がそのキー。`/tmp/obs-active-window.txt` にも同じ値が書かれる。
