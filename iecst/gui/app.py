"""The window: a folder, a block, and the scan controls around it.

Everything with a decision in it is in :mod:`iecst.gui.panel`; this module only
draws it and forwards clicks.  Tkinter is in the standard library, so the GUI
adds nothing to install -- which is why it was chosen over a web view or Qt for
a tool whose whole point is that it has no dependencies.

Run it with ``python -m iecst.gui`` or the ``iecst-gui`` script.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from iecst.core.errors import IecError
from iecst.gui.panel import Panel, runnable_pous

__all__ = ["Gui", "main"]

#: How often the free run takes a scan, in milliseconds of wall-clock time.
#: One tick is one scan; the scan cycle itself is the block's own ``cycle_time``,
#: which is what the virtual clock -- and therefore every timer -- follows.
FREE_RUN_PERIOD = 20


class _Scrollable(ttk.Frame):
    """A frame that scrolls when the block has more variables than fit."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)

        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width)
        )
        # Tk sends wheel events to the widget under the pointer; on the canvas
        # alone the list would only scroll while hovering its own background.
        for widget in (self.canvas, self.body):
            widget.bind("<MouseWheel>", self._wheel)
            widget.bind("<Button-4>", self._wheel)
            widget.bind("<Button-5>", self._wheel)

    def _wheel(self, event: tk.Event) -> None:
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step, "units")


