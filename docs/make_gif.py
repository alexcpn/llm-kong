"""Record a game against the live LLM Kong to an animated GIF, with Kong's thinking shown under the board.

    python3 docs/make_gif.py                                   # docs/images/gameplay.gif (about a cent of LLM calls)
    python3 docs/make_gif.py --seed 4 --seconds 60 --out out.gif

A bot plays you (the lookahead reference player). The game runs in real time while the LLM thinks in the
background, exactly as when you play: Kong keeps throwing with his old plan until the new one arrives.
The panel under the board shows each strategist call: while it is out ("thinking ... 3.2 s"), then what
the model wrote: what it thinks of you, its plan and why.
"""

from __future__ import annotations

import argparse
import curses
import os
import sys
import textwrap
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import dk  # noqa: E402
from dkgame import render  # noqa: E402
from dkgame.cast import Cast  # noqa: E402
from dkgame.director_kong import ParametricKong, snapshot_game  # noqa: E402
from dkgame.engine import TICK, Game  # noqa: E402
from dkgame.lookahead import LookaheadPlayer  # noqa: E402

RGB = {curses.COLOR_YELLOW: (230, 200, 40), curses.COLOR_RED: (220, 50, 50), curses.COLOR_CYAN: (60, 200, 220),
       curses.COLOR_GREEN: (80, 200, 80), curses.COLOR_MAGENTA: (210, 80, 210), curses.COLOR_WHITE: (225, 225, 225)}
BG, DIM = (24, 24, 28), (150, 150, 160)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SYMBOLS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "※")   # the mono face has no fireball glyph
CW, CH = 11, 22
PANEL = 11                                                 # rows for Kong's thinking under the game screen
pairs: dict = {}

# draw with the game's own renderer into a character grid instead of a terminal
curses.init_pair = lambda n, fg, bg: pairs.__setitem__(n, (fg, bg))
curses.color_pair = lambda n: n << 8
for _name, _col in (("girder", curses.COLOR_YELLOW), ("danger", curses.COLOR_RED), ("ladder", curses.COLOR_CYAN),
                    ("player", curses.COLOR_GREEN), ("goal", curses.COLOR_MAGENTA), ("ui", curses.COLOR_WHITE)):
    curses.init_pair(render.PAIRS[_name], _col, -1)


class Grid:
    def __init__(self, h: int, w: int) -> None:
        self.h, self.w = h, w
        self.erase()

    def getmaxyx(self):
        return self.h, self.w

    def erase(self) -> None:
        self.g = [[(" ", 0)] * self.w for _ in range(self.h)]

    def addnstr(self, y, x, text, n, attr=0) -> None:
        for i, ch in enumerate(text[:n]):
            if 0 <= y < self.h and x + i < self.w:
                self.g[y][x + i] = (ch, attr)

    def refresh(self) -> None:
        pass


