# OBS Scene Switcher

アクティブウィンドウに合わせてOBSのProgramシーンを切り替える、PySide6 / Qt Widgetsアプリです。

## 対応環境

| OS / セッション | 監視方式・Window / App ID |
|---|---|
| Ubuntu 24.04以降 x64 / X11 | python-xlib、WM_CLASSのクラス名（なければインスタンス名） |
| Ubuntu 24.04以降 x64 / KDE Plasma 5・6 Wayland | KWinスクリプトとユーザーjournal、resourceClass / resourceName |
| Windows 11 x64 | Win32 API、前面ウィンドウの実行ファイル名（拡張子なし・小文字） |

WaylandではX11へフォールバックしません。GNOME Wayland、Sway / Hyprland、ARM64、トレイ常駐、自動更新は対象外です。監視方式や未対応の理由は画面に表示します。

## インストール

GitHub ReleasesからOSに合うファイルを取得します。初版は署名なしです。

- Ubuntu: `sudo apt install ./obs-scene-switcher-*-linux-amd64.deb`。アプリ一覧、または `obs-scene-switcher` で起動します。
- Windows: `*-windows-x64.exe` は単体起動用、`*-windows-x64-setup.exe` はユーザー単位のインストーラーです。管理者権限不要で、更新・ショートカット・アンインストールに対応します。
- `SHA256SUMS` を `sha256sum --check SHA256SUMS` や PowerShell の `Get-FileHash -Algorithm SHA256` で照合できます。

Python、Qt、Qtのxcb / Waylandプラグインは同梱します。Linuxのシステム共有ライブラリはdebの依存でインストールされます。

### KDE Waylandの追加準備

Qt 5 / 6の `qdbus` と `journalctl`、読み取り可能なユーザーjournalが必要です。Ubuntu 24.04 Plasma 5では `sudo apt install qdbus-qt5`、Plasma 6環境ではディストリビューションのQt 6 qdbusパッケージをインストールしてください。`qdbus6`、`qdbus-qt6`、`qdbus`、`qdbus-qt5`、`/usr/lib/qt6/bin/qdbus`、`/usr/lib/qt5/bin/qdbus` を探索します。

KWinのスクリプト出力がjournalに記録される必要があります。`kwin_scripting.debug=true` をKWinの `QT_LOGGING_RULES` に追加し、ログアウト・ログインして適用します。systemdでKWinを起動する環境では `systemctl --user edit plasma-kwin_wayland.service` の例:

```ini
[Service]
Environment="QT_LOGGING_RULES=kwin_scripting.debug=true"
```

既存のログルールがある場合は保持してください。`journalctl --user -u plasma-kwin_wayland.service` で確認できます。アプリは一意のマーカーで自分の通知だけを読み、起動から5秒以内に通知が来なければSafeへ切り替えを試み、エラーを表示します。systemd以外で起動されるPlasmaでも、KWinの出力がユーザーjournalへ送られていれば利用できます。

