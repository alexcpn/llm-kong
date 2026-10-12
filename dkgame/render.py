"""Curses drawing for the game in plain characters: board, Kong, Pauline, barrels, HUD, what the
characters say, and Kong's plan. Works in any terminal with basic colours."""

from __future__ import annotations

import curses
import textwrap

PAIRS = {"girder": 1, "danger": 2, "ladder": 3, "player": 4, "goal": 5, "ui": 6}

# Kong, 4 wide (x-1 to x+2) and 3 tall: a big brow, close-set eyes, tapering to a point
KONG = ("▄▄▄▄", "▐òó▌", " ▀▀ ")
KONG_STORMING = ("▄▄▄▄", "▐ÒÓ▌", " ▀▀ ")


def setup_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    for name, colour in (("girder", curses.COLOR_YELLOW), ("danger", curses.COLOR_RED), ("ladder", curses.COLOR_CYAN),
                         ("player", curses.COLOR_GREEN), ("goal", curses.COLOR_MAGENTA), ("ui", curses.COLOR_WHITE)):
        curses.init_pair(PAIRS[name], colour, bg)


def c(name: str) -> int:
    try:
        return curses.color_pair(PAIRS[name])
    except curses.error:
        return 0


def put(win, y: int, x: int, text: str, attr: int = 0) -> None:
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w and text:
        try:
            win.addnstr(y, x, text, max(0, w - x - (1 if y == h - 1 else 0)), attr)
        except curses.error:
            pass


def centre(win, y: int, text: str, attr: int = 0) -> None:
    put(win, y, max(0, (win.getmaxyx()[1] - len(text)) // 2), text, attr)


def needed_size(layout: dict) -> tuple[int, int]:
    return layout["floors"][0] + 8, layout["width"] + 2


def draw_game(win, layout: dict, s: dict, hud: dict) -> None:
    """layout = Layout.to_dict(); s = Game.state(); hud = score/best/opponent/plan/banner."""
    win.erase()
    h, w = win.getmaxyx()
    need_h, need_w = needed_size(layout)
    if h < need_h or w < need_w:
        put(win, 1, 1, f"Please enlarge the terminal to at least {need_w} x {need_h}.", c("ui") | curses.A_BOLD)
        put(win, 3, 1, "Q quits.")
        win.refresh()
        return
    left = max(0, (w - layout["width"]) // 2)
    p = s["player"]
    left_n = s.get("kong", {}).get("barrels_left")
    barrels = f"  ●×{left_n:02d}" if left_n is not None else f"  vs {hud['opponent']}"
    # score in gold and lives as red hearts; each flashes when it changes (hud flags), hearts keep
    # blinking on the last life
    blink = int(s.get("t", 0) * 6) % 2 == 0
    gold = c("girder") | curses.A_BOLD
    red = c("danger") | curses.A_BOLD
    ui = c("ui") | curses.A_BOLD
    score_look = gold | (curses.A_REVERSE if hud.get("score_flash") and blink else 0)
    hearts_flash = hud.get("lives_flash") or s["lives"] == 1
    hearts_look = red | (curses.A_REVERSE if hearts_flash and blink else 0)
    x = left
    for text, look in ((f"SCORE {hud['score']:06d}", score_look), (f"  BEST {hud['best']:06d}  LV {s['level']:02d}  ", ui),
                       ("♥" * s["lives"] or "-", hearts_look), (f"  TIME {int(s['time_left']):02d}{barrels}", ui)):
        put(win, 0, x, text, look)
        x += len(text)
    said = hud.get("said")                                # (speaker, line): Kong, Pauline or the player
    if said:
        name = {"kong": "KONG", "pauline": "PAULINE", "player": "YOU"}.get(said[0], said[0].upper())
        colour = {"kong": "danger", "pauline": "goal", "player": "player"}.get(said[0], "ui")
        put(win, 1, left, f'{name}: "{said[1]}"'[: layout["width"]], c(colour) | curses.A_BOLD)
    oy = 3                                                # header, Kong's speech, then room for his head
    floors = layout["floors"]
    span = layout["x_max"] - layout["x_min"] + 1
    for i, row in enumerate(floors):
        slope = "\\" if i % 2 == 0 else "/"
        put(win, oy + row + 1, left + layout["x_min"], slope + "=" * (span - 2) + slope, c("girder") | curses.A_BOLD)
    for lad in layout["ladders"]:
        for row in range(floors[lad["top_floor"]] + 1, floors[lad["bottom_floor"]] + 1):
            put(win, oy + row, left + lad["x"], "H", c("ladder") | curses.A_BOLD)
    top = floors[-1]
    kong = s.get("kong", {})
    kx, ky = kong.get("x", layout["x_min"] + 1), int(kong.get("y", top) + 0.5)
    loose = kong.get("mode", "perch") != "perch"
    # 3 rows tall (girders are only 3 rows apart), 4 wide from x-1 to x+2
    look = c("danger") | curses.A_BOLD | (curses.A_REVERSE if loose else 0)
    for dy, row in enumerate(KONG_STORMING if loose else KONG):
        put(win, oy + ky - 2 + dy, left + kx - 1, row, look)
    put(win, oy + top - 1, left + layout["goal"]["x"] - 1, " O ", c("goal") | curses.A_BOLD)
    put(win, oy + top, left + layout["goal"]["x"] - 1, "|♀|", c("goal") | curses.A_BOLD)
    for b in s["barrels"]:
        put(win, oy + int(b["y"] + 0.5), left + b["x"], "●", c("danger") | curses.A_BOLD)
    for f in s["fireballs"]:
        put(win, oy + int(f["y"] + 0.5), left + f["x"], "※", c("danger") | curses.A_BOLD)
    for pop in hud.get("popups", []):
        put(win, oy + int(pop["y"]), left + pop["x"], pop["text"], c("girder") | curses.A_BOLD)
    if not (p["invulnerable"] and int(s["t"] * 6) % 2):
        put(win, oy + int(p["y"] + 0.5), left + p["x"], "@", c("player") | curses.A_BOLD)
    bottom = oy + floors[0] + 2
    if hud.get("plan"):                                  # wrapped to the board's width, under the board
        rows = max(1, min(3, h - bottom - 2))            # leave room for the controls and footer rows
        lines = textwrap.wrap(f"Kong's plan: {hud['plan']}", layout["width"], max_lines=rows, placeholder=" …")
        for i, ln in enumerate(lines):
            put(win, bottom + i, left, ln, c("player") | curses.A_BOLD)
        bottom += len(lines) - 1
    put(win, bottom + 1, left, "←→ move  ↑↓ climb  SPACE jump  P pause  Q quit", c("ui"))
    llm, cost = hud.get("llm") or "", hud.get("cost") or ""
    if llm:                                               # which models play Kong, their speed, the cost so far
        width = max(layout["width"], w - left - 1)
        line = f"LLM  {llm}"
        if len(line) + len(cost) + 2 > width:            # too long: the cost moves to the start
            line = f"LLM {cost}  {llm}" if cost else line
            cost = ""
            if len(line) > width and " · " in line:      # and the call count goes, if it still doesn't fit
                line = line.rsplit(" · ", 1)[0]
        put(win, bottom + 2, left, line[:width], c("goal") | curses.A_BOLD)
        if cost:
            put(win, bottom + 2, left + width - len(cost), cost, c("girder") | curses.A_BOLD)
    if hud.get("banner"):
        centre(win, oy + floors[0] // 2, hud["banner"], c("ui") | curses.A_REVERSE | curses.A_BOLD)
    win.refresh()
