"""Obsidian is optional: the vault is plain Markdown. This module only helps the first run.

* ``status(vault)``: is the app installed, is this folder already a registered vault?
* ``first_run(vault, layout)``: when the folder has never been opened in Obsidian, write a
  minimal ``.obsidian`` (new notes go to the inbox, daily notes to the daily folder) and a
  welcome note, so the first open lands somewhere useful instead of an empty window.
* ``open_vault(vault)``: open it through the ``obsidian://`` URI when Obsidian already knows
  the vault; otherwise start the app and say how to add the folder.

Obsidian's own settings (``obsidian.json``) are only read, never written.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

DOWNLOAD = "https://obsidian.md/download"


def _candidates() -> list[Path]:
    home = Path.home()
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local")
        progs = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
        return [local / "Programs/Obsidian/Obsidian.exe", local / "Obsidian/Obsidian.exe",
                *[Path(p) / "Obsidian/Obsidian.exe" for p in progs if p]]
    if sys.platform == "darwin":
        return [Path("/Applications/Obsidian.app"), home / "Applications/Obsidian.app"]
    return [Path(p) for p in (shutil.which("obsidian") or "", "/snap/bin/obsidian",
                              str(home / ".local/share/flatpak/exports/bin/md.obsidian.Obsidian"),
                              "/var/lib/flatpak/exports/bin/md.obsidian.Obsidian") if p]


def find_app() -> Path | None:
    for path in _candidates():
        if path.exists():
            return path
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\obsidian\shell\open\command") as key:
                command = winreg.QueryValue(key, None)
            exe = Path(command.split('"')[1] if command.startswith('"') else command.split()[0])
            if exe.exists():
                return exe
        except OSError:
            pass
    return None


def _config_files() -> list[Path]:
    home = Path.home()
    if os.name == "nt":
        return [Path(os.environ.get("APPDATA") or home / "AppData/Roaming") / "obsidian/obsidian.json"]
    if sys.platform == "darwin":
        return [home / "Library/Application Support/obsidian/obsidian.json"]
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [xdg / "obsidian/obsidian.json", home / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json"]


def _same(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return False


def registered(vault: Path) -> bool:
    for path in _config_files():
        try:
            vaults = json.loads(path.read_text(encoding="utf-8")).get("vaults") or {}
        except (OSError, ValueError):
            continue
        if any(_same(v.get("path", ""), vault) for v in vaults.values() if isinstance(v, dict)):
            return True
    return False


def install_hint() -> str:
    if os.name == "nt":
        return f"winget install Obsidian.Obsidian  (or download: {DOWNLOAD})"
    if sys.platform == "darwin":
        return f"brew install --cask obsidian  (or download: {DOWNLOAD})"
    return f"flatpak install flathub md.obsidian.Obsidian  (or AppImage/deb: {DOWNLOAD})"


TEXT = {
    "zh": dict(
        welcome="欢迎使用 OriMem",
        missing="未检测到 Obsidian。OriMem 不依赖它（库是普通 Markdown 文件夹，agent 照常读写），想要图形界面时安装：",
        add="首次打开：在 Obsidian 里选“打开本地仓库 / Open folder as vault”，选择：",
        ready="已在 Obsidian 中登记，可用 `orimem open` 打开。",
    ),
    "en": dict(
        welcome="Welcome to OriMem",
        missing="Obsidian was not found. OriMem does not need it (the vault is a plain Markdown folder agents read and write); for a UI install it:",
        add="First open: in Obsidian choose \"Open folder as vault\" and pick:",
        ready="Registered in Obsidian; `orimem open` opens it.",
    ),
}


def welcome_name(lang: str) -> str:
    return TEXT.get(lang, TEXT["en"])["welcome"] + ".md"


def status(vault: Path, lang: str = "en") -> dict:
    app = find_app()
    t = TEXT.get(lang, TEXT["en"])
    out = dict(installed=bool(app), app=str(app) if app else None, vault_registered=registered(vault),
               vault_configured=(vault / ".obsidian").is_dir())
    if not app:
        out["next_step"] = t["missing"] + " " + install_hint()
    elif not out["vault_registered"]:
        out["next_step"] = t["add"] + " " + str(vault)
    else:
        out["next_step"] = t["ready"]
    return out


def first_run(vault: Path, lay: dict) -> list[str]:
    """Only for a folder Obsidian has never opened (no ``.obsidian``); never touches an existing one."""
    created = []
    lang = lay.get("language", "zh")
    note = vault / welcome_name(lang)
    if not note.exists():
        template = Path(__file__).resolve().parent / "templates" / "vault" / f"welcome.{lang}.md"
        if not template.exists():
            template = template.with_name("welcome.en.md")
        note.write_text(template.read_text(encoding="utf-8").format(**lay), encoding="utf-8")
        created.append(note.name)
    settings = vault / ".obsidian"
    if not settings.exists():
        settings.mkdir()
        (settings / "app.json").write_text(json.dumps(
            {"newFileLocation": "folder", "newFileFolderPath": lay["inbox"], "alwaysUpdateLinks": True},
            ensure_ascii=False, indent=2), encoding="utf-8")
        (settings / "daily-notes.json").write_text(json.dumps(
            {"folder": lay["daily"], "format": "YYYY-MM-DD"}, ensure_ascii=False, indent=2), encoding="utf-8")
        created.append(".obsidian (app.json, daily-notes.json)")
    return created


def _launch(target: str) -> None:
    if os.name == "nt":
        os.startfile(target)  # noqa: S606 - URI or exe chosen above
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_vault(vault: Path, lang: str = "en") -> dict:
    info = status(vault, lang)
    if not info["installed"]:
        return info
    if info["vault_registered"]:
        note = vault / welcome_name(lang)
        target = note if note.exists() else vault
        _launch("obsidian://open?path=" + quote(str(target), safe=""))
        info["opened"] = "uri"
    else:
        app = Path(info["app"])
        if sys.platform == "darwin" or os.name == "nt":
            _launch(str(app))
        else:
            subprocess.Popen([str(app)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        info["opened"] = "app"
    return info
