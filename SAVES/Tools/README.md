# 🧰 Tools

## Switch JKSV -> Archive + Eden sync

Use `sync_switch_saves.py` to copy the newest JKSV backup for each Switch game into:

- `SAVES/Nintendo/Switch/Eden/<Game Name> [TitleID]/latest`
- Eden's save folder for the same `TitleID`, when that Eden game folder already exists

The script is intentionally safe by default: without `--apply`, it only previews what it would copy.

```bat
sync_switch_saves_preview.bat
```

To apply the copy:

```bat
sync_switch_saves_apply.bat
```

To sync only one game:

```bat
python SAVES\Tools\sync_switch_saves.py --game "Tears" --apply
```

To keep watching JKSV every 30 seconds:

```bat
python SAVES\Tools\sync_switch_saves.py --watch 30 --apply
```

Configuration lives in `SAVES/Tools/switch_sync_config.json`. Add or correct game title IDs there when JKSV and Eden use different names.

Before writing into Eden, the script backs up the existing Eden save folder into `SAVES/Tools/sync_backups/eden`, which is ignored by Git.
