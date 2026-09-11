"""Selected-club workflow: current catalog in, GodjiOS import ZIP out."""

from datetime import datetime
from pathlib import Path
from .catalog import export_catalog, read_catalog, retain_catalog_matches
from .core import Scanner


def catalog_label(path):
    catalog = read_catalog(path)
    return str(catalog.get("clubName") or catalog.get("name") or catalog.get("clubId") or Path(path).stem)


def available_catalogs(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.glob("*.zip"), key=lambda item: item.name.casefold()):
        try:
            catalog = read_catalog(path)
            result.append({"path": path, "label": catalog_label(path), "items": len(catalog["items"])})
        except (OSError, ValueError):
            continue
    return result


def scan_club(catalog_path, output_dir, all_drives=True, include_reviewed=False, emit=None, source_label=None):
    """Search locally but retain and export only entries from one club's catalog."""
    catalog_path = Path(catalog_path).resolve()
    catalog = read_catalog(catalog_path)
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError("Output folder already exists; choose a new folder")
    output_dir.mkdir(parents=True)
    callback = emit or (lambda _: None)
    scanner = Scanner(callback, max_files=2_000_000)
    roots = []
    if all_drives:
        from .windows import fixed_drives
        roots = fixed_drives()
    result = scanner.run(roots=roots, catalog=catalog, system=True)
    retain_catalog_matches(result)
    result["clubCatalog"] = source_label or str(catalog_path)
    result["clubLabel"] = catalog_label(catalog_path)
    result["includedReviewedCandidates"] = bool(include_reviewed)
    from .automatic import save_reports
    save_reports(result, output_dir)
    try:
        from .icons import extract_icons
        extract_icons(result, output_dir)
    except Exception as error:
        result.setdefault("warnings", []).append({"source": "icons", "message": str(error)})
    save_reports(result, output_dir)
    destination = output_dir / "godjios-import.zip"
    exported = export_catalog(result, destination, catalog_path, include_reviewed=include_reviewed, assets_root=output_dir)
    exported.update({"clubId": catalog.get("clubId", ""), "clubLabel": result["clubLabel"],
                     "scan": str((output_dir / "scan.json").resolve()), "report": str((output_dir / "report.html").resolve())})
    return exported


def scan_cloud_club(base_url, token, club, output_dir, all_drives=True, include_reviewed=False, emit=None):
    """Fetch an API catalog into a temporary v3 template and scan only its entries."""
    import tempfile
    from .cloud_api import create_catalog_zip
    with tempfile.TemporaryDirectory(prefix="GodjiScanner-cloud-") as temporary:
        template = Path(temporary) / "catalog.zip"
        create_catalog_zip(base_url, token, club, template)
        return scan_club(template, output_dir, all_drives, include_reviewed, emit,
                         source_label=f"Cloud API: {club['id']}")


def default_output_folder(base, label):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(char if char.isalnum() or char in "-_ " else "_" for char in label).strip() or "club"
    return Path(base) / f"{safe}-{stamp}"


