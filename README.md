# Poetree

A poem grows into a flower, conifer, or fern as you enter **one to five words at a time**. The words make the plant. A Python script opens a drawing window; composition happens in the terminal. Requires Python 3.11 or newer.

## Run on Windows

This checkout already has a local `.venv` with the dependencies installed:

```powershell
cd C:\Users\HP\art_practice\everything-computer\poetree
.\.venv\Scripts\python.exe plant_poem.py
```

For a fresh copy:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe plant_poem.py
```

## Run on macOS

Copy `plant_poem.py` and `requirements.txt` into a folder, open a terminal there, and run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python plant_poem.py
```

Do not copy the Windows `.venv` to a Mac. Create it again there. The program uses portable Pygame APIs; macOS runtime behavior has not been tested on this Windows machine.

## Options and controls -- lowk just use the --help flag and skip the slop below

```sh
python plant_poem.py --plant flower
python plant_poem.py --plant conifer --connectors --seed 42
python plant_poem.py --plant fern --no-turns
python plant_poem.py --load output/my-poem.json
python plant_poem.py --load output/my-poem.json --reflow
```

Without `--plant`, choose a plant in the terminal. Enter words and press Enter. Use the terminal or the window's buttons for:

| Command | Behavior |
|---|---|
| `/undo` | Remove the last submission and restore the preceding growth state. |
| `/read` | Speak the poem in entry order, highlighting each spoken word. |
| `/stop` | Cancel speech preparation or stop playback. |
| `/save` | Save PNG and JSON; continue composing. |
| `/save output/my-poem` | Save with a particular basename. |
| `/finish` | Save and end composition; keep the window open for reading. |

In the window, Left/Right step through words silently; Escape stops playback. Closing the window saves and exits. To add words after finishing, reopen the JSON. Reading is also available after finishing. This is a visual/audio reading aid, not a complete native screen-reader interface.

`--no-turns` disables submission-based growth. `--connectors` adds faint lines, off by default. `--seed NUMBER` makes the growth choices reproducible; otherwise each new plant receives a fresh random seed. `--voice NAME` selects an edge-tts voice (default `en-US-AriaNeural`). `--output output/name` supplies the save basename at launch.

**All runtime saves, loads, and speech-cache files are confined to the folder containing the script.** Paths that escape it, including resolved symlinks, are rejected. By default each run saves to its own timestamped basename in `output/`. A resumed session saves a new copy unless you explicitly choose the old basename.

`--load` normally preserves the saved coordinates. Add `--reflow` to apply current spacing rules to an older session without changing its words, turn boundaries, seed, or grammar. The original file is preserved unless you explicitly save over its basename.

## How growth works

The engine is an **input-controlled stochastic parametric L-system**. It stores a tape of modules, starts with one bud, and rewrites the whole old tape into a new tape. It never rewrites newly emitted modules in the same generation.

| Module | Interpretation |
|---|---|
| `B(id, kind, level, side)` | Dormant axis, leaf, side-shoot, or petal bud. |
| `F(word_id, kind, level)` | Advance and place this word occurrence exactly once. |
| `T(angle)` | Turn through a preset angle. |
| `[` and `]` | Save and restore turtle position and direction. |
| `G(ring)` | Start a petal near the head, or continue it from its previous section's endpoint. |

A submission supplies an external activation parameter and its word IDs. The selected bud rewrites to its preset production; every other module uses its identity rule. This is an interactive, conditional extension of an L-system, **not a claim that an ordinary autonomous L-system naturally grows exactly one shoot per human turn**. No analysis of word meaning, sentiment, rhyme, or letters takes place.

Default mode assigns each submission to one growth unit. The first unit is an axis; subsequent units are chosen automatically at eligible sites. The scheduler favors a coherent form and uses a seeded random choice among equivalent sites. Thus one word followed by five and three words followed by three produce different structures. Choice probabilities are restrained; branch angles never depend on word lengths.

- **Flower:** first a stem, then a leaf, six radial petal sites, a second leaf, and further petal whorls. A long poem can keep growing around the flower head.

  Successive petal sections continue from the actual endpoint of the preceding curved section, with normal word spacing and no blank ring between them. A long phrase on one petal does not create a matching empty gap on the other five. Collision padding also stays local to that petal.
- **Conifer:** a vertical axis with pairs of downward-sloping branches. Lower branches gain more space as the crown grows.
- **Fern:** a gently bending central frond and pairs of upward-sloping leaflets.

