"""Offline checks: python -m unittest -v. Writes fixtures only to test-output/."""
import json
import math
import os
from pathlib import Path
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import plant_poem as p


class GrowthTests(unittest.TestCase):
    def make(self, plant="fern", turns=True, chunks=None):
        session = p.Session(plant, seed=42, turns=turns)
        for text in chunks or ["we grow", "toward the light", "and begin again", "under quiet skies"]:
            session.add(text)
        return session

    def test_parallel_rewriting(self):
        rules = {"A": "AB", "B": "A"}
        tape = "A"
        for expected in ("AB", "ABA", "ABAAB", "ABAABABA"):
            tape = p.parallel_rewrite(tape, lambda s: rules[s])
            self.assertEqual("".join(tape), expected)

    def test_every_occurrence_once(self):
        for plant in p.PLANTS:
            session = self.make(plant, chunks=["rain rain", "Rain, rain!", "rain"])
            ids = [m.args[0] for m in session.grammar.tape if m.op == "F"]
            self.assertEqual(sorted(ids), list(range(5)))
            self.assertEqual(session.words, ["rain", "rain", "Rain,", "rain!", "rain"])

    def test_grouping_changes_default_growth(self):
        a = self.make(chunks=["one", "two three four five six"])
        b = self.make(chunks=["one two three", "four five six"])
        self.assertNotEqual(a.grammar.tape, b.grammar.tape)

    def test_continuous_grouping_invariance(self):
        for plant in p.PLANTS:
            a = self.make(plant, False, ["one", "two three four five six", "seven"])
            b = self.make(plant, False, ["one two three", "four five", "six seven"])
            self.assertEqual(a.words, b.words)
            self.assertEqual(a.grammar.tape, b.grammar.tape)
            self.assertEqual(a.grammar.rng.getstate(), b.grammar.rng.getstate())

    def test_word_length_does_not_change_rules(self):
        a = self.make(chunks=["a b", "c d e"])
        b = self.make(chunks=["extraordinarily magnificent", "unquestionably radiant sunlight"])
        self.assertEqual(a.grammar.tape, b.grammar.tape)

    def test_seed_reproducible(self):
        self.assertEqual(self.make().grammar.tape, self.make().grammar.tape)

    def test_undo_restores_rng_and_tape(self):
        s = self.make()
        before = s.pack(None)
        s.add("a new beginning")
        s.undo()
        self.assertEqual(before, s.pack(None))

    def test_invalid_input_is_nonmutating(self):
        s = self.make()
        before = s.pack(None)
        for text in ("", "a b c d e f", "x" * 121, "a\x00b"):
            with self.assertRaises(ValueError):
                s.add(text)
            self.assertEqual(before, s.pack(None))

    def test_outside_path_rejected(self):
        with self.assertRaises(ValueError):
            p.inside(p.ROOT.parent / "outside.json")

    def test_partial_plant(self):
        s = self.make(chunks=["begin"])
        self.assertEqual(s.words, ["begin"])


