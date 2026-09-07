"""Manual review model and a small Windows-native UI for scan results."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re


TARGETS = ("client", "server", "both")
TARGET_LABELS = {"client": "Клиенты", "server": "Сервер", "both": "Все ПК"}
SERVER_SOFTWARE = re.compile(r"ccboot|clubmonitor|godji|lwserver|[\\/]server(?:[\\/]|$)|[\\/]server[ _-]", re.I)


def default_target(item):
    evidence = " ".join((item.get("title", ""), item.get("launch", {}).get("path", "")))
    return "server" if SERVER_SOFTWARE.search(evidence) else "client"


def create_review(scan, existing=None):
    """Create a stable editable review document; all discovery data stays in scan.json."""
    choices = {item.get("itemId"): item for item in (existing or {}).get("items", [])}
    items = []
    for found in scan.get("items", []):
        previous = choices.get(found.get("id"), {})
        group = found.get("displayGroup", "candidates")
        target = previous.get("target", default_target(found))
        items.append({
            "itemId": found.get("id"),
            "title": found.get("title", ""),
            "selected": bool(previous.get("selected", group == "applications")),
            "target": target if target in TARGETS else default_target(found),
            "displayGroup": group,
            "contentKind": found.get("contentKind", ""),
        })
    return {
        "schemaVersion": 1,
        "kind": "godji-scanner-review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "scannerVersion": scan.get("scannerVersion", ""),
        "scanId": scan.get("scanId") or scan.get("scannedAt", ""),
        "clubId": scan.get("clubId", ""),
        "items": items,
    }


def load_review(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("schemaVersion") != 1 or data.get("kind") != "godji-scanner-review" or not isinstance(data.get("items"), list):
        raise ValueError("Unsupported review schema")
    return data


def run_review(scan, output, existing=None):
    """Open the selector window and save to output only when the user clicks Save."""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError as error:
        raise RuntimeError("Tkinter is unavailable; use a Windows build of GodjiScanner") from error

    review = create_review(scan, existing)
    found = {item.get("id"): item for item in scan.get("items", [])}
    root = tk.Tk()
    root.title("GodjiScanner — отбор приложений")
    root.geometry("1180x720")
    root.minsize(900, 520)

    state = {item["itemId"]: item for item in review["items"]}
    query = tk.StringVar()
    group_filter = tk.StringVar(value="Все")
    target_filter = tk.StringVar(value="Все")
    selected_only = tk.BooleanVar(value=False)
    columns = ("selected", "title", "group", "target", "path")
    tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="extended")
    headings = {"selected": "Включить", "title": "Название", "group": "Раздел", "target": "Где запускать", "path": "Путь запуска"}
    widths = {"selected": 85, "title": 235, "group": 150, "target": 125, "path": 550}
    for column in columns:
        tree.heading(column, text=headings[column])
        tree.column(column, width=widths[column], anchor="center" if column in {"selected", "group", "target"} else "w", stretch=column == "path")
    tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
    scrollbar = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
    scrollbar.pack(side="left", fill="y", padx=(0, 10), pady=10)
    tree.configure(yscrollcommand=scrollbar.set)

    sidebar = ttk.Frame(root, padding=(0, 10, 10, 10))
    sidebar.pack(side="right", fill="y")
    ttk.Label(sidebar, text="Поиск").pack(anchor="w")
    ttk.Entry(sidebar, textvariable=query, width=28).pack(fill="x", pady=(0, 12))
    ttk.Label(sidebar, text="Показать").pack(anchor="w")
    groups = {"Все", "applications", "candidates", "components"}
    ttk.Combobox(sidebar, textvariable=group_filter, values=sorted(groups), state="readonly").pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(sidebar, text="Только включённые", variable=selected_only).pack(anchor="w", pady=(0, 12))
    ttk.Label(sidebar, text="Назначить выбранным").pack(anchor="w")
    target_box = ttk.Combobox(sidebar, textvariable=target_filter, values=["Все", "Клиенты", "Сервер", "Все ПК"], state="readonly")
    target_box.pack(fill="x", pady=(0, 6))

    labels = {"applications": "Рекомендуемое", "candidates": "Кандидат", "components": "Компонент"}

    def visible(item, found_item):
        needle = query.get().strip().casefold()
        if group_filter.get() != "Все" and item["displayGroup"] != group_filter.get():
            return False
        if selected_only.get() and not item["selected"]:
            return False
        haystack = " ".join((item["title"], found_item.get("launch", {}).get("path", ""))).casefold()
        return not needle or needle in haystack

    def refresh(*_):
        selected = set(tree.selection())
        tree.delete(*tree.get_children())
        for item in review["items"]:
            found_item = found.get(item["itemId"], {})
            if not visible(item, found_item):
                continue
            values = ("☑" if item["selected"] else "☐", item["title"], labels.get(item["displayGroup"], item["displayGroup"]),
                      TARGET_LABELS[item["target"]], found_item.get("launch", {}).get("path", ""))
            tree.insert("", "end", iid=item["itemId"], values=values)
        for item_id in selected:
            if tree.exists(item_id):
                tree.selection_add(item_id)

    def toggle(*_):
        for item_id in tree.selection():
            state[item_id]["selected"] = not state[item_id]["selected"]
        refresh()

    def apply_target():
        mapped = {"Клиенты": "client", "Сервер": "server", "Все ПК": "both"}
        target = mapped.get(target_filter.get())
        if not target:
            return
        for item_id in tree.selection():
            state[item_id]["target"] = target
        refresh()

    def set_selection(value):
        for item_id in tree.selection():
            state[item_id]["selected"] = value
        refresh()

    def save():
        review["items"] = list(state.values())
        review["savedAt"] = datetime.now(timezone.utc).isoformat()
        review["selectedCount"] = sum(item["selected"] for item in review["items"])
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        messagebox.showinfo("GodjiScanner", f"Сохранено: {output}\nВыбрано: {review['selectedCount']}")

    for variable in (query, group_filter, selected_only):
        variable.trace_add("write", refresh)
    ttk.Button(sidebar, text="Применить роль", command=apply_target).pack(fill="x", pady=(0, 12))
    ttk.Button(sidebar, text="Включить выбранные", command=lambda: set_selection(True)).pack(fill="x", pady=2)
    ttk.Button(sidebar, text="Исключить выбранные", command=lambda: set_selection(False)).pack(fill="x", pady=(2, 16))
    ttk.Button(sidebar, text="Сохранить отбор", command=save).pack(fill="x")
    ttk.Label(sidebar, text="Двойной щелчок по строке\nвключает или исключает её.", justify="left").pack(anchor="w", pady=(16, 0))
    tree.bind("<Double-1>", toggle)
    refresh()
    root.mainloop()