def run_club_selector(catalogs_dir, output_base, automatic=None):
    """Open a simple operator UI to select a club catalog and produce an import ZIP."""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox, filedialog
    except ImportError as error:
        raise RuntimeError("Tkinter is unavailable; use a Windows build of GodjiScanner") from error

    catalog_paths = []
    remote_records = []
    root = tk.Tk()
    root.title("GodjiScanner — сканирование клуба")
    root.geometry("700x470")
    root.minsize(620, 400)
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Что нужно отсканировать?", font=("Segoe UI", 15, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Выберите клуб из Godji Cloud или ZIP-каталог. Полный поиск без списка тоже доступен ниже.").pack(anchor="w", pady=(4, 12))
    listbox = tk.Listbox(frame, height=11, exportselection=False)
    listbox.pack(fill="both", expand=True)
    status = tk.StringVar(value="Добавьте ZIP-каталог клуба в папку clubs или выберите его вручную.")
    include_reviewed = tk.BooleanVar(value=False)
    deep_scan = tk.BooleanVar(value=True)

    def refresh(extra=None):
        nonlocal catalog_paths
        records = available_catalogs(catalogs_dir) + remote_records
        if extra:
            try:
                records.append({"path": Path(extra), "label": catalog_label(extra), "items": len(read_catalog(extra)["items"])})
            except (OSError, ValueError) as error:
                messagebox.showerror("GodjiScanner", f"Не удалось открыть каталог: {error}")
                return
        catalog_paths = records
        listbox.delete(0, "end")
        for record in records:
            suffix = "Cloud API" if record.get("source") == "cloud" else f"{record['items']} карточек"
            listbox.insert("end", f"{record['label']} — {suffix}")
        if records:
            listbox.selection_set(0)
            status.set("Выберите клуб и запустите сканирование.")

    def add_catalog():
        path = filedialog.askopenfilename(title="Каталог GodjiOS", filetypes=[("GodjiOS catalog", "*.zip"), ("All files", "*.*")])
        if path:
            refresh(path)

    def add_cloud_catalog():
        from .cloud_api import list_clubs
        window = tk.Toplevel(root)
        window.title("Godji Cloud API")
        window.transient(root)
        window.grab_set()
        panel = ttk.Frame(window, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Manager API key", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(panel, text="Ключ используется только для этого запуска и не сохраняется на диск.").pack(anchor="w", pady=(3, 7))
        token = tk.StringVar(value=__import__("os").environ.get("GODJI_MANAGER_API_KEY", ""))
        url = tk.StringVar(value="https://cloud.godjios.ru")
        ttk.Entry(panel, textvariable=token, show="•", width=58).pack(fill="x")
        ttk.Label(panel, text="Адрес Cloud").pack(anchor="w", pady=(10, 2))
        ttk.Entry(panel, textvariable=url, width=58).pack(fill="x")
        clubs_box = tk.Listbox(panel, height=10, width=58, exportselection=False)
        clubs = []
        note = tk.StringVar(value="Введите ключ и нажмите «Получить клубы»." )

        def load_clubs():
            nonlocal clubs
            try:
                clubs = list_clubs(url.get(), token.get())
            except Exception as error:
                messagebox.showerror("GodjiScanner", str(error), parent=window)
                return
            clubs_box.delete(0, "end")
            for club in clubs:
                clubs_box.insert("end", club["name"])
            if clubs:
                clubs_box.selection_set(0)
                note.set("Выберите клуб и добавьте его в список сканирования.")
            else:
                note.set("Для этого ключа нет доступных клубов.")

        def select_cloud_club():
            selected = clubs_box.curselection()
            if not selected:
                messagebox.showwarning("GodjiScanner", "Сначала выберите клуб.", parent=window)
                return
            club = clubs[selected[0]]
            remote_records.append({"source": "cloud", "club": club, "token": token.get(), "baseUrl": url.get(),
                                   "label": club["name"], "items": 0})
            refresh()
            listbox.selection_clear(0, "end")
            listbox.selection_set(len(catalog_paths) - 1)
            status.set("Клуб из Cloud добавлен. Запустите сканирование.")
            window.destroy()

        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(9, 3))
        ttk.Button(actions, text="Получить клубы", command=load_clubs).pack(side="left")
        ttk.Button(actions, text="Добавить выбранный клуб", command=select_cloud_club).pack(side="right")
        clubs_box.pack(fill="both", expand=True)
        ttk.Label(panel, textvariable=note, wraplength=440).pack(anchor="w", pady=(7, 0))

    def start():
        selected = listbox.curselection()
        if not selected:
            messagebox.showwarning("GodjiScanner", "Сначала выберите клуб.")
            return
        record = catalog_paths[selected[0]]
        destination = default_output_folder(output_base, record["label"])
        status.set("Сканирование запущено. Не закрывайте это окно…")
        root.update()
        def progress(event):
            if event.get("event") == "progress":
                status.set("Поиск: " + event.get("source", "…"))
                root.update_idletasks()
        try:
            if record.get("source") == "cloud":
                exported = scan_cloud_club(record["baseUrl"], record["token"], record["club"], destination,
                                           deep_scan.get(), include_reviewed.get(), progress)
            else:
                exported = scan_club(record["path"], destination, deep_scan.get(), include_reviewed.get(), progress)
        except Exception as error:
            status.set("Ошибка сканирования")
            messagebox.showerror("GodjiScanner", str(error))
            return
        status.set("Готово")
        messagebox.showinfo("GodjiScanner", "Готов ZIP для импорта:\n" + exported["output"] + "\n\nОтчёт:\n" + exported["report"])

    def start_automatic():
        if automatic is None:
            messagebox.showwarning("GodjiScanner", "Автоматический режим недоступен в этом запуске.")
            return
        root.destroy()
        automatic()

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(10, 0))
    ttk.Button(buttons, text="Выбрать ZIP-каталог…", command=add_catalog).pack(side="left")
    ttk.Button(buttons, text="Выбрать клуб из Cloud…", command=add_cloud_catalog).pack(side="left", padx=(8, 0))
    ttk.Button(buttons, text="Автоматический поиск без списка", command=start_automatic).pack(side="right")
    ttk.Checkbutton(frame, text="Искать на всех локальных дисках", variable=deep_scan).pack(anchor="w", pady=(12, 2))
    ttk.Checkbutton(frame, text="Включить кандидаты, требующие проверки", variable=include_reviewed).pack(anchor="w")
    ttk.Button(frame, text="Сканировать клуб и создать ZIP", command=start).pack(anchor="e", pady=(10, 5))
    ttk.Label(frame, textvariable=status, wraplength=620).pack(anchor="w")
    refresh()
    root.mainloop()
