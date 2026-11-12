#!/usr/bin/env python3
"""Fullscreen game launcher for the living-room PC.

Modes:
  launcher.py --menu ACTION_FILE   show the picker; writes "play:N", "desktop",
                                   "reboot" or "poweroff" to ACTION_FILE and exits
  launcher.py --run N              exec game N from games.json (cwd + env applied)

The window is fully closed before a game starts, so the game gets focus.
session.sh runs the loop: picker -> game -> picker ...
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

CONF_DIR = os.path.expanduser("~/.config/zlauncher")
CONF_PATH = os.path.join(CONF_DIR, "games.json")
COVERS_DIR = os.path.join(CONF_DIR, "covers")
STATE_DIR = os.path.expanduser("~/.local/state/zlauncher")
HELPER_UUID = "zlauncher-helper@game-station"

W, H = 1920, 1080
TILE_W, TILE_H = 448, 640
SMALL = 0.78
GAP = 70
ACCENT = (232, 196, 106)
WHITE = (245, 245, 240)
GREY = (170, 172, 178)
PANEL = (18, 20, 26)

EXEC_EXTS = (".appimage", ".sh", ".x86_64")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


# ---------------------------------------------------------------- config

def load_conf():
    try:
        with open(CONF_PATH) as f:
            conf = json.load(f)
    except (OSError, ValueError):
        conf = {}
    conf.setdefault("games", [])
    conf.setdefault("last", 0)
    conf.setdefault("browse_root", os.path.expanduser("~"))
    conf.setdefault("joy_buttons", {"a": 0, "b": 1, "x": 2, "y": 3, "select": 6, "start": 7})
    return conf


def save_conf(conf):
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONF_PATH)


def run_game(idx):
    conf = load_conf()
    g = conf["games"][idx]
    exe = g["exec"]
    cwd = g.get("cwd") or os.path.dirname(exe)
    env = os.environ.copy()
    env.update({k: str(v) for k, v in g.get("env", {}).items()})
    os.chdir(cwd)
    os.execve(exe, [exe] + list(g.get("args", [])), env)


def guess_name(path):
    base = os.path.basename(path)
    stem = re.sub(r"\.(appimage|sh|x86_64)$", "", base, flags=re.I)
    stem = re.split(r"[-_. ]v?\d", stem)[0] or stem
    if stem.lower() in ("game", "run", "start", "launch", "bin") or len(stem) < 3:
        stem = os.path.basename(os.path.dirname(path))
    return re.sub(r"[_\-]+", " ", stem).strip().title()[:40]


def is_launchable(path):
    name = os.path.basename(path).lower()
    if name.endswith(EXEC_EXTS):
        return True
    if ".so" in name or not os.access(path, os.X_OK):
        return False
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return False
    return magic == b"\x7fELF" or magic[:2] == b"#!"


# ---------------------------------------------------------------- UI

def main_menu(action_path):
    import pygame
    from pygame._sdl2 import controller

    shot = os.environ.get("ZL_SHOT")
    orig_env = dict(os.environ)  # games must not inherit our SDL_* settings
    os.environ.setdefault("SDL_RENDER_SCALE_QUALITY", "1")
    os.environ["SDL_VIDEO_WAYLAND_WMCLASS"] = "zlauncher"
    os.environ["SDL_VIDEO_X11_WMCLASS"] = "zlauncher"
    if not shot and "SDL_VIDEODRIVER" not in os.environ:
        for drv in ("wayland", "x11"):
            os.environ["SDL_VIDEODRIVER"] = drv
            try:
                pygame.display.init()
                break
            except pygame.error:
                continue
    pygame.display.init()
    pygame.font.init()
    pygame.joystick.init()
    controller.init()
    pygame.display.set_caption("Juegos")
    if shot:
        screen = pygame.display.set_mode((W, H))
    else:
        screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.SCALED, vsync=1)
    pygame.mouse.set_visible(False)
    pygame.key.set_repeat(350, 110)

    fdir = "/usr/share/fonts/truetype/ubuntu/"

    def font(name, size):
        try:
            return pygame.font.Font(fdir + name, size)
        except (OSError, FileNotFoundError):
            return pygame.font.Font(None, int(size * 1.3))

    F_TITLE = font("Ubuntu-B.ttf", 68)
    F_BIG = font("Ubuntu-M.ttf", 44)
    F_MED = font("Ubuntu-M.ttf", 34)
    F_SMALL = font("Ubuntu-R.ttf", 28)
    F_HINT = font("Ubuntu-M.ttf", 26)
    F_BTN = font("Ubuntu-B.ttf", 22)

    conf = load_conf()
    browse_root = os.path.expanduser(conf["browse_root"])
    if not os.path.isdir(browse_root):
        browse_root = os.path.expanduser("~")

    # ---------------- image helpers
    def cover_crop(img, size):
        tw, th = size
        iw, ih = img.get_size()
        scale = max(tw / iw, th / ih)
        nw, nh = max(1, int(iw * scale + 0.5)), max(1, int(ih * scale + 0.5))
        img = pygame.transform.smoothscale(img, (nw, nh))
        return img.subsurface(((nw - tw) // 2, (nh - th) // 2, tw, th)).copy()

    def rounded(surf, r):
        s = surf.convert_alpha()
        mask = pygame.Surface(s.get_size(), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=r)
        s.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        return s

    def wrap(text, fnt, width):
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if fnt.size(t)[0] <= width:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def placeholder(name):
        h = sum(ord(c) * (i + 7) for i, c in enumerate(name)) % 360
        s = pygame.Surface((TILE_W, TILE_H))
        c1, c2 = pygame.Color(0), pygame.Color(0)
        c1.hsva = (h, 55, 45, 100)
        c2.hsva = ((h + 40) % 360, 70, 15, 100)
        for y in range(TILE_H):
            t = y / TILE_H
            s.fill([int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)], (0, y, TILE_W, 1))
        lines = wrap(name, F_BIG, TILE_W - 60)
        y = TILE_H // 2 - len(lines) * 26
        for ln in lines:
            r = F_BIG.render(ln, True, WHITE)
            s.blit(r, ((TILE_W - r.get_width()) // 2, y))
            y += 52
        return s

    def add_tile():
        s = pygame.Surface((TILE_W, TILE_H), pygame.SRCALPHA)
        pygame.draw.rect(s, (255, 255, 255, 28), s.get_rect(), border_radius=22)
        pygame.draw.rect(s, (255, 255, 255, 120), s.get_rect(), width=4, border_radius=22)
        cx, cy = TILE_W // 2, TILE_H // 2 - 40
        pygame.draw.rect(s, WHITE, (cx - 8, cy - 70, 16, 140), border_radius=8)
        pygame.draw.rect(s, WHITE, (cx - 70, cy - 8, 140, 16), border_radius=8)
        r = F_BIG.render("Añadir juego", True, WHITE)
        s.blit(r, ((TILE_W - r.get_width()) // 2, cy + 110))
        return s

    def make_bg(img):
        bg = cover_crop(img, (W, H))
        bg = pygame.transform.smoothscale(bg, (192, 108))
        bg = pygame.transform.smoothscale(bg, (40, 22))
        bg = pygame.transform.smoothscale(bg, (W, H))
        shade = pygame.Surface((W, H), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 120))
        bg.blit(shade, (0, 0))
        grad = pygame.Surface((1, 256), pygame.SRCALPHA)
        for y in range(256):
            grad.set_at((0, y), (0, 0, 0, int(200 * (y / 255) ** 1.6)))
        bg.blit(pygame.transform.smoothscale(grad, (W, 420)), (0, H - 420))
        return bg.convert()

    default_bg = pygame.Surface((W, H))
    for y in range(H):
        t = y / H
        default_bg.fill((int(20 + 10 * t), int(28 + 4 * t), int(40 - 20 * t)), (0, y, W, 1))

    # Game Station wallpaper: background of the "+" tile and start of the boot fade
    boot_img = None
    wp = os.path.expanduser("~/.local/share/backgrounds/gamestation.png")
    if os.path.isfile(wp):
        try:
            wimg = pygame.image.load(wp)
            boot_img = cover_crop(wimg, (W, H)).convert()
            default_bg = make_bg(wimg)
        except pygame.error:
            pass

    class Tile:
        def __init__(self, game):
            self.game = game
            self.scale = SMALL
            if game is None:
                self.img = add_tile()
                self.bg = default_bg
                return
            img = None
            cov = game.get("cover")
            if cov and os.path.isfile(cov):
                try:
                    img = pygame.image.load(cov)
                except pygame.error:
                    img = None
            if img is not None:
                self.img = rounded(cover_crop(img, (TILE_W, TILE_H)), 22)
                self.bg = make_bg(img)
            else:
                ph = placeholder(game.get("name", "?"))
                self.img = rounded(ph, 22)
                self.bg = make_bg(ph)
            self.small = pygame.transform.smoothscale(self.img, (int(TILE_W * SMALL), int(TILE_H * SMALL)))

    # ---------------- state
    st = {
        "tiles": [],
        "sel": 0,
        "cam": None,
        "bg_prev": None,
        "bg_alpha": 0.0,
        "toast": None,
        "modal": [],  # stack of overlay screens
        "quit": None,
        "mouse_t": 0.0,
        "rects": [],
        "phase": None,   # running game: stages out -> splash -> back
        "boot": time.time(),
        "grace": time.time(),
    }

    def rebuild(select=None):
        st["tiles"] = [Tile(g) for g in conf["games"]] + [Tile(None)]
        n = len(st["tiles"])
        if select is not None:
            st["sel"] = select
        st["sel"] = max(0, min(st["sel"], n - 1))
        for i, t in enumerate(st["tiles"]):
            t.scale = 1.0 if i == st["sel"] else SMALL

    st["sel"] = conf.get("last", 0)
    rebuild()

    def toast(msg, secs=3.5):
        st["toast"] = (msg, time.time() + secs)

    def set_sel(i):
        n = len(st["tiles"])
        i = max(0, min(i, n - 1))
        if i != st["sel"]:
            st["bg_prev"] = st["tiles"][st["sel"]].bg
            st["bg_alpha"] = 255.0
            st["sel"] = i

    # ---------------- input
    devs = {"ctrl": {}, "joy": {}}
    held = {}  # source -> [action, next_time]
    axis_state = {}
    REPEAT_DELAY, REPEAT_RATE = 0.36, 0.11
    CBTN = {
        pygame.CONTROLLER_BUTTON_A: "a", pygame.CONTROLLER_BUTTON_B: "b",
        pygame.CONTROLLER_BUTTON_X: "x", pygame.CONTROLLER_BUTTON_Y: "y",
        pygame.CONTROLLER_BUTTON_START: "start", pygame.CONTROLLER_BUTTON_GUIDE: "start",
        pygame.CONTROLLER_BUTTON_BACK: "select",
        pygame.CONTROLLER_BUTTON_LEFTSHOULDER: "lb", pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: "rb",
    }
    CDPAD = {
        pygame.CONTROLLER_BUTTON_DPAD_LEFT: "left", pygame.CONTROLLER_BUTTON_DPAD_RIGHT: "right",
        pygame.CONTROLLER_BUTTON_DPAD_UP: "up", pygame.CONTROLLER_BUTTON_DPAD_DOWN: "down",
    }
    JBTN = {v: k for k, v in conf["joy_buttons"].items()}
    KEYS = {
        pygame.K_LEFT: "left", pygame.K_RIGHT: "right", pygame.K_UP: "up", pygame.K_DOWN: "down",
        pygame.K_RETURN: "a", pygame.K_KP_ENTER: "a", pygame.K_ESCAPE: "b",
        pygame.K_TAB: "start", pygame.K_F2: "y", pygame.K_DELETE: "x",
        pygame.K_PAGEUP: "lb", pygame.K_PAGEDOWN: "rb",
    }

    def press_dir(src, action, out):
        old = held.pop(src, None)
        if action:
            held[src] = [action, time.time() + REPEAT_DELAY]
            if not old or old[0] != action:
                out.append(action)

    def axis_dir(key, value, neg, pos, out):
        cur = axis_state.get(key)
        if value > 0.6:
            new = pos
        elif value < -0.6:
            new = neg
        elif abs(value) < 0.35:
            new = None
        else:
            new = cur
        if new != cur:
            axis_state[key] = new
            press_dir(key, new, out)

    def translate(ev, out):
        t = ev.type
        if t == pygame.JOYDEVICEADDED:
            idx = ev.device_index
            try:
                if controller.is_controller(idx):
                    c = controller.Controller(idx)
                    devs["ctrl"][c.as_joystick().get_instance_id()] = c
                else:
                    j = pygame.joystick.Joystick(idx)
                    devs["joy"][j.get_instance_id()] = j
                toast("Mando conectado")
            except pygame.error:
                pass
        elif t == pygame.JOYDEVICEREMOVED:
            iid = ev.instance_id
            if devs["ctrl"].pop(iid, None) or devs["joy"].pop(iid, None):
                toast("Mando desconectado")
            for k in [k for k in held if k[1] == iid]:
                held.pop(k)
        elif t == pygame.CONTROLLERBUTTONDOWN:
            if ev.button in CDPAD:
                press_dir(("dpad", ev.instance_id, ev.button), CDPAD[ev.button], out)
            elif ev.button in CBTN:
                out.append(CBTN[ev.button])
        elif t == pygame.CONTROLLERBUTTONUP:
            held.pop(("dpad", ev.instance_id, ev.button), None)
        elif t == pygame.CONTROLLERAXISMOTION:
            v = ev.value / 32767.0
            if ev.axis == pygame.CONTROLLER_AXIS_LEFTX:
                axis_dir(("ax", ev.instance_id, 0), v, "left", "right", out)
            elif ev.axis == pygame.CONTROLLER_AXIS_LEFTY:
                axis_dir(("ax", ev.instance_id, 1), v, "up", "down", out)
        elif t in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP, pygame.JOYAXISMOTION, pygame.JOYHATMOTION):
            if ev.instance_id in devs["ctrl"]:
                return  # handled as a game controller
            if t == pygame.JOYBUTTONDOWN and ev.button in JBTN:
                out.append(JBTN[ev.button])
            elif t == pygame.JOYAXISMOTION and ev.axis in (0, 1):
                neg, pos = ("left", "right") if ev.axis == 0 else ("up", "down")
                axis_dir(("ax", ev.instance_id, ev.axis), ev.value, neg, pos, out)
            elif t == pygame.JOYHATMOTION:
                x, y = ev.value
                act = "left" if x < 0 else "right" if x > 0 else "up" if y > 0 else "down" if y < 0 else None
                press_dir(("hat", ev.instance_id, ev.hat), act, out)
        elif t == pygame.KEYDOWN and ev.key in KEYS:
            out.append(KEYS[ev.key])

    def repeats(out):
        now = time.time()
        for v in held.values():
            if now >= v[1]:
                out.append(v[0])
                v[1] = now + REPEAT_RATE

    # ---------------- drawing helpers
    BTN_COL = {"A": (90, 190, 110), "B": (220, 85, 80), "X": (80, 140, 230), "Y": (235, 195, 70),
               "☰": (120, 120, 130), "LB": (120, 120, 130), "RB": (120, 120, 130), "START": (120, 120, 130)}

    def draw_hints(surf, hints, y=H - 62):
        items = []
        for btn, label in hints:
            lb = F_HINT.render(label, True, WHITE)
            items.append((btn, lb))
        total = sum(44 + 12 + lb.get_width() + 40 for _, lb in items) - 40
        x = (W - total) // 2
        for btn, lb in items:
            col = BTN_COL.get(btn, (120, 120, 130))
            txt = F_BTN.render(btn if len(btn) < 3 else btn[:5], True, (15, 15, 15))
            bw = max(44, txt.get_width() + 22)
            pygame.draw.rect(surf, col, (x, y, bw, 44), border_radius=22)
            surf.blit(txt, (x + (bw - txt.get_width()) // 2, y + (44 - txt.get_height()) // 2))
            x += bw + 12
            surf.blit(lb, (x, y + (44 - lb.get_height()) // 2))
            x += lb.get_width() + 40

    def text_shadow(surf, fnt, text, pos, col=WHITE, center=True):
        r = fnt.render(text, True, col)
        sh = fnt.render(text, True, (0, 0, 0))
        sh.set_alpha(150)
        x, y = pos
        if center:
            x -= r.get_width() // 2
        surf.blit(sh, (x + 3, y + 3))
        surf.blit(r, (x, y))

    def dim(surf, a=170):
        d = pygame.Surface((W, H), pygame.SRCALPHA)
        d.fill((0, 0, 0, a))
        surf.blit(d, (0, 0))

    # ---------------- overlay screens
    class ListMenu:
        def __init__(self, title, items, subtitle=None, on_cancel=None):
            self.title, self.items, self.subtitle = title, items, subtitle
            self.sel, self.on_cancel = 0, on_cancel

        def action(self, a):
            if a == "up":
                self.sel = (self.sel - 1) % len(self.items)
            elif a == "down":
                self.sel = (self.sel + 1) % len(self.items)
            elif a == "a":
                st["modal"].remove(self)
                cb = self.items[self.sel][1]
                if cb:
                    cb()
            elif a in ("b", "start"):
                st["modal"].remove(self)
                if self.on_cancel:
                    self.on_cancel()

        def draw(self, surf):
            dim(surf)
            pw = 760
            ph = 150 + len(self.items) * 76 + (50 if self.subtitle else 0)
            px, py = (W - pw) // 2, (H - ph) // 2 - 30
            pygame.draw.rect(surf, PANEL, (px, py, pw, ph), border_radius=26)
            pygame.draw.rect(surf, (60, 64, 74), (px, py, pw, ph), width=2, border_radius=26)
            y = py + 36
            for ln in wrap(self.title, F_BIG, pw - 80)[:2]:
                r = F_BIG.render(ln, True, WHITE)
                surf.blit(r, (px + (pw - r.get_width()) // 2, y))
                y += 52
            if self.subtitle:
                r = F_SMALL.render(self.subtitle, True, GREY)
                surf.blit(r, (px + (pw - r.get_width()) // 2, y))
                y += 46
            y += 14
            for i, (label, _) in enumerate(self.items):
                rect = pygame.Rect(px + 40, y, pw - 80, 64)
                if i == self.sel:
                    pygame.draw.rect(surf, ACCENT, rect, border_radius=16)
                    col = (20, 20, 20)
                else:
                    col = WHITE
                r = F_MED.render(label, True, col)
                surf.blit(r, (rect.x + 30, rect.y + (64 - r.get_height()) // 2))
                y += 76
            draw_hints(surf, [("A", "Aceptar"), ("B", "Volver")])

    class Browser:
        """Gamepad-friendly file browser. mode: 'exec' or 'image'."""

        def __init__(self, mode, start, on_pick, on_cancel):
            self.mode, self.on_pick, self.on_cancel = mode, on_pick, on_cancel
            self.preview_cache = {}
            self.open_dir(start if os.path.isdir(start) else os.path.expanduser("~"))

        def open_dir(self, d, focus=None):
            self.dir = os.path.abspath(d)
            entries = []
            if self.mode == "image":
                entries.append(("none", None, "(Sin carátula)"))
            if self.dir != "/":
                entries.append(("up", os.path.dirname(self.dir), "<  Subir"))
            try:
                names = sorted(os.listdir(self.dir), key=str.lower)
            except OSError:
                names = []
            dirs, files = [], []
            for n in names[:3000]:
                if n.startswith("."):
                    continue
                p = os.path.join(self.dir, n)
                if os.path.isdir(p):
                    dirs.append(("dir", p, n + "/"))
                elif self.mode == "image" and n.lower().endswith(IMAGE_EXTS):
                    files.append(("file", p, n))
                elif self.mode == "exec" and is_launchable(p):
                    files.append(("file", p, n))
            self.entries = entries + files + dirs
            self.sel = 0
            if focus:
                for i, e in enumerate(self.entries):
                    if e[1] == focus:
                        self.sel = i
            elif files:
                self.sel = self.entries.index(files[0])
            self.top = 0

        def action(self, a):
            n = len(self.entries)
            if a == "up" and n:
                self.sel = (self.sel - 1) % n
            elif a == "down" and n:
                self.sel = (self.sel + 1) % n
            elif a in ("lb", "left") and n:
                self.sel = max(0, self.sel - 10)
            elif a in ("rb", "right") and n:
                self.sel = min(n - 1, self.sel + 10)
            elif a == "a" and n:
                kind, p, _ = self.entries[self.sel]
                if kind in ("dir", "up"):
                    self.open_dir(p, focus=self.dir if kind == "up" else None)
                else:
                    st["modal"].remove(self)
                    self.on_pick(p)
            elif a == "b":
                if self.dir != "/" and self.dir != os.path.abspath(browse_root):
                    self.open_dir(os.path.dirname(self.dir), focus=self.dir)
                else:
                    st["modal"].remove(self)
                    self.on_cancel()
            elif a == "start":
                st["modal"].remove(self)
                self.on_cancel()

        def preview(self, p):
            if p not in self.preview_cache:
                try:
                    img = pygame.image.load(p)
                    iw, ih = img.get_size()
                    sc = min(560 / iw, 720 / ih)
                    self.preview_cache[p] = pygame.transform.smoothscale(img, (int(iw * sc), int(ih * sc)))
                except pygame.error:
                    self.preview_cache[p] = None
            return self.preview_cache[p]

        def draw(self, surf):
            dim(surf, 210)
            title = "Elige el ejecutable del juego" if self.mode == "exec" else "Elige una carátula (opcional)"
            text_shadow(surf, F_TITLE, title, (W // 2, 50))
            path = self.dir if len(self.dir) < 90 else "…" + self.dir[-88:]
            r = F_SMALL.render(path, True, GREY)
            surf.blit(r, ((W - r.get_width()) // 2, 140))
            lx, lw = 140, 1060 if self.mode == "image" else 1640
            rows, rh, y0 = 11, 66, 200
            if self.sel < self.top:
                self.top = self.sel
            if self.sel >= self.top + rows:
                self.top = self.sel - rows + 1
            if not self.entries:
                r = F_MED.render("(carpeta vacía)", True, GREY)
                surf.blit(r, (lx + 30, y0 + 10))
            for i in range(self.top, min(len(self.entries), self.top + rows)):
                kind, p, label = self.entries[i]
                rect = pygame.Rect(lx, y0 + (i - self.top) * rh, lw, rh - 8)
                sel = i == self.sel
                if sel:
                    pygame.draw.rect(surf, ACCENT, rect, border_radius=14)
                col = (20, 20, 20) if sel else (WHITE if kind == "file" or kind == "none" else (190, 205, 230))
                icon = ""
                txt = label if len(label) < 70 else label[:67] + "…"
                r = F_MED.render(icon + txt, True, col)
                surf.blit(r, (rect.x + 24, rect.y + (rect.h - r.get_height()) // 2))
            if len(self.entries) > rows:
                bar_h = rows * rh
                kh = max(40, bar_h * rows // len(self.entries))
                ky = y0 + (bar_h - kh) * self.top // max(1, len(self.entries) - rows)
                pygame.draw.rect(surf, (70, 70, 80), (lx + lw + 16, y0, 8, bar_h), border_radius=4)
                pygame.draw.rect(surf, GREY, (lx + lw + 16, ky, 8, kh), border_radius=4)
            if self.mode == "image" and self.entries and self.entries[self.sel][0] == "file":
                img = self.preview(self.entries[self.sel][1])
                if img:
                    surf.blit(img, (1260 + (560 - img.get_width()) // 2, 200 + (720 - img.get_height()) // 2))
            draw_hints(surf, [("A", "Abrir / elegir"), ("B", "Atrás"), ("LB", "Página"), ("START", "Cancelar")])

    class Keyboard:
        ROWS = ["1234567890", "QWERTYUIOP", "ASDFGHJKLÑ", "ZXCVBNM-:'", ".,!?&()+_/"]
        SPECIAL = ["Espacio", "Borrar", "Mayús", "OK"]

        def __init__(self, title, text, on_done, on_cancel):
            self.title, self.text, self.on_done, self.on_cancel = title, text, on_done, on_cancel
            self.r, self.c, self.upper = 1, 0, False
            pygame.key.start_text_input()

        def close(self):
            pygame.key.stop_text_input()
            st["modal"].remove(self)

        def raw(self, ev):
            if ev.type == pygame.TEXTINPUT:
                self.text = (self.text + ev.text)[:40]
                return True
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_BACKSPACE:
                    self.text = self.text[:-1]
                    return True
                if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.finish()
                    return True
                if ev.key == pygame.K_ESCAPE:
                    self.close()
                    self.on_cancel()
                    return True
                return ev.key not in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN, pygame.K_TAB)
            return False

        def finish(self):
            name = self.text.strip()
            if not name:
                toast("El nombre no puede estar vacío")
                return
            self.close()
            self.on_done(name)

        def row_len(self, r):
            return len(self.SPECIAL) if r == len(self.ROWS) else len(self.ROWS[r])

        def action(self, a):
            nrows = len(self.ROWS) + 1
            if a in ("left", "right"):
                self.c = (self.c + (1 if a == "right" else -1)) % self.row_len(self.r)
            elif a in ("up", "down"):
                old = self.row_len(self.r)
                self.r = (self.r + (1 if a == "down" else -1)) % nrows
                self.c = min(int(self.c * self.row_len(self.r) / old), self.row_len(self.r) - 1)
            elif a == "a":
                if self.r == len(self.ROWS):
                    k = self.SPECIAL[self.c]
                    if k == "Espacio":
                        self.text = (self.text + " ")[:40]
                    elif k == "Borrar":
                        self.text = self.text[:-1]
                    elif k == "Mayús":
                        self.upper = not self.upper
                    else:
                        self.finish()
                else:
                    ch = self.ROWS[self.r][self.c]
                    self.text = (self.text + (ch if self.upper or not self.text or self.text.endswith(" ") else ch.lower()))[:40]
            elif a == "x":
                self.text = self.text[:-1]
            elif a == "y":
                self.text = (self.text + " ")[:40]
            elif a == "start":
                self.finish()
            elif a == "b":
                self.close()
                self.on_cancel()

        def draw(self, surf):
            dim(surf, 210)
            text_shadow(surf, F_TITLE, self.title, (W // 2, 80))
            box = pygame.Rect(360, 200, 1200, 96)
            pygame.draw.rect(surf, PANEL, box, border_radius=18)
            pygame.draw.rect(surf, ACCENT, box, width=3, border_radius=18)
            caret = "|" if int(time.time() * 2) % 2 else " "
            r = F_BIG.render(self.text + caret, True, WHITE)
            surf.blit(r, (box.x + 30, box.y + (box.h - r.get_height()) // 2))
            ky = 350
            kw, kh, kg = 116, 92, 12
            for ri, row in enumerate(self.ROWS):
                x0 = (W - (len(row) * (kw + kg) - kg)) // 2
                for ci, ch in enumerate(row):
                    rect = pygame.Rect(x0 + ci * (kw + kg), ky + ri * (kh + kg), kw, kh)
                    sel = (ri, ci) == (self.r, self.c)
                    pygame.draw.rect(surf, ACCENT if sel else (44, 47, 56), rect, border_radius=14)
                    lab = ch if self.upper else ch.lower()
                    t = F_BIG.render(lab, True, (20, 20, 20) if sel else WHITE)
                    surf.blit(t, (rect.centerx - t.get_width() // 2, rect.centery - t.get_height() // 2))
            ri = len(self.ROWS)
            sw = [380, 260, 260, 260]
            x = (W - (sum(sw) + kg * 3)) // 2
            for ci, lab in enumerate(self.SPECIAL):
                rect = pygame.Rect(x, ky + ri * (kh + kg), sw[ci], kh)
                sel = (ri, ci) == (self.r, self.c)
                on = lab == "Mayús" and self.upper
                pygame.draw.rect(surf, ACCENT if sel else ((80, 90, 70) if on else (44, 47, 56)), rect, border_radius=14)
                t = F_MED.render(lab, True, (20, 20, 20) if sel else WHITE)
                surf.blit(t, (rect.centerx - t.get_width() // 2, rect.centery - t.get_height() // 2))
                x += sw[ci] + kg
            draw_hints(surf, [("A", "Escribir"), ("X", "Borrar"), ("Y", "Espacio"), ("START", "Aceptar"), ("B", "Cancelar")])

    # ---------------- actions
    def write_action(a):
        with open(action_path, "w") as f:
            f.write(a)
        st["quit"] = a

    def copy_cover(src, name):
        if not src:
            return None
        os.makedirs(COVERS_DIR, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "juego"
        dst = os.path.join(COVERS_DIR, "%s-%d%s" % (slug, int(time.time()), os.path.splitext(src)[1].lower()))
        try:
            shutil.copyfile(src, dst)
            return dst
        except OSError:
            return src

    def helper_active():
        if shot:
            return os.environ.get("ZL_FAKE_HELPER") == "1"
        try:
            out = subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path", "/org/gnome/Shell",
                 "--method", "org.gnome.Shell.Extensions.GetExtensionInfo", HELPER_UUID],
                capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        m = re.search(r"'state': <([\d.]+)>", out)
        return bool(m) and float(m.group(1)) == 1.0

    def spawn(g):
        exe = g["exec"]
        env = dict(orig_env)
        env.update({k: str(v) for k, v in g.get("env", {}).items()})
        log = open(os.path.join(STATE_DIR, "game.log"), "a")
        log.write("=== %s %s\n" % (time.strftime("%F %T"), exe))
        log.flush()
        try:
            if shot:
                return subprocess.Popen(["sleep", "2"])
            return subprocess.Popen([exe] + list(g.get("args", [])), cwd=g.get("cwd") or os.path.dirname(exe),
                                    env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    start_new_session=True)
        except OSError as e:
            log.write("error: %s\n" % e)
            return None
        finally:
            log.close()

    splash_cache = {}

    def draw_splash(surf, i, label, now):
        t = st["tiles"][i]
        surf.blit(t.bg, (0, 0))
        if i not in splash_cache:
            splash_cache.clear()
            splash_cache[i] = pygame.transform.smoothscale(t.img, (int(TILE_W * 1.15), int(TILE_H * 1.15)))
        big = splash_cache[i]
        x, y = (W - big.get_width()) // 2, 90
        pygame.draw.rect(surf, ACCENT, (x - 8, y - 8, big.get_width() + 16, big.get_height() + 16), width=6, border_radius=30)
        surf.blit(big, (x, y))
        text_shadow(surf, F_TITLE, label, (W // 2, 880))
        a = now * 5.0
        rect = pygame.Rect(0, 0, 54, 54)
        rect.center = (W // 2, 1010)
        pygame.draw.circle(surf, (80, 82, 92), rect.center, 27, width=5)
        pygame.draw.arc(surf, ACCENT, rect, a, a + 1.9, width=6)

    def play(i):
        g = conf["games"][i]
        exe = g["exec"]
        if not os.path.isfile(exe):
            toast("No encuentro el juego. ¿Está conectado el disco?")
            return
        if not os.access(exe, os.X_OK):
            toast("El archivo del juego no tiene permiso de ejecución")
            return
        conf["last"] = i
        save_conf(conf)
        if helper_active():
            # the launcher window stays open behind the game: no desktop in between
            st["phase"] = {"stage": "out", "t": time.time(), "idx": i, "proc": None,
                           "hidden": False, "was_hidden": False}
            return
        # fallback without the GNOME extension: close the window, session.sh runs the game
        t = st["tiles"][i]
        draw_splash(screen, i, "Iniciando %s…" % g["name"], time.time())
        pygame.display.flip()
        pygame.time.wait(400)
        write_action("play:%d" % i)

    def add_game():
        data = {}

        def got_exec(p):
            data["exec"] = p
            st["modal"].append(Browser("image", os.path.dirname(p), got_cover, cancel))

        def got_cover(p):
            data["cover"] = p
            st["modal"].append(Keyboard("Nombre del juego", guess_name(data["exec"]), got_name, cancel))

        def got_name(name):
            g = {"name": name, "exec": data["exec"], "cwd": os.path.dirname(data["exec"]),
                 "cover": copy_cover(data.get("cover"), name)}
            conf["games"].append(g)
            save_conf(conf)
            rebuild(select=len(conf["games"]) - 1)
            toast("«%s» añadido" % name)

        def cancel():
            toast("Cancelado")

        st["modal"].append(Browser("exec", browse_root, got_exec, cancel))

    def game_options(i):
        g = conf["games"][i]

        def rename():
            def done(name):
                g["name"] = name
                save_conf(conf)
                rebuild()
            st["modal"].append(Keyboard("Cambiar nombre", g["name"], done, lambda: None))

        def recover():
            def done(p):
                g["cover"] = copy_cover(p, g["name"])
                save_conf(conf)
                rebuild()
            st["modal"].append(Browser("image", os.path.dirname(g["exec"]), done, lambda: None))

        def move(d):
            j = i + d
            if 0 <= j < len(conf["games"]):
                conf["games"][i], conf["games"][j] = conf["games"][j], conf["games"][i]
                save_conf(conf)
                rebuild(select=j)

        def remove():
            def yes():
                conf["games"].pop(i)
                save_conf(conf)
                rebuild(select=max(0, i - 1))
                toast("«%s» quitado de la lista" % g["name"])
            st["modal"].append(ListMenu("¿Quitar «%s»?" % g["name"], [("No", None), ("Sí, quitar", yes)],
                                        subtitle="Solo se quita de la lista, no se borra el juego"))

        items = [("Jugar", lambda: play(i)), ("Cambiar nombre", rename), ("Cambiar carátula", recover)]
        if i > 0:
            items.append(("Mover a la izquierda", lambda: move(-1)))
        if i < len(conf["games"]) - 1:
            items.append(("Mover a la derecha", lambda: move(1)))
        items += [("Quitar de la lista", remove), ("Volver", None)]
        st["modal"].append(ListMenu(g["name"], items))

    def system_menu():
        st["modal"].append(ListMenu("Menú", [
            ("Añadir juego", add_game),
            ("Salir al escritorio", lambda: write_action("desktop")),
            ("Reiniciar", lambda: write_action("reboot")),
            ("Apagar", lambda: write_action("poweroff")),
            ("Volver", None),
        ]))

    def main_action(a):
        n = len(st["tiles"])
        sel = st["sel"]
        is_add = sel == n - 1
        if a == "left":
            set_sel(sel - 1)
        elif a == "right":
            set_sel(sel + 1)
        elif a == "lb":
            set_sel(0)
        elif a == "rb":
            set_sel(n - 1)
        elif a == "a":
            add_game() if is_add else play(sel)
        elif a == "y" and not is_add:
            game_options(sel)
        elif a in ("start", "select"):
            system_menu()

    # ---------------- main draw
    def draw_main(surf, dt, hints=True):
        tiles, sel = st["tiles"], st["sel"]
        surf.blit(tiles[sel].bg, (0, 0))
        if st["bg_prev"] is not None and st["bg_alpha"] > 0:
            st["bg_prev"].set_alpha(int(st["bg_alpha"]))
            surf.blit(st["bg_prev"], (0, 0))
            st["bg_prev"].set_alpha(None)
            st["bg_alpha"] = max(0.0, st["bg_alpha"] - dt * 900)
        k = 1 - math.exp(-dt * 14)
        x, centers = 0.0, []
        for i, t in enumerate(tiles):
            t.scale += ((1.0 if i == sel else SMALL) - t.scale) * k
            w = TILE_W * t.scale
            centers.append(x + w / 2)
            x += w + GAP
        if st["cam"] is None:
            st["cam"] = centers[sel]
        st["cam"] += (centers[sel] - st["cam"]) * k
        cy = 470
        st["rects"] = []
        for i, t in enumerate(tiles):
            w, h = int(TILE_W * t.scale), int(TILE_H * t.scale)
            sx = int(W / 2 + centers[i] - st["cam"] - w / 2)
            sy = int(cy - h / 2)
            st["rects"].append(pygame.Rect(sx, sy, w, h))
            if sx > W or sx + w < 0:
                continue
            if i == sel:
                glow = pygame.Rect(sx - 8, sy - 8, w + 16, h + 16)
                pygame.draw.rect(surf, ACCENT, glow, width=6, border_radius=28)
            if abs(t.scale - 1.0) < 0.003:
                img = t.img
            elif t.game is not None and abs(t.scale - SMALL) < 0.003:
                img = t.small
            else:
                img = pygame.transform.smoothscale(t.img, (w, h))
            surf.blit(img, (sx, sy))
            if i != sel and t.game is not None:
                a = int(130 * (1.0 - t.scale) / (1.0 - SMALL))
                ov = pygame.Surface((w, h), pygame.SRCALPHA)
                pygame.draw.rect(ov, (0, 0, 0, max(0, min(255, a))), ov.get_rect(), border_radius=18)
                surf.blit(ov, (sx, sy))
        cur = tiles[sel]
        name = cur.game["name"] if cur.game else "Añadir un juego nuevo"
        text_shadow(surf, F_TITLE, name, (W // 2, 840))
        if not hints:
            pass
        elif cur.game:
            draw_hints(surf, [("A", "Jugar"), ("Y", "Opciones"), ("START", "Menú")])
        else:
            draw_hints(surf, [("A", "Añadir juego"), ("START", "Menú")])
        # status line
        text_shadow(surf, F_BIG, time.strftime("%H:%M"), (W - 170, 40), center=False)
        ndev = len(devs["ctrl"]) + len(devs["joy"])
        col = (110, 210, 120) if ndev else (150, 150, 150)
        pygame.draw.circle(surf, col, (70, 66), 10)
        text_shadow(surf, F_SMALL, "Mando conectado" if ndev else "Sin mando (pulsa el botón central)", (92, 48), GREY, center=False)

    def draw_toast(surf):
        if not st["toast"]:
            return
        msg, until = st["toast"]
        left = until - time.time()
        if left <= 0:
            st["toast"] = None
            return
        r = F_MED.render(msg, True, WHITE)
        box = pygame.Rect(0, 0, r.get_width() + 60, 70)
        box.center = (W // 2, 66)
        s = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(s, (15, 15, 20, 230), s.get_rect(), border_radius=35)
        s.blit(r, (30, (70 - r.get_height()) // 2))
        s.set_alpha(int(255 * min(1.0, left / 0.4)))
        surf.blit(s, box.topleft)

    # ---------------- loop
    clock = pygame.time.Clock()
    FADE_OUT, FADE_IN, FADE_BOOT = 0.3, 0.45, 0.9
    black = pygame.Surface((W, H))
    black.fill((0, 0, 0))
    start = time.time()
    script = [a for a in os.environ.get("ZL_SCRIPT", "").split(",") if a]
    frames = 0
    while st["quit"] is None:
        ph = st["phase"]
        busy = (st["modal"] or st["toast"] or held or st["bg_alpha"] > 0 or ph
                or time.time() - st.get("busy_t", 0) < 1.0 or time.time() - st["boot"] < 1.0)
        fps = 5 if ph and ph["hidden"] else 60 if busy else 15
        dt = min(clock.tick(fps) / 1000.0, 0.1)
        actions = []
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                write_action("desktop")
                break
            if ph:
                if ev.type == pygame.WINDOWFOCUSLOST and ph["stage"] == "splash":
                    ph["hidden"] = ph["was_hidden"] = True   # the game is in front: stop drawing
                elif ev.type == pygame.WINDOWFOCUSGAINED:
                    ph["hidden"] = False
                elif ev.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
                    translate(ev, [])
                continue
            top = st["modal"][-1] if st["modal"] else None
            if top is not None and hasattr(top, "raw") and top.raw(ev):
                continue
            if ev.type == pygame.MOUSEMOTION:
                pygame.mouse.set_visible(True)
                st["mouse_t"] = time.time()
            elif ev.type == pygame.MOUSEBUTTONDOWN and not st["modal"]:
                if ev.button == 1:
                    for i, r in enumerate(st["rects"]):
                        if r.collidepoint(ev.pos):
                            if i == st["sel"]:
                                actions.append("a")
                            else:
                                set_sel(i)
                elif ev.button == 3:
                    actions.append("y")
            elif ev.type == pygame.MOUSEWHEEL and not st["modal"]:
                actions.append("left" if ev.y > 0 or ev.x < 0 else "right")
            translate(ev, actions)
        repeats(actions)
        if actions:
            st["busy_t"] = time.time()
        now = time.time()
        if ph:
            actions = []
            el = now - ph["t"]
            if ph["stage"] == "out" and el >= FADE_OUT:
                ph["proc"] = spawn(conf["games"][ph["idx"]])
                ph["stage"], ph["t"], ph["started"] = "splash", now, now
                if ph["proc"] is None:
                    toast("No se pudo lanzar el juego (mira game.log)")
                    ph["stage"] = "back"
            elif ph["stage"] == "splash" and ph["proc"].poll() is not None:
                if now - ph["started"] < 5 and not ph["was_hidden"]:
                    toast("El juego se ha cerrado al arrancar (mira game.log)", 6)
                ph["stage"], ph["t"], ph["hidden"] = "back", now, False
            elif ph["stage"] == "back" and el >= FADE_OUT + FADE_IN:
                st["phase"] = ph = None
                pygame.event.clear()
                held.clear()
                axis_state.clear()
                st["grace"] = now
        if now - st["grace"] < 0.5:
            actions = []  # ignore buttons still pressed from the game
        if script and frames % 20 == 10:
            actions.append(script.pop(0))
        for a in actions:
            if st["quit"] is not None:
                break
            if st["modal"]:
                st["modal"][-1].action(a)
            else:
                main_action(a)
        if st["quit"] is not None:
            break
        if pygame.mouse.get_visible() and time.time() - st["mouse_t"] > 3:
            pygame.mouse.set_visible(False)
        if ph and ph["hidden"]:
            continue  # game on top: only keep answering the compositor
        fade = 0.0
        fade_src = black
        if ph:
            el = now - ph["t"]
            name = conf["games"][ph["idx"]]["name"]
            if ph["stage"] == "out":
                draw_main(screen, dt, hints=False)
                fade = el / FADE_OUT
            elif ph["stage"] == "splash":
                label = "Volviendo…" if ph["was_hidden"] else "Cargando %s" % name
                draw_splash(screen, ph["idx"], label, now)
                fade = 1.0 - el / FADE_IN
            elif el < FADE_OUT:
                draw_splash(screen, ph["idx"], "Volviendo…", now)
                fade = el / FADE_OUT
            else:
                draw_main(screen, dt, hints=True)
                fade = 1.0 - (el - FADE_OUT) / FADE_IN
        else:
            draw_main(screen, dt, hints=not st["modal"])
            for m in st["modal"]:
                m.draw(screen)
            fade = 1.0 - (now - st["boot"]) / FADE_BOOT
            fade_src = boot_img or black
        draw_toast(screen)
        fade = max(0.0, min(1.0, fade))
        if fade > 0:
            fade_src.set_alpha(int(255 * (fade * fade * (3 - 2 * fade))))
            screen.blit(fade_src, (0, 0))
        pygame.display.flip()
        frames += 1
        if shot and not script and frames > 60 + 20 * len(os.environ.get("ZL_SCRIPT", "").split(",")):
            pygame.image.save(screen, shot)
            break
    pygame.quit()


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    if len(sys.argv) >= 3 and sys.argv[1] == "--run":
        run_game(int(sys.argv[2]))
    elif len(sys.argv) >= 3 and sys.argv[1] == "--menu":
        try:
            main_menu(sys.argv[2])
        except Exception:
            with open(os.path.join(STATE_DIR, "launcher.log"), "a") as f:
                f.write(time.strftime("%F %T ") + traceback.format_exc() + "\n")
            raise
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