In `--no-turns` mode the same ordered words fill groups of three, with the last group allowed to be partial. Replaying those groups makes morphology independent of submission boundaries. Adding words may extend the last group and reflow the plant. Undo still removes the last *actual submission*.

Words are whitespace-delimited. Hyphenated words and contractions count as one; punctuation/case are preserved. Repeated words are separate occurrences, each with a distinct ID. Static branch order is intentionally ambiguous; speech and keyboard stepping follow entry order.

Text is measured and rotated along its supporting segment. A separating-axis collision check uses rotated text boxes. Reflow extends crowded segments and moves their descendants without changing branch angles; a bounded spacing fallback handles denser layouts. Existing words may move. The drawing auto-fits the window. Long poems can become small on screen; PNG export is larger but capped at 24 megapixels/7000 pixels per side. For bounded resource use, this proof of concept accepts at most 1500 words, each at most 120 characters; it rejects excess input without losing existing work.

You may finish after any submission, including a single word. The preliminary outline suggestions (flower 12–20 words, fern 20–30, conifer 25–40) are **rough design estimates, not validated thresholds**. The number and sizes of submissions matter. Six petal sites, for example, take six turns to populate.

## Speech and saved state

Speech uses `edge-tts` online synthesis, with explicit `WordBoundary` events, and Pygame MP3 playback. The text of your poem is sent to Microsoft's speech service when you press Read. No API key or Edge installation is required. Audio and timing are cached locally in `.speech-cache/`; composition works without internet.

The program matches boundary fragments sequentially to original word occurrences, including split hyphenated words. It does not guess if the service returns incompatible text normalization or incomplete timing: it reports the issue and leaves composition usable. English is the initial speech target. Numbers, abbreviations, unusual symbols, and other languages depend on the voice/service. Punctuation-only tokens remain in the picture but have no spoken word boundary. Playback highlighting follows the audio player's elapsed time; device latency may cause a small offset. Stop cancels preparation and invalidates late responses.

The online service is not a guaranteed availability contract. Network/audio failures are reported without losing the poem. The included offline checks do not require speech service access.

PNG exports omit buttons, status text, and playback highlights. JSON stores the original submissions, word IDs, grammar, choices, random state, layout, flags, and format version. Loading validates the saved grammar against deterministic replay; it restores saved coordinates exactly. Undo and continued growth replay the same seed and input history. Sessions are data, never executable Python or pickle.

## Verification

```sh
python -m unittest -v
```

The tests cover parallel rewriting, every word occurrence exactly once, grouping differences, continuous-mode grouping independence, angle independence from word length, repeatable seeds, exact undo, invalid input, constrained paths, collision-free sample layouts, save/reload/continued growth, malformed sessions, and speech fragment alignment. Test artifacts are written to `test-output/`.

Verified in this checkout on Windows/Python 3.12: all 21 offline tests passed, including regressions for independent petal spacing and continuous curved endpoints; PNG previews and the window surface were visually inspected; an additional integration check synthesized online speech, decoded the MP3 with SDL's dummy audio driver, observed all eight sample word highlights in order, stopped playback, finished/saved, and reopened the session for more input. Physical speaker latency and a native macOS run remain unverified.

## Sources and design boundary

Algorithm references:

- [University of Chicago: Lindenmayer Systems tutorial](https://www.classes.cs.uchicago.edu/archive/2022/winter/11111-1/lindenmayer-systems/index.html): rewriting, turtle interpretation, stacks, stochastic productions, and conditional parametric rules.
- [St-Amour et al., *Herbarium Racketensis: A Stroll through the Woods* (2017)](https://users.cs.northwestern.edu/~robby/pubs/papers/icfp2017-sffyf.pdf): parallel rewriting and graphical interpretation. The presets here are original simplified interactive grammars, not copies of the paper's plants.

The poetry design uses only the user's supplied definition (meaningful visual arrangement of text and space) and [Good Universe Next Door's supplied article](https://gooduniversenextdoor.com/2022/12/18/examples-of-inventive-poetry-forms/). [The supplied Shadow Poetry page](http://www.shadowpoetry.com/resources/wip/shape.html) could not be retrieved; no claims were inferred from its unavailable text. The typography itself should suggest the plant, even without connector lines. These are stylized visual categories, not comprehensive botanical models.

Implementation references: [Pygame text](https://www.pygame.org/docs/ref/font.html), [rotation](https://www.pygame.org/docs/ref/transform.html), [audio](https://www.pygame.org/docs/ref/music.html), and [edge-tts](https://github.com/rany2/edge-tts).