class LayoutTests(unittest.TestCase):
    make = GrowthTests.make
    @classmethod
    def setUpClass(cls):
        cls.renderer = p.Renderer()
        cls.out = p.inside("test-output")
        cls.out.mkdir(exist_ok=True)

    def test_all_presets_render_and_roundtrip(self):
        for plant in p.PLANTS:
            s = self.make(plant)
            self.renderer.update(s)
            self.assertFalse(self.renderer.overlaps(self.renderer.sprites))
            base = self.out / plant
            json_path, png_path = p.save_session(s, self.renderer, base)
            self.assertTrue(png_path.stat().st_size > 500)
            restored = p.Session.load(json_path)
            self.assertEqual(restored.pack(restored.saved_layout), s.pack(self.renderer.layout))
            s.add("another turn")
            restored.add("another turn")
            self.assertEqual(s.pack(None), restored.pack(None))

    def test_restore_exact_layout(self):
        s = self.make()
        self.renderer.update(s)
        before = json.dumps(self.renderer.layout)
        path, _ = p.save_session(s, self.renderer, self.out / "exact")
        restored = p.Session.load(path)
        self.renderer.update(restored)
        self.assertEqual(before, json.dumps(self.renderer.layout))

    def test_tampered_session_rejected(self):
        s = self.make()
        self.renderer.update(s)
        data = s.pack(self.renderer.layout)
        data["words"][0] = "tampered"
        path = self.out / "invalid.json"
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            p.Session.load(path)

    def test_repeated_words_and_many_seeds(self):
        for plant in p.PLANTS:
            for seed in range(6):
                s = p.Session(plant, seed=seed)
                for i in range(20):
                    s.add(" ".join(["rain"] * (i % 5 + 1)))
                self.renderer.update(s)
                self.assertFalse(self.renderer.overlaps(self.renderer.sprites), (plant, seed))

    def test_empty_save(self):
        s = p.Session(seed=0)
        self.renderer.update(s)
        path, _ = p.save_session(s, self.renderer, self.out / "empty")
        self.assertEqual(p.Session.load(path).words, [])

    def test_flower_long_petal_does_not_push_other_rays_out(self):
        s = self.make("flower", chunks=["stem", "leaf", "one", "two", "three", "four",
                                       "five", "six", "leaf", "outer", "next", "again",
                                       "far", "end", "last"])
        before, _ = self.renderer.flower_origins(s)
        tape = s.grammar.tape
        inner = next(i for i, m in enumerate(tape) if m.op == "G" and m.args[0] == 0)
        word_id = next(m.args[0] for m in tape[inner+1:] if m.op == "F")
        outer = next(i for i in range(inner+1, len(tape)) if tape[i].op == "G" and tape[i].args[0] == 1)
        s.words[word_id] = "extraordinarily-long-and-unrelated"
        after, _ = self.renderer.flower_origins(s)
        changed = {index for index in before if after[index] != before[index]}
        self.assertEqual(changed, {outer})
        self.assertGreater(math.hypot(*after[outer]), math.hypot(*before[outer]))

    def test_flower_collision_padding_stays_on_its_ray(self):
        s = self.make("flower", chunks=["stem", "leaf", "one", "two", "three", "four",
                                       "five", "six", "leaf", "outer", "next", "again",
                                       "far", "end", "last"])
        before, _ = self.renderer.flower_origins(s)
        tape = s.grammar.tape
        inner = next(i for i, m in enumerate(tape) if m.op == "G" and m.args[0] == 0)
        word_id = next(m.args[0] for m in tape[inner+1:] if m.op == "F")
        after, _ = self.renderer.flower_origins(s, {word_id: 90})
        differences = [math.dist(after[i], before[i]) for i in before if after[i] != before[i]]
        self.assertEqual(len(differences), 1)
        self.assertAlmostEqual(differences[0], 90)

    def test_next_petal_section_starts_at_previous_curved_endpoint(self):
        s = p.Session("flower", seed=42)
        s.words = ["stem", "first", "curve", "next", "section"]
        M = p.Module
        s.grammar.tape = [M("F", (0, "axis", 0)), M("["), M("G", (0,)),
                          M("T", (-24,)), M("F", (1, "petal", 0)),
                          M("T", (48,)), M("F", (2, "petal", 0)), M("]"),
                          M("["), M("G", (1,)), M("F", (3, "petal", 1)),
                          M("F", (4, "petal", 1)), M("]")]
        lines = self.renderer.interpret(s)["lines"]
        self.assertAlmostEqual(math.dist(lines[3][2:], lines[5][:2]), 0)

    def test_manual_undo_layout_exact(self):
        s = self.make()
        self.renderer.update(s)
        before = self.renderer.layout
        s.add("more words")
        self.renderer.update(s)
        s.undo()
        self.renderer.update(s)
        self.assertEqual(before, self.renderer.layout)


class SpeechTests(unittest.TestCase):
    def test_occurrences_and_split_words(self):
        boundaries = [dict(text=t, offset=i * 10_000_000, duration=5_000_000) for i, t in enumerate(["rain", "rain", "sun", "lit", "don't"])]
        timings = p.align_boundaries(["rain,", "rain!", "sun-lit", "don't"], boundaries)
        self.assertEqual([t[2] for t in timings], [0, 1, 2, 2, 3])
        self.assertEqual(timings[1][0], 1)

    def test_bad_timing_rejected(self):
        with self.assertRaises(ValueError):
            p.align_boundaries(["42"], [dict(text="forty", offset=0, duration=1)])


if __name__ == "__main__":
    unittest.main()