class Gui:
    """The application window."""

    def __init__(self, root: tk.Tk, folder: str | None = None, pou: str | None = None) -> None:
        self.root = root
        self.panel = Panel()
        self._free_running = False
        self._free_run_job: str | None = None
        self._value_vars: dict[str, tk.StringVar] = {}
        self._readout_vars: dict[str, tk.StringVar] = {}

        root.title("iecst — прогон блока из CODESYS")
        root.minsize(760, 520)

        self._build_toolbar()
        self._build_variables()
        self._build_controls()

        if folder:
            self.folder_var.set(folder)
            self.load()

    # -- construction ------------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        bar.pack(fill="x")

        self.folder_var = tk.StringVar()
        self.pou_var = tk.StringVar()
        self.cycle_var = tk.StringVar(value="10ms")

        ttk.Label(bar, text="Папка проекта:").grid(row=0, column=0, sticky="w")
        ttk.Entry(bar, textvariable=self.folder_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(bar, text="Обзор…", command=self.browse).grid(row=0, column=2)

        ttk.Label(bar, text="Блок:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.pou_box = ttk.Combobox(bar, textvariable=self.pou_var, state="readonly")
        self.pou_box.grid(row=1, column=1, sticky="ew", padx=4, pady=(6, 0))

        ttk.Label(bar, text="Время скана:").grid(row=1, column=2, sticky="e", pady=(6, 0))
        ttk.Entry(bar, textvariable=self.cycle_var, width=9).grid(
            row=1, column=3, sticky="w", padx=(4, 0), pady=(6, 0)
        )
        ttk.Button(bar, text="Загрузить", command=self.load).grid(
            row=0, column=3, sticky="ew", padx=(4, 0)
        )

        bar.columnconfigure(1, weight=1)

    def _build_variables(self) -> None:
        area = ttk.Frame(self.root, padding=(10, 4))
        area.pack(fill="both", expand=True)

        self.inputs_box = ttk.LabelFrame(area, text="Входы — что задавать", padding=6)
        self.outputs_box = ttk.LabelFrame(area, text="Выходы — что контролировать", padding=6)
        self.inputs_box.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self.outputs_box.pack(side="left", fill="both", expand=True, padx=(5, 0))

        self.inputs_list = _Scrollable(self.inputs_box)
        self.outputs_list = _Scrollable(self.outputs_box)
        self.inputs_list.pack(fill="both", expand=True)
        self.outputs_list.pack(fill="both", expand=True)

    def _build_controls(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 4, 10, 10))
        bar.pack(fill="x")

        self.scans_var = tk.StringVar(value="1")
        self.period_var = tk.StringVar(value=str(FREE_RUN_PERIOD))

        ttk.Label(bar, text="Проходов:").pack(side="left")
        ttk.Spinbox(bar, from_=1, to=1_000_000, textvariable=self.scans_var, width=7).pack(
            side="left", padx=(4, 10)
        )
        ttk.Button(bar, text="Шаг", command=self.step_once).pack(side="left")
        ttk.Button(bar, text="Прогон", command=self.run_scans).pack(side="left", padx=4)

        self.free_button = ttk.Button(bar, text="Пуск", command=self.toggle_free_run)
        self.free_button.pack(side="left", padx=(10, 4))
        ttk.Label(bar, text="интервал, мс:").pack(side="left")
        ttk.Spinbox(bar, from_=1, to=5000, textvariable=self.period_var, width=6).pack(
            side="left", padx=(4, 10)
        )
        ttk.Button(bar, text="Сброс", command=self.reset).pack(side="left")

        self.status_var = tk.StringVar(value="проект не загружен")
        ttk.Label(self.root, textvariable=self.status_var, padding=(12, 0, 12, 8)).pack(
            fill="x"
        )

    # -- loading -----------------------------------------------------------

    def browse(self) -> None:
        chosen = filedialog.askdirectory(title="Папка с исходниками блока")
        if chosen:
            self.folder_var.set(chosen)
            self.load()

    def load(self) -> None:
        """Load the folder, filling the block picker from what is in it."""
        folder = self.folder_var.get().strip()
        if not folder:
            self._fail("Укажите папку с исходниками.")
            return

        self._stop_free_run()
        try:
            names = runnable_pous(folder)
            selected = self.pou_var.get().strip()
            if selected.upper() not in names:
                selected = names[0] if names else ""
            self.pou_box["values"] = names
            self.pou_var.set(selected)
            self.panel.open(folder, selected or None, self.cycle_var.get().strip() or "10ms")
        except IecError as exc:
            self._fail(exc.message)
            return

        self._fill_variable_lists()
        self._refresh_status()

    def _fill_variable_lists(self) -> None:
        for child in self.inputs_list.body.winfo_children():
            child.destroy()
        for child in self.outputs_list.body.winfo_children():
            child.destroy()
        self._value_vars.clear()
        self._readout_vars.clear()

        for index, row in enumerate(self.panel.inputs):
            self._add_input_row(index, row)
        for index, row in enumerate(self.panel.outputs):
            self._add_output_row(index, row)

    def _add_input_row(self, index: int, row: Any) -> None:
        variable = row.variable
        active = tk.BooleanVar(value=row.active)
        ttk.Checkbutton(
            self.inputs_list.body,
            variable=active,
            command=lambda n=variable.name, v=active: self._toggle(n, v.get()),
        ).grid(row=index, column=0, sticky="w")

        ttk.Label(self.inputs_list.body, text=variable.name, width=14).grid(
            row=index, column=1, sticky="w"
        )
        ttk.Label(self.inputs_list.body, text=variable.type_name, width=11, foreground="#666").grid(
            row=index, column=2, sticky="w"
        )

        members = variable.enum_members
        text = tk.StringVar(value=row.text)
        self._value_vars[variable.name] = text
        text.trace_add(
            "write", lambda *_a, n=variable.name, var=text: self.panel.set_text(n, var.get())
        )

        if variable.is_bool:
            # A BOOL is a checkbox, so there is no text to misread.
            boolean = tk.BooleanVar(value=row.text.strip().upper() == "TRUE")
            box = ttk.Checkbutton(
                self.inputs_list.body,
                variable=boolean,
                command=lambda n=variable.name, v=boolean, t=text: t.set(
                    "TRUE" if v.get() else "FALSE"
                ),
            )
        elif members:
            box = ttk.Combobox(
                self.inputs_list.body, textvariable=text, values=list(members), state="readonly"
            )
            if not text.get():
                text.set(next(iter(members)))
        else:
            box = ttk.Entry(self.inputs_list.body, textvariable=text)
        box.grid(row=index, column=3, sticky="ew", padx=(6, 2))
        self.inputs_list.body.columnconfigure(3, weight=1)

    def _add_output_row(self, index: int, row: Any) -> None:
        variable = row.variable
        active = tk.BooleanVar(value=row.active)
        ttk.Checkbutton(
            self.outputs_list.body,
            variable=active,
            command=lambda n=variable.name, v=active: self._toggle_output(n, v.get()),
        ).grid(row=index, column=0, sticky="w")

        ttk.Label(self.outputs_list.body, text=variable.name, width=14).grid(
            row=index, column=1, sticky="w"
        )
        ttk.Label(self.outputs_list.body, text=variable.type_name, width=11, foreground="#666").grid(
            row=index, column=2, sticky="w"
        )

        readout = tk.StringVar(value="—")
        self._readout_vars[variable.name] = readout
        ttk.Label(self.outputs_list.body, textvariable=readout).grid(
            row=index, column=3, sticky="w", padx=(6, 2)
        )
        self.outputs_list.body.columnconfigure(3, weight=1)

    # -- what the checkboxes mean ------------------------------------------

    def _toggle(self, name: str, active: bool) -> None:
        """Ticking an input both drives it and writes it once, at once.

        Otherwise the value in the entry would sit unused until the next scan,
        and a field that has been typed into but not ticked looks broken.
        """
        self.panel.set_active(name, active)
        if active:
            self._run_guarded(self.panel.write_inputs)

    def _toggle_output(self, name: str, active: bool) -> None:
        self.panel.set_active(name, active)

    # -- running -----------------------------------------------------------

    def step_once(self) -> None:
        self._run_guarded(self.panel.step)

    def run_scans(self) -> None:
        try:
            scans = int(self.scans_var.get())
        except ValueError:
            self._fail("«Проходов» должно быть числом.")
            return
        if scans > 0:
            self._run_guarded(lambda: self.panel.run(scans))

    def toggle_free_run(self) -> None:
        if self._free_running:
            self._stop_free_run()
        else:
            self._start_free_run()

    def _start_free_run(self) -> None:
        if not self.panel.loaded or self._free_running:
            return
        self._free_running = True
        self.free_button.configure(text="Стоп")
        self._tick()

    def _stop_free_run(self) -> None:
        self._free_running = False
        self.free_button.configure(text="Пуск")
        if self._free_run_job is not None:
            self.root.after_cancel(self._free_run_job)
            self._free_run_job = None

    def _tick(self) -> None:
        """One free-run scan, then come back after the configured interval."""
        if not self._free_running:
            return
        if not self._run_guarded(self.panel.step):
            self._stop_free_run()
            return
        self._free_run_job = self.root.after(self._period_ms(), self._tick)

    def _period_ms(self) -> int:
        try:
            return max(1, int(self.period_var.get()))
        except ValueError:
            return FREE_RUN_PERIOD

    def reset(self) -> None:
        self._stop_free_run()
        if self.panel.loaded:
            self._run_guarded(self.panel.reset)

    def _run_guarded(self, action: Callable[[], Any]) -> bool:
        """Run *action*, reporting a bad value or a runtime error instead of dying.

        A failed step must not leave the free run ticking against a broken
        state, so the caller learns whether it worked.
        """
        try:
            action()
        except IecError as exc:
            self._fail(exc.message)
            return False
        self._refresh_status()
        return True

    # -- status ------------------------------------------------------------

    def _refresh_status(self) -> None:
        self.status_var.set(self.panel.describe())
        for name, text in self.panel.read().items():
            if name in self._readout_vars:
                self._readout_vars[name].set(text)

    def _fail(self, message: str) -> None:
        self.status_var.set(message)
        messagebox.showerror("iecst", message)


def main(folder: str | None = None, pou: str | None = None) -> int:
    """Open the window.  *folder* and *pou* pre-fill it, for convenience."""
    root = tk.Tk()
    Gui(root, folder, pou)
    root.mainloop()
    return 0
