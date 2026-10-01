#!/usr/bin/env python3
"""Poetree: an interactive, word-grown L-system. See README.md for rules/sources.

Install: python -m pip install pygame edge-tts
Run:     python plant_poem.py --plant fern
All output paths are confined to the directory containing this script.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import random
import secrets
import sys
import threading
import unicodedata

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
ROOT = Path(__file__).resolve().parent
VERSION = 1
PLANTS = ("flower", "conifer", "fern")
PAPER = (248, 246, 237)
INK = (33, 65, 51)
MUTED = (99, 113, 101)
HIGHLIGHT = (190, 67, 43)
MAX_WORDS = 1500  # Bound geometry and export memory; never silently discard words.


def inside(path: str | Path) -> Path:
    """Resolve symlinks too: save/load/cache must remain under this project."""
    p = Path(path)
    p = (ROOT / p).resolve() if not p.is_absolute() else p.resolve()
    if not p.is_relative_to(ROOT):
        raise ValueError(f"Choose a path inside {ROOT}")
    return p


def tokenize(text: str) -> list[str]:
    words = text.split()
    if not 1 <= len(words) <= 5:
        raise ValueError("Enter one to five words per submission.")
    if any(len(w) > 120 for w in words):
        raise ValueError("Please keep each word to 120 characters or fewer.")
    if any(unicodedata.category(c) in ("Cc", "Cs") for w in words for c in w):
        raise ValueError("Control characters are not supported.")
    return words


def parallel_rewrite(tape, production):
    """Only read the old generation. Never rewrite newly emitted modules."""
    return [new for old in tape for new in production(old)]


@dataclass(frozen=True)
class Module:
    op: str
    args: tuple = ()

    def pack(self):
        return [self.op, list(self.args)]


class Grammar:
    """Input-controlled parametric L-system with dormant, identity-rule buds.

    B(id, kind, level, side) is a bud. F(word_id, kind, level) advances the
    turtle and places exactly one word. T(angle), [ and ] turn/save/restore.
    A turn supplies an activation parameter and a word budget. All modules
    rewrite in parallel: selected B -> preset production; other B -> B;
    F/T/brackets -> themselves. Already placed words never reproduce.
    """

    def __init__(self, plant: str, seed: int):
        self.plant = plant
        self.rng = random.Random(seed)
        self.serial = 0
        self.generation = 0
        self.tape = [self.bud("axis", 0)]
        self.choices: list[int] = []

    def bud(self, kind, level, side=0):
        self.serial += 1
        return Module("B", (self.serial, kind, level, side))

    def branch(self, angle, kind, level, side=0):
        return [Module("["), Module("T", (angle,)), self.bud(kind, level, side), Module("]")]

    def choose(self):
        buds = [m for m in self.tape if m.op == "B"]
        if self.generation == 0:
            return buds[0]
        if self.plant == "flower":
            # One leaf, then a flower head early; later include remaining leaves.
            if self.generation == 1:
                pool = [b for b in buds if b.args[1] == "leaf"]
            else:
                pool = [b for b in buds if b.args[1] == "petal"]
                if pool:
                    ring = min(b.args[2] for b in pool)
                    pool = [b for b in pool if b.args[2] == ring]
                if self.generation == 8:
                    pool = [b for b in buds if b.args[1] == "leaf"] or pool
            return self.rng.choice(pool or buds)
        # Alternate axis extension with paired lateral growth. Prefer the oldest
        # available level to keep the silhouette coherent and avoid isolated tips.
        want = "axis" if self.generation % 3 == 0 else "side"
        pool = [b for b in buds if b.args[1] == want]
        if not pool:
            pool = buds
        level = min(b.args[2] for b in pool)
        pool = [b for b in pool if b.args[2] == level]
        return self.rng.choice(pool)

    def production(self, bud, word_ids):
        _, kind, level, side = bud.args
        result = [Module("["), Module("G", (level,))] if kind == "petal" else []
        for i, word_id in enumerate(word_ids):
            if kind == "petal" and len(word_ids) > 1:
                # A gently bending petal; fixed angles independent of word length.
                result.append(Module("T", (-24 if i == 0 else 48 / (len(word_ids) - 1),)))
            result.append(Module("F", (word_id, kind, level)))
            if kind == "axis" and self.plant == "fern":
                result.append(Module("T", (3 / (level + 1),)))
        if kind == "axis":
            if self.plant == "flower":
                # Leaf buds originate halfway up this stem, not at its flower.
                cut = max(1, len(result) // 2)
                leaves = self.branch(-62, "leaf", level, -1) + self.branch(62, "leaf", level, 1)
                result[cut:cut] = leaves
                # Six radial sites. Later submissions form outer whorls, so a
                # long flower poem stays a flower rather than becoming a tree.
                phase = self.rng.choice((-8, 0, 8))
                for angle in (-150, -90, -30, 30, 90, 150):
                    result += self.branch(angle + phase, "petal", level)
            else:
                angle = 112 if self.plant == "conifer" else 57
                result += self.branch(-angle, "side", level, -1)
                result += self.branch(angle, "side", level, 1)
                result += [self.bud("axis", level + 1)]
        elif kind == "petal":
            result += [Module("]"), self.bud("petal", level + 1)]
        return result

    def grow(self, word_ids):
        active = self.choose()
        replacement = self.production(active, word_ids)
        self.tape = parallel_rewrite(self.tape, lambda m: replacement if m == active else [m])
        self.choices.append(active.args[0])
        self.generation += 1


class Session:
    def __init__(self, plant="flower", seed=None, turns=True, connectors=False):
        self.plant = plant
        self.seed = secrets.randbits(64) if seed is None else seed
        self.turns = turns
        self.connectors = connectors
        self.submissions: list[str] = []
        self.words: list[str] = []
        self.grammar = Grammar(plant, self.seed)
        self.saved_layout = None

    def rebuild(self):
        self.words = [w for text in self.submissions for w in tokenize(text)]
        self.grammar = Grammar(self.plant, self.seed)
        if self.turns:
            lengths = [len(tokenize(text)) for text in self.submissions]
        else:
            # Fixed groups of three, independent of terminal submission boundaries.
            lengths = [min(3, len(self.words) - i) for i in range(0, len(self.words), 3)]
        start = 0
        for length in lengths:
            self.grammar.grow(list(range(start, start + length)))
            start += length
        self.saved_layout = None

    def add(self, text):
        words = tokenize(text)
        if len(self.words) + len(words) > MAX_WORDS:
            raise ValueError(f"This proof of concept supports up to {MAX_WORDS} words. Save or finish this plant.")
        self.submissions.append(text.strip())
        self.rebuild()

    def undo(self):
        if not self.submissions:
            return False
        self.submissions.pop()
        self.rebuild()
        return True

    def pack(self, layout):
        return {
            "format": "poetree", "version": VERSION, "plant": self.plant,
            "seed": self.seed, "turns": self.turns, "connectors": self.connectors,
            "submissions": self.submissions, "words": self.words,
            "grammar": [m.pack() for m in self.grammar.tape],
            "choices": self.grammar.choices,
            "random_state": self.grammar.rng.getstate(), "layout": layout,
        }

    @classmethod
    def load(cls, path):
        path = inside(path)
        if path.stat().st_size > 12_000_000:
            raise ValueError("Session file is too large.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != "poetree" or data.get("version") != VERSION:
            raise ValueError("Unsupported Poetree session format/version.")
        if data.get("plant") not in PLANTS or type(data.get("seed")) is not int:
            raise ValueError("Invalid plant or seed in session.")
        if type(data.get("turns")) is not bool or type(data.get("connectors")) is not bool:
            raise ValueError("Invalid mode settings.")
        submissions = data.get("submissions")
        if not isinstance(submissions, list) or len(submissions) > MAX_WORDS or not all(isinstance(s, str) for s in submissions):
            raise ValueError("Invalid submissions.")
        obj = cls(data["plant"], data["seed"], data["turns"], data["connectors"])
        obj.submissions = submissions
        obj.rebuild()
        if len(obj.words) > MAX_WORDS:
            raise ValueError("Too many words in this session.")
        # Regeneration both validates the saved grammar and restores random state
        # without deserializing executable objects or trusting arbitrary modules.
        packed = obj.pack(None)
        for key in ("words", "grammar", "choices", "random_state"):
            if json.dumps(packed[key]) != json.dumps(data.get(key)):
                raise ValueError(f"Session {key} does not match its submissions and seed.")
        layout = data.get("layout")
        if not isinstance(layout, dict) or set(layout) != {"words", "lines"}:
            raise ValueError("Invalid saved layout.")
        if not isinstance(layout["words"], list) or not isinstance(layout["lines"], list):
            raise ValueError("Invalid saved layout arrays.")
        if len(layout["words"]) != len(obj.words) or not len(obj.words) <= len(layout["lines"]) <= 2 * len(obj.words):
            raise ValueError("Saved layout has the wrong word count.")
        for row in layout["words"] + layout["lines"]:
            if not isinstance(row, list) or len(row) != 4 or not all(type(v) in (int, float) and math.isfinite(v) and abs(v) < 1e9 for v in row):
                raise ValueError("Invalid saved coordinates.")
        if any(type(row[0]) is not int for row in layout["words"]) or sorted(row[0] for row in layout["words"]) != list(range(len(obj.words))):
            raise ValueError("Saved layout word IDs are invalid.")
        obj.saved_layout = layout
        return obj


def pygame_module():
    try:
        import pygame
    except ImportError as exc:
        raise RuntimeError("Install dependencies first: python -m pip install -r requirements.txt") from exc
    return pygame


class Renderer:
    def __init__(self):
        self.pg = pygame_module()
        self.pg.font.init()
        # Pygame's bundled font avoids host font substitution and extra assets.
        self.font = self.pg.font.Font(None, 32)
        self.small = self.pg.font.Font(None, 21)
        self.title = self.pg.font.Font(None, 29)
        self.layout = {"words": [], "lines": []}
        self.sprites = []
        self.bounds = (0, 0, 1, 1)
        self.surface_cache = {}

    def measure(self, word):
        return self.font.size(word)

    def flower_origins(self, session, extra=None):
        """Continue each petal from its own actual endpoint, with no blank ring.

        Coordinates are relative to the flower head. Following the preceding
        curved path (rather than its radial distance) prevents disconnected
        fragments even when consecutive submissions have different lengths.
        """
        angle, stack, groups, current = -90.0, [], [], None
        for index, m in enumerate(session.grammar.tape):
            if m.op == "[":
                stack.append(angle)
            elif m.op == "]":
                angle = stack.pop()
            elif m.op == "T":
                angle += m.args[0]
            elif m.op == "G":
                current = [index, round(angle % 360, 6), m.args[0], []]
                groups.append(current)
            elif m.op == "F" and m.args[1] == "petal":
                length = self.measure(session.words[m.args[0]])[0] + 14 + (extra or {}).get(m.args[0], 0)
                current[3].append((length, angle))
        ends, origins, extent = {}, {}, 0
        for index, ray, ring, segments in sorted(groups, key=lambda group: group[2]):
            x, y = ends.get(ray, (28 * math.cos(math.radians(ray)),
                                  28 * math.sin(math.radians(ray))))
            origins[index] = (x, y)
            for length, heading in segments:
                x += length * math.cos(math.radians(heading))
                y += length * math.sin(math.radians(heading))
                extent = max(extent, math.hypot(x, y))
            ends[ray] = (x, y)
        return origins, extent

    def interpret(self, session, stretch=1.0, extra=None):
        x, y, angle = 0.0, 0.0, -90.0
        stack = []
        words, lines = [], []
        top_level = max((m.args[2] for m in session.grammar.tape if m.op == "F"), default=0)
        origins, head_extent = self.flower_origins(session, extra)
        stem_length = sum(self.measure(session.words[m.args[0]])[0] + 14 for m in session.grammar.tape if m.op == "F" and m.args[1] == "axis")
        flower_stem_scale = max(1.5, head_extent * 1.25 / max(1, stem_length))
        for index, m in enumerate(session.grammar.tape):
            if m.op == "[":
                stack.append((x, y, angle))
            elif m.op == "]":
                if not stack:
                    raise ValueError("Unbalanced L-system stack.")
                x, y, angle = stack.pop()
            elif m.op == "T":
                angle += m.args[0]
            elif m.op == "G":
                dx, dy = origins[index]
                nx, ny = x + dx * stretch, y + dy * stretch
                # Later sections already meet a drawn segment; do not add a
                # second chord from the center through earlier petal text.
                lines.append([x, y, nx, ny] if m.args[0] == 0 else [nx, ny, nx, ny])
                x, y = nx, ny
            elif m.op == "F":
                word_id, kind, level = m.args
                width, height = self.measure(session.words[word_id])
                # A little more room at branch junctions. All angles stay fixed.
                length = (width + 14 + (extra or {}).get(word_id, 0)) * stretch
                if session.plant == "flower" and kind == "axis":
                    length *= flower_stem_scale
                if session.plant == "conifer" and kind == "side":
                    length *= 1 + min(4, top_level - level) * 0.18
                dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
                cx, cy = x + dx * length / 2, y + dy * length / 2
                words.append([word_id, cx, cy, angle])
                nx, ny = x + dx * length, y + dy * length
                lines.append([x, y, nx, ny])
                x, y = nx, ny
        if stack:
            raise ValueError("Unbalanced L-system stack.")
        return {"words": words, "lines": lines}

    def make_sprites(self, session, layout):
        sprites = []
        for word_id, x, y, angle in layout["words"]:
            # 180-degree flips keep letter order intact and avoid upside-down text.
            tilt = (angle + 90) % 180 - 90
            normal = self.pg.transform.rotozoom(self.font.render(session.words[word_id], True, INK), -tilt, 1)
            lit = self.pg.transform.rotozoom(self.font.render(session.words[word_id], True, HIGHLIGHT), -tilt, 1)
            rect = normal.get_rect(center=(round(x), round(y)))
            w, h = self.measure(session.words[word_id])
            rad = math.radians(tilt)
            dx, dy = math.cos(rad), math.sin(rad)
            corners = [(x + a * dx - b * dy, y + a * dy + b * dx)
                       for a, b in ((-w/2-3, -h/2-3), (w/2+3, -h/2-3),
                                    (w/2+3, h/2+3), (-w/2-3, h/2+3))]
            sprites.append((word_id, normal, lit, rect, corners))
        return sprites

    @staticmethod
    def polygons_overlap(a, b):
        # Separating-axis test for the two rotated text rectangles. Axis-aligned
        # boxes alone greatly overestimate the space taken by diagonal words.
        for poly in (a, b):
            for i in (0, 1):
                x1, y1 = poly[i]
                x2, y2 = poly[i+1]
                nx, ny = -(y2-y1), x2-x1
                pa = [x*nx+y*ny for x, y in a]
                pb = [x*nx+y*ny for x, y in b]
                if max(pa) <= min(pb) or max(pb) <= min(pa):
                    return False
        return True

    @classmethod
    def collisions(cls, sprites):
        # Sweep by left bound to avoid comparing every pair in large poems.
        active, pairs = [], []
        for sprite in sorted(sprites, key=lambda s: s[3].left):
            rect = sprite[3].inflate(6, 6)
            active = [s for s in active if s[3].right + 3 > rect.left]
            for other in active:
                if rect.colliderect(other[3].inflate(6, 6)) and cls.polygons_overlap(sprite[4], other[4]):
                    pairs.append((sprite[0], other[0]))
            active.append(sprite)
        return pairs

    @classmethod
    def overlaps(cls, sprites):
        return bool(cls.collisions(sprites))

    def update(self, session):
        if session.saved_layout is not None:
            self.layout = session.saved_layout
            self.sprites = self.make_sprites(session, self.layout)
        else:
            # Reflow only the later word's supporting segment at each collision;
            # all its descendants follow. This preserves angles and avoids
            # spreading the entire plant because of one crowded junction.
            extra = {}
            for attempt in range(100):
                layout = self.interpret(session, extra=extra)
                sprites = self.make_sprites(session, layout)
                pairs = self.collisions(sprites)
                if not pairs:
                    break
                for word_id in {max(a, b) for a, b in pairs}:
                    extra[word_id] = extra.get(word_id, 0) + 18
            else:
                # Bounded global fallback for densely crossing growth.
                for attempt in range(1, 25):
                    layout = self.interpret(session, 1.1 ** attempt, extra)
                    sprites = self.make_sprites(session, layout)
                    if not self.overlaps(sprites):
                        break
                else:
                    raise ValueError("This layout is too crowded. Undo or try another seed.")
            self.layout, self.sprites = layout, sprites
        self.surface_cache.clear()
        if self.sprites:
            bounds = self.sprites[0][3].copy()
            for sprite in self.sprites[1:]:
                bounds.union_ip(sprite[3])
            for x1, y1, x2, y2 in self.layout["lines"]:
                bounds.union_ip(self.pg.Rect(math.floor(min(x1, x2)), math.floor(min(y1, y2)),
                                            max(1, math.ceil(abs(x2-x1))), max(1, math.ceil(abs(y2-y1)))))
            self.bounds = tuple(bounds.inflate(100, 100))
        else:
            self.bounds = (-150, -150, 300, 300)

    def draw_plant(self, surface, area, connectors=False, highlight=None):
        pg = self.pg
        bx, by, bw, bh = self.bounds
        scale = min(area.width / bw, area.height / bh, 1.7)
        ox = area.centerx - (bx + bw / 2) * scale
        oy = area.centery - (by + bh / 2) * scale
        if connectors:
            for x1, y1, x2, y2 in self.layout["lines"]:
                pg.draw.aaline(surface, (204, 211, 195), (x1 * scale + ox, y1 * scale + oy), (x2 * scale + ox, y2 * scale + oy))
        for word_id, normal, lit, rect, _ in self.sprites:
            key = (word_id, word_id == highlight, round(scale, 5))
            if key not in self.surface_cache:
                original = lit if word_id == highlight else normal
                self.surface_cache[key] = pg.transform.smoothscale(original, (max(1, round(original.get_width() * scale)), max(1, round(original.get_height() * scale))))
            sprite = self.surface_cache[key]
            dest = sprite.get_rect(center=(round(rect.centerx * scale + ox), round(rect.centery * scale + oy)))
            surface.blit(sprite, dest)
            if word_id == highlight:
                pg.draw.rect(surface, HIGHLIGHT, dest.inflate(8, 8), 1, border_radius=3)

    def export(self, session, path):
        _, _, bw, bh = self.bounds
        scale = min(2, 7000 / max(bw, bh), math.sqrt(24_000_000 / (bw * bh)))
        size = (max(640, round(bw * scale)), max(640, round(bh * scale)))
        surface = self.pg.Surface(size)
        surface.fill(PAPER)
        self.draw_plant(surface, surface.get_rect().inflate(-40, -40), session.connectors)
        self.pg.image.save(surface, str(path))


def save_session(session, renderer, base):
    base = inside(base)
    if base.suffix in (".json", ".png"):
        base = base.with_suffix("")
    base.parent.mkdir(parents=True, exist_ok=True)
    json_path, png_path = inside(base.with_suffix(".json")), inside(base.with_suffix(".png"))
    # Stage both before replacing the previous save. PNG extension is required.
    pending_json = inside(base.with_suffix(".pending.json"))
    pending_png = inside(base.with_suffix(".pending.png"))
    pending_json.write_text(json.dumps(session.pack(renderer.layout), ensure_ascii=False, indent=2), encoding="utf-8")
    renderer.export(session, pending_png)
    pending_png.replace(png_path)
    pending_json.replace(json_path)
    return json_path, png_path


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def align_boundaries(words, boundaries):
    """Map service fragments to occurrence IDs, never match by word alone.

    Hyphenated words may yield several boundaries; each highlights the same ID.
    Do not guess timing when the provider normalizes text in an unexpected way.
    """
    source = ""
    owners = []
    for i, word in enumerate(words):
        value = normalized(word)
        source += value
        owners.extend([i] * len(value))
    cursor, timing = 0, []
    for boundary in boundaries:
        value = normalized(boundary["text"])
        if not value:
            continue
        if not source.startswith(value, cursor):
            raise ValueError("Speech word timing could not be matched to this text. Try spelling out numbers or abbreviations.")
        word_id = owners[cursor]
        timing.append([boundary["offset"] / 10_000_000, boundary["duration"] / 10_000_000, word_id])
        cursor += len(value)
    if cursor != len(source) or not timing:
        raise ValueError("The speech service returned incomplete word timing.")
    return timing


class Speech:
    def __init__(self, events, voice):
        self.events = events
        self.voice = voice
        self.ticket = 0
        self.future = None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.timing = []
        self.audio = None
        self.playing = False

    def stop(self):
        self.ticket += 1
        if self.future:
            self.future.cancel()
        self.playing = False
        self.timing = []
        pg = pygame_module()
        if pg.mixer.get_init():
            pg.mixer.music.stop()
            pg.mixer.music.unload()
        self.audio = None

    def request(self, words):
        self.stop()
        ticket = self.ticket
        self.future = asyncio.run_coroutine_threadsafe(self.prepare(list(words), ticket), self.loop)

    async def prepare(self, words, ticket):
        try:
            import edge_tts
            key = hashlib.sha256((self.voice + "\n" + " ".join(words)).encode()).hexdigest()
            cache = inside(".speech-cache")
            cache.mkdir(exist_ok=True)
            audio_path, timings_path = inside(cache / f"{key}.mp3"), inside(cache / f"{key}.json")
            if audio_path.exists() and timings_path.exists():
                timing = json.loads(timings_path.read_text())
            else:
                audio = bytearray()
                boundaries = []
                client = edge_tts.Communicate(" ".join(words), self.voice, boundary="WordBoundary", connect_timeout=10, receive_timeout=20)
                async with asyncio.timeout(90):
                    async for event in client.stream():
                        if event["type"] == "audio":
                            audio.extend(event["data"])
                        elif event["type"] == "WordBoundary":
                            boundaries.append(event)
                timing = align_boundaries(words, boundaries)
                if not audio:
                    raise ValueError("No speech audio received.")
                audio_path.write_bytes(audio)
                timings_path.write_text(json.dumps(timing))
            self.events.put(("speech", (ticket, audio_path, timing)))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.events.put(("speech_error", (ticket, f"Speech unavailable: {exc}")))

    def play(self, path, timing):
        pg = pygame_module()
        if not pg.mixer.get_init():
            pg.mixer.init()
        pg.mixer.music.load(str(path))
        pg.mixer.music.play()
        self.audio = path
        self.timing = timing
        self.starts = [t[0] for t in timing]
        self.playing = True

    def highlighted(self):
        if not self.playing:
            return None
        pg = pygame_module()
        if not pg.mixer.music.get_busy():
            self.playing = False
            return None
        elapsed = pg.mixer.music.get_pos() / 1000
        index = bisect.bisect_right(self.starts, elapsed) - 1
        return self.timing[index][2] if index >= 0 else None

    def close(self):
        self.stop()
        async def cancel_pending():
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            asyncio.run_coroutine_threadsafe(cancel_pending(), self.loop).result(timeout=2)
        except (TimeoutError, RuntimeError):
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=1)
        if not self.thread.is_alive():
            self.loop.close()


class App:
    def __init__(self, session, args):
        self.session, self.args = session, args
        self.renderer = Renderer()
        self.renderer.update(session)
        self.pg = self.renderer.pg
        self.pg.display.init()
        self.screen = self.pg.display.set_mode((1050, 820), self.pg.RESIZABLE)
        self.pg.display.set_caption("Poetree | a growing poem")
        self.events = queue.Queue()
        self.speech = Speech(self.events, args.voice)
        self.finished = False
        self.running = True
        self.status = "Enter one to five words in the terminal."
        self.buttons = []
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.save_base = inside(args.output or f"output/{session.plant}-{stamp}")
        self.manual_word = None

    def announce(self, text):
        self.status = text
        print(text, flush=True)

    def terminal(self):
        while self.running:
            try:
                line = input("poetree> ")
            except EOFError:
                self.events.put(("command", "/finish"))
                return
            self.events.put(("command", line))

    def command(self, line):
        line = line.strip()
        if not line:
            return
        command, _, argument = line.partition(" ")
        try:
            if command == "/stop":
                self.speech.stop()
                self.manual_word = None
                self.announce("Playback stopped.")
            elif command == "/read":
                if not self.session.words:
                    raise ValueError("Add some words first.")
                self.manual_word = None
                self.speech.request(self.session.words)
                self.announce("Preparing speech online; poem text is sent to Microsoft's speech service.")
            elif command in ("/save", "/finish"):
                if argument:
                    self.save_base = inside(argument)
                paths = save_session(self.session, self.renderer, self.save_base)
                if command == "/finish":
                    self.finished = True
                self.announce(f"{'Finished. ' if self.finished else ''}Saved {paths[0].name} and {paths[1].name}. Window stays open.")
            elif command == "/undo":
                if self.finished:
                    raise ValueError("Composition is finished. Load the saved JSON to continue.")
                self.speech.stop()
                self.manual_word = None
                if self.session.undo():
                    self.renderer.update(self.session)
                    self.announce("Last submission removed.")
                else:
                    self.announce("Nothing to undo.")
            elif command.startswith("/"):
                raise ValueError("Commands: /undo /read /stop /save [name] /finish")
            elif self.finished:
                raise ValueError("Composition is finished. Read, save, or close the window.")
            else:
                # Transactional: a layout failure never leaves half-applied words.
                old_submissions = self.session.submissions[:]
                old_layout = self.renderer.layout
                try:
                    self.session.add(line)
                    self.renderer.update(self.session)
                except Exception:
                    self.session.submissions = old_submissions
                    self.session.rebuild()
                    self.session.saved_layout = old_layout
                    self.renderer.update(self.session)
                    raise
                self.speech.stop()
                self.manual_word = None
                self.announce(f"{len(self.session.words)} words / {len(self.session.submissions)} submissions")
        except (ValueError, OSError, RuntimeError, self.pg.error) as exc:
            self.announce(str(exc))

    def draw(self):
        pg, r = self.pg, self.renderer
        width, height = self.screen.get_size()
        self.screen.fill(PAPER)
        self.screen.blit(r.title.render("P O E T R E E", True, INK), (30, 24))
        mode = "turns" if self.session.turns else "continuous"
        label = f"{self.session.plant}  /  {mode}  /  {len(self.session.words)} words"
        self.screen.blit(r.small.render(label, True, MUTED), (30, 59))
        highlight = self.speech.highlighted() if self.speech.playing else self.manual_word
        area = pg.Rect(15, 92, max(1, width - 30), max(1, height - 220))
        r.draw_plant(self.screen, area, self.session.connectors, highlight)
        if not self.session.words:
            message = r.title.render("A plant begins with a few words.", True, MUTED)
            self.screen.blit(message, message.get_rect(center=area.center))
        self.buttons = []
        for i, (label, cmd) in enumerate((("Undo", "/undo"), ("Read", "/read"), ("Stop", "/stop"), ("Save", "/save"), ("Finish", "/finish"))):
            rect = pg.Rect(30 + i * 94, height - 99, 84, 32)
            pg.draw.rect(self.screen, (227, 232, 219), rect, border_radius=5)
            text = r.small.render(label, True, INK)
            self.screen.blit(text, text.get_rect(center=rect.center))
            self.buttons.append((rect, cmd))
        status = self.status
        while r.small.size(status)[0] > width - 60 and len(status) > 4:
            status = status[:-4] + "..."
        self.screen.blit(r.small.render(status, True, MUTED), (30, height - 55))
        help_text = "Type in terminal | Left/Right: read words silently | Esc: stop"
        self.screen.blit(r.small.render(help_text, True, MUTED), (30, height - 30))
        pg.display.flip()

    def run(self):
        print("\nPOETREE — words become a plant")
        print("Enter 1–5 words. /undo /read /stop /save [name] /finish")
        print(f"All files stay in {ROOT}")
        print("Early-outline suggestions: flower 12–20, fern 20–30, conifer 25–40 words.")
        print("These are estimates; short, partial plants are welcome. Close window to save and exit.")
        threading.Thread(target=self.terminal, daemon=True).start()
        clock = self.pg.time.Clock()
        try:
            while self.running:
                for event in self.pg.event.get():
                    if event.type == self.pg.QUIT:
                        try:
                            save_session(self.session, self.renderer, self.save_base)
                            self.running = False
                        except (OSError, ValueError, self.pg.error) as exc:
                            self.announce(f"Could not save: {exc}. Use /save with another name.")
                    elif event.type == self.pg.MOUSEBUTTONDOWN and event.button == 1:
                        for rect, cmd in self.buttons:
                            if rect.collidepoint(event.pos):
                                self.command(cmd)
                    elif event.type == self.pg.KEYDOWN:
                        if event.key == self.pg.K_ESCAPE:
                            self.command("/stop")
                        elif event.key in (self.pg.K_LEFT, self.pg.K_RIGHT) and self.session.words:
                            self.speech.stop()
                            delta = 1 if event.key == self.pg.K_RIGHT else -1
                            self.manual_word = max(0, min(len(self.session.words) - 1, (self.manual_word if self.manual_word is not None else -1) + delta))
                            self.announce(f"{self.manual_word + 1}: {self.session.words[self.manual_word]}")
                while not self.events.empty():
                    kind, data = self.events.get_nowait()
                    if kind == "command":
                        self.command(data)
                    elif kind == "speech" and data[0] == self.speech.ticket:
                        try:
                            self.speech.play(data[1], data[2])
                            self.announce("Reading in entry order.")
                        except self.pg.error as exc:
                            self.announce(f"Audio unavailable: {exc}")
                    elif kind == "speech_error" and data[0] == self.speech.ticket:
                        self.announce(data[1])
                self.draw()
                clock.tick(30)
        except KeyboardInterrupt:
            save_session(self.session, self.renderer, self.save_base)
        finally:
            self.running = False
            self.speech.close()
            self.pg.quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plant", choices=PLANTS)
    parser.add_argument("--no-turns", action="store_true", help="Ignore submission boundaries for growth.")
    parser.add_argument("--connectors", action="store_true", help="Draw faint structural connecting lines.")
    parser.add_argument("--seed", type=int, help="Reproduce random growth choices.")
    parser.add_argument("--load", metavar="JSON", help="Resume a saved session inside this folder.")
    parser.add_argument("--reflow", action="store_true", help="With --load, recompute spacing using the current layout rules.")
    parser.add_argument("--output", help="Save basename inside this folder (default: timestamped output/...).")
    parser.add_argument("--voice", default="en-US-AriaNeural", help="edge-tts voice name.")
    args = parser.parse_args()
    try:
        if args.load:
            if args.plant or args.no_turns or args.seed is not None:
                parser.error("--load restores plant, growth mode, and seed; omit those flags.")
            session = Session.load(args.load)
            if args.reflow:
                session.saved_layout = None
            if args.connectors:
                session.connectors = True
        else:
            if args.reflow:
                parser.error("--reflow requires --load.")
            plant = args.plant
            while plant not in PLANTS:
                plant = input("Plant [flower / conifer / fern]: ").strip().lower()
            session = Session(plant, args.seed, not args.no_turns, args.connectors)
        App(session, args).run()
    except (ValueError, OSError, RuntimeError, EOFError) as exc:
        print(f"Poetree: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
