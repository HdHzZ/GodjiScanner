"""Human-facing zero-argument entry point; the agent CLI remains unchanged."""
import csv
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import sys

from .core import Scanner, Cancelled
from . import __version__


def output_folder(base):
    name = datetime.now().strftime("scan-%Y%m%d-%H%M%S-%f")
    candidates = [Path(base) / "results", Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GodjiScanner/results"]
    last_error = None
    for parent in candidates:
        try:
            destination = parent / name
            destination.mkdir(parents=True, exist_ok=False)
            return destination
        except OSError as error:
            last_error = error
    raise last_error


def save_reports(result, folder):
    from .presentation import classify
    classify(result)
    (folder / "scan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    headings = ["Название", "Статус", "Папка установки", "Путь запуска", "Аргументы", "Рабочая папка", "Источники"]
    rows = []
    for item in result["items"]:
        launch = item["launch"]
        rows.append([item["title"], "Найдено" if item["status"] == "found" else "Нужна проверка",
                     item.get("installPath") or "", launch["path"],
                     json.dumps(launch["args"], ensure_ascii=False), launch.get("workingDirectory") or "",
                     ", ".join(item["sources"])])
    with (folder / "applications.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(headings)
        # Keep spreadsheet viewers from interpreting discovered names as formulas.
        # This is the operator's compact list. The complete diagnostic inventory
        # remains available in scan.json and the closed report sections.
        primary_rows = [row for row, item in zip(rows, result["items"])
                        if item["displayGroup"] == "applications"]
        writer.writerows([["'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
                           for value in row] for row in primary_rows])
    esc = html.escape
    header = "".join(f"<th>{esc(h)}</th>" for h in headings)
    bodies = {key: [] for key in ["applications", "candidates", "components"]}
    for row, item in zip(rows, result["items"]):
        icon = item.get("iconFile", "")
        prefix = f'<img width="24" height="24" alt="" src="{esc(icon, quote=True)}"> ' if re_safe_icon(icon) else ""
        cells = [f"<td>{prefix}{esc(row[0])}</td>"] + [f"<td>{esc(v)}</td>" for v in row[1:]]
        bodies[item["displayGroup"]].append("<tr>" + "".join(cells) + "</tr>")
    sections = []
    for key, label in [("applications", "Рекомендуемые игры, приложения и настройки"), ("candidates", "Прочее ПО — добавить вручную при необходимости"), ("components", "Установщики и служебные файлы")]:
        content = "".join(bodies[key])
        sections.append(f'<details {"open" if key == "applications" else ""}><summary>{label}: {len(bodies[key])}</summary><table><thead><tr>{header}</tr></thead><tbody>{content}</tbody></table></details>')
    body = "".join(sections)
    notices = "".join(f"<li>{esc(w.get('source', ''))}: {esc(w.get('message', ''))}</li>" for w in result["warnings"])
    state = "Поиск завершён не полностью" if result["partial"] else "Поиск завершён"
    confirmed = sum(i["status"] == "found" for i in result["items"])
    report = f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<title>GodjiScanner — результаты</title><style>
body{{font:15px system-ui,sans-serif;margin:28px;background:#f4f6fa;color:#172033}}
table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:10px;border:1px solid #dce1eb;text-align:left;vertical-align:top;overflow-wrap:anywhere;max-width:360px}}
th{{background:#e6ebf5;position:sticky;top:0}}h1{{font-size:26px}}.note{{padding:14px;background:#fff;border-left:4px solid #657ce5}}li{{overflow-wrap:anywhere}}
</style><h1>{state}</h1><p>Рекомендуемых карточек: {len(bodies['applications'])}. Дополнительных диагностических записей: {len(rows)-len(bodies['applications'])}.</p>
<p class="note">Главный раздел содержит игры, игровые лаунчеры, ПО устройств и системные настройки. Остальные записи сохранены ниже для проверки, но не предназначены для импорта в каталог. Сканер ничего не запускал. Для поиска по странице нажмите Ctrl+F.</p>
<p>Полный результат: <b>scan.json</b>. Таблица рекомендуемых карточек: <b>applications.csv</b>.</p>
<ul>{notices}</ul>{body}</html>'''
    (folder / "report.html").write_text(report, encoding="utf-8")


def re_safe_icon(path):
    import re
    return bool(re.fullmatch(r"icons/icon-\d+\.png", path))


def run_automatic(base=None, roots=None, system=True, pause=None, open_review=False):
    from .windows import fixed_drives
    if base is None:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    if pause is None:
        pause = sys.stdin.isatty()
    code = 0
    try:
        destination = output_folder(base)
        print("GodjiScanner — автоматический поиск без списка\n", flush=True)
        print("Поиск установленных игр и программ на локальных дисках.", flush=True)
        print("Это может занять несколько минут. Для отмены нажмите Ctrl+C.", flush=True)
        print(f"Результаты: {destination}\n", flush=True)
        labels = {"steam": "Библиотеки Steam", "epic": "Библиотеки Epic Games",
                  "windows": "Реестр Windows и ярлыки", "filesystem": "Поиск EXE на дисках"}
        with (destination / "scan.log").open("w", encoding="utf-8") as log:
            def progress(event):
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
                log.flush()
                if event["event"] == "progress":
                    if "filesVisited" in event:
                        print(f"Проверено файлов: {event['filesVisited']:,}. Находок: {len(scanner.items)}", flush=True)
                    else:
                        print(labels.get(event["source"], event["source"]) + "…", flush=True)
                elif event["event"] == "warning":
                    print("Часть данных недоступна; подробности в отчёте.", flush=True)
            scanner = Scanner(progress, max_files=2000000)
            try:
                selected = fixed_drives() if roots is None else roots
                if not selected:
                    scanner.warning("filesystem", "Локальные диски для обхода не обнаружены")
                result = scanner.run(roots=selected, system=system)
            except (KeyboardInterrupt, Cancelled):
                code = 130
                result = {"schemaVersion": 1, "scannerVersion": __version__,
                          "scannedAt": datetime.now(timezone.utc).isoformat(), "clubId": "",
                          "items": sorted(scanner.items.values(), key=lambda i: i["title"].casefold()),
                          "matches": [], "warnings": scanner.warnings + [{"source": "scan", "message": "Поиск отменён пользователем; сохранены частичные результаты"}],
                          "partial": True, "durationSeconds": 0, "cancelled": True}
            # Persist the useful discovery data before optional icon extraction.
            # Icon resources can be slow or unavailable on a real club PC.
            save_reports(result, destination)
            if not result.get("cancelled") and system:
                print("Извлечение локальных иконок…", flush=True)
                try:
                    from .icons import extract_icons
                    extract_icons(result, destination)
                except Exception as error:
                    result["warnings"].append({"source": "icons", "message": str(error)})
            # Write icon references, or warnings, back into the existing report.
            save_reports(result, destination)
            if open_review and not result.get("cancelled"):
                from .review import run_review
                print("Открываю окно ручного отбора…", flush=True)
                run_review(result, destination / "review.json")
        print(f"\nРекомендуемых карточек: {result.get('summary', {}).get('applications', 0)}.", flush=True)
        print(f"Диагностических записей: {len(result['items']) - result.get('summary', {}).get('applications', 0)}.", flush=True)
        if result["partial"]:
            print("Поиск неполный: проверьте предупреждения в отчёте.", flush=True)
        print(f"Откройте отчёт: {destination / 'report.html'}", flush=True)
        print("Для проверки передайте файл scan.json из этой папки.", flush=True)
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr, flush=True)
        code = 1
    if pause:
        try:
            input("\nНажмите Enter, чтобы закрыть окно…")
        except (EOFError, KeyboardInterrupt):
            pass
    return code
