#!/usr/bin/env python3
"""
LimitLens Menubar Application.

This module provides a lightweight macOS menubar application using `rumps`.
It polls the LimitLens CLI for quota data and updates the menubar with real-time
remaining quota percentages, helping users avoid rate limits. It also triggers
desktop notifications when quotas run critically low.
"""
import json
import os
import re
import shlex
import subprocess  # nosec B404
import sys
import threading
import time
import warnings
from datetime import datetime

try:
    from .logging import get_logger as _get_logger
    log = _get_logger("limitlens.menubar")
except Exception:  # pragma: no cover - isolated import failure
    import logging
    log = logging.getLogger("limitlens.menubar")
    log.addHandler(logging.NullHandler())


def _write_log_direct(message, *, exc=False):
    """Append *message* directly to the log file, bypassing the singleton logger.

    This ensures that callers which patch HOME or LIMITLENS_LOG_PATH *after*
    module import still get output in the expected file (the singleton logger
    captures the path at initialisation time).
    """
    import traceback
    try:
        log_path = os.environ.get("LIMITLENS_LOG_PATH") or os.path.join(
            os.path.expanduser("~/.cache/limitlens"), "limitlens.log"
        )
        log_path = os.path.expanduser(log_path)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as _f:
            _f.write(f"[{datetime.now().isoformat()}] {message}\n")
            if exc:
                traceback.print_exc(file=_f)
            _f.write("\n")
    except Exception:
        pass

MENUBAR_REFRESH_TIMEOUT_SECONDS = 45
MENUBAR_SYNC_CODEX_TIMEOUT_SECONDS = 90

_RUMPS_AVAILABLE = True
try:
    import rumps
except ImportError:  # pragma: no cover - exercised only on non-macOS without mac extras
    _RUMPS_AVAILABLE = False

    class _UnavailableRumps:
        separator = "---"

        class App:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("limitlens-menubar requires macOS and the 'rumps' package")

        class MenuItem:
            def __init__(self, title, callback=None):
                self.title = title
                self.callback = callback

            def __str__(self):
                return self.title

        @staticmethod
        def timer(_interval):
            return lambda func: func

        @staticmethod
        def quit_application():
            return None

    rumps = _UnavailableRumps()


_APPKIT_AVAILABLE = True
try:
    from AppKit import (  # type: ignore
        NSAppearance,
        NSBezelStyleInline,
        NSBezelStyleRounded,
        NSButton,
        NSColor,
        NSControlSizeSmall,
        NSFont,
        NSMakePoint,
        NSMakeRect,
        NSMinYEdge,
        NSPopover,
        NSPopoverBehaviorTransient,
        NSProgressIndicator,
        NSScrollView,
        NSTextField,
        NSView,
        NSViewController,
        NSVisualEffectBlendingModeBehindWindow,
        NSVisualEffectMaterialHUDWindow,
        NSVisualEffectView,
        NSViewWidthSizable,
        NSViewHeightSizable,
    )
except Exception:  # pragma: no cover - exercised outside macOS/pyobjc runtimes
    _APPKIT_AVAILABLE = False

    NSAppearance = None
    NSBezelStyleInline = None
    NSBezelStyleRounded = None
    NSButton = None
    NSColor = None
    NSControlSizeSmall = None
    NSFont = None
    NSMakePoint = None
    NSMakeRect = None
    NSMinYEdge = None
    NSPopover = None
    NSPopoverBehaviorTransient = None
    NSProgressIndicator = None
    NSScrollView = None
    NSTextField = None
    NSView = None
    NSViewController = None
    NSVisualEffectBlendingModeBehindWindow = None
    NSVisualEffectMaterialHUDWindow = None
    NSVisualEffectView = None
    NSViewWidthSizable = None
    NSViewHeightSizable = None


POPOVER_WIDTH = 420
POPOVER_HEIGHT = 540

# Modern dashboard layout constants.
PAD_X = 18
PAD_Y = 16
CARD_RADIUS = 16
ROW_RADIUS = 12
PROGRESS_HEIGHT = 6
ROW_HEIGHT = 66
ROW_GAP = 8


