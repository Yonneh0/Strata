"""The Monitor tab's own windows: the numbers printed under its graphs, and the averages the page keeps.

The line under a graph is the last minute of samples the server keeps (one each second) - the newest and the two
extremes - so the numbers and the shape above them are one reading, and the Speed card's min and max come from the
same window as its two lines. Two graphs print no line at all: VRAM and the disk read, whose extremes say nothing the
shape above does not say better. The power and temperature averages (#4968, PR by noahark) weigh each poll by the
seconds since the one before it (at most 2), leave idle time out, keep a missing sensor out of its own average and
start afresh with a new totals window or another model; the temperature one is a tooltip of the power card's
temperature graph, because that graph's own line is already the last minute. Tooltips are plain English title= text
(the tab is English-only): no tooltip box of the page's own and no catalog keys. Runs app.js's own functions under
node with stand-ins.

    python -m unittest serve.test_web_monitor_windows
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
NODE = shutil.which("node")


def block(text: str, start: str, end: str) -> str:
    a = text.index(start)
    return text[a:text.index(end, a)]


def run(script: str):
    # the script goes in a file: a Windows command line is too short for the 600-request fixture
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "monitor_windows.js"
        path.write_text(script, encoding="utf-8")
        out = subprocess.run([NODE, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is needed to run the page's own code")
class MonitorWindows(unittest.TestCase):
    def setUp(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.code = ("const els = {}; const $ = (id) => (els[id] ||= {id, textContent: '', innerHTML: '', title: ''});\n"
                     "const fmt = (n, d = 0) => (n == null ? '–' : Number(n).toFixed(d));\n"
                     + block(app, "function stat(", "function setSide(")          # stat and statLine
                     + "function speedRangeLines(h) {\n"                          # the Speed card's min and max, as renderMonitor writes them
                     + block(app, "  // the min and max under the two speeds", "  // a model split across several cards")
                     + "\n}\n"
                     + block(app, "const HW_GAP_S", "\nfunction renderMonitor("))

    def test_the_min_and_max_are_the_window_the_graph_is_drawn_from(self):
        got = run(self.code + """
speedRangeLines({tok_s: [null, 20, 500, 30], prefill_tok_s_mean: [2000, 2000]});
const first = [$('sr-speed').textContent, $('sr-speed').title];
speedRangeLines({tok_s: [20, 500, 30, 700], prefill_tok_s_mean: [2000, 2600]});
process.stdout.write(JSON.stringify({first, text: $('sr-speed').textContent, prefill: $('sr-prefill').textContent}));""")
        self.assertEqual(got["first"][0], "min 20.0 · max 500.0", "the missing sample is left out, not counted as a zero")
        self.assertEqual(got["first"][1], "The slowest and the fastest second of the last 3 the server keeps (a sample each second) - the same window the graph above is drawn from, while it is answering.")
        self.assertEqual(got["text"], "min 20.0 · max 700.0", "a faster second in the next poll moves the number: the card watched from the finished requests never did that")
        self.assertEqual(got["prefill"], "min 2000 · max 2600")

    def test_averages_weigh_time_cap_gaps_and_leave_idle_and_missing_sensors_out(self):
        # polls (seconds, working, power W, temps): one second apart, then a 60 s gap (a background tab), an idle stretch, a card that
        # stops reporting its temperature
        polls = [(0, True, 100, [40, 50]), (1, True, 100, [40, 50]), (2, True, 200, [60, None]), (62, True, 300, [70, 70]),
                 (63, False, 900, [99, 99]), (64, False, 900, [99, 99]), (65, True, None, [80, 80])]
        got = run(self.code + f"""
let now = 0; Date.now = () => now * 1000;
const out = [];
for (const [t, working, p, temps] of {json.dumps(polls)}) {{
  now = t; $('sn-sp-power-temp').title = '';                       // the graph's line writes its own tooltip each poll
  sampleHw({{gpu_power: p, gpus: temps.map((x, i) => ({{index: i, temp: x, name: 'GPU ' + i}}))}}, working, '1|m');
  out.push([$('sr-power').textContent, $('sn-sp-power-temp').title]);
}}
process.stdout.write(JSON.stringify({{out, avg: hwAverages(hwWin), power: $('sr-power').title, temp: $('sn-sp-power-temp').title}}));""")
        # weights: 1 (first poll), 1, 1, 2 (60 s gap capped), idle polls out, 1 (the poll after an idle one)
        avg = got["avg"]
        self.assertEqual(avg["seconds"], 6)
        self.assertAlmostEqual(avg["power"], (100 + 100 + 200 + 2 * 300) / 5)    # the last sample has no power reading
        self.assertAlmostEqual(avg["temps"][0], (40 + 40 + 60 + 2 * 70 + 80) / 6)
        self.assertAlmostEqual(avg["temps"][1], (50 + 50 + 2 * 70 + 80) / 5)       # its missing reading is left out, not counted as 0
        self.assertEqual(got["out"][4], got["out"][3])                             # idle polls change nothing
        self.assertEqual(got["power"], "Average power while the server was working, over the 6 s this page has watched. Idle time is left out.")
        self.assertTrue(got["temp"].startswith("GPU 0: 60 °C · GPU 1: 64 °C\nAverage temperature of each card while the server was working, over the 6 s"))

    def test_one_card_reports_its_temperature_from_the_top_level_fields(self):
        # a machine with one GPU has no per-card rows at all: the average is the top-level reading, or the figure was
        # one no server of a single card ever got to show
        got = run(self.code + """
