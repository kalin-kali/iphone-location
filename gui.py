#!/usr/bin/env python3
import os
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

import tkintermapview

import loc

REFRESH_MS = 2000
LOG_POLL_MS = 1000
LOG_MAX_LINES = 200

MAP_TILE_SERVER = "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
MAP_DEFAULT_POSITION = (42.6977, 23.3219)  # Sofia
MAP_DEFAULT_ZOOM = 7
MAP_WIDTH = 540
MAP_HEIGHT = 640
LEFT_WIDTH = 400
LEFT_HEIGHT = 640

BG = "#0a0d0a"
FIELD_BG = "#0f140f"
FG = "#00ff41"
FG_DIM = "#189632"
BORDER = "#00ff41"
DANGER = "#ff3b3b"
PICKED = "#ffdd55"

MONO_CANDIDATES = ("DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono", "Consolas", "Courier New", "Courier")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("iPhone Location // root@kali")
        self.resizable(False, False)

        self._setup_style()

        self.status_var = tk.StringVar(value="ПРОВЕРКА...")
        self.cursor_var = tk.StringVar(value="_")
        self._preset_markers = []
        self._picked_marker = None
        self._route_path = None
        self._suppress_map_click = False
        self._drawing = False
        self._drawn_points = []
        self._drawn_markers = []
        self._drawn_path = None

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, padding=(10, 10, 5, 10), width=LEFT_WIDTH, height=LEFT_HEIGHT)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        right = ttk.Frame(body, padding=(5, 10, 10, 10))
        right.pack(side="left", fill="both", expand=True)

        # --- status ---
        status_frame = ttk.Frame(left)
        status_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(status_frame, text="[root@kali]$ статус:", font=self.font_bold).pack(anchor="w")
        status_row = ttk.Frame(status_frame)
        status_row.pack(fill="x")
        ttk.Label(status_row, textvariable=self.status_var, font=self.font_bold, foreground=FG).pack(side="left")
        ttk.Label(status_row, textvariable=self.cursor_var, font=self.font_bold, foreground=FG).pack(side="left")
        self._blink_cursor()

        # --- tabs: presets / routes / search ---
        notebook = ttk.Notebook(left)
        notebook.pack(fill="x", pady=(0, 8))

        presets_tab = ttk.Frame(notebook, padding=8)
        notebook.add(presets_tab, text="Локации")
        self.presets_frame = ttk.Frame(presets_tab)
        self.presets_frame.pack(fill="x")
        ttk.Button(presets_tab, text="+ Добави локация", command=self.add_preset).pack(anchor="w", pady=(6, 0))

        routes_tab = ttk.Frame(notebook, padding=8)
        notebook.add(routes_tab, text="Маршрути")
        self.routes_frame = ttk.Frame(routes_tab)
        self.routes_frame.pack(fill="x")
        ttk.Button(routes_tab, text="+ Добави маршрут (.gpx)", command=self.add_route).pack(anchor="w", pady=(6, 0))

        draw_row = ttk.Frame(routes_tab)
        draw_row.pack(fill="x", pady=(4, 0))
        self.draw_button = ttk.Button(draw_row, text="✎ Рисувай маршрут", command=self.toggle_drawing)
        self.draw_button.pack(side="left")
        self.draw_save_button = ttk.Button(
            draw_row, text="✓ Запази", command=self.save_drawing, state="disabled"
        )
        self.draw_save_button.pack(side="left", padx=4)
        self.draw_cancel_button = ttk.Button(
            draw_row, text="✕ Отказ", style="Danger.TButton", command=self.cancel_drawing, state="disabled"
        )
        self.draw_cancel_button.pack(side="left")

        interval_row = ttk.Frame(routes_tab)
        interval_row.pack(fill="x", pady=(6, 0))
        ttk.Label(interval_row, text="Секунди между точки:").pack(side="left")
        self.interval_entry = ttk.Entry(interval_row, width=6)
        self.interval_entry.pack(side="left", padx=(4, 0))
        ttk.Label(routes_tab, text="(празно = авто темпо по разстояние)", foreground=FG_DIM).pack(
            anchor="w", pady=(2, 0)
        )

        self.draw_status_var = tk.StringVar(value="")
        ttk.Label(routes_tab, textvariable=self.draw_status_var, foreground=FG_DIM).pack(anchor="w", pady=(4, 0))

        playback_frame = ttk.LabelFrame(routes_tab, text="Playback", padding=8)
        playback_frame.pack(fill="x", pady=(8, 0))

        self.smooth_enabled = True
        self.smooth_toggle_button = ttk.Button(
            playback_frame, text="⚡ Плавно движение (ВКЛ.)", command=self.toggle_smooth
        )
        self.smooth_toggle_button.pack(fill="x", pady=(6, 0))

        search_tab = ttk.Frame(notebook, padding=8)
        notebook.add(search_tab, text="Търсене")
        search_row = ttk.Frame(search_tab)
        search_row.pack(fill="x")
        self.search_entry = ttk.Entry(search_row)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<Return>", lambda e: self.do_search())
        ttk.Button(search_row, text="Търси", command=self.do_search).pack(side="left", padx=(6, 0))
        self.search_results_frame = ttk.Frame(search_tab)
        self.search_results_frame.pack(fill="x", pady=(8, 0))

        # --- custom coordinates (always visible, paired with map clicks) ---
        custom_frame = ttk.LabelFrame(left, text="Собствени координати", padding=10)
        custom_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(custom_frame, text="Latitude:").grid(row=0, column=0, sticky="w")
        self.lat_entry = ttk.Entry(custom_frame)
        self.lat_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=3)

        ttk.Label(custom_frame, text="Longitude:").grid(row=1, column=0, sticky="w")
        self.lon_entry = ttk.Entry(custom_frame)
        self.lon_entry.grid(row=1, column=1, sticky="ew", padx=5, pady=3)
        custom_frame.columnconfigure(1, weight=1)

        ttk.Button(custom_frame, text="Постави ме тук", command=self.set_custom).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        ttk.Label(custom_frame, text="(кликни на картата, за да попълниш полетата)", foreground=FG_DIM).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )

        # --- stop button reserves its slot at the bottom before console expands ---
        stop_frame = ttk.Frame(left)
        stop_frame.pack(fill="x", side="bottom")
        ttk.Button(stop_frame, text="Спри симулацията (реална локация)", command=self.stop).pack(fill="x")

        # --- console fills whatever is left ---
        console_frame = ttk.LabelFrame(left, text="Конзола", padding=(6, 6))
        console_frame.pack(fill="both", expand=True, pady=(0, 8))

        self.log_text = tk.Text(
            console_frame, height=4, bg=BG, fg=FG, insertbackground=FG,
            font=self.font_normal, relief="flat", wrap="word", state="disabled",
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER, bd=0,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(console_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        # --- map (right column) ---
        self.map_widget = tkintermapview.TkinterMapView(right, width=MAP_WIDTH, height=MAP_HEIGHT, corner_radius=0)
        self.map_widget.pack(fill="both", expand=True)
        self.map_widget.set_tile_server(MAP_TILE_SERVER, max_zoom=20)
        self.map_widget.set_position(*MAP_DEFAULT_POSITION)
        self.map_widget.set_zoom(MAP_DEFAULT_ZOOM)
        self.map_widget.add_left_click_map_command(self.on_map_click)
        self._suppress_map_click_on(self.map_widget.button_zoom_in)
        self._suppress_map_click_on(self.map_widget.button_zoom_out)

        self.render_presets()
        self.render_routes()

        self._log_pos = os.path.getsize(loc.LOG_FILE) if os.path.exists(loc.LOG_FILE) else 0
        self.after(200, self.refresh_status)
        self.after(300, self.poll_log)

        self.update_idletasks()
        self.geometry(f"{self.winfo_reqwidth()}x{self.winfo_reqheight()}")

    def _setup_style(self):
        available = set(tkfont.families())
        family = next((f for f in MONO_CANDIDATES if f in available), "TkFixedFont")
        self.font_normal = (family, 10)
        self.font_bold = (family, 10, "bold")
        self.font_title = (family, 11, "bold")
        self.option_add("*Font", self.font_normal)

        self.configure(bg=BG)

        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".", background=BG, foreground=FG, font=self.font_normal)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=self.font_normal)
        style.configure(
            "TLabelframe", background=BG, foreground=FG, bordercolor=BORDER, relief="solid", borderwidth=1
        )
        style.configure("TLabelframe.Label", background=BG, foreground=FG, font=self.font_title)

        style.configure(
            "TButton", background=BG, foreground=FG, bordercolor=BORDER, focuscolor=BG,
            font=self.font_normal, padding=6, relief="solid", borderwidth=1,
        )
        style.map(
            "TButton",
            background=[("active", FG), ("pressed", FG)],
            foreground=[("active", BG), ("pressed", BG)],
            bordercolor=[("active", FG)],
        )

        style.configure(
            "Danger.TButton", background=BG, foreground=DANGER, bordercolor=DANGER,
            focuscolor=BG, font=self.font_bold, padding=4, relief="solid", borderwidth=1,
        )
        style.map(
            "Danger.TButton",
            background=[("active", DANGER), ("pressed", DANGER)],
            foreground=[("active", BG), ("pressed", BG)],
        )

        style.configure(
            "TEntry", fieldbackground=FIELD_BG, foreground=FG, insertcolor=FG,
            bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
        )
        style.map("TEntry", fieldbackground=[("focus", FIELD_BG)])

        style.configure(
            "Vertical.TScrollbar", background=BG, troughcolor=BG, bordercolor=BORDER,
            arrowcolor=FG, relief="flat",
        )
        style.map("Vertical.TScrollbar", background=[("active", FG)])

        style.configure("TNotebook", background=BG, bordercolor=BORDER, tabmargins=[2, 2, 2, 0])
        style.configure(
            "TNotebook.Tab", background=BG, foreground=FG_DIM, font=self.font_normal,
            padding=[10, 4], bordercolor=BORDER,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", FG)],
            foreground=[("selected", BG)],
        )

    def _blink_cursor(self):
        self.cursor_var.set(" " if self.cursor_var.get() == "_" else "_")
        self.after(500, self._blink_cursor)

    def poll_log(self):
        try:
            size = os.path.getsize(loc.LOG_FILE) if os.path.exists(loc.LOG_FILE) else 0
            if size < self._log_pos:
                self._log_pos = 0
            if size > self._log_pos:
                with open(loc.LOG_FILE) as f:
                    f.seek(self._log_pos)
                    new_data = f.read()
                    self._log_pos = f.tell()
                self._append_log(new_data)
        except OSError:
            pass
        self.after(LOG_POLL_MS, self.poll_log)

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{line_count - LOG_MAX_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # --- map ---

    def on_map_click(self, coords):
        if self._suppress_map_click:
            self._suppress_map_click = False
            return
        if self._drawing:
            self._add_drawn_point(coords)
            return
        lat, lon = coords
        self.lat_entry.delete(0, "end")
        self.lat_entry.insert(0, f"{lat:.6f}")
        self.lon_entry.delete(0, "end")
        self.lon_entry.insert(0, f"{lon:.6f}")
        self._set_picked_marker(lat, lon, "избрано")

    def _set_picked_marker(self, lat, lon, text, pan=True):
        if self._picked_marker:
            self._picked_marker.delete()
        self._picked_marker = self.map_widget.set_marker(
            lat, lon, text=text, marker_color_circle=PICKED, marker_color_outside=PICKED, text_color=PICKED,
        )
        if pan:
            self.map_widget.set_position(lat, lon)

    def render_map_presets(self):
        for m in self._preset_markers:
            m.delete()
        self._preset_markers = []
        for name, coords in loc.load_locations().items():
            marker = self.map_widget.set_marker(
                coords["lat"], coords["lon"], text=name,
                marker_color_circle=FG, marker_color_outside=FG, text_color=FG,
                command=lambda m, lat=coords["lat"], lon=coords["lon"], n=name: self._marker_click(lat, lon, n),
            )
            self._preset_markers.append(marker)

    def _marker_click(self, lat, lon, name):
        self._suppress_map_click = True
        if self._drawing:
            self._add_drawn_point((lat, lon))
            return
        self.set_location(lat, lon, name)

    def _suppress_map_click_on(self, canvas_button):
        """tkintermapview's on-canvas buttons (zoom +/-) don't stop their click
        from also reaching the generic map-click handler, so a click on them
        would otherwise also drop a point / fill the coordinate fields."""
        original_command = canvas_button.command

        def wrapped():
            self._suppress_map_click = True
            original_command()

        canvas_button.command = wrapped

    def _show_route_path(self, points):
        if self._route_path:
            self._route_path.delete()
            self._route_path = None
        if points:
            self._route_path = self.map_widget.set_path(points, color=FG, width=3)
            self.map_widget.set_position(*points[0])
            self.map_widget.set_zoom(14)

    # --- draw route ---

    def toggle_drawing(self):
        if self._drawing:
            self.cancel_drawing()
            return
        self._drawing = True
        self.draw_button.configure(text="✎ Рисувам...")
        self.draw_save_button.configure(state="normal")
        self.draw_cancel_button.configure(state="normal")
        self.draw_status_var.set("0 точки (кликай на картата)")

    def _add_drawn_point(self, coords):
        lat, lon = coords
        self._drawn_points.append((lat, lon))
        marker = self.map_widget.set_marker(
            lat, lon, text=str(len(self._drawn_points)),
            marker_color_circle=PICKED, marker_color_outside=PICKED, text_color=BG,
        )
        self._drawn_markers.append(marker)
        if self._drawn_path:
            self._drawn_path.delete()
            self._drawn_path = None
        if len(self._drawn_points) >= 2:
            self._drawn_path = self.map_widget.set_path(self._drawn_points, color=PICKED, width=3)
        self.draw_status_var.set(f"{len(self._drawn_points)} точки (кликай на картата)")

    def save_drawing(self):
        if len(self._drawn_points) < 2:
            messagebox.showerror("Грешка", "Трябват поне 2 точки за маршрут (кликни на картата).")
            return
        interval_text = self.interval_entry.get().strip()
        interval_seconds = None
        if interval_text:
            try:
                interval_seconds = float(interval_text)
                if interval_seconds <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Грешка", "Секундите между точки трябва да са положително число.")
                return
        name = simpledialog.askstring("Нов маршрут", "Име:", parent=self)
        if not name:
            return
        loc.save_drawn_route(name, self._drawn_points, interval_seconds=interval_seconds)
        self._reset_drawing()
        self.render_routes()

    def cancel_drawing(self):
        self._reset_drawing()

    def _reset_drawing(self):
        for m in self._drawn_markers:
            m.delete()
        self._drawn_markers = []
        if self._drawn_path:
            self._drawn_path.delete()
            self._drawn_path = None
        self._drawn_points = []
        self._drawing = False
        self.draw_button.configure(text="✎ Рисувай маршрут")
        self.draw_save_button.configure(state="disabled")
        self.draw_cancel_button.configure(state="disabled")
        self.draw_status_var.set("")

    # --- presets ---

    def render_presets(self):
        for widget in self.presets_frame.winfo_children():
            widget.destroy()
        locations = loc.load_locations()
        if not locations:
            ttk.Label(self.presets_frame, text="(няма записани локации)").pack()
        else:
            for name, coords in locations.items():
                row = ttk.Frame(self.presets_frame)
                row.pack(fill="x", pady=2)
                ttk.Button(
                    row, text=name, width=18,
                    command=lambda lat=coords["lat"], lon=coords["lon"], n=name: self.set_location(lat, lon, n),
                ).pack(side="left")
                ttk.Button(
                    row, text="✕", width=3, style="Danger.TButton", command=lambda n=name: self.remove_preset(n)
                ).pack(side="left", padx=4)
        if hasattr(self, "map_widget"):
            self.render_map_presets()

    def add_preset(self):
        name = simpledialog.askstring("Нова локация", "Име:", parent=self)
        if not name:
            return
        lat = simpledialog.askfloat("Нова локация", "Latitude:", parent=self)
        if lat is None:
            return
        lon = simpledialog.askfloat("Нова локация", "Longitude:", parent=self)
        if lon is None:
            return
        locations = loc.load_locations()
        locations[name] = {"lat": lat, "lon": lon}
        loc.save_locations(locations)
        self.render_presets()

    def remove_preset(self, name):
        if not messagebox.askyesno("Изтриване", f"Да изтрия ли '{name}'?"):
            return
        locations = loc.load_locations()
        locations.pop(name, None)
        loc.save_locations(locations)
        self.render_presets()

    # --- routes ---

    def render_routes(self):
        for widget in self.routes_frame.winfo_children():
            widget.destroy()
        routes = loc.list_routes()
        if not routes:
            ttk.Label(self.routes_frame, text="(няма запазени маршрути)").pack()
            return
        for name in routes:
            count = loc.route_point_count(loc.route_path(name))
            label = f"{name} ({count} т.)" if count is not None else name
            row = ttk.Frame(self.routes_frame)
            row.pack(fill="x", pady=2)
            ttk.Button(
                row, text=label, width=24,
                command=lambda n=name: self.play_route(n),
            ).pack(side="left")
            ttk.Button(
                row, text="✕", width=3, style="Danger.TButton", command=lambda n=name: self.remove_route(n)
            ).pack(side="left", padx=4)

    def add_route(self):
        src_path = filedialog.askopenfilename(
            title="Избери GPX файл", filetypes=[("GPX файлове", "*.gpx"), ("Всички файлове", "*.*")]
        )
        if not src_path:
            return
        name = simpledialog.askstring("Нов маршрут", "Име:", parent=self)
        if not name:
            return
        try:
            loc.add_route(name, src_path)
        except (FileNotFoundError, ValueError) as e:
            messagebox.showerror("Грешка", str(e))
            return
        self.render_routes()

    def remove_route(self, name):
        if not messagebox.askyesno("Изтриване", f"Да изтрия ли маршрут '{name}'?"):
            return
        loc.remove_route(name)
        self.render_routes()

    def toggle_smooth(self):
        self.smooth_enabled = not self.smooth_enabled
        if self.smooth_enabled:
            self.smooth_toggle_button.configure(text="⚡ Плавно движение (ВКЛ.)", style="TButton")
        else:
            self.smooth_toggle_button.configure(text="⚡ ТЕЛЕПОРТ между точки (ИЗКЛ. плавно)", style="Danger.TButton")

    def play_route(self, name):
        path = loc.route_path(name)

        self.status_var.set(f"Зареждам маршрут... ({name})")

        def worker():
            try:
                loc.do_play_route(path, smooth=self.smooth_enabled)
                points = loc.route_points(path)
                self.after(0, lambda: self._show_route_path(points))
                self.after(0, lambda: self.status_var.set(f"Активен маршрут: {name}"))
            except SystemExit:
                self.after(0, lambda: self.status_var.set("Грешка - виж tunneld.log"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Грешка", str(e)))
                self.after(0, self.refresh_status)

        threading.Thread(target=worker, daemon=True).start()

    # --- custom coordinates / search ---

    def set_custom(self):
        try:
            lat = float(self.lat_entry.get())
            lon = float(self.lon_entry.get())
        except ValueError:
            messagebox.showerror("Грешка", "Въведи валидни числа за latitude/longitude.")
            return
        self.set_location(lat, lon, None)

    def do_search(self):
        query = self.search_entry.get().strip()
        if not query:
            return
        for widget in self.search_results_frame.winfo_children():
            widget.destroy()
        ttk.Label(self.search_results_frame, text="Търся...").pack()

        def worker():
            try:
                results = loc.search_places(query)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Грешка при търсене", str(e)))
                self.after(0, lambda: [w.destroy() for w in self.search_results_frame.winfo_children()])
                return
            self.after(0, lambda: self.show_search_results(results))

        threading.Thread(target=worker, daemon=True).start()

    def show_search_results(self, results):
        for widget in self.search_results_frame.winfo_children():
            widget.destroy()
        if not results:
            ttk.Label(self.search_results_frame, text="Няма намерени резултати.").pack()
            return
        for r in results:
            name = r["name"]
            short = name if len(name) <= 34 else name[:31] + "..."
            row = ttk.Frame(self.search_results_frame)
            row.pack(fill="x", pady=2)
            ttk.Button(
                row, text=short, width=32,
                command=lambda lat=r["lat"], lon=r["lon"], n=name: self.set_location(lat, lon, n),
            ).pack(side="left")

    def set_location(self, lat, lon, name):
        label = name if name else f"{lat}, {lon}"
        self.status_var.set(f"Свързвам се... ({label})")
        self._set_picked_marker(lat, lon, label)
        if self._route_path:
            self._route_path.delete()
            self._route_path = None

        def worker():
            try:
                loc.do_set(lat, lon)
                self.after(0, lambda: self.status_var.set(f"Активна: {label}"))
            except SystemExit:
                self.after(0, lambda: self.status_var.set("Грешка - виж tunneld.log"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Грешка", str(e)))
                self.after(0, self.refresh_status)

        threading.Thread(target=worker, daemon=True).start()

    def stop(self):
        def worker():
            loc.do_stop()
            self.after(0, lambda: self.status_var.set("Реална GPS локация"))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_status(self):
        pid = loc.get_running_pid()
        if pid:
            label = loc.get_state_label()
            self.status_var.set(f"Активна: {label}" if label else f"Активна симулация (PID {pid})")
        else:
            self.status_var.set("Реална GPS локация")
        self.after(REFRESH_MS, self.refresh_status)


if __name__ == "__main__":
    App().mainloop()
