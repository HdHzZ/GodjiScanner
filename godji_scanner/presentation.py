"""Lossless, offline classification: no installed path is removed or rewritten."""
import ntpath
import re
from collections import Counter


def norm(path):
    return ntpath.normcase(ntpath.normpath(path or ""))


def classify(result):
    items = result["items"]
    games = sorted([i for i in items if (i.get("identity") or {}).get("provider") in {"steam", "epic"}
                    and i.get("installPath")], key=lambda i: len(i["installPath"]), reverse=True)
    helpers = re.compile(r"^(?:\._.*|7z[a-z]*|qtwebengineprocess|cefsubprocess|crash.*|unitycrash.*|createdump|"
                         r"unins.*|uninstall.*|vcredist.*|dxsetup.*|vc_redist.*|"
                         r"ace-service.*|acep_report.*|elevated_tracing_service|notification_helper|"
                         r"conhost|chrome_pwa_launcher|werfault|hpatchz|xdelta3|zstd|"
                         r"steamservice|steamwebhelper|steamerrorreporter.*)$", re.I)
    game_launchers = re.compile(
        r"steam|epic games|battle\.net|battlestate|bsglauncher|ea (app|desktop)|ubisoft|rockstar games|"
        r"riot client|league of legends|lesta|vk play|4game|hoyoplay|gog galaxy|wargaming|my\.games|tlauncher|minecraft launcher",
        re.I)
    device_software = re.compile(
        r"logitech|g hub|razer|synapse|steelseries|corsair|icue|hyperx|lamzu|redragon|bloody|a4tech|akko|"
        r"wooting|endgame gear|glorious|pulsar|zowie|xtrfy|asus armoury|armoury crate|rog |msi center|"
        r"nvidia (app|control panel|broadcast)|amd software|adrenalin|realtek|nahimic|sonic studio|dts|dolby|"
        r"gigabyte control|aorus|keychron|vgn|vxe|dareu|rapoo|fantech|roccat|turtle beach",
        re.I)
    installer_path = re.compile(r"(?:\\|/)(?:package cache|windows\\installer)(?:\\|/)", re.I)
    groups = []
    steam_games = {str((game.get("identity") or {}).get("appId")): game for game in games}
    epic_games = {str((game.get("identity") or {}).get("appId")): game for game in games
                  if (game.get("identity") or {}).get("provider") == "epic"}
    for item in items:
        path = item["launch"]["path"]
        sources = set(item["sources"])
        item["relatedTo"] = None
        item["launch"]["kind"] = ("uri" if re.match(r"^[a-z][a-z0-9+.-]*:", path, re.I)
                                    and not re.match(r"^[a-z]:[\\/]", path, re.I) else
                                    "script" if ntpath.splitext(path)[1].lower() in {".bat", ".cmd"} else "executable")
        item["argumentsSource"] = ("shortcut" if "shortcut" in sources else "manifest" if sources & {"steam", "epic"}
                                   else "built-in" if "windows-settings" in sources else
                                   "catalog" if "catalog-path" in sources else "unknown")
        item["reviewReason"] = "" if item["status"] == "found" else "Проверьте способ запуска и аргументы"
        if "url-shortcut" in sources and re.match(r"^https?://", path, re.I):
            # Internet shortcuts packaged with games often point to EULAs, forums
            # and support. Preserve them, but never promote them as launch cards.
            item["displayGroup"] = "components"
            item["reviewReason"] = "Веб-ссылка из установленного ПО; не является приложением"
        elif "url-shortcut" in sources and path.casefold().startswith("steam://rungameid/"):
            appid = path.rsplit("/", 1)[-1].split("?", 1)[0]
            owner = steam_games.get(appid)
            if owner:
                item["displayGroup"] = "components"
                item["relatedTo"] = owner["id"]
                item["reviewReason"] = "Дублирующий Steam-ярлык; основная карточка использует подтверждённый Steam AppID"
            else:
                item["displayGroup"] = "candidates"
        elif sources & {"windows-settings", "catalog-path"}:
            item["displayGroup"] = "applications"
            item["contentKind"] = "system" if "windows-settings" in sources else "catalog"
        elif (item.get("identity") or {}).get("provider") in {"steam", "epic"} and item.get("installPath"):
            item["displayGroup"] = "applications"
            item["contentKind"] = "game"
        elif game_launchers.search(item["title"]) or game_launchers.search(path):
            item["displayGroup"] = "applications"
            item["contentKind"] = "game-launcher"
        elif device_software.search(item["title"]) or device_software.search(path):
            item["displayGroup"] = "applications"
            item["contentKind"] = "device-software"
        elif installer_path.search(path) or re.match(r"^(?:uninstall|install |register |start |stop )", item["title"], re.I):
            item["displayGroup"] = "components"
            item["contentKind"] = "installer-or-service"
            item["reviewReason"] = "Установщик, деинсталлятор или служебная команда; не является карточкой клуба"
        else:
            owner = next((game for game in games if norm(path).startswith(norm(game["installPath"]) + "\\")), None)
            if owner:
                item["displayGroup"] = "components"
                item["relatedTo"] = owner["id"]
            elif helpers.fullmatch(ntpath.splitext(ntpath.basename(path))[0]):
                item["displayGroup"] = "components"
            else:
                item["displayGroup"] = "candidates"
                item["contentKind"] = "other-software"
                if sources & {"shortcut", "registry"}:
                    item["reviewReason"] = "Обычное ПО из ярлыка или реестра; добавьте в каталог только при необходимости"
    # Keep launch variants distinct, even if they use one executable. The report
    # groups only filesystem components under a manifest-backed game.
    for game in games:
        members = [i["id"] for i in items if i.get("relatedTo") == game["id"]]
        if members:
            groups.append({"primaryId": game["id"], "componentIds": members})
    result["groups"] = groups
    result["summary"] = dict(Counter(i["displayGroup"] for i in items))
    return result