KWin API・ログ設定の参考: [KDE scripting tutorial](https://develop.kde.org/docs/plasma/kwin/)、[KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/)。

## 使い方

1. OBSで **ツール → WebSocket サーバー設定** を有効にします。Safeや切替先のシーンを作成します。
2. Host / Port / Passwordを設定し、**Test OBS** で接続を確認します。
3. **Fallback / Safe scene** とマッピングを設定します。**Add / Edit / Delete** で編集し、**Use current** で最後に監視したIDを利用できます。
   「Ignore case」と「Match ID components」は既定で有効です。前者は大文字・小文字を区別せず、後者は `firefox` を `firefox_firefox` のような区切り付きIDにも一致させます。完全一致するマッピングがある場合はそちらを優先します。
4. **Save** → **Start**。画面に監視状態、OBS接続状態、ID、シーン、ログを表示します。
5. **Stop** で停止します。OBSが切断された場合は自動再接続し、**Reconnect** でも接続をやり直せます。

X11 / Windowsは100ms間隔で取得し、変更時だけ処理します。150msのデバウンスで最後のフォーカスを採用します。起動時、未登録のID、取得不能・フォーカスなしの場合はSafeへ切り替えます。監視異常でもSafeへの切替を試みてエラーを表示します。OBS側が接続不能なら切替の成功は保証できません。

OBSの接続状態は約2秒ごとに確認し、復帰後に目的シーンを再送します。通常のStopと終了ではOBSシーンを維持します。停止は非同期で、監視・通信の終了を待って画面を閉じます。

設定形式は従来と同じJSONです。マッピングは保存後の次のフォーカス変更から反映されます。

- Linux: `$XDG_CONFIG_HOME/obs-kwin-switcher/config.json`（既定 `~/.config/obs-kwin-switcher/config.json`）
- Windows: `%APPDATA%/obs-scene-switcher/config.json`

アンインストール後も設定を保持します。最後に適用したIDはOSの一時ディレクトリの `obs-active-window.txt` にも書き込みます。

## 接続できるのに監視が始まらない場合

**Test OBS** はOBS接続のみを確認します。`KWin journal monitor exited unexpectedly` はウィンドウ監視用の `journalctl` が終了したことを示します。修正版では終了コードと診断出力もログに表示します。

debでは外部の `journalctl` / `qdbus` にアプリ同梱ライブラリを読み込ませないよう、子プロセスのライブラリ検索パスを元に戻しています。古いdebを使用している場合は修正版を再ビルド・インストールしてください。更新後も失敗する場合は、終了コード以下の診断メッセージと、端末での `journalctl --user -n 5 --no-pager` の結果を確認してください。

## 開発・ビルド

Python 3.14とuvを使用します。依存の確定バージョンは `uv.lock` に収録しています。

```bash
uv sync --locked --group dev --group build
uv run python main.py
QT_QPA_PLATFORM=offscreen uv run pytest -q  # Linux / headless
uv run python packaging/build.py linux   # Ubuntu 24.04 x64
```

WindowsではInno Setup 6の `iscc` をPATHに追加し、`uv run python packaging/build.py windows` を実行します。展開済みアプリを先にインストーラーへ収録し、その後単体exeを生成します。成果物は `dist/` に出力します。

ビルド時にQtの対応するソースタグからライセンスと第三者通知を取得するため、ネットワーク接続が必要です。同梱文書は展開済みアプリの `_internal/licenses/` にあります。単体exeは実行時の一時展開先に同じ文書を含みます。詳細は [THIRD_PARTY.md](packaging/THIRD_PARTY.md) を参照してください。

## CIとリリース確認

GitHub ActionsはPR、main / masterへのpush、手動実行でテスト・ビルドし、成果物を保存します。Ubuntu 24.04とWindows runnerで次を確認します。

- 共通処理・OS判定・取得失敗・監視異常・停止 / 再起動のテスト
- pytest-qtによる設定、マッピング、状態表示、エラー、非同期終了のテスト
- Xvfb + Openboxで実ウィンドウのX11監視
- debのインストール、xcbとheadless WestonのWaylandでパッケージ済みGUIの起動、削除、設定保持
- Windows単体exeの起動、サイレントインストール、更新、起動、アンインストール、設定保持

`vX.Y.Z`タグはpyproject.tomlのバージョン一致と両OSの全ジョブ成功後、GitHub Releasesにdeb・単体exe・セットアップexe・SHA-256を公開します。

リリース前に実機で以下を確認してください。CIのheadless WaylandテストはKWin監視の実機検証を代替しません。

- [ ] Plasma 5 Wayland + 実OBS: マッピング、フォーカスなし、journal停止時のSafe、再接続
- [ ] Plasma 6 Wayland + 実OBS: 同上、起動時の通知確認
- [ ] X11 + 実OBS: WM_CLASS、Safe、再接続、連続Start / Stop
- [ ] Windows 11 + 実OBS: 実行ファイルID、アクセスできないプロセスのSafe、再接続、終了
- [ ] 高DPIとウィンドウサイズ変更時のレイアウト
