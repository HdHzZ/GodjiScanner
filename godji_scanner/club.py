"""Selected-club workflow: current catalog in, GodjiOS import ZIP out."""

from datetime import datetime
from pathlib import Path
import shutil

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


def scan_club(catalog_path, output_dir, all_drives=True, include_reviewed=False, emit=None):
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
    result["clubCatalog"] = str(catalog_path)
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


def default_output_folder(base, label):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(char if char.isalnum() or char in "-_ " else "_" for char in label).strip() or "club"
    return Path(base) / f"{safe}-{stamp}"


def run_club_selector(catalogs_dir, output_base):
    """Open a simple operator UI to select a club catalog and produce an import ZIP."""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox, filedialog
    except ImportError as error:
        raise RuntimeError("Tkinter is unavailable; use a Windows build of GodjiScanner") from error

    catalog_paths = []
    root = tk.Tk()
    root.title("GodjiScanner — сканирование клуба")
    root.geometry("700x470")
    root.minsize(620, 400)
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Выберите клуб", font=("Segoe UI", 15, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Каталог клуба сохраняет ID, категории и обложки для импорта в GodjiOS.").pack(anchor="w", pady=(4, 12))
    listbox = tk.Listbox(frame, height=11, exportselection=False)
    listbox.pack(fill="both", expand=True)
    status = tk.StringVar(value="Добавьте ZIP-каталог клуба в папку clubs или выберите его вручную.")
    include_reviewed = tk.BooleanVar(value=False)
    deep_scan = tk.BooleanVar(value=True)

    def refresh(extra=None):
        nonlocal catalog_paths
        records = available_catalogs(catalogs_dir)
        if extra:
            try:
                records.append({"path": Path(extra), "label": catalog_label(extra), "items": len(read_catalog(extra)["items"])})
            except (OSError, ValueError) as error:
                messagebox.showerror("GodjiScanner", f"Не удалось открыть каталог: {error}")
                return
        catalog_paths = records
        listbox.delete(0, "end")
        for record in records:
            listbox.insert("end", f"{record['label']} — {record['items']} карточек")
        if records:
            listbox.selection_set(0)
            status.set("Выберите клуб и запустите сканирование.")

    def add_catalog():
        path = filedialog.askopenfilename(title="Каталог GodjiOS", filetypes=[("GodjiOS catalog", "*.zip"), ("All files", "*.*")])
        if path:
            refresh(path)

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
            exported = scan_club(record["path"], destination, deep_scan.get(), include_reviewed.get(), progress)
        except Exception as error:
            status.set("Ошибка сканирования")
            messagebox.showerror("GodjiScanner", str(error))
            return
        status.set("Готово")
        messagebox.showinfo("GodjiScanner", "Готов ZIP для импорта:\n" + exported["output"] + "\n\nОтчёт:\n" + exported["report"])

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(10, 0))
    ttk.Button(buttons, text="Добавить каталог ZIP…", command=add_catalog).pack(side="left")
    ttk.Checkbutton(frame, text="Искать на всех локальных дисках", variable=deep_scan).pack(anchor="w", pady=(12, 2))
    ttk.Checkbutton(frame, text="Включить кандидаты, требующие проверки", variable=include_reviewed).pack(anchor="w")
    ttk.Button(frame, text="Сканировать клуб и создать ZIP", command=start).pack(anchor="e", pady=(10, 5))
    ttk.Label(frame, textvariable=status, wraplength=620).pack(anchor="w")
    refresh()
    root.mainloop()