class LimitLensApp(rumps.App):
    def __init__(self):
        import os
        base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_dir, "assets", "iconTemplate.png")
        if not os.path.exists(icon_path):
            icon_path = None

        super(LimitLensApp, self).__init__(
            "⏳ Loading...",
            icon=icon_path,
            template=True if icon_path else None,
            quit_button=None
        )

        # Persistent items — stored on self so their callbacks are never lost
        self._item_refresh = rumps.MenuItem("Refresh", callback=self._on_refresh)
        self._item_quick_refresh = rumps.MenuItem("Quick Refresh", callback=self._on_quick_refresh)
        self._item_open_config = rumps.MenuItem("Open Config", callback=self._on_open_config)
        self._item_copy_status = rumps.MenuItem("Copy Status", callback=self._on_copy_status)
        self._item_open_dashboard = rumps.MenuItem("Open Dashboard", callback=self._on_open_dashboard)
        self._item_doctor = rumps.MenuItem("Run Doctor", callback=self._on_doctor)
        self._item_doctor_report = rumps.MenuItem("Copy Doctor Report", callback=self._on_doctor_report)
        self._item_quit    = rumps.MenuItem("Quit",    callback=self._on_quit)
        self._sep_top      = rumps.separator
        self._sep_bot      = rumps.separator

        self.menu = [self._item_open_dashboard, self._item_refresh, self._item_quick_refresh, self._sep_top, self._item_quit]

        self._is_fetching = False
        self._queued_sync_codex = None
        self._fetch_lock = threading.Lock()
        self._pending_title = None
        self._pending_menu_items = None
        self._pending_dashboard_model = None
        self._dashboard_model = self._empty_dashboard_model()
        self._popover = None
        self._popover_content = None
        self._popover_installed = False
        self._notified_set = set()
        self._has_loaded_once = False
        self._last_refresh_label = None
        self._last_status_summary = "LimitLens status\nNo data loaded yet."
        # Keep the macOS menubar compact. Full details live in the dropdown.
        self._max_title_items = 2
        self._install_popover()

        # Load display config for thresholds and refresh interval.
        try:
            from .config import load_display_config as _ldcfg
            _dcfg = _ldcfg()
            self._refresh_interval = max(30, int(_dcfg.get("menubar_refresh_seconds", 300)))
            self._notify_warn_pct = float(_dcfg.get("notify_warn_pct", 30.0))
            self._notify_critical_pct = float(_dcfg.get("notify_critical_pct", 10.0))
            self._eye_break_enabled = bool(_dcfg.get("eye_break_enabled", True))
            self._eye_break_interval = max(60, int(_dcfg.get("eye_break_minutes", 20)) * 60)
        except Exception:  # pragma: no cover - config failures must not crash the app
            self._refresh_interval = 300
            self._notify_warn_pct = 30.0
            self._notify_critical_pct = 10.0
            self._eye_break_enabled = True
            self._eye_break_interval = 20 * 60
            log.exception("Menubar config load failure")  # RedactFilter handles PII scrubbing

        self._start_refresh_timer()
        if self._eye_break_enabled:
            self._start_eye_break_timer()

    def _start_refresh_timer(self):
        """Start a daemon thread that refreshes data on the configured interval."""
        def _loop():
            while True:
                time.sleep(self._refresh_interval)
                self.fetch_data()
        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    def _start_eye_break_timer(self):
        """Start a daemon thread that reminds the user to rest their eyes."""
        def _loop():
            while True:
                time.sleep(self._eye_break_interval)
                self._send_eye_break_reminder()
        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    def _send_eye_break_reminder(self):
        self.notify("Eye Break", "Close your eyes for 20 seconds.")

    def refresh(self, _=None):
        self.fetch_data()

    def _on_refresh(self, _):
        # Manual refresh forces a full Codex account sync for fresh data.
        self.fetch_data(sync_codex=True)

    def _on_quick_refresh(self, _):
        self.fetch_data(sync_codex=False)

    def _on_open_config(self, _):
        try:
            from .config import auto_detect_providers, limitlens_config_path

            path = limitlens_config_path()
            if not os.path.exists(path):
                auto_detect_providers(path, write=True, interactive=False)
            subprocess.Popen(["open", path])  # nosec B603 B607
        except Exception as e:  # pragma: no cover - depends on macOS desktop state
            self._pending_title = f"⚠️ {str(e)[:15]}"

    def _on_copy_status(self, _):
        try:
            subprocess.run(
                ["pbcopy"],
                input=self._last_status_summary,
                text=True,
                check=False,
                timeout=5,
            )  # nosec B603 B607
            self._pending_title = "✓ Status copied"
        except Exception as e:  # pragma: no cover - depends on macOS pasteboard state
            self._pending_title = f"⚠️ {str(e)[:15]}"

    def _on_open_dashboard(self, _):
        self._show_dashboard()

    def _on_doctor(self, _):
        try:
            cmd = f"{shlex.quote(sys.executable)} -m limitlens doctor"
            script = f'tell application "Terminal" to do script {json.dumps(cmd)}'
            subprocess.Popen(["osascript", "-e", script])  # nosec B603 B607
            self._pending_title = "Doctor opened"
        except Exception as e:  # pragma: no cover - depends on macOS desktop state
            self._pending_title = f"⚠️ {str(e)[:15]}"

    def _on_doctor_report(self, _):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "limitlens", "doctor", "--report"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
                stdin=subprocess.DEVNULL,
            )  # nosec B603
            text = proc.stdout.strip() or proc.stderr.strip() or "No doctor report output."
            subprocess.run(["pbcopy"], input=text, text=True, check=False, timeout=5)  # nosec B603 B607
            self._pending_title = "✓ Report copied"
        except Exception as e:  # pragma: no cover - depends on macOS pasteboard state
            self._pending_title = f"⚠️ {str(e)[:15]}"

    def _on_quit(self, _):
        rumps.quit_application()

    def refreshDashboard_(self, _):
        self._on_refresh(None)

    def quickRefreshDashboard_(self, _):
        self._on_quick_refresh(None)

    def openConfigDashboard_(self, _):
        self._on_open_config(None)

    def copyStatusDashboard_(self, _):
        self._on_copy_status(None)

    def doctorDashboard_(self, _):
        self._on_doctor(None)

    def doctorReportDashboard_(self, _):
        self._on_doctor_report(None)

    def quitDashboard_(self, _):
        self._on_quit(None)

    @rumps.timer(1)
    def check_updates(self, _):
        if not self._popover_installed:
            self._install_popover()
        if self._pending_title is not None:
            self.title = self._pending_title
            self._pending_title = None
        if self._pending_menu_items is not None:
            # Rebuild only the dynamic middle section.
            # Never clear persistent items — that breaks their click handlers.
            self.menu.clear()
            if self._pending_menu_items:
                for item in self._pending_menu_items:
                    if isinstance(item, tuple) and len(item) == 3 and item[0] == "submenu":
                        submenu = rumps.MenuItem(item[1])
                        for child in item[2]:
                            if isinstance(child, tuple) and len(child) == 3 and child[0] == "callback":
                                callback = child[2]
                                if hasattr(callback, "callback") and hasattr(callback, "title"):
                                    submenu.add(callback)
                                else:
                                    submenu.add(rumps.MenuItem(child[1], callback=callback))
                            elif hasattr(child, "callback") and hasattr(child, "title"):
                                submenu.add(child)
                            else:
                                submenu.add(rumps.MenuItem(child))
                        self.menu.add(submenu)
                    else:
                        self.menu.add(item)
            else:
                self.menu.add(rumps.MenuItem("No active quotas found"))
            self.menu.add(self._sep_bot)
            self.menu.add(self._item_quit)
            self._pending_menu_items = None
        if self._pending_dashboard_model is not None:
            self._dashboard_model = self._pending_dashboard_model
            self._pending_dashboard_model = None
            self._refresh_dashboard()

    def notify(self, title, message):
        script = 'on run argv\n display notification (item 1 of argv) with title (item 2 of argv)\n end run'
        subprocess.Popen(["osascript", "-e", script, message, title])  # nosec B603 B607

    def _install_popover(self):
        if not _APPKIT_AVAILABLE:
            return
        status_item = self._status_item()
        if status_item is None:
            return
        try:
            self._popover = NSPopover.alloc().init()
            self._popover.setBehavior_(NSPopoverBehaviorTransient)
            self._popover.setContentSize_((POPOVER_WIDTH, POPOVER_HEIGHT))
            controller = NSViewController.alloc().init()
            controller.setView_(self._render_dashboard_view(self._dashboard_model))
            self._popover.setContentViewController_(controller)
            self._popover_content = controller
            self._popover_installed = True
        except Exception:
            self._popover = None
            self._popover_content = None
            self._popover_installed = False

    def _status_item(self):
        nsapp = getattr(self, "_nsapp", None)
        return getattr(nsapp, "nsstatusitem", None)

    def _show_dashboard(self):
        self._refresh_dashboard()
        if not self._popover_installed or self._popover is None:
            return
        try:
            status_item = self._status_item()
            if status_item is None:
                return
            button = status_item.button()
            if self._popover.isShown():
                self._popover.performClose_(None)
            else:
                self._popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, NSMinYEdge)
        except Exception:
            return

    def _refresh_dashboard(self):
        if not self._popover_installed or self._popover_content is None:
            return
        try:
            self._popover_content.setView_(self._render_dashboard_view(self._dashboard_model))
        except Exception:
            self._popover_installed = False

    @staticmethod
    def _ns_color(level="text"):
        if level == "good":
            return NSColor.colorWithCalibratedRed_green_blue_alpha_(0.26, 0.86, 0.42, 1.0)
        if level == "warn":
            return NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.76, 0.18, 1.0)
        if level == "bad":
            return NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.36, 0.30, 1.0)
        if level == "muted":
            return NSColor.colorWithCalibratedWhite_alpha_(0.78, 0.85)
        if level == "subtle":
            return NSColor.colorWithCalibratedWhite_alpha_(0.55, 0.90)
        if level == "card":
            return NSColor.colorWithCalibratedRed_green_blue_alpha_(0.22, 0.25, 0.30, 0.45)
        if level == "row":
            return NSColor.colorWithCalibratedRed_green_blue_alpha_(0.23, 0.26, 0.31, 0.62)
        if level == "button":
            return NSColor.colorWithCalibratedRed_green_blue_alpha_(0.31, 0.35, 0.42, 0.78)
        if level == "track":
            return NSColor.colorWithCalibratedWhite_alpha_(0.36, 0.38)
        return NSColor.colorWithCalibratedWhite_alpha_(0.96, 1.0)

    @staticmethod
    def _cg_color(level="text"):
        color = LimitLensApp._ns_color(level)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return color.CGColor()

    @staticmethod
    def _card(x, y, width, height, material="card"):
        card = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        card.setWantsLayer_(True)
        if card.layer():
            card.layer().setBackgroundColor_(LimitLensApp._cg_color(material))
            card.layer().setCornerRadius_(CARD_RADIUS)
        return card

    @staticmethod
    def _progress_bar(pct, x, y, width, height, level="text"):
        track = NSView.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        track.setWantsLayer_(True)
        if track.layer():
            track.layer().setBackgroundColor_(LimitLensApp._cg_color("track"))
            track.layer().setCornerRadius_(height / 2.0)

        fill_width = 0 if pct is None else int(width * max(0.0, min(100.0, float(pct))) / 100.0)
        if fill_width < 1 and pct is not None and float(pct) > 0:
            fill_width = 1
        fill = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, fill_width, height))
        fill.setWantsLayer_(True)
        if fill.layer():
            fill.layer().setBackgroundColor_(LimitLensApp._cg_color(level))
            fill.layer().setCornerRadius_(height / 2.0)
        track.addSubview_(fill)
        return track

    @staticmethod
    def _section_header(text, x, y, width, color="subtle"):
        return LimitLensApp._label(text, x, y, width, 16, size=11, weight="bold", color=color)

    def _pill_button(self, title, action, x, y, width, height):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setTitle_(title)
        button.setBezelStyle_(NSBezelStyleInline)
        button.setControlSize_(NSControlSizeSmall)
        button.setFont_(NSFont.systemFontOfSize_(11))
        button.setTarget_(self)
        button.setAction_(action)
        button.setWantsLayer_(True)
        if button.layer():
            button.layer().setBackgroundColor_(LimitLensApp._cg_color("button"))
            button.layer().setCornerRadius_(height / 2.0)
        return button

    @staticmethod
    def _label(text, x, y, width, height, size=13, weight="regular", color="text", alignment="left"):
        label = NSTextField.labelWithString_(str(text or ""))
        label.setFrame_(NSMakeRect(x, y, width, height))
        if weight == "bold":
            font = NSFont.boldSystemFontOfSize_(size)
        elif weight == "semibold":
            if hasattr(NSFont, "systemFontOfSize_weight_"):
                font = NSFont.systemFontOfSize_weight_(size, 0.65)
            else:
                font = NSFont.boldSystemFontOfSize_(size)
        else:
            font = NSFont.systemFontOfSize_(size)
        label.setFont_(font)
        label.setTextColor_(LimitLensApp._ns_color(color))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        if alignment == "right":
            try:
                label.setAlignment_(2)
            except Exception:
                pass
        return label

    def _button(self, title, action, x, y, width, height):
        return self._pill_button(title, action, x, y, width, height)

    def _add_button_row(self, parent, actions, y, height=26):
        gap = 8
        total_width = sum(width for _, _, width in actions) + gap * (len(actions) - 1)
        x = int((POPOVER_WIDTH - total_width) / 2)
        for title, action, width in actions:
            parent.addSubview_(self._pill_button(title, action, x, y, width, height))
            x += width + gap

    @staticmethod
    def _progress(pct, x, y, width, height):
        return LimitLensApp._progress_bar(pct, x, y, width, height, level="text")

    def _render_dashboard_view(self, model):
        root = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, POPOVER_WIDTH, POPOVER_HEIGHT))
        root.setMaterial_(NSVisualEffectMaterialHUDWindow)
        root.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        root.setState_(1)
        root.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        try:
            root.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        except Exception:
            pass

        # Header
        root.addSubview_(self._label("LimitLens", PAD_X, POPOVER_HEIGHT - 28, 130, 24, size=17, weight="bold"))
        refreshed = model.get("last_refresh")
        subtitle = f"Updated {refreshed}" if refreshed else (model.get("subtitle") or "")
        root.addSubview_(self._label(subtitle, POPOVER_WIDTH - 258, POPOVER_HEIGHT - 26, 240, 16, size=10, color="subtle", alignment="right"))

        # Hero card
        rec = model.get("recommendation")
        hero_y = POPOVER_HEIGHT - 148
        hero_h = 110
        hero_w = POPOVER_WIDTH - 2 * PAD_X
        root.addSubview_(self._card(PAD_X, hero_y, hero_w, hero_h, material="card"))

        if rec:
            root.addSubview_(self._section_header("Best option", PAD_X + 16, hero_y + hero_h - 22, 120, color="subtle"))
            root.addSubview_(self._label(rec["icon"], PAD_X + 16, hero_y + hero_h - 60, 32, 28, size=24))
            title_w = hero_w - 120
            root.addSubview_(self._label(rec["title"], PAD_X + 56, hero_y + hero_h - 52, title_w, 20, size=15, weight="bold"))
            root.addSubview_(self._label(rec["subtitle"], PAD_X + 56, hero_y + hero_h - 72, title_w, 18, size=11, color="muted"))
            root.addSubview_(self._label(self._pct_label(rec["pct"]), PAD_X + hero_w - 86, hero_y + hero_h - 54, 64, 24, size=18, weight="bold", color=rec["level"], alignment="right"))
            root.addSubview_(self._progress_bar(rec["pct"], PAD_X + 16, hero_y + 18, hero_w - 32, 8, level=rec["level"]))
        else:
            root.addSubview_(self._label(model.get("message") or "No recommendation available", PAD_X + 16, hero_y + hero_h / 2 - 10, hero_w - 32, 22, size=13, color="muted"))

        # All quotas list
        list_top = POPOVER_HEIGHT - 178
        root.addSubview_(self._label("All quotas", PAD_X, list_top, 180, 18, size=12, weight="bold"))
        scroll_y = 118
        scroll_h = max(112, list_top - scroll_y - 12)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(PAD_X - 2, scroll_y, POPOVER_WIDTH - 2 * PAD_X + 4, scroll_h))
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(False)
        scroll.setBorderType_(0)
        try:
            scroll.setDrawsBackground_(False)
            scroll.contentView().setDrawsBackground_(False)
        except Exception:
            pass
        # Force a light scroller knob so it stays visible on the dark vibrancy
        # material; overlay scrollers with the default knob can be hard to see.
        _vscroller = scroll.verticalScroller()
        if _vscroller is not None:
            _vscroller.setKnobStyle_(2)  # NSScrollerKnobStyleLight

        content_rows = model.get("rows") or []
        content_height = max(112, len(content_rows) * (ROW_HEIGHT + ROW_GAP) + 8)
        doc = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, POPOVER_WIDTH - 2 * PAD_X, content_height))
        row_y = content_height - ROW_HEIGHT - 4
        if content_rows:
            for row in content_rows:
                self._add_row_view(doc, row, row_y, compact=False)
                row_y -= (ROW_HEIGHT + ROW_GAP)
        else:
            doc.addSubview_(self._label("Refresh or run Doctor to find providers.", 8, content_height - 34, 320, 20, size=12, color="muted"))
        scroll.setDocumentView_(doc)
        try:
            clip = scroll.contentView()
            clip.scrollToPoint_(NSMakePoint(0, max(0, content_height - scroll_h)))
            scroll.reflectScrolledClipView_(clip)
        except Exception:
            pass
        root.addSubview_(scroll)

        # Footer actions: keep diagnostics out of the dashboard chrome.
        self._add_button_row(root, [("Refresh", "refreshDashboard:", 82), ("Quick Refresh", "quickRefreshDashboard:", 112), ("Open Config", "openConfigDashboard:", 100)], 70)
        self._add_button_row(root, [("Copy Status", "copyStatusDashboard:", 104), ("Quit", "quitDashboard:", 58)], 34)

        return root

    def _add_row_view(self, parent, row, y, compact=False):
        width = parent.frame().size.width if parent.frame() else (POPOVER_WIDTH - 2 * PAD_X)
        height = ROW_HEIGHT if not compact else 44

        # Rounded row background
        bg = NSView.alloc().initWithFrame_(NSMakeRect(0, y, width, height))
        bg.setWantsLayer_(True)
        if bg.layer():
            bg.layer().setBackgroundColor_(LimitLensApp._cg_color("row"))
            bg.layer().setCornerRadius_(ROW_RADIUS)
        parent.addSubview_(bg)

        icon_x = 14 if not compact else 8
        title_x = icon_x + 34
        pct_width = 62
        pct_x = int(width - pct_width - 14)
        title_width = max(120, pct_x - title_x - 10)
        bar_x = title_x
        bar_width = max(80, width - title_x - 18)

        parent.addSubview_(self._label(row["icon"], icon_x, y + 22, 26, 26, size=20))
        parent.addSubview_(self._label(row["title"], title_x, y + 39, title_width, 18, size=12, weight="bold"))
        parent.addSubview_(self._label(row["detail"], title_x, y + 22, title_width, 16, size=10, color="muted"))
        parent.addSubview_(self._label(row["pct_label"], pct_x, y + 39, pct_width, 18, size=13, weight="bold", color=row["level"], alignment="right"))
        parent.addSubview_(self._progress_bar(row["pct"], bar_x, y + 10, bar_width, 5, level=row["level"]))

    @staticmethod
    def _safe_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _tool_icon(cls, tool_key="", name="", section=""):
        """Return a unique emoji: known tool → fixed icon, custom → keyword
        match on name, or deterministic pool pick based on name adler32 hash."""
        from .core import get_tool_icon
        return get_tool_icon(tool_key=tool_key, name=name, section=section)

    @staticmethod
    def _emoji(pct):
        if pct is None:
            return "⚪"
        if pct >= 50:
            return "🟢"
        if pct >= 15:
            return "🟡"
        return "🔴"

    @staticmethod
    def _bar(pct, width=10):
        if pct is None:
            return "─" * width
        pct = max(0.0, min(100.0, float(pct)))
        filled = int((pct / 100.0) * width + 0.5)
        if pct > 0 and filled == 0:
            filled = 1
        return "▰" * filled + "▱" * (width - filled)

    @staticmethod
    def _compact(text, max_len=18):
        text = str(text or "")
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    @staticmethod
    def _format_number(value):
        if value is None:
            return "?"
        try:
            value = float(value)
        except (TypeError, ValueError):
            return str(value)
        abs_value = abs(value)
        if abs_value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}B"
        if abs_value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if abs_value >= 10_000:
            return f"{value / 1_000:.1f}K"
        if value.is_integer():
            return f"{int(value)}"
        return f"{value:.2f}".rstrip("0").rstrip(".")

    @classmethod
    def _format_amount(cls, value, unit=None):
        unit = str(unit or "").strip()
        if value is None:
            return "?"
        if unit.lower() in ("$", "usd"):
            try:
                return f"${float(value):.2f}"
            except (TypeError, ValueError):
                return f"${value}"
        return cls._format_number(value)

    @classmethod
    def _format_ratio(cls, remaining, total, unit=None):
        unit = str(unit or "").strip()
        if unit.lower() in ("%", "% left"):
            return "?" if remaining is None else f"{cls._format_number(remaining)}% left"
        left = cls._format_amount(remaining, unit)
        right = cls._format_amount(total, unit)
        suffix = "" if unit.lower() in ("", "$", "usd") else f" {unit}"
        if total is None:
            return f"{left}{suffix}"
        return f"{left}/{right}{suffix}"

    @classmethod
    def _row(cls, section, name, label, pct_left, remaining=None, total=None, unit=None,
             status=None, used=None, pct_used=None, notify_id=None, notify_label=None,
             display_group=None, display_label=None, window_label=None):
        pct_left = cls._safe_float(pct_left)
        pct_used = cls._safe_float(pct_used)
        if pct_used is None and pct_left is not None:
            pct_used = max(0.0, 100.0 - pct_left)
        return {
            "section": section,
            "name": name,
            "label": label,
            "pct_left": pct_left,
            "pct_used": pct_used,
            "remaining": remaining,
            "total": total,
            "unit": unit,
            "status": status,
            "used": used,
            "notify_id": notify_id,
            "notify_label": notify_label or f"{section} {label}",
            "display_group": display_group,
            "display_label": display_label or label,
            "window_label": window_label,
        }

    @classmethod
    def _format_window_parts(cls, row):
        parts = []
        window_parts = row.get("window_parts")
        if not window_parts and row.get("window_label"):
            window_parts = [{"window_label": row.get("window_label"), "pct_left": row.get("pct_left")}]
        for part in window_parts or []:
            pct = cls._safe_float(part.get("pct_left"))
            pct_str = "n/a" if pct is None else f"{cls._format_number(pct)}%"
            w_lbl = part.get('window_label')
            if w_lbl == "5h":
                w_lbl = "h"
            elif w_lbl == "week":
                w_lbl = "w"
            bar = cls._bar(pct, width=6)
            parts.append(f"[{bar}] {w_lbl} {pct_str}")
        return "   ".join(parts)

    @classmethod
    def _format_dashboard_window_parts(cls, row):
        parts = []
        window_parts = row.get("window_parts")
        if not window_parts and row.get("window_label"):
            window_parts = [{"window_label": row.get("window_label"), "pct_left": row.get("pct_left")}]
        for part in window_parts or []:
            pct = cls._safe_float(part.get("pct_left"))
            pct_str = "n/a" if pct is None else f"{cls._format_number(pct)}%"
            w_lbl = part.get("window_label")
            if w_lbl == "5h":
                w_lbl = "5h"
            elif w_lbl == "week":
                w_lbl = "week"
            parts.append(f"{w_lbl} {pct_str}")
        return " · ".join(parts)

    @classmethod
    def _group_antigravity_window_rows(cls, rows):
        grouped = {}
        display_rows = []
        order = {"5h": 0, "week": 1}

        for row in rows:
            group_key = row.get("display_group")
            window_label = row.get("window_label")
            if not group_key or not window_label:
                display_rows.append(row)
                continue

            pct_left = cls._safe_float(row.get("pct_left"))
            if group_key not in grouped:
                combined = dict(row)
                combined["label"] = row.get("display_label") or row.get("label")
                combined["window_parts"] = []
                grouped[group_key] = combined
                display_rows.append(combined)

            combined = grouped[group_key]
            combined["window_parts"].append({
                "window_label": window_label,
                "pct_left": pct_left,
            })
            combined["window_parts"].sort(key=lambda part: order.get(part.get("window_label"), 99))
            current_pct = cls._safe_float(combined.get("pct_left"))
            if current_pct is None or (pct_left is not None and pct_left < current_pct):
                combined["pct_left"] = pct_left

        return display_rows

    def _empty_dashboard_model(self):
        return {
            "state": "loading",
            "title": "LimitLens",
            "subtitle": "Loading quota data...",
            "recommendation": None,
            "low_rows": [],
            "rows": [],
            "last_refresh": None,
            "message": None,
        }

    def _recommendation_model(self, item):
        pct = self._safe_float(item.get("headroom_pct"))
        if pct is None:
            return None
        name = item.get("name") or item.get("tool") or "quota"
        if " (" in name:
            name = name.split(" (", 1)[0]
        if " → " in name:
            profile, model = name.split(" → ", 1)
            profile = profile.split(":", 1)[-1]
            name = f"{profile} / {model}"
        name = name.replace("codex-", "").strip()
        name = {"amp": "Amp", "pioneer": "Pioneer"}.get(name.lower(), name)
        note = item.get("note") or item.get("reset_label") or item.get("command") or "Best available right now"
        return {
            "icon": self._tool_icon(item.get("tool", ""), name),
            "title": name,
            "subtitle": note,
            "pct": pct,
            "level": self._level_for_pct(pct),
        }

    def _row_model(self, row):
        pct = row.get("pct_left")
        section = str(row.get("section") or "")
        name = str(row.get("name") or "")
        label = str(row.get("label") or "")
        title = label if section.lower() in name.lower() else f"{name} / {label}"
        window_parts = self._format_dashboard_window_parts(row)
        if window_parts:
            detail = window_parts
        elif pct is None and row.get("used") is not None:
            unit = str(row.get("unit") or "").strip()
            suffix = f" {unit}" if unit else ""
            detail = f"{self._format_number(row['used'])}{suffix} used"
        else:
            detail = self._format_ratio(row.get("remaining"), row.get("total"), row.get("unit"))
        status = row.get("status")
        if status and status != "running":
            detail = f"{detail} · {status}" if detail and detail != "?" else status
        return {
            "icon": self._tool_icon(section=section),
            "section": section,
            "title": title,
            "detail": detail if detail != "?" else "No usage details",
            "pct": pct,
            "pct_label": self._pct_label(pct),
            "level": self._level_for_pct(pct),
        }

    def _build_dashboard_model(self, data, display_rows, low_rows):
        recs = (data.get("recommendations") or {}).get("hard") or []
        fresh_recs = [r for r in recs if not r.get("stale")]
        recommendation = None
        for rec in fresh_recs or recs:
            recommendation = self._recommendation_model(rec)
            if recommendation:
                break

        sorted_rows = sorted(display_rows, key=lambda r: (r.get("pct_left") is None, -(r.get("pct_left") or -1)))
        if recommendation:
            title = "Use this next"
            subtitle = "Best recommendation from current quota data"
        elif display_rows:
            title = "Quota dashboard"
            subtitle = "No recommendation available yet"
        else:
            title = "No active quotas"
            subtitle = "Refresh or check provider setup"

        return {
            "state": "ready",
            "title": title,
            "subtitle": subtitle,
            "recommendation": recommendation,
            "low_rows": [self._row_model(row) for row in sorted(low_rows, key=lambda r: r.get("pct_left") or 0)[:4]],
            "rows": [self._row_model(row) for row in sorted_rows],
            "last_refresh": self._last_refresh_label,
            "message": None,
        }

    @staticmethod
    def _pct_label(pct):
        if pct is None:
            return "n/a"
        if 0 < pct < 1:
            return f"{pct:.1f}%"
        return f"{pct:.0f}%"

    @staticmethod
    def _level_for_pct(pct):
        if pct is None:
            return "neutral"
        if pct >= 50:
            return "good"
        if pct >= 15:
            return "warn"
        return "bad"

    def _format_title(self, display_items):
        visible = display_items[:self._max_title_items]
        extra = len(display_items) - len(visible)
        suffix = f" +{extra}" if extra > 0 else ""
        return " · ".join(visible) + suffix

    def _recommendation_title_items(self, data, rows):
        recs = data.get("recommendations") or {}
        hard_recs = recs.get("hard") or []
        waste_recs = recs.get("waste_watch") or []

        seen_names = set()
        selected = []

        def add_item(item):
            name = item.get("name")
            pct = self._safe_float(item.get("headroom_pct"))
            if pct is not None and not item.get("stale"):
                if name and name not in seen_names:
                    seen_names.add(name)
                    selected.append(item)
                    return True
            return False

        # 1. Action slot (Top hard recommendation)
        if hard_recs:
            add_item(hard_recs[0])

        # 2. Expiring slot (Urgent waste)
        for w in waste_recs:
            if add_item(w):
                break

        # 3. Fill remaining with all other valid items for +N count
        for h in hard_recs:
            add_item(h)

        items = []
        for item in selected:
            pct = self._safe_float(item.get("headroom_pct"))
            tool = item.get("tool") or ""
            name = item.get("name") or item.get("tool") or "quota"
            icon = self._tool_icon(tool, name)
            items.append(f"{icon}{pct:.0f}%")

        if items:
            return items

        sortable_rows = [r for r in rows if r.get("pct_left") is not None]
        sortable_rows.sort(key=lambda r: r["pct_left"], reverse=True)
        return [
            f"{self._tool_icon(section=row['section'])}{row['pct_left']:.0f}%"
            for row in sortable_rows
        ]

    def _format_recommendation_row(self, item):
        pct = self._safe_float(item.get("headroom_pct"))
        if pct is None:
            return None
        name = item.get("name") or item.get("tool") or "quota"
        if " (" in name:
            name = name.split(" (", 1)[0]
        if " → " in name:
            profile, model = name.split(" → ", 1)
            profile = profile.split(":", 1)[-1]
            name = f"{profile} / {model}"
        name = name.replace("codex-", "").strip()
        name = {"amp": "Amp", "pioneer": "Pioneer"}.get(name.lower(), name)
        note = item.get("note") or item.get("reset_label") or item.get("command") or ""
        
        icon = self._tool_icon(item.get('tool', ''), name)
        status_dot = self._emoji(pct)
        bar = self._bar(pct)
        
        clean_name = self._compact(name, 22)
        
        if 0 < pct < 1:
            pct_str = f"{pct:.1f}%"
        else:
            pct_str = f"{pct:.0f}%"
            
        extra_str = self._compact(note, 22)
        return f"{icon} {status_dot} {clean_name:<24} [{bar}]  {pct_str:>4}   {extra_str}"

    def _format_recommendation_compact(self, item):
        pct = self._safe_float(item.get("headroom_pct"))
        if pct is None:
            return None
        name = item.get("name") or item.get("tool") or "quota"
        if " (" in name:
            name = name.split(" (", 1)[0]
        if " → " in name:
            profile, model = name.split(" → ", 1)
            profile = profile.split(":", 1)[-1]
            name = f"{profile} · {model}"
        name = name.replace("codex-", "").strip()
        icon = self._tool_icon(item.get("tool", ""), name)
        return f"{icon} {self._compact(name, 30)} · {pct:.0f}%"

    def _format_usage_row(self, row):
        pct = row.get("pct_left")
        name = self._compact(row["name"], 14)
        label = self._compact(row["label"], 16)
        section = self._compact(row["section"], 8)
        
        if section.lower() in name.lower() or name.lower() in section.lower():
            title = f"{name} ({label})"
        else:
            title = f"{section} {name} ({label})"
            
        icon = self._tool_icon(section=row['section'])
        status_dot = self._emoji(pct)
        bar = self._bar(pct)
        
        title_str = self._compact(title, 24)
        
        if pct is None:
            pct_str = "n/a"
        elif 0 < pct < 1:
            pct_str = f"{pct:.1f}%"
        else:
            pct_str = f"{pct:.0f}%"
            
        if pct is None and row.get("used") is not None:
            unit = str(row.get("unit") or "").strip()
            suffix = f" {unit}" if unit else ""
            ratio_str = f"{self._format_number(row['used'])}{suffix} used"
        else:
            ratio_str = self._format_ratio(row.get("remaining"), row.get("total"), row.get("unit"))
            
        status = row.get("status")
        status_suffix = f" [{status}]" if status and status != "running" else ""
        
        window_parts = self._format_window_parts(row)
        if window_parts:
            return f"{icon} {status_dot} {title_str:<24} {window_parts} {status_suffix}".rstrip()
        else:
            extra_str = f"({ratio_str}){status_suffix}" if ratio_str != "?" else status_suffix.strip()
            return f"{icon} {status_dot} {title_str:<24} [{bar}]  {pct_str:>4}   {extra_str}"

    def _format_usage_compact(self, row):
        pct = row.get("pct_left")
        section = str(row.get("section") or "")
        name = str(row.get("name") or "")
        label = str(row.get("label") or "")
        icon = self._tool_icon(section=section)
        title = label if section.lower() in name.lower() else f"{name} · {label}"
        window_parts = self._format_window_parts(row)
        if window_parts:
            return f"{icon} {self._compact(title, 30)} · {window_parts}"
        if pct is None:
            if row.get("used") is not None:
                unit = str(row.get("unit") or "").strip()
                suffix = f" {unit}" if unit else ""
                return f"{icon} {self._compact(title, 30)} · {self._format_number(row['used'])}{suffix} used"
            return f"{icon} {self._compact(title, 30)} · n/a"
        return f"{icon} {self._compact(title, 30)} · {pct:.0f}%"

    def _make_all_quotas_menu(self, rows):
        sorted_rows = sorted(rows, key=lambda r: (r.get("pct_left") is None, -(r.get("pct_left") or -1)))
        return ("submenu", "All Quotas", [self._format_usage_row(row) for row in sorted_rows])

    def _make_overview_menu(self, rec_rows, low_rows):
        items = []
        if rec_rows:
            items.append("Recommended next")
            items.extend(rec_rows)
        if low_rows:
            if items:
                items.append(rumps.separator)
            items.append("Low quota alerts")
            items.extend(self._format_usage_compact(row) for row in sorted(low_rows, key=lambda r: r.get("pct_left") or 0)[:3])
        if self._last_refresh_label:
            if items:
                items.append(rumps.separator)
            items.append(f"Last refreshed: {self._last_refresh_label}")
        if not items:
            items.append("No active recommendations")
        return ("submenu", "Overview", items)

    def _make_actions_menu(self):
        return ("submenu", "Actions", [
            ("callback", "Open Dashboard…", self._on_open_dashboard),
            ("callback", "Refresh", self._on_refresh),
            ("callback", "Quick Refresh", self._on_quick_refresh),
            ("callback", "Open Config", self._on_open_config),
            ("callback", "Copy Status", self._on_copy_status),
            ("callback", "Run Doctor", self._on_doctor),
            ("callback", "Copy Doctor Report", self._on_doctor_report),
        ])

    def _build_status_summary(self, rec_rows, low_rows, rows):
        lines = ["LimitLens status"]
        if rec_rows:
            lines.append("Recommended:")
            lines.extend(f"- {row}" for row in rec_rows)
        if low_rows:
            lines.append("Low quota:")
            lines.extend(f"- {self._format_usage_compact(row)}" for row in low_rows)
        if rows:
            lines.append("All quotas:")
            for row in sorted(rows, key=lambda r: (r.get("pct_left") is None, -(r.get("pct_left") or -1))):
                lines.append(f"- {self._format_usage_compact(row)}")
        if self._last_refresh_label:
            lines.append(f"Last refreshed: {self._last_refresh_label}")
        return "\n".join(lines)

    def _set_refresh_failure(self, message, title_message=None):
        from .core import redact_text

        message = str(message or "Unknown error").strip() or "Unknown error"
        message = redact_text(message)
        message = re.sub(r"/Users/[^/\s]+", "~", message)
        message = re.sub(r"(?i)\b(token|cookie|authorization|api[_-]?key|password|secret)=\S+", r"\1=<redacted>", message)
        title_message = title_message or message.split("\n")[-1][:20]
        self._pending_title = f"⚠️ {title_message}"
        self._last_status_summary = "\n".join([
            "LimitLens status",
            f"Refresh failed: {message}",
            "Try Quick Refresh, then run `limitlens doctor` if it keeps failing.",
        ])
        self._pending_menu_items = [
            f"⚠️ Refresh failed: {message[:48]}",
            "Try Quick Refresh",
            "Run `limitlens doctor` in Terminal",
            self._item_open_dashboard,
            self._item_refresh,
            self._item_quick_refresh,
            self._item_open_config,
            self._item_copy_status,
        ]
        self._pending_dashboard_model = {
            "state": "error",
            "title": "Refresh failed",
            "subtitle": "Try Quick Refresh or run Doctor",
            "recommendation": None,
            "low_rows": [],
            "rows": [],
            "last_refresh": self._last_refresh_label,
            "message": message,
        }

    def _collect_rows(self, data, check_low_quota):
        rows = []

        # Custom configured tools (for example manual Kilo Code quotas)
        custom = data.get("custom") or {}
        for tool in custom.get("tools", []):
            tool_name = tool.get("name") or tool.get("id") or "Custom"
            for tier in tool.get("tiers", []):
                if tier.get("visible", True) is False:
                    continue
                label = tier.get("label", tool_name)
                pct = self._safe_float(tier.get("pct_left"))
                rows.append(self._row(
                    "Custom", tool_name, label, pct,
                    remaining=tier.get("remaining"), total=tier.get("total"), unit=tier.get("unit"),
                    used=tier.get("used"), pct_used=tier.get("pct_used"), status=tool.get("status"),
                    notify_id=f"custom-{tool.get('id', tool_name)}-{label}", notify_label=tool_name,
                ))
                check_low_quota(f"custom-{tool.get('id', tool_name)}-{label}", tool_name, pct)

        # Codex
        codex = data.get("codex") or {}
        for acc in codex.get("accounts", []):
            if "error" in acc:
                continue
            acc_name = acc.get("name", "Codex")
            for lim in acc.get("limits", []):
                label = lim.get("label", "limit")
                pct = self._safe_float(lim.get("left_percent"))

                window_label = None
                display_label = "quota"
                if label == "5h window":
                    window_label = "5h"
                elif label == "weekly":
                    window_label = "week"

                rows.append(self._row(
                    "Codex", acc_name, label, pct,
                    remaining=lim.get("remaining"), total=lim.get("total"), unit=lim.get("unit"),
                    notify_id=f"codex-{acc_name}-{label}", notify_label=f"Codex ({acc_name}) {label}",
                    display_group=f"codex-{acc_name}" if window_label else None,
                    display_label=display_label, window_label=window_label,
                ))
                check_low_quota(f"codex-{acc_name}-{label}", f"Codex ({acc_name}) {label}", pct)

        # Amp
        amp = data.get("amp") or {}
        for tier in amp.get("tiers", []):
            if tier.get("visible", True) is False:
                continue
            label = tier.get("label", "Amp")
            pct = self._safe_float(tier.get("pct_left"))
            rows.append(self._row(
                "Amp", "Amp", label, pct,
                remaining=tier.get("remaining"), total=tier.get("total"), unit="$",
                used=tier.get("used"), pct_used=tier.get("pct_used"),
                notify_id=f"amp-{label}", notify_label=label,
            ))
            remaining = tier.get("remaining")
            detail = f"(${remaining:.2f} remaining)" if isinstance(remaining, (int, float)) else ""
            check_low_quota(f"amp-{label}", label, pct, detail)

        # Antigravity
        ag = data.get("antigravity") or {}
        for prof in ag.get("profiles", []):
            prof_name = prof.get("name", "default")
            status = prof.get("status", "running")
            for model in prof.get("models", []):
                if model.get("visible", True) is False:
                    continue
                label = model.get("label", "model")
                display_label = label
                window_label = None
                # Each group exposes a 5h and a weekly bucket sharing the same
                # label; keep the visible label stable and key notifications by
                # window so rows stay distinct without crowding the menu title.
                limit_type = model.get("limit_type")
                if limit_type == "5h window":
                    window_label = "5h"
                elif limit_type == "weekly":
                    window_label = "week"
                pct = self._safe_float(model.get("pct_left"))
                notify_suffix = f"-{window_label}" if window_label else ""
                notify_label = f"{label} ({window_label})" if window_label else label
                rows.append(self._row(
                    "Antigrav", prof_name, label, pct,
                    remaining=pct, total=100, unit="% left", status=status,
                    notify_id=f"ag-{prof_name}-{label}{notify_suffix}", notify_label=f"Antigravity ({prof_name}) {notify_label}",
                    display_group=f"ag-{prof_name}-{display_label}" if window_label else None,
                    display_label=display_label, window_label=window_label,
                ))
                if status == "running":
                    check_low_quota(
                        f"ag-{prof_name}-{label}{notify_suffix}",
                        f"Antigravity ({prof_name}) {notify_label}",
                        pct,
                    )

        # OpenCode / credits
        op_data = data.get("opencode") or {}
        for lim in op_data.get("credit_limits", []):
            name = lim.get("name", "credits")
            pct = self._safe_float(lim.get("pct_left"))
            unit = lim.get("unit") or "credits"
            rows.append(self._row(
                "OpenCode", "OpenCode", name, pct,
                remaining=lim.get("remaining"), total=lim.get("total"), unit=unit,
                used=lim.get("used"), pct_used=lim.get("pct_used"),
                notify_id=f"opencode-{name}", notify_label=f"OpenCode ({name})",
            ))
            rem = lim.get("remaining")
            unit_sym = "$" if str(unit).lower() in ("usd", "$") else ""
            detail = f"({unit_sym}{rem:.2f} remaining)" if isinstance(rem, (int, float)) else ""
            check_low_quota(f"opencode-{name}", f"OpenCode ({name})", pct, detail)

        # Pioneer
        pioneer = data.get("pioneer") or {}
        for tier in pioneer.get("tiers", []):
            if tier.get("visible", True) is False:
                continue
            label = tier.get("label", "Pioneer")
            pct = self._safe_float(tier.get("pct_left"))
            rows.append(self._row(
                "Pioneer", "Pioneer", label, pct,
                remaining=tier.get("remaining"), total=tier.get("total"), unit=tier.get("unit") or pioneer.get("unit"),
                used=tier.get("used"), pct_used=tier.get("pct_used"),
                notify_id=f"pioneer-{label}", notify_label=label,
            ))
            check_low_quota(f"pioneer-{label}", label, pct)

        # Cursor
        cursor = data.get("cursor") or {}
        for tier in cursor.get("tiers", []):
            label = tier.get("label", "Cursor")
            pct = self._safe_float(tier.get("pct_left"))
            rows.append(self._row(
                "Cursor", "Cursor", label, pct,
                remaining=tier.get("remaining"), total=tier.get("total"), unit=tier.get("unit"),
                used=tier.get("used"), pct_used=tier.get("pct_used"),
                notify_id=f"cursor-{label}", notify_label=f"Cursor {label}",
            ))
            check_low_quota(f"cursor-{label}", f"Cursor {label}", pct)

        # Cline CLI (ClinePass quota windows)
        cline = data.get("cline") or {}
        for win in cline.get("windows", []):
            label = win.get("label") or win.get("type") or "quota"
            pct = self._safe_float(win.get("pct_left"))
            rows.append(self._row(
                "Cline", "Cline", label, pct,
                remaining=pct, total=100, unit="% left",
                status=cline.get("status"),
                notify_id=f"cline-{label}", notify_label=f"Cline {label}",
            ))
            check_low_quota(f"cline-{label}", f"Cline {label}", pct)

        return rows

    def _build_menu_items(self, data, rows):
        menu_items = []
        display_rows = self._group_antigravity_window_rows(rows)

        recs = (data.get("recommendations") or {}).get("hard") or []
        fresh_recs = [r for r in recs if not r.get("stale")]
        rec_source = fresh_recs or recs
        formatted_recs = [self._format_recommendation_compact(r) for r in rec_source[:2]]
        formatted_recs = [r for r in formatted_recs if r]
        low_rows = [r for r in display_rows if r.get("pct_left") is not None and r["pct_left"] < self._notify_warn_pct]

        menu_items.append(self._make_overview_menu(formatted_recs, low_rows))
        if display_rows:
            menu_items.append(self._make_all_quotas_menu(display_rows))
        menu_items.append(self._make_actions_menu())

        self._last_status_summary = self._build_status_summary(formatted_recs, low_rows, display_rows)
        self._pending_dashboard_model = self._build_dashboard_model(data, display_rows, low_rows)

        return menu_items

    def fetch_data(self, sync_codex=False):
        with self._fetch_lock:
            if self._is_fetching:
                # Preserve the stronger sync intent. If the queued request
                # already wants a full sync, keep it. Otherwise use the
                # incoming sync_codex value.
                if self._queued_sync_codex is None or sync_codex:
                    self._queued_sync_codex = sync_codex
                return
            self._is_fetching = True
        self._pending_title = "↻ Refreshing…"

        def worker():
            try:
                cmd = [sys.executable, "-m", "limitlens", "--json"]
                timeout = MENUBAR_REFRESH_TIMEOUT_SECONDS
                if sync_codex:
                    # Forcing a Codex sync spawns codex exec per account, so allow more time.
                    cmd.append("--sync-codex")
                    timeout = MENUBAR_SYNC_CODEX_TIMEOUT_SECONDS
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)  # nosec B603

                if proc.returncode == 0:
                    data = json.loads(proc.stdout)
                    active_keys = set()
                    suppress_notifications = not self._has_loaded_once

                    def check_low_quota(id_str, label, pct, details=""):
                        pct = self._safe_float(pct)
                        if pct is None:
                            return
                        active_keys.add(id_str)
                        if pct < self._notify_critical_pct:
                            if not suppress_notifications and id_str not in self._notified_set:
                                self.notify(
                                    "LimitLens Quota Warning",
                                    f"{label} is running low ({pct:.1f}% left). {details}".strip(),
                                )
                                self._notified_set.add(id_str)
                        elif pct >= min(self._notify_warn_pct, self._notify_critical_pct + 5.0) and id_str in self._notified_set:
                            self._notified_set.remove(id_str)

                    rows = self._collect_rows(data, check_low_quota)
                    self._notified_set.intersection_update(active_keys)

                    title_items = self._recommendation_title_items(data, rows)
                    self._pending_title = self._format_title(title_items) if title_items else "⚪ No quota"
                    self._last_refresh_label = datetime.now().strftime("%I:%M %p").lstrip("0")
                    self._pending_menu_items = self._build_menu_items(data, rows)
                    self._has_loaded_once = True
                else:
                    err_msg = proc.stderr.strip().split("\n")[-1] if proc.stderr else "Unknown error"
                    self._set_refresh_failure(err_msg)
                    # Log with RedactFilter active — proc.stderr is NOT written raw
                    log.warning(
                        "Menubar command failure (rc=%s): %s",
                        proc.returncode,
                        proc.stderr or "",
                    )
                    _write_log_direct(
                        f"Menubar command failure (rc={proc.returncode}): {proc.stderr or ''}"
                    )
            except subprocess.TimeoutExpired:
                self._set_refresh_failure("Refresh timed out", title_message="Timeout")
                log.exception("Menubar subprocess timeout")
                _write_log_direct("Menubar subprocess timeout", exc=True)
            except Exception as e:
                self._set_refresh_failure(str(e))
                log.exception("Menubar exception in worker")
                _write_log_direct(f"Menubar exception in worker: {e}", exc=True)
            finally:
                with self._fetch_lock:
                    self._is_fetching = False
                    queued = self._queued_sync_codex
                    self._queued_sync_codex = None
                if queued is not None:
                    self.fetch_data(sync_codex=queued)

        t = threading.Thread(target=worker, daemon=True)
        t.start()


def main():
    if sys.platform != "darwin" or not _RUMPS_AVAILABLE:
        print("limitlens-menubar requires macOS and the optional [mac] dependencies.", file=sys.stderr)
        return 1

    app = LimitLensApp()
    # Fetch once before starting the AppKit loop so the UI does not remain
    # stuck at "Loading..." if the first timer tick is delayed.
    app.fetch_data()
    deadline = time.monotonic() + 16
    while app._is_fetching is True and time.monotonic() < deadline:
        time.sleep(0.05)
    app.check_updates(None)
    app.run()
    return 0


if __name__ == "__main__":
    main()