def to_image(grid: list, font, symbols) -> Image.Image:
    img = Image.new("RGB", (len(grid[0]) * CW, len(grid) * CH), BG)
    d = ImageDraw.Draw(img)
    for y, row in enumerate(grid):
        for x, (ch, a) in enumerate(row):
            if ch == " " and not a & curses.A_REVERSE:
                continue
            fg = RGB.get(pairs.get((a >> 8) & 0xff, (curses.COLOR_WHITE, -1))[0], DIM if a == 0 else RGB[curses.COLOR_WHITE])
            bg = BG
            if a & curses.A_REVERSE:
                fg, bg = bg, fg
            x0, y0 = x * CW, y * CH
            if bg != BG:
                d.rectangle([x0, y0, x0 + CW - 1, y0 + CH - 1], fill=bg)
            if ch == "▀":
                d.rectangle([x0, y0, x0 + CW - 1, y0 + CH // 2 - 1], fill=fg)
            elif ch == "▄":
                d.rectangle([x0, y0 + CH // 2, x0 + CW - 1, y0 + CH - 1], fill=fg)
            elif ch in SYMBOLS[1]:
                d.text((x0 - 1, y0 + 3), ch, font=symbols, fill=fg)
            elif ch != " ":
                d.text((x0, y0 + 1), ch, font=font, fill=fg)
    return img


def mind(win, top: int, left: int, width: int, think: dict, t: float) -> None:
    """Kong's thinking: the call in flight, or the last reply (what it sees, its plan, why)."""
    bold = curses.A_BOLD
    render.put(win, top, left, "─" * width, render.c("ui"))
    if think["asked"] is not None:
        dots = "." * (1 + int(t * 3) % 3)
        head = f"KONG IS THINKING{dots:<3} {t - think['asked']:4.1f}s  ({think['why']})"
        render.put(win, top + 1, left, head[:width], render.c("goal") | bold | (curses.A_REVERSE if int(t * 2) % 2 else 0))
    elif think["reply"]:
        head = f"KONG THOUGHT ({think['model'].removeprefix('claude-')}): landed after {think['took']:.1f}s"
        render.put(win, top + 1, left, head[:width], render.c("goal") | bold)
    reply = think["reply"]
    if not reply:                                      # the first call: nothing written yet
        return
    rows = []
    for label, key, colour in (("Sees you:", "opponent_model", "ui"), ("Why:", "reasons", "ladder")):
        for line in textwrap.wrap(f"{label} {reply.get(key, '')}", width, max_lines=4, placeholder=" …"):
            rows.append((line, colour))
    for i, (line, colour) in enumerate(rows[:PANEL - 2]):
        render.put(win, top + 2 + i, left, line, render.c(colour) | (bold if colour != "ui" else 0))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--board", default="classic")
    ap.add_argument("--seconds", type=float, default=70, help="game seconds to record (stops earlier on game over)")
    ap.add_argument("--frame", type=int, default=2, help="ticks per GIF frame (1 tick = 0.05 s)")
    ap.add_argument("--out", default=os.path.join(HERE, "docs", "images", "gameplay.gif"))
    args = ap.parse_args()
    dk.load_env_files()
    for k, v in dk.LLM_DEFAULTS.items():
        os.environ.setdefault(k, v)
    layout, params = dk.make_variant(args.board, args.seed)
    kong = dk.make_kong("ai")
    game = Game(layout, params, kong, seed=args.seed)
    kong.attach(game)
    cast = Cast(seed=args.seed)
    kong.cast = cast
    scorer, player = dk.Scorer(), LookaheadPlayer()
    lay = layout.to_dict()
    need_h, need_w = render.needed_size(lay)
    win, panel = Grid(need_h, need_w + 2), Grid(PANEL, need_w + 2)   # the game screen as it plays, then the panel
    left = max(0, (win.w - lay["width"]) // 2)
    model = kong.director.layer.model.split("/")[-1]
    speech = {"speaker": "", "text": "", "until": 0.0, "last": None}
    flash = {"score": 0, "lives": game.lives, "score_until": -1.0, "lives_until": -1.0}
    think = {"asked": None, "why": "", "reply": None, "took": 0.0, "model": model, "seen": 0, "wall": 0.0}
    ask = kong.director.update

    def update(t, **kw):                                           # note why each strategist call is made
        think["why"] = {"interval": "every 20 s"}.get(kw.get("trigger"), kw.get("trigger", ""))
        if not kong.director.decisions:
            think["why"] = "first look at you"
        ask(t, **kw)

    kong.director.update = update
    frames, lag, n = [], 0.0, 0
    start = time.monotonic()
    while not game.over and game.t < args.seconds:
        if player._since % player.replan_every == 0:               # it looks ahead on a copy: the live Kong
            view = snapshot_game(game)                              # (threads, LLM client) can't be copied, so
            view.kong = ParametricKong(kong.knobset.snapshot())     # the copy throws by the LLM's current knobs
            view.kong.reset(game.seed if hasattr(game, "seed") else 0)
            view.kong.hold_until = kong.hold_until
            keys = player.act(view)
        else:
            keys = player.act(game)
        game.step(keys)
        scorer.update(game)
        fresh = dk.new_events(game.events, speech["last"])
        speech["last"] = game.events[-1] if game.events else None
        said = cast.react(fresh, game.t)
        if said:
            speech.update(speaker=said[0], text=said[1], until=game.t + dk.SPEECH_SECONDS)
        d = kong.director
        if d._job is not None and think["asked"] is None:          # a strategist call just went out
            think.update(asked=game.t, wall=time.monotonic())
        if think["asked"] is not None and len(d.decisions) > think["seen"]:   # its answer was applied
            think["seen"] = len(d.decisions)
            dec = d.decisions[-1]
            if dec.reply:
                think.update(reply=dec.reply, took=time.monotonic() - think["wall"])
            think["asked"] = None
        if scorer.score != flash["score"]:
            flash.update(score=scorer.score, score_until=game.t + 1.0)
        if game.lives != flash["lives"]:
            flash.update(lives=game.lives, lives_until=game.t + 2.0)
        n += 1
        if n % args.frame == 0:
            llm, cost = kong.llm_status()
            hud = {"score": scorer.score, "best": 5000, "opponent": "LLM Kong", "plan": kong.display(), "banner": "",
                   "popups": scorer.popups, "said": (speech["speaker"], speech["text"]) if game.t < speech["until"] else None,
                   "llm": llm, "cost": cost, "score_flash": game.t < flash["score_until"],
                   "lives_flash": game.t < flash["lives_until"]}
            render.draw_game(win, lay, game.state(), hud)
            panel.erase()
            mind(panel, 0, left, lay["width"], think, game.t)
            frames.append([row[:] for row in win.g + panel.g])
        wait = start + game.t - time.monotonic()                   # keep to real time, so the LLM's speed is real
        if wait > 0:
            time.sleep(wait)
        else:
            lag = max(lag, -wait)
    took = time.monotonic() - start
    font = ImageFont.truetype(FONT, 17)
    symbols = ImageFont.truetype(SYMBOLS[0], 15)
    images = [to_image(f, font, symbols) for f in frames]
    durations = [int(TICK * args.frame * 1000)] * len(images)
    durations[-1] = 3000
    images[0].save(args.out, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True)
    llm, cost = kong.llm_status()
    print(f"{args.out}: {len(images)} frames, game {game.t:.1f}s in {took:.1f}s wall (max lag {lag:.2f}s), "
          f"{os.path.getsize(args.out) / 1e6:.1f} MB; lives {game.lives}, level {game.level}, score {scorer.score}; "
          f"{len(kong.director.decisions)} plans; {llm} {cost}")


if __name__ == "__main__":
    main()
