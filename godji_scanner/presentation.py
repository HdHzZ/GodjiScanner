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
        elif sources & {"windows-settings", "steam", "epic", "shortcut", "registry", "catalog-path"}:
            item["displayGroup"] = "applications"
        else:
            owner = next((game for game in games if norm(path).startswith(norm(game["installPath"]) + "\\")), None)
            if owner:
                item["displayGroup"] = "components"
                item["relatedTo"] = owner["id"]
            elif helpers.fullmatch(ntpath.splitext(ntpath.basename(path))[0]):
                item["displayGroup"] = "components"
            else:
                item["displayGroup"] = "candidates"
    # Keep launch variants distinct, even if they use one executable. The report
    # groups only filesystem components under a manifest-backed game.
    for game in games:
        members = [i["id"] for i in items if i.get("relatedTo") == game["id"]]
        if members:
            groups.append({"primaryId": game["id"], "componentIds": members})
    result["groups"] = groups
    result["summary"] = dict(Counter(i["displayGroup"] for i in items))
    return result