let now = 0; Date.now = () => now * 1000;
for (const [t, temp] of [[0, 50], [1, 60], [2, 70]]) { now = t; $('sn-sp-power-temp').title = ''; sampleHw({gpu_power: 100, gpu_temp: temp}, true, 'k'); }
process.stdout.write(JSON.stringify({avg: hwAverages(hwWin).temps, title: $('sn-sp-power-temp').title}));""")
        self.assertAlmostEqual(got["avg"][0], 60)
        self.assertTrue(got["title"].startswith("the GPU: 60 °C\nAverage temperature"), got["title"])

    def test_a_new_totals_window_or_another_model_starts_afresh(self):
        got = run(self.code + """
let now = 0; Date.now = () => now * 1000;
const hw = (p) => ({gpu_power: p, gpus: [{index: 0, temp: 50, name: 'A'}]});
const seen = [];
for (const [t, p, key] of [[0, 100, '1|a'], [1, 100, '1|a'], [2, 300, '1|b'], [3, 300, '1|b'], [4, 500, '2|b']]) {
  now = t; sampleHw(hw(p), true, key); seen.push($('sr-power').textContent);
}
process.stdout.write(JSON.stringify(seen));""")
        self.assertEqual(got, ["avg 100 W", "avg 100 W", "avg 300 W", "avg 300 W", "avg 500 W"])

    def test_no_samples_and_no_cards_show_nothing(self):
        got = run(self.code + """
sampleHw({gpus: []}, true, 'k'); sampleHw({gpu_power: null, gpus: []}, false, 'k');
speedRangeLines({});
process.stdout.write(JSON.stringify([$('sr-power').textContent, $('sn-sp-power-temp').textContent, $('sr-speed').textContent, $('sr-speed').title]));""")
        self.assertEqual(got, ["", "", "", ""])

    def test_a_graph_line_names_its_window_and_nothing_else(self):
        got = run(self.code + """
statLine('sp-power', [10, 200, 60], 0, ' W');
statLine('sp-gpu-pcie', [1200, 1800], 1, ' GB/s', {div: 1024, head: 'Gen4 x16', brief: true, tip: 'Traffic the GPU reads over its link.'});
statLine('sp-cpu-temp', [], 0, '°C', {now: true, none: 'no sensor here', noneTitle: 'This machine has none.'});
process.stdout.write(JSON.stringify([$('sn-sp-power').textContent, $('sn-sp-power').title,
                                     $('sn-sp-gpu-pcie').textContent, $('sn-sp-gpu-pcie').title,
                                     $('sn-sp-cpu-temp').textContent, $('sn-sp-cpu-temp').title]));""")
        self.assertEqual(got[0], "min 10 · max 200 W")
        self.assertEqual(got[1], "The last 3 seconds the server keeps (a sample each second) - the same window the graph above is drawn from.")
        self.assertEqual(got[2], "Gen4 x16 · 1.8 GB/s")
        self.assertTrue(got[3].startswith("Traffic the GPU reads over its link. The last 2 seconds"))
        self.assertEqual(got[4], "no sensor here")
        self.assertEqual(got[5], "This machine has none.")

    def test_tooltips_are_english_title_text_and_no_catalog_keys(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        self.assertNotIn("data-tip", app)
        self.assertNotIn("#st-tip", css)
        self.assertNotIn('tr("monitor.', app)
        for text in ["Which model this server runs - apps ask for it by this exact name.",
                     "Average answer speed since the server started: answer tokens divided by the time spent writing them. Waiting in the queue and reading the prompt are not counted.",
                     "Average prompt speed since the server started, counting only the tokens that were really read. What came from the cache is left out.",
                     "The average prompt and answer length of one request.",
                     "How much of each prompt came from the engine's cache instead of being read again. High means follow-ups are fast.",
                     "When this server began counting, and how many requests it has served. Loading or unloading the model does not reset it.",
                     "Averages start with the first request."]:
            self.assertIn(text, app)
        for f in (WEB / "locales").glob("*.json"):
            self.assertFalse([k for k in json.loads(f.read_text(encoding="utf-8")) if k.startswith("monitor.")], f.name)

    def test_the_model_line_is_on_the_state_card_and_the_cards_have_their_elements(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('<div class="state-model" id="state-model"></div>', html)
        self.assertIn('<span class="card-title" id="state-title">Model state</span>', html,
                     "the card is headed by the model's name, and keeps these words until a model is loaded")
        metrics = block(app, "const METRICS = [", "];")
        self.assertNotIn('key: "model"', metrics, "the model is a line on the state card now, not a card of its own")
        self.assertLess(metrics.index('key: "speed"'), metrics.index('key: "gpu"'))
        self.assertNotIn('key: "temp"', metrics, "the temperature is a graph of the power card now")
        self.assertNotIn('key: "pcie"', metrics, "and the PCIe traffic a graph of the load card")
        for el in ('id="sr-speed"', 'id="sr-prefill"', 'id="sr-power"', 'id="mv-${m.key}"', 'id="mv-${m.key}-${s.id}"',
                   'id="sn-${g.id}"', 'class="st-card metric-card" data-key="${m.key}"'):
            self.assertIn(el, app)
        for card in metrics.split("{key: ")[1:]:
            self.assertTrue("graphs:" in card, card.split(",")[0])

    def test_two_graph_rows_share_a_card_without_drawing_over_each_other(self):
        css = (WEB / "app.css").read_text(encoding="utf-8")
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("min-height: min-content", css,
                      "a row of graphs cannot shrink under its own lines - which is how the lower graph came to sit on "
                      "the numbers of the one above it")
        self.assertNotIn('statLine("sp-vram"', app, "VRAM prints no numbers under its graph")
        self.assertNotIn('statLine("sp-disk"', app, "nor does the disk read")
        self.assertNotIn("avg ${f(r.avg)}", app, "a mean over the minute of samples is noise where the shape is the point")


if __name__ == "__main__":
    unittest.main()
