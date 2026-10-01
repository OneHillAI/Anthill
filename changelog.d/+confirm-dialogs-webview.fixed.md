**Destructive actions now work in the desktop app.** The packaged desktop webview does not run native
confirmation dialogs, so buttons such as model Uninstall, delete conversation, remove user and revoke
token silently did nothing. They now use an in-app confirmation that works in the desktop app. The model
picker carries a working per-model Uninstall, and the separate raw model-storage list was removed:
uninstall lives on each model where you pick it.
